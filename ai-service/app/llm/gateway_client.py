"""LLM 게이트웨이 클라이언트 (건국대 API Gateway, Mindlogic factchat-cloud).

OpenAI Chat Completions 호환 — 기존 OpenAI SDK에 base_url만 교체해서 사용.
response_format(json_schema, strict) 지원 확인됨 — 구조화 출력을 강제한다
(AI_설계.md §3).

모델은 미확정: gemini-3.5-flash-lite(최저 비용 후보)부터 시작해서 정합성
판정 정확도를 실데이터로 테스트한 뒤 부족하면 상위 모델로 전환.

이 모듈이 LLM 호출 한 번으로 같이 처리하는 것 (AI_설계.md §2/§3, 비용 절감 위해
정합성 판정과 그룹B 채움말 판단을 한 호출에 묶기로 확정):
- consistency: 슬라이드 주장 vs 발화 내용 대조
- off_topic: 슬라이드 주제와 무관한 발화 구간
- logic_gaps: 근거 없이 결론으로 건너뛰는 구간
- script_diff: 대본이 있을 때만 — 대본과 실제 발화 차이
- 그룹B 채움말("그"/"저"/"뭐"/"뭔가"/"좀"/"막"/"그냥"/"같다") — 문맥상 필러 용법인지
  정상 용법인지 판단. 그룹A(음/어, VAD 기반)는 app/audio/delivery_metrics.py가 이미
  처리하므로 여기서 다루지 않음 — run_analysis.py에서 두 결과를 합친다.

LLM에게 원시 ms 타임스탬프를 직접 만들어내라고 시키지 않는다(환각 위험). 대신
transcript.segments를 번호 붙여서 보여주고, LLM은 "몇 번 세그먼트가 근거/위치인지"
인덱스만 답하게 한 뒤 여기서 실제 start_ms/end_ms로 치환한다.
"""

from __future__ import annotations

import json
import logging
import os

from dataclasses import dataclass

from openai import OpenAI
from pydantic import BaseModel

from app.parsing.pptx_parser import Slide
from app.schemas.job import TranscriptSegment
from app.schemas.report import (
    Consistency,
    ConsistencyCheck,
    ConsistencyVerdict,
    Filler,
    FillerType,
    LogicGap,
    OffTopicSegment,
    ScriptDeviation,
    ScriptDiff,
    ScriptDiffKind,
)
from app.stt.clova_client import TranscriptResult, WordTiming

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3.5-flash-lite"
DEFAULT_BASE_URL = "https://factchat-cloud.mindlogic.ai/v1/gateway"

# 근거/위치를 못 찾았을 때 LLM이 답하는 sentinel 인덱스.
NO_SEGMENT = -1

# 그룹B 채움말 후보 — 정확히 일치하면 후보로 잡는 단어들.
# app/schemas/report.py의 FillerType 그룹B 항목과 동일해야 함(문서 §2 참고).
GROUP_B_EXACT: dict[str, FillerType] = {
    "그": FillerType.GEU,
    "저": FillerType.JEO,
    "뭐": FillerType.MWO,
    "뭔가": FillerType.MWONGA,
    "좀": FillerType.JOM,
    "막": FillerType.MAK,
    "그냥": FillerType.GEUNYANG,
}
# "같다"는 어미가 활용돼서("같아요"/"같은"/"같습니다") 원형 그대로 나오는 경우가
# 드물어 어간 접두 매칭으로 후보를 넓게 잡는다. 오탐(예: "같이") 걸러내는 건 LLM 몫.
GATDA_PREFIX = "같"
GATDA_TYPE = FillerType.GATDA

# 후보 단어의 문맥 판단을 돕기 위해 앞뒤로 같이 보여줄 단어 수.
CONTEXT_WINDOW = 4
# 문맥에서 판단 대상 후보를 명확히 표시하는 마커(발화 텍스트에 나올 일이 없는 기호).
_MARK_OPEN, _MARK_CLOSE = "《", "》"


class LlmError(Exception):
    """LLM 실패. app.schemas.job.ErrorCode.LLM_FAILED에 대응."""


