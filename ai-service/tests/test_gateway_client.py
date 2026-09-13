"""app/llm/gateway_client.py 단위 테스트.

실제 LLM 게이트웨이 호출(analyze_consistency 전체)은 네트워크/API 키가 필요해서
여기서 다루지 않는다(scripts/test_clova_upload.py처럼 실측용 스크립트가 STT 쪽엔
있지만, LLM 쪽은 아직 없음 — 모델 확정 전까지는 필요할 때 수동으로 검증).
여기서는 후보 탐지, strict 스키마 정규화, LLM 응답 -> report 모델 매핑처럼
순수 로직만 검증한다.
"""

from __future__ import annotations

import json

from app.llm import gateway_client as gc
from app.parsing.pptx_parser import Slide
from app.schemas.job import TranscriptSegment
from app.schemas.report import ConsistencyVerdict, FillerType, ScriptDiffKind
from app.stt.clova_client import TranscriptResult, WordTiming


def _words(*texts: str) -> list[WordTiming]:
    words = []
    t = 0
    for text in texts:
        words.append(WordTiming(text=text, start_ms=t, end_ms=t + 200))
        t += 300
    return words


class TestFindGroupBCandidates:
    def test_exact_matches_are_found_with_correct_type(self):
        words = _words("안녕하세요", "그", "사람이", "저", "발표를")
        candidates = gc.find_group_b_candidates(words)

        found = {c.word.text: c.filler_type for c in candidates}
        assert found == {"그": FillerType.GEU, "저": FillerType.JEO}

    def test_gatda_prefix_matches_inflected_forms(self):
        words = _words("좋은", "것", "같아요")
        candidates = gc.find_group_b_candidates(words)

        assert len(candidates) == 1
        assert candidates[0].word.text == "같아요"
        assert candidates[0].filler_type == FillerType.GATDA

    def test_non_candidate_words_are_ignored(self):
        words = _words("발표를", "시작하겠습니다")
        assert gc.find_group_b_candidates(words) == []

    def test_word_index_matches_position_in_list(self):
        words = _words("음", "그", "다음", "좀")
        candidates = gc.find_group_b_candidates(words)
        assert [c.word_index for c in candidates] == [1, 3]


class TestContextSnippet:
    def test_marks_target_word_with_brackets(self):
        words = _words("발표를", "시작하기", "그", "전에", "먼저")
        snippet = gc._context_snippet(words, index=2)
        assert snippet == "발표를 시작하기 《그》 전에 먼저"

    def test_clamps_at_boundaries(self):
        words = _words("그", "사람이", "말했다")
        snippet = gc._context_snippet(words, index=0)
        assert snippet == "《그》 사람이 말했다"


class TestStrictJsonSchema:
    def test_object_nodes_get_additional_properties_false_and_full_required(self):
        schema = gc._strict_json_schema(gc._LlmOutput)

        def _assert_strict(node):
            if isinstance(node, dict):
                if node.get("type") == "object" and "properties" in node:
                    assert node["additionalProperties"] is False
                    assert set(node["required"]) == set(node["properties"].keys())
                for v in node.values():
                    _assert_strict(v)
            elif isinstance(node, list):
                for v in node:
                    _assert_strict(v)

        _assert_strict(schema)
        # $defs 안의 하위 모델들도 정규화됐는지(빈 스키마가 아닌지) 확인
        assert "$defs" in schema or "properties" in schema


class TestResolveSegmentMs:
    def _segments(self):
        return [
            TranscriptSegment(start_ms=0, end_ms=1000, text="a"),
            TranscriptSegment(start_ms=1000, end_ms=2000, text="b"),
        ]

    def test_valid_index(self):
        assert gc._resolve_segment_ms(self._segments(), 1) == (1000, 2000)

    def test_no_segment_sentinel_returns_none(self):
        assert gc._resolve_segment_ms(self._segments(), gc.NO_SEGMENT) is None

    def test_out_of_range_returns_none(self):
        assert gc._resolve_segment_ms(self._segments(), 99) is None
        assert gc._resolve_segment_ms(self._segments(), -5) is None


