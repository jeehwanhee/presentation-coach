"""SQS 컨슈머 — Spring이 push한 잡 메시지를 폴링해서 pipeline.run_analysis로 넘긴다.

인프라 레이어의 마지막 단계. app.pipeline.run_analysis는 S3/SQS를 전혀
몰라도 되게 설계했으므로, 여기서는 "메시지 받기 → S3 다운로드 →
run_analysis 호출 → 콜백 POST → 메시지 삭제"만 얇게 감싼다(API_명세서 §3).

한 번에 메시지 1개만 처리하는 단일 워커 루프(해커톤 스코프에 맞춘 최소 구현) —
동시에 여러 잡을 처리하려면 나중에 여러 프로세스로 이 poll_forever()를 띄우면 됨.

실행: `python -m app.clients.sqs_consumer` (systemd 서비스로 등록해서 상시 실행 예정)
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from pathlib import Path

import boto3
import httpx

from app.clients.s3_client import S3Error, download_object
from app.llm.gateway_client import LlmError
from app.parsing.pptx_parser import PptxParseError
from app.pipeline.run_analysis import AnalysisInput, run_analysis
from app.schemas.job import (
    AnalysisCallbackFailure,
    AnalysisCallbackSuccess,
    ErrorCode,
    ErrorDetail,
    SqsJobMessage,
)
from app.stt.clova_client import SttError

logger = logging.getLogger(__name__)

_QUEUE_URL_ENV = "SQS_QUEUE_URL"
_REGION_ENV = "AWS_REGION"
_WORKER_SECRET_ENV = "WORKER_SECRET"
_DEFAULT_REGION = "ap-northeast-2"

_POLL_WAIT_SECONDS = 20  # SQS 롱폴링 최댓값.
# 잡 하나 처리 시간 추정치(오디오 최대 10분 기준 STT+VAD+LLM 여유 포함) + 콜백
# 재시도 시간까지 감안한 넉넉한 값 — 실측하면서 조정 필요(AI_설계.md §5 남은 이슈).
_VISIBILITY_TIMEOUT_SECONDS = 900
_CALLBACK_TIMEOUT_SECONDS = 30
_CALLBACK_RETRY_BACKOFFS = [2, 5, 15]  # 첫 시도는 즉시, 이후 이 순서로 백오프.


def poll_forever() -> None:
    """SQS_QUEUE_URL을 롱폴링하며 잡을 순서대로 처리한다. Ctrl+C로 정지 가능."""
    queue_url = os.environ.get(_QUEUE_URL_ENV, "")
    if not queue_url:
        raise RuntimeError(f"{_QUEUE_URL_ENV}가 설정되지 않음(.env 확인)")

    region = os.environ.get(_REGION_ENV, _DEFAULT_REGION)
    sqs = boto3.client("sqs", region_name=region)

    logger.info("SQS 폴링 시작: %s", queue_url)
    try:
        while True:
            response = sqs.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=_POLL_WAIT_SECONDS,
                VisibilityTimeout=_VISIBILITY_TIMEOUT_SECONDS,
            )
            for message in response.get("Messages", []):
                _handle_message(sqs, queue_url, message)
    except KeyboardInterrupt:
        logger.info("SQS 폴링 종료(Ctrl+C)")


def _handle_message(sqs, queue_url: str, message: dict) -> None:
    receipt_handle = message["ReceiptHandle"]

    try:
        job = SqsJobMessage.model_validate_json(message["Body"])
    except Exception:
        # presentation_id를 모르면 콜백을 보낼 대상도 없음 — 메시지를 지우지
        # 않고 그대로 둬서 visibility timeout 후 재배달(→ DLQ)에 맡긴다.
        logger.exception("잡 메시지 파싱 실패, 삭제 안 함: %r", message.get("Body"))
        return

    logger.info("잡 수신: presentation_id=%s", job.presentation_id)

    with tempfile.TemporaryDirectory(prefix=f"job-{job.presentation_id}-") as tmp:
        result = _process_job(job, Path(tmp))
    # tmp 디렉토리는 with 블록을 빠져나오며 자동 삭제(API_명세서 §3.2 "Python은
    # 로컬 임시파일 삭제").

    if _post_callback(job.callback_url, result):
        sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
        logger.info("잡 완료, 메시지 삭제: presentation_id=%s status=%s", job.presentation_id, result.status)
    else:
        # 콜백 자체가 실패한 경우에만 SQS 재배달에 맡김(§3.2 — STT/LLM
        # 이중 과금 방지를 위해 Python이 먼저 재시도부터 다 해본 뒤에만).
        logger.error("콜백 재시도 전부 실패, 메시지 유지: presentation_id=%s", job.presentation_id)


def _process_job(job: SqsJobMessage, tmp_dir: Path) -> AnalysisCallbackSuccess | AnalysisCallbackFailure:
    pptx_path = tmp_dir / "slides.pptx"
    audio_path = tmp_dir / "audio.webm"

    try:
        download_object(job.slide_s3_key, pptx_path)
    except S3Error as exc:
        return _failure(job.presentation_id, ErrorCode.PPTX_PARSE_FAILED, f"슬라이드 다운로드 실패: {exc}")

    try:
        download_object(job.audio_s3_key, audio_path)
    except S3Error as exc:
        return _failure(job.presentation_id, ErrorCode.AUDIO_NOT_FOUND, f"오디오 다운로드 실패: {exc}")

    try:
        import librosa

        audio_duration_ms = int(librosa.get_duration(path=str(audio_path)) * 1000)
    except Exception as exc:
        return _failure(job.presentation_id, ErrorCode.AUDIO_NOT_FOUND, f"오디오 길이 계산 실패: {exc}")

    analysis_input = AnalysisInput(
        pptx_path=pptx_path,
        audio_path=audio_path,
        audio_duration_ms=audio_duration_ms,
        script=job.script,
    )

    try:
        output = run_analysis(analysis_input)
    except PptxParseError as exc:
        return _failure(job.presentation_id, ErrorCode.PPTX_PARSE_FAILED, str(exc))
    except SttError as exc:
        return _failure(job.presentation_id, ErrorCode.STT_FAILED, str(exc))
    except LlmError as exc:
        return _failure(job.presentation_id, ErrorCode.LLM_FAILED, str(exc))
    except Exception as exc:  # 예상 못 한 실패 — all-or-nothing이므로 무조건 FAILED로 콜백
        logger.exception("run_analysis에서 예상 못 한 예외: presentation_id=%s", job.presentation_id)
        return _failure(job.presentation_id, ErrorCode.TIMEOUT, f"알 수 없는 파이프라인 실패: {exc}")

    return AnalysisCallbackSuccess(
        presentation_id=job.presentation_id,
        transcript=output.transcript,
        report=output.report,
    )


def _failure(presentation_id: int, code: ErrorCode, message: str) -> AnalysisCallbackFailure:
    logger.warning("잡 실패: presentation_id=%s code=%s message=%s", presentation_id, code.value, message)
    return AnalysisCallbackFailure(
        presentation_id=presentation_id,
        error=ErrorDetail(code=code, message=message),
    )


def _post_callback(callback_url: str, payload: AnalysisCallbackSuccess | AnalysisCallbackFailure) -> bool:
    """콜백을 POST하고 200이면 True. 실패 시 백오프로 재시도, 다 실패하면 False."""
    secret = os.environ.get(_WORKER_SECRET_ENV, "")
    if not secret:
        logger.error(f"{_WORKER_SECRET_ENV}가 설정되지 않음(.env 확인) — 콜백 전송 불가")
        return False

    body = payload.model_dump(mode="json")
    headers = {"X-Worker-Secret": secret}
    attempts = [0.0, *_CALLBACK_RETRY_BACKOFFS]  # 첫 시도는 대기 없음.

    for attempt_no, wait_seconds in enumerate(attempts, start=1):
        if wait_seconds:
            time.sleep(wait_seconds)
        try:
            resp = httpx.post(callback_url, json=body, headers=headers, timeout=_CALLBACK_TIMEOUT_SECONDS)
        except httpx.HTTPError as exc:
            logger.warning("콜백 요청 예외(%d/%d): %s", attempt_no, len(attempts), exc)
            continue

        if resp.status_code == 200:
            return True
        logger.warning(
            "콜백 실패(%d/%d, status=%d): %s", attempt_no, len(attempts), resp.status_code, resp.text
        )

    return False


if __name__ == "__main__":
    # `python -m app.clients.sqs_consumer`로 직접 실행되는 진입점이라
    # (scripts/test_*.py처럼 load_dotenv()를 먼저 해주는 래퍼가 없음) 여기서
    # 직접 .env를 로드해야 함 — 안 그러면 .env에 값이 있어도 os.environ에
    # 안 채워져서 SQS_QUEUE_URL 등이 비어있는 것처럼 보임.
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    poll_forever()