@dataclass
class ConsistencyAnalysisResult:
    """LLM 한 번 호출로 얻는 결과 묶음.

    group_b_fillers는 delivery.fillers(그룹A)와 별개 리스트로 나온다 —
    run_analysis.py에서 둘을 합쳐야 최종 fillers 전체 목록이 된다.
    """

    consistency: Consistency
    off_topic: list[OffTopicSegment]
    logic_gaps: list[LogicGap]
    script_diff: ScriptDiff | None
    group_b_fillers: list[Filler]
    # 모델별 비용 비교용(2026-09-12 추가) — 게이트웨이가 OpenAI 호환 usage 필드를
    # 주면 그대로 담는다. 응답에 usage가 없으면 None(운영 로직은 이 필드를 안 봄,
    # scripts/test_model_comparison.py에서 모델 간 토큰 소모량 비교에만 사용).
    token_usage: dict | None = None


@dataclass
class GroupBCandidate:
    word_index: int  # transcript.words 안에서의 위치 (디버깅용)
    word: WordTiming
    filler_type: FillerType


def find_group_b_candidates(words: list[WordTiming]) -> list[GroupBCandidate]:
    """단어 목록에서 그룹B 필러 후보를 문자열 매칭으로 뽑는다.

    여기서는 "후보"만 뽑는다 — 실제로 필러인지(vs "저는 발표를"의 정상적인
    1인칭 대명사 "저") 판단은 LLM이 문맥을 보고 한다(analyze_consistency).
    """
    candidates: list[GroupBCandidate] = []
    for i, w in enumerate(words):
        text = w.text.strip()
        if not text:
            continue
        if text in GROUP_B_EXACT:
            candidates.append(GroupBCandidate(i, w, GROUP_B_EXACT[text]))
        elif text.startswith(GATDA_PREFIX):
            candidates.append(GroupBCandidate(i, w, GATDA_TYPE))
    return candidates


def _context_snippet(words: list[WordTiming], index: int) -> str:
    """후보 단어를 《 》로 표시한 앞뒤 문맥 스니펫. LLM이 정확히 어느 토큰을
    판단해야 하는지 헷갈리지 않게(같은 단어가 문맥 안에 여러 번 나올 수 있어서)."""
    start = max(0, index - CONTEXT_WINDOW)
    end = min(len(words), index + CONTEXT_WINDOW + 1)
    parts = []
    for j in range(start, end):
        text = words[j].text
        parts.append(f"{_MARK_OPEN}{text}{_MARK_CLOSE}" if j == index else text)
    return " ".join(parts)


# --- LLM 응답 스키마 (내부용 — app.schemas.report와 1:1은 아님) ---
# segment_index/evidence_segment_index는 transcript.segments의 번호. 원시 ms는
# LLM에게 만들게 하지 않고 여기서 인덱스 -> 실제 ms로 치환한다(환각 방지).


class _LlmConsistencyCheck(BaseModel):
    slide_index: int
    claim: str
    verdict: ConsistencyVerdict
    evidence_span: str
    evidence_segment_index: int  # 근거 없으면 NO_SEGMENT(-1)


class _LlmOffTopic(BaseModel):
    start_segment_index: int
    end_segment_index: int
    text: str
    reason: str


class _LlmLogicGap(BaseModel):
    segment_index: int
    text: str
    note: str


class _LlmScriptDeviation(BaseModel):
    segment_index: int
    script_text: str
    spoken_text: str
    kind: ScriptDiffKind


class _LlmScriptDiff(BaseModel):
    matched_ratio: float
    deviations: list[_LlmScriptDeviation]


class _LlmGroupBJudgment(BaseModel):
    candidate_id: int
    is_filler: bool


class _LlmOutput(BaseModel):
    checks: list[_LlmConsistencyCheck]
    off_topic: list[_LlmOffTopic]
    logic_gaps: list[_LlmLogicGap]
    script_diff: _LlmScriptDiff | None
    group_b_fillers: list[_LlmGroupBJudgment]


class _LlmChecksOnly(BaseModel):
    """누락 슬라이드 재시도 호출 전용 응답 스키마 — checks만 받는다."""

    checks: list[_LlmConsistencyCheck]