class TestToResult:
    def _segments(self):
        return [
            TranscriptSegment(start_ms=0, end_ms=1000, text="첫 세그먼트"),
            TranscriptSegment(start_ms=1000, end_ms=2000, text="둘째 세그먼트"),
        ]

    def test_consistency_counts_and_evidence_resolution(self):
        parsed = gc._LlmOutput(
            checks=[
                gc._LlmConsistencyCheck(
                    slide_index=0,
                    claim="주장1",
                    verdict=ConsistencyVerdict.SUPPORTED,
                    evidence_span="근거 텍스트",
                    evidence_segment_index=0,
                ),
                gc._LlmConsistencyCheck(
                    slide_index=1,
                    claim="주장2",
                    verdict=ConsistencyVerdict.NOT_MENTIONED,
                    evidence_span="",
                    evidence_segment_index=gc.NO_SEGMENT,
                ),
            ],
            off_topic=[],
            logic_gaps=[],
            script_diff=None,
            group_b_fillers=[],
        )

        result = gc._to_result(parsed, [], self._segments(), candidates=[], has_script=False)

        assert result.consistency.total_claims == 2
        assert result.consistency.supported_count == 1
        assert result.consistency.checks[0].evidence_at_ms == 0
        assert result.consistency.checks[1].evidence_at_ms is None
        assert result.consistency.checks[1].evidence_span is None

    def test_off_topic_and_logic_gap_out_of_range_are_dropped(self):
        parsed = gc._LlmOutput(
            checks=[],
            off_topic=[
                gc._LlmOffTopic(
                    start_segment_index=0, end_segment_index=99, text="x", reason="y"
                )
            ],
            logic_gaps=[gc._LlmLogicGap(segment_index=99, text="x", note="y")],
            script_diff=None,
            group_b_fillers=[],
        )

        result = gc._to_result(parsed, [], self._segments(), candidates=[], has_script=False)

        assert result.off_topic == []
        assert result.logic_gaps == []

    def test_script_diff_forced_none_when_no_script(self):
        parsed = gc._LlmOutput(
            checks=[],
            off_topic=[],
            logic_gaps=[],
            script_diff=gc._LlmScriptDiff(matched_ratio=0.5, deviations=[]),
            group_b_fillers=[],
        )

        result = gc._to_result(parsed, [], self._segments(), candidates=[], has_script=False)

        assert result.script_diff is None

    def test_script_diff_kept_when_script_given(self):
        parsed = gc._LlmOutput(
            checks=[],
            off_topic=[],
            logic_gaps=[],
            script_diff=gc._LlmScriptDiff(
                matched_ratio=0.8,
                deviations=[
                    gc._LlmScriptDeviation(
                        segment_index=1,
                        script_text="원래 대본",
                        spoken_text="실제 발화",
                        kind=ScriptDiffKind.CHANGED,
                    )
                ],
            ),
            group_b_fillers=[],
        )

        result = gc._to_result(parsed, [], self._segments(), candidates=[], has_script=True)

        assert result.script_diff is not None
        assert result.script_diff.matched_ratio == 0.8
        assert len(result.script_diff.deviations) == 1
        assert result.script_diff.deviations[0].at_ms == 1000

    def test_group_b_judgment_true_becomes_filler_false_is_dropped(self):
        words = _words("그", "저")
        candidates = [
            gc.GroupBCandidate(0, words[0], FillerType.GEU),
            gc.GroupBCandidate(1, words[1], FillerType.JEO),
        ]
        parsed = gc._LlmOutput(
            checks=[],
            off_topic=[],
            logic_gaps=[],
            script_diff=None,
            group_b_fillers=[
                gc._LlmGroupBJudgment(candidate_id=0, is_filler=True),
                gc._LlmGroupBJudgment(candidate_id=1, is_filler=False),
            ],
        )

        result = gc._to_result(parsed, [], self._segments(), candidates, has_script=False)

        assert len(result.group_b_fillers) == 1
        f = result.group_b_fillers[0]
        assert f.type == FillerType.GEU
        assert f.text == "그"
        assert f.at_ms == 0

    def test_missing_judgment_defaults_to_not_filler(self):
        words = _words("막")
        candidates = [gc.GroupBCandidate(0, words[0], FillerType.MAK)]
        parsed = gc._LlmOutput(
            checks=[], off_topic=[], logic_gaps=[], script_diff=None, group_b_fillers=[]
        )

        result = gc._to_result(parsed, [], self._segments(), candidates, has_script=False)

        assert result.group_b_fillers == []


