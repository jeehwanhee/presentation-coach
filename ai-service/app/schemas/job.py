"""Spring ↔ Python 계약 스키마.

docs/API_명세서.md §3 "Spring ↔ Python 계약"을 그대로 옮긴 것.
- §3.1: Spring → SQS → Python 잡 메시지
- §3.2: Python → Spring 결과 콜백 (POST /api/internal/analysis-results, X-Worker-Secret 헤더 필요)
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel

from app.schemas.report import AnalysisReport


class PresentationStatus(str, Enum):
    """§1 Enum: presentation.status"""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    DONE = "DONE"
    FAILED = "FAILED"


class ErrorCode(str, Enum):
    """§1 Enum: error.code"""

    STT_FAILED = "STT_FAILED"
    PPTX_PARSE_FAILED = "PPTX_PARSE_FAILED"
    AUDIO_NOT_FOUND = "AUDIO_NOT_FOUND"
    LLM_FAILED = "LLM_FAILED"
    TIMEOUT = "TIMEOUT"


class ErrorDetail(BaseModel):
    code: ErrorCode
    message: str


class SqsJobMessage(BaseModel):
    """§3.1 Spring → SQS → Python 잡 메시지 본문."""

    presentation_id: int
    slide_s3_key: str
    audio_s3_key: str
    script: str | None = None
    callback_url: str


class TranscriptSegment(BaseModel):
    start_ms: int
    end_ms: int
    text: str


class AnalysisCallbackSuccess(BaseModel):
    """§3.2 결과 콜백 — 성공. status는 DONE/FAILED 둘 중 하나만 전송."""

    presentation_id: int
    status: Literal[PresentationStatus.DONE] = PresentationStatus.DONE
    transcript: list[TranscriptSegment]
    report: AnalysisReport


class AnalysisCallbackFailure(BaseModel):
    """§3.2 결과 콜백 — 실패. 부분 실패도 전체 실패로 취급(all-or-nothing)."""

    presentation_id: int
    status: Literal[PresentationStatus.FAILED] = PresentationStatus.FAILED
    error: ErrorDetail