_SYSTEM_PROMPT = """\
너는 발표 리허설을 돕는 코치의 분석 엔진이다. 발표자의 슬라이드 내용과 실제
발화 전사(시간 순서대로 번호가 붙은 세그먼트)를 비교해서 아래 항목을 판단한다.
반드시 주어진 JSON 스키마 형식으로만 답하고, 없는 사실을 지어내지 않는다.

1. checks (슬라이드별 정합성): 각 슬라이드의 핵심 주장을 하나 이상 뽑아서, 그
   주장이 발화에서 어떻게 다뤄졌는지 판정한다.
   - SUPPORTED: 발화에서 해당 주장을 뒷받침하는 내용을 실제로 말함.
   - NOT_MENTIONED: 슬라이드에는 있는데 발화에서 언급 자체를 안 함.
   - NO_BASIS: 슬라이드 내용과 다르거나 근거 없이 다른 주장을 함.
   evidence_segment_index는 판정의 근거가 된 세그먼트 번호(NOT_MENTIONED면 -1).
   **주장 추출은 슬라이드 내용만 보고 한다 — 발화 분량과 무관하다.** 발화가
   아주 짧거나(예: 마이크 테스트), 슬라이드 내용을 전혀 언급하지 않았어도 그
   이유로 주장 추출 자체를 건너뛰지 않는다. 주어진 slides 목록의 모든
   slide_index에 대해 최소 1개씩 checks 항목을 반드시 만들 것 — 근거를 못
   찾으면 그 주장은 NOT_MENTIONED로 판정하면 되고, 주장을 아예 안 만드는 것은
   허용되지 않는다.
2. off_topic: 슬라이드 주제와 무관한 발화 구간(예: 잡담, 본론과 상관없는 이야기).
   start/end_segment_index로 구간을 표시. **checks에서 슬라이드들을
   NOT_MENTIONED로 판정했다는 사실 하나만으로 off_topic 판단을 생략하지
   않는다.** "슬라이드 내용을 언급 안 함"과 "슬라이드 주제와 무관한 얘기를
   함"은 서로 다른 질문이니 반드시 둘 다 따로 확인할 것 — 발화 내용이 슬라이드
   주제와 실제로 무관하다면(예: 발표 주제와 전혀 다른 얘기를 처음부터 끝까지
   했다면) 그 구간(들)을 반드시 off_topic으로도 표시해야 한다. 발화 전체가
   슬라이드 주제와 무관하다면 전체 세그먼트 범위를 하나의 off_topic 항목으로
   만들 것 — "전부 무관해서 애매하다"는 이유로 off_topic을 통째로 비워두지
   않는다.
3. logic_gaps: 근거 설명 없이 결론으로 건너뛰거나 인과관계가 불명확한 구간.
4. script_diff: 대본이 주어졌을 때만 채운다(대본이 없으면 반드시 null).
   대본 문장과 실제 발화가 다른 지점을 생략(대본에 있는데 말 안 함)/추가(대본에
   없는데 말함)/변경(같은 내용을 다른 표현으로) 중 하나로 분류.
5. group_b_fillers: 후보 목록으로 주어진 단어들이 실제로 "채움말/군더더기
   표현"으로 쓰였는지 판단한다. 《 》로 표시된 단어가 판단 대상이다.
   - 필러로 볼 것: 의미 없이 말을 잇기 위해 넣은 경우 (예: "그... 그게 그러니까",
     "좀... 그런 게 있어서", "막 그렇게 됐어요"처럼 실질적 의미 기여가 없는 사용).
   - 필러가 아닌 것: 정상적인 문법적 역할 (예: "저는 발표를 시작하겠습니다"의
     "저"=1인칭 대명사, "그 사람이 말한"의 "그"=지시대명사, "3배 좀 넘게"의
     "좀"=수량 부사, "다른 것 같습니다"의 "같다"=추측 표현이지만 문장 의미상
     필요한 서술어).
   판단이 애매하면 정상 용법(is_filler=false) 쪽으로 판정한다(과탐 방지).

세그먼트 번호(segment_index)는 항상 입력으로 주어진 범위 안의 정수여야 하고,
근거를 못 찾으면 -1을 쓴다. 지어낸 번호를 쓰지 않는다.\
"""


