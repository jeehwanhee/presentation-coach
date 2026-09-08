"""app/llm/gateway_client.py 단위 테스트.

실제 LLM 게이트웨이 호출(analyze_consistency 전체)은 네트워크/API 키가 필요해서
여기서 다루지 않는다(scripts/test_clova_upload.py처럼 실측용 스크립트가 STT 쪽엔
있지만, LLM 쪽은 아직 없음 — 모델 확정 전까지는 필요할 때 수동으로 검증).
여기서는 후보 탐지, strict 스키마 정규화, LLM 응답 -> report 모델 매핑처럼
순수 로직만 검증한다.
"""

from __future__ import annotations

from app.llm import gateway_client as gc
from app.schemas.job import TranscriptSegment
from app.schemas.report import ConsistencyVerdict, FillerType, ScriptDiffKind
from app.stt.clova_client import WordTiming


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

        result = gc._to_result(parsed, self._segments(), candidates=[], has_script=False)

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

        result = gc._to_result(parsed, self._segments(), candidates=[], has_script=False)

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

        result = gc._to_result(parsed, self._segments(), candidates=[], has_script=False)

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

        result = gc._to_result(parsed, self._segments(), candidates=[], has_script=True)

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

        result = gc._to_result(parsed, self._segments(), candidates, has_script=False)

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

        result = gc._to_result(parsed, self._segments(), candidates, has_script=False)

        assert result.group_b_fillers == []
