"""분석 리포트 스키마.

docs/API_명세서.md §2.3.1 "리포트 스키마 (제품 핵심)"을 그대로 옮긴 것.
필드명·enum을 바꾸려면 반드시 그 문서부터 고칠 것 (문서 상단 경고 참고).

- 결과가 없는 배열 필드는 빈 배열 []로 (null 금지).
- script_diff만 예외 — 대본이 없으면 객체 전체가 null.
- slide_index는 0부터 시작 (python-pptx 관례와 동일, pptx_parser.py 참고).
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ConsistencyVerdict(str, Enum):
    """§1 Enum: consistency.verdict"""

    SUPPORTED = "SUPPORTED"  # 뒷받침됨
    NOT_MENTIONED = "NOT_MENTIONED"  # 미언급
    NO_BASIS = "NO_BASIS"  # 근거없음


class FillerType(str, Enum):
    """§1 Enum: filler.type

    2026-09-07 확장 (API_명세서.md §2.3.1 참고) — 탐지 방식이 두 그룹으로 나뉜다:
    - EUM/EO(음/어): STT가 텍스트에서 지워버리는 순수 발성. VAD(Silero VAD)로
      단어 타임스탬프 사이 빈틈을 교차 검증해서 탐지 (app/audio/delivery_metrics.py).
    - GEU/JEO/MWO/MWONGA/JOM/MAK/GEUNYANG/GATDA(그/저/뭐/뭔가/좀/막/그냥/같다):
      실제 단어라 STT 텍스트에 항상 남지만, "그 사람이"처럼 필러 아닌 정상
      용법과 헷갈리기 쉬워서 LLM이 문맥 보고 판단 (app/llm/gateway_client.py).
    """

    EUM = "음"
    EO = "어"
    GEU = "그"
    JEO = "저"
    MWO = "뭐"
    MWONGA = "뭔가"
    JOM = "좀"
    MAK = "막"
    GEUNYANG = "그냥"
    GATDA = "같다"
    ETC = "기타"


class ScriptDiffKind(str, Enum):
    """§1 Enum: script_diff.kind"""

    OMITTED = "생략"
    ADDED = "추가"
    CHANGED = "변경"


class ConsistencyCheck(BaseModel):
    slide_index: int
    claim: str
    verdict: ConsistencyVerdict
    evidence_span: str | None = None
    evidence_at_ms: int | None = None


class Consistency(BaseModel):
    checks: list[ConsistencyCheck] = Field(default_factory=list)
    supported_count: int
    total_claims: int


class OffTopicSegment(BaseModel):
    start_ms: int
    end_ms: int
    text: str
    reason: str


class LogicGap(BaseModel):
    at_ms: int
    text: str
    note: str


class LongPause(BaseModel):
    start_ms: int
    duration_ms: int


class Filler(BaseModel):
    type: FillerType
    # 실제 발화 원문 어절. 음/어/그도 채워서 보낸다(디버깅·QA용).
    # 기타는 이 필드가 없으면 뭐가 왜 기타로 묶였는지 알 수 없으니 필수.
    text: str
    at_ms: int
    duration_ms: int


class VolumeVariation(BaseModel):
    relative_std: float
    note: str


class Delivery(BaseModel):
    wpm: float
    silence_total_ms: int
    long_pauses: list[LongPause] = Field(default_factory=list)
    fillers: list[Filler] = Field(default_factory=list)
    filler_count: int
    volume_variation: VolumeVariation


class ScriptDeviation(BaseModel):
    at_ms: int
    script_text: str
    spoken_text: str
    kind: ScriptDiffKind


class ScriptDiff(BaseModel):
    matched_ratio: float
    deviations: list[ScriptDeviation] = Field(default_factory=list)


class AnalysisReport(BaseModel):
    """report_json 컬럼에 통째로 저장되는 최종 리포트. §2.3.1 JSON 예시와 1:1 대응."""

    consistency: Consistency
    off_topic: list[OffTopicSegment] = Field(default_factory=list)
    logic_gaps: list[LogicGap] = Field(default_factory=list)
    delivery: Delivery
    # 대본 없으면 null, 있으면 ScriptDiff 객체 (§2.3.1 "대본 없을때"/"있을때" 참고)
    script_diff: ScriptDiff | None = None