# 1차 호출에서 슬라이드가 누락됐을 때 그 슬라이드만 다시 강제 판정시키는 재시도
# 전용 프롬프트. _fallback_claim(슬라이드 첫 줄 잘라내기)보다 먼저 시도한다 —
# 실제 LLM 판단이 원문 첫 줄보다 낫기 때문. 발화 분량/내용과 무관하게 반드시
# 모든 slide_index에 대해 checks를 만들라고 다시 한번 명시적으로 강제한다.
_RETRY_SYSTEM_PROMPT = """\
너는 발표 슬라이드의 핵심 주장을 뽑아 발화 내용과 대조하는 채점자다. 지금
주어지는 slides는 1차 분석에서 어떤 이유로든 판정이 누락된 슬라이드들이다.
발화 분량이 적거나 슬라이드 내용과 전혀 무관해 보여도 그 이유로 절대
건너뛰지 않는다 — 주어진 slides 목록의 모든 slide_index에 대해 반드시
1개 이상의 checks 항목을 만들어야 한다(항목을 아예 안 만드는 것은 허용되지
않는다).

각 슬라이드의 핵심 주장을 슬라이드 텍스트만 보고 뽑은 뒤, 그 주장이
transcript_segments(시간 순서대로 번호가 붙은 발화 세그먼트)에서 어떻게
다뤄졌는지 판정한다.
- SUPPORTED: 발화에서 해당 주장을 뒷받침하는 내용을 실제로 말함.
- NOT_MENTIONED: 슬라이드에는 있는데 발화에서 언급 자체를 안 함.
- NO_BASIS: 슬라이드 내용과 다르거나 근거 없이 다른 주장을 함.
evidence_segment_index는 판정의 근거가 된 세그먼트 번호(근거를 못 찾으면
-1). 지어낸 세그먼트 번호를 쓰지 않는다. 반드시 주어진 JSON 스키마 형식으로만
답한다.\
"""


def _client(base_url: str, api_key: str) -> OpenAI:
    return OpenAI(base_url=base_url, api_key=api_key)


def _strict_json_schema(model: type[BaseModel]) -> dict:
    """pydantic 스키마를 OpenAI 호환 strict 모드 요구사항에 맞게 정규화한다.

    strict 모드는 모든 object 노드에 additionalProperties:false가 있어야 하고,
    모든 속성이 required에 들어가 있어야 한다(옵셔널 필드는 타입에 null을
    포함시켜 표현하되 required 목록에서 빠지면 안 됨). pydantic
    model_json_schema()는 이 조건을 기본으로 만족하지 않으므로 후처리한다.
    """
    schema = model.model_json_schema()

    def _walk(node: object) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"].keys())
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for value in node:
                _walk(value)

    _walk(schema)
    return schema


def _build_user_payload(
    slides: list[Slide],
    segments: list[TranscriptSegment],
    script: str | None,
    candidates: list[GroupBCandidate],
    words: list[WordTiming],
) -> dict:
    return {
        "slides": [{"slide_index": s.slide_index, "text": s.text} for s in slides],
        "transcript_segments": [
            {"index": i, "start_ms": seg.start_ms, "end_ms": seg.end_ms, "text": seg.text}
            for i, seg in enumerate(segments)
        ],
        "script": script,  # None이면 그대로 JSON null -> "대본 없음"으로 인식
        "group_b_candidates": [
            {
                "candidate_id": i,
                "word": c.word.text,
                "context": _context_snippet(words, c.word_index),
            }
            for i, c in enumerate(candidates)
        ],
    }


def _resolve_segment_ms(
    segments: list[TranscriptSegment], index: int
) -> tuple[int, int] | None:
    """세그먼트 인덱스 -> (start_ms, end_ms). 범위 밖이면 None(그 항목은 버림)."""
    if index == NO_SEGMENT or not (0 <= index < len(segments)):
        return None
    seg = segments[index]
    return seg.start_ms, seg.end_ms


_FALLBACK_CLAIM_MAX_LEN = 80


def _fallback_claim(slide_text: str) -> str:
    """LLM이 이 슬라이드에 대한 checks 항목을 하나도 안 만들었을 때 쓰는 대체 주장문.

    시스템 프롬프트가 "모든 슬라이드에 최소 1개"를 지시하지만 LLM 지시 이행은
    보장되지 않으므로(발화가 아주 짧거나 주제와 무관할 때 슬라이드 자체를
    건너뛰는 사례 실측 확인), _to_result에서 코드로 한 번 더 강제한다. 슬라이드
    원문의 첫 줄(대개 제목/핵심 문장)을 잘라서 claim으로 쓴다.
    """
    for line in slide_text.splitlines():
        line = line.strip()
        if line:
            return line[:_FALLBACK_CLAIM_MAX_LEN]
    return "(슬라이드 내용 없음)"


