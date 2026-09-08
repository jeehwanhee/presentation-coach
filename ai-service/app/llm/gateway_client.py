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
2. off_topic: 슬라이드 주제와 무관한 발화 구간(예: 잡담, 본론과 상관없는 이야기).
   start/end_segment_index로 구간을 표시.
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


def _to_result(
    parsed: _LlmOutput,
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

    return _to_result(parsed, transcript.segments, candidates, has_script=script is not None)