class TestSlideCoverageBackfill:
    """LLM이 일부 슬라이드에 대한 checks를 안 만들었을 때 코드가 채워 넣는지 검증.

    실측(발화가 마이크 테스트 한 줄뿐이었던 케이스)에서 5슬라이드 중 1개만
    checks에 나온 사례가 있어서, 슬라이드 커버리지를 코드로 강제하게 됨.
    """

    def _segments(self):
        return [TranscriptSegment(start_ms=0, end_ms=1000, text="세그먼트")]

    def _slides(self):
        return [
            Slide(slide_index=0, text="제목 슬라이드\n부제목"),
            Slide(slide_index=1, text="문제 정의\n핵심 내용"),
            Slide(slide_index=2, text="솔루션"),
        ]

    def test_missing_slides_are_backfilled_as_not_mentioned(self):
        # LLM이 slide_index=1만 checks로 만들고 0, 2는 빼먹은 상황을 재현.
        parsed = gc._LlmOutput(
            checks=[
                gc._LlmConsistencyCheck(
                    slide_index=1,
                    claim="문제 정의 주장",
                    verdict=ConsistencyVerdict.SUPPORTED,
                    evidence_span="근거",
                    evidence_segment_index=0,
                )
            ],
            off_topic=[],
            logic_gaps=[],
            script_diff=None,
            group_b_fillers=[],
        )

        result = gc._to_result(parsed, self._slides(), self._segments(), candidates=[], has_script=False)

        assert [c.slide_index for c in result.consistency.checks] == [0, 1, 2]
        assert result.consistency.total_claims == 3
        assert result.consistency.supported_count == 1  # 보강된 건 SUPPORTED로 안 침

        backfilled_0 = result.consistency.checks[0]
        assert backfilled_0.verdict == ConsistencyVerdict.NOT_MENTIONED
        assert backfilled_0.claim == "제목 슬라이드"  # 첫 줄을 그대로 씀
        assert backfilled_0.evidence_span is None
        assert backfilled_0.evidence_at_ms is None

        backfilled_2 = result.consistency.checks[2]
        assert backfilled_2.verdict == ConsistencyVerdict.NOT_MENTIONED
        assert backfilled_2.claim == "솔루션"

    def test_no_backfill_when_llm_already_covered_every_slide(self):
        parsed = gc._LlmOutput(
            checks=[
                gc._LlmConsistencyCheck(
                    slide_index=i,
                    claim=f"주장{i}",
                    verdict=ConsistencyVerdict.SUPPORTED,
                    evidence_span="근거",
                    evidence_segment_index=0,
                )
                for i in range(3)
            ],
            off_topic=[],
            logic_gaps=[],
            script_diff=None,
            group_b_fillers=[],
        )

        result = gc._to_result(parsed, self._slides(), self._segments(), candidates=[], has_script=False)

        assert result.consistency.total_claims == 3
        assert [c.claim for c in result.consistency.checks] == ["주장0", "주장1", "주장2"]

    def test_checks_are_sorted_by_slide_index_after_backfill(self):
        # LLM이 순서를 안 지키고(2번 먼저) 0/1은 빼먹은 상황.
        parsed = gc._LlmOutput(
            checks=[
                gc._LlmConsistencyCheck(
                    slide_index=2,
                    claim="솔루션 주장",
                    verdict=ConsistencyVerdict.NO_BASIS,
                    evidence_span="근거",
                    evidence_segment_index=0,
                )
            ],
            off_topic=[],
            logic_gaps=[],
            script_diff=None,
            group_b_fillers=[],
        )

        result = gc._to_result(parsed, self._slides(), self._segments(), candidates=[], has_script=False)

        assert [c.slide_index for c in result.consistency.checks] == [0, 1, 2]

    def test_empty_slide_text_falls_back_to_placeholder_claim(self):
        parsed = gc._LlmOutput(
            checks=[], off_topic=[], logic_gaps=[], script_diff=None, group_b_fillers=[]
        )
        slides = [Slide(slide_index=0, text="   \n  ")]

        result = gc._to_result(parsed, slides, self._segments(), candidates=[], has_script=False)

        assert result.consistency.checks[0].claim == "(슬라이드 내용 없음)"