def _retry_missing_slide_checks(
    client: OpenAI,
    model: str,
    missing_slides: list[Slide],
    segments: list[TranscriptSegment],
) -> list[_LlmConsistencyCheck]:
    """1차 호출에서 누락된 슬라이드만 골라 LLM에게 재판정을 다시 강제로 시킨다.

    _fallback_claim(슬라이드 원문 첫 줄 잘라내기)으로 바로 채우지 않고, 발화
    분량과 무관하게 진짜 주장 추출 + 판정을 한 번 더 LLM에 요청한다 — 이
    호출도 실패하면(네트워크/파싱 등) 예외를 그대로 던지고, 호출부
    (analyze_consistency)에서 잡아서 _to_result의 코드 백필로 넘어간다(최종
    안전망은 그대로 유지).
    """
    payload = {
        "slides": [{"slide_index": s.slide_index, "text": s.text} for s in missing_slides],
        "transcript_segments": [
            {"index": i, "start_ms": seg.start_ms, "end_ms": seg.end_ms, "text": seg.text}
            for i, seg in enumerate(segments)
        ],
    }
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _RETRY_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "missing_slide_checks",
                "strict": True,
                "schema": _strict_json_schema(_LlmChecksOnly),
            },
        },
    )
    raw = response.choices[0].message.content
    if not raw:
        raise LlmError("누락 슬라이드 재시도 응답이 비어있음")
    return _LlmChecksOnly.model_validate_json(raw).checks


def _to_result(
    parsed: _LlmOutput,
    slides: list[Slide],
    segments: list[TranscriptSegment],
    candidates: list[GroupBCandidate],
    has_script: bool,
) -> ConsistencyAnalysisResult:
    checks: list[ConsistencyCheck] = []
    for c in parsed.checks:
        resolved = _resolve_segment_ms(segments, c.evidence_segment_index)
        checks.append(
            ConsistencyCheck(
                slide_index=c.slide_index,
                claim=c.claim,
                verdict=c.verdict,
                evidence_span=c.evidence_span if resolved else None,
                evidence_at_ms=resolved[0] if resolved else None,
            )
        )

    # 슬라이드 커버리지 강제: 프롬프트로 "모든 슬라이드에 최소 1개"를 요청해도
    # LLM이 빼먹을 수 있어(발화가 부실할 때 실측으로 확인된 사례), 코드에서
    # 한 번 더 검증하고 빠진 슬라이드는 NOT_MENTIONED로 채워 넣는다 — 리포트에
    # 슬라이드가 통째로 누락되는 일이 없도록.
    covered = {c.slide_index for c in checks}
    for slide in slides:
        if slide.slide_index in covered:
            continue
        logger.warning(
            "슬라이드 %d에 대한 정합성 판정을 LLM이 만들지 않아 자동으로 보강함",
            slide.slide_index,
        )
        checks.append(
            ConsistencyCheck(
                slide_index=slide.slide_index,
                claim=_fallback_claim(slide.text),
                verdict=ConsistencyVerdict.NOT_MENTIONED,
                evidence_span=None,
                evidence_at_ms=None,
            )
        )
    checks.sort(key=lambda c: c.slide_index)

    supported_count = sum(1 for c in checks if c.verdict == ConsistencyVerdict.SUPPORTED)
    consistency = Consistency(
        checks=checks, supported_count=supported_count, total_claims=len(checks)
    )

    off_topic: list[OffTopicSegment] = []
    for ot in parsed.off_topic:
        start = _resolve_segment_ms(segments, ot.start_segment_index)
        end = _resolve_segment_ms(segments, ot.end_segment_index)
        if start is None or end is None:
            logger.warning("off_topic 세그먼트 인덱스 범위 밖이라 버림: %r", ot)
            continue
        off_topic.append(
            OffTopicSegment(start_ms=start[0], end_ms=end[1], text=ot.text, reason=ot.reason)
        )

    logic_gaps: list[LogicGap] = []
    for lg in parsed.logic_gaps:
        resolved = _resolve_segment_ms(segments, lg.segment_index)
        if resolved is None:
            logger.warning("logic_gap 세그먼트 인덱스 범위 밖이라 버림: %r", lg)
            continue
        logic_gaps.append(LogicGap(at_ms=resolved[0], text=lg.text, note=lg.note))

    script_diff: ScriptDiff | None = None
    if has_script and parsed.script_diff is not None:
        deviations: list[ScriptDeviation] = []
        for d in parsed.script_diff.deviations:
            # 2026-09-12 실측(presentation_id=18)에서 확인: LLM이 script_text와
            # spoken_text가 완전히 동일한데도 "변경"으로 오판정하는 할루시네이션
            # 사례 발견(원본 JSON 바이트 비교로 확인, 공백/자모분리 등 숨은 차이
            # 없음). 실제 차이가 없는 no-op deviation은 어느 모델을 쓰든 다시
            # 나올 수 있어서, 프롬프트만 믿지 않고 여기서도 방어적으로 걸러낸다.
            if d.script_text.strip() == d.spoken_text.strip():
                logger.warning(
                    "script_diff: script_text==spoken_text인 no-op deviation 버림: %r", d
                )
                continue
            resolved = _resolve_segment_ms(segments, d.segment_index)
            if resolved is None:
                logger.warning("script_diff 세그먼트 인덱스 범위 밖이라 버림: %r", d)
                continue
            deviations.append(
                ScriptDeviation(
                    at_ms=resolved[0],
                    script_text=d.script_text,
                    spoken_text=d.spoken_text,
                    kind=d.kind,
                )
            )
        script_diff = ScriptDiff(
            matched_ratio=parsed.script_diff.matched_ratio, deviations=deviations
        )
    # has_script=False인데 LLM이 script_diff를 채워 보내는 경우 방지(스키마 위반은
    # 아니지만 §2.3.1 계약상 대본 없으면 무조건 null이어야 함).

    judgment_by_id = {j.candidate_id: j.is_filler for j in parsed.group_b_fillers}
    group_b_fillers: list[Filler] = []
    for i, cand in enumerate(candidates):
        if not judgment_by_id.get(i, False):
            continue
        group_b_fillers.append(
            Filler(
                type=cand.filler_type,
                text=cand.word.text,
                at_ms=cand.word.start_ms,
                duration_ms=cand.word.end_ms - cand.word.start_ms,
            )
        )

    return ConsistencyAnalysisResult(
        consistency=consistency,
        off_topic=off_topic,
        logic_gaps=logic_gaps,
        script_diff=script_diff,
        group_b_fillers=group_b_fillers,
    )


