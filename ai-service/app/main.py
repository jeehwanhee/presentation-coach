"""FastAPI 진입점.

이 서비스의 본체는 SQS 워커 루프(app/clients/sqs_consumer.py)지만, 개발 중에는
로컬 파일 경로만으로 파이프라인을 검증할 수 있도록 디버그 엔드포인트를 같이 둔다.
헬스체크는 배포 후 EC2에서 살아있는지 확인용.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel

from app.pipeline.run_analysis import AnalysisInput, AnalysisOutput, run_analysis

app = FastAPI(title="발표 리허설 코치 · ai-service")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


class DebugAnalyzeRequest(BaseModel):
    """로컬 개발용 요청. 실서비스 트래픽은 SQS로만 들어온다(API_명세서 §3.1) —
    이 엔드포인트는 S3/SQS 없이 파이프라인 로직만 직접 검증하기 위한 것."""

    pptx_path: str
    audio_path: str
    audio_duration_ms: int
    script: str | None = None


@app.post("/debug/analyze")
def debug_analyze(req: DebugAnalyzeRequest) -> AnalysisOutput:
    """로컬 파일 경로로 파이프라인을 직접 실행해본다 (S3/SQS 우회).

    4개 단계(pptx_parser/clova_client/delivery_metrics/gateway_client) 전부
    구현 완료 — .env에 CLOVA/LLM 키가 채워져 있으면 실제로 끝까지 돈다.
    실패 시 PptxParseError/SttError/LlmError 등이 그대로 500으로 나간다
    (SQS 워커 경로에서는 이 예외들을 §1 error.code로 매핑해서 콜백을 보낸다 —
    이 디버그 엔드포인트는 그 매핑 없이 원시 예외를 그대로 노출한다).
    """
    return run_analysis(
        AnalysisInput(
            pptx_path=Path(req.pptx_path),
            audio_path=Path(req.audio_path),
            audio_duration_ms=req.audio_duration_ms,
            script=req.script,
        )
    )