# --- 재시도(누락 슬라이드 강제 재판정) LLM 호출 테스트용 페이크 OpenAI 클라이언트 ---
# openai.OpenAI 인스턴스를 실제로 만들지 않고 client.chat.completions.create(...)
# 호출만 흉내낸다 — 네트워크/API 키 없이 순수 로직(요청 payload 구성, 응답 파싱,
# analyze_consistency 안에서의 흐름 분기)만 검증하기 위함.


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content, usage=None):
        self.choices = [_FakeChoice(content)]
        self.usage = usage


class _FakeCompletions:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _FakeChat:
    def __init__(self, completions):
        self.completions = completions


class _FakeClient:
    def __init__(self, responses):
        self.chat = _FakeChat(_FakeCompletions(responses))


class TestRetryMissingSlideChecks:
    def _segments(self):
        return [TranscriptSegment(start_ms=0, end_ms=1000, text="세그먼트")]

    def test_sends_only_missing_slides_and_returns_parsed_checks(self):
        missing_slides = [Slide(slide_index=2, text="솔루션")]
        response_json = json.dumps(
            {
                "checks": [
                    {
                        "slide_index": 2,
                        "claim": "실제 LLM이 뽑은 주장",
                        "verdict": "SUPPORTED",
                        "evidence_span": "근거",
                        "evidence_segment_index": 0,
                    }
                ]
            }
        )
        fake_client = _FakeClient([_FakeResponse(response_json)])

        checks = gc._retry_missing_slide_checks(
            fake_client, "test-model", missing_slides, self._segments()
        )

        assert len(checks) == 1
        assert checks[0].slide_index == 2
        assert checks[0].claim == "실제 LLM이 뽑은 주장"
        assert checks[0].verdict == ConsistencyVerdict.SUPPORTED

        # 요청 payload에 누락된 슬라이드만 들어갔는지 확인(전체 슬라이드를 다시
        # 보내는 게 아니라 재시도 대상만 보내야 함).
        call = fake_client.chat.completions.calls[0]
        sent_payload = json.loads(call["messages"][1]["content"])
        assert sent_payload["slides"] == [{"slide_index": 2, "text": "솔루션"}]
        assert call["messages"][0]["content"] == gc._RETRY_SYSTEM_PROMPT

    def test_empty_response_raises_llm_error(self):
        fake_client = _FakeClient([_FakeResponse(None)])

        try:
            gc._retry_missing_slide_checks(fake_client, "test-model", [], self._segments())
            assert False, "LlmError가 발생해야 함"
        except gc.LlmError:
            pass