def analyze_consistency(
    slides: list[Slide],
    transcript: TranscriptResult,
    script: str | None,
    model: str = DEFAULT_MODEL,
) -> ConsistencyAnalysisResult:
    """슬라이드 + 발화 전문 + (있으면) 대본을 LLM에 넣어 정합성/주제이탈/
    논리비약/대본대조 + 그룹B 채움말 판단을 한 번에 구조화 출력받는다.

    Raises:
        LlmError: 호출/파싱 실패 시.
    """
    api_key = os.environ.get("LLM_GATEWAY_API_KEY", "")
    base_url = os.environ.get("LLM_GATEWAY_BASE_URL", DEFAULT_BASE_URL)
    if not api_key:
        raise LlmError("LLM_GATEWAY_API_KEY가 설정되지 않음(.env 확인)")

    candidates = find_group_b_candidates(transcript.words)
    payload = _build_user_payload(
        slides, transcript.segments, script, candidates, transcript.words
    )

    client = _client(base_url, api_key)
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "presentation_analysis",
                    "strict": True,
                    "schema": _strict_json_schema(_LlmOutput),
                },
            },
        )
    except Exception as exc:  # openai/httpx 예외를 통일된 LlmError로 변환
        raise LlmError(f"LLM 게이트웨이 호출 실패: {exc}") from exc

    raw = response.choices[0].message.content
    if not raw:
        raise LlmError("LLM 응답이 비어있음")

    try:
        parsed = _LlmOutput.model_validate_json(raw)
    except Exception as exc:
        raise LlmError(f"LLM 응답 파싱 실패: {exc}") from exc

    # 슬라이드 커버리지 강제(2단계): 1차 호출에서 빠진 슬라이드가 있으면, 그
    # 슬라이드들만 골라 발화 내용과 무관하게 진짜 재판정을 한 번 더 시도한다.
    # 이 재시도 호출조차 실패하면 여기서는 경고만 남기고 넘어가고, 최종적으로
    # _to_result가 여전히 비어있는 슬라이드를 NOT_MENTIONED로 백필한다.
    covered = {c.slide_index for c in parsed.checks}
    missing_slides = [s for s in slides if s.slide_index not in covered]
    if missing_slides:
        try:
            retry_checks = _retry_missing_slide_checks(
                client, model, missing_slides, transcript.segments
            )
            parsed.checks.extend(retry_checks)
        except Exception as exc:
            logger.warning(
                "누락 슬라이드 재시도 LLM 호출 실패, 코드 백필로 대체함: %s", exc
            )

    result = _to_result(parsed, slides, transcript.segments, candidates, has_script=script is not None)

    usage = getattr(response, "usage", None)
    if usage is not None:
        result.token_usage = {
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        }
    return result