class TestAnalyzeConsistencyRetryWiring:
    """analyze_consistency가 1차 호출에서 빠진 슬라이드를 재시도 호출로 메꾸는지,
    재시도마저 실패하면 기존 코드 백필(_fallback_claim)로 넘어가는지 검증한다.
    """

    def _slides(self):
        return [
            Slide(slide_index=0, text="제목 슬라이드"),
            Slide(slide_index=1, text="본론 슬라이드"),
        ]

    def _transcript(self):
        return TranscriptResult(
            full_text="안녕하세요",
            words=_words("안녕하세요"),
            segments=[TranscriptSegment(start_ms=0, end_ms=1000, text="안녕하세요")],
        )

    def _first_call_response(self, *, only_slide_index: int) -> _FakeResponse:
        # 1차 호출이 slide_index=only_slide_index 하나만 만들고 나머지를 빼먹은
        # 상황을 재현.
        body = json.dumps(
            {
                "checks": [
                    {
                        "slide_index": only_slide_index,
                        "claim": "1차 주장",
                        "verdict": "SUPPORTED",
                        "evidence_span": "근거",
                        "evidence_segment_index": 0,
                    }
                ],
                "off_topic": [],
                "logic_gaps": [],
                "script_diff": None,
                "group_b_fillers": [],
            }
        )
        return _FakeResponse(body)

    def test_retry_call_fills_missing_slide_with_real_llm_claim(self, monkeypatch):
        monkeypatch.setenv("LLM_GATEWAY_API_KEY", "test-key")

        retry_body = json.dumps(
            {
                "checks": [
                    {
                        "slide_index": 1,
                        "claim": "재시도로 뽑은 진짜 주장",
                        "verdict": "NOT_MENTIONED",
                        "evidence_span": "",
                        "evidence_segment_index": gc.NO_SEGMENT,
                    }
                ]
            }
        )
        fake_client = _FakeClient(
            [self._first_call_response(only_slide_index=0), _FakeResponse(retry_body)]
        )
        monkeypatch.setattr(gc, "_client", lambda base_url, api_key: fake_client)

        result = gc.analyze_consistency(self._slides(), self._transcript(), script=None)

        assert [c.slide_index for c in result.consistency.checks] == [0, 1]
        # 재시도 호출이 실제로 두 번째(별도) 호출로 나갔는지 확인
        assert len(fake_client.chat.completions.calls) == 2
        # 코드 백필(원문 첫 줄)이 아니라 재시도 LLM이 만든 진짜 주장이 쓰였는지 확인
        assert result.consistency.checks[1].claim == "재시도로 뽑은 진짜 주장"
        assert result.consistency.checks[1].verdict == ConsistencyVerdict.NOT_MENTIONED

    def test_no_retry_call_when_first_response_already_covers_every_slide(self, monkeypatch):
        monkeypatch.setenv("LLM_GATEWAY_API_KEY", "test-key")

        body = json.dumps(
            {
                "checks": [
                    {
                        "slide_index": i,
                        "claim": f"주장{i}",
                        "verdict": "SUPPORTED",
                        "evidence_span": "근거",
                        "evidence_segment_index": 0,
                    }
                    for i in range(2)
                ],
                "off_topic": [],
                "logic_gaps": [],
                "script_diff": None,
                "group_b_fillers": [],
            }
        )
        fake_client = _FakeClient([_FakeResponse(body)])
        monkeypatch.setattr(gc, "_client", lambda base_url, api_key: fake_client)

        result = gc.analyze_consistency(self._slides(), self._transcript(), script=None)

        assert len(fake_client.chat.completions.calls) == 1  # 재시도 호출 없음
        assert [c.claim for c in result.consistency.checks] == ["주장0", "주장1"]

    def test_retry_call_failure_falls_back_to_code_backfill(self, monkeypatch):
        monkeypatch.setenv("LLM_GATEWAY_API_KEY", "test-key")

        fake_client = _FakeClient(
            [
                self._first_call_response(only_slide_index=0),
                RuntimeError("게이트웨이 타임아웃"),
            ]
        )
        monkeypatch.setattr(gc, "_client", lambda base_url, api_key: fake_client)

        result = gc.analyze_consistency(self._slides(), self._transcript(), script=None)

        assert [c.slide_index for c in result.consistency.checks] == [0, 1]
        # 재시도가 예외를 던졌으니 _to_result의 코드 백필(슬라이드 첫 줄)로 대체됨
        assert result.consistency.checks[1].claim == "본론 슬라이드"
        assert result.consistency.checks[1].verdict == ConsistencyVerdict.NOT_MENTIONED
