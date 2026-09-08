import json
from pathlib import Path

from app.schemas.job import (
    AnalysisCallbackFailure,
    AnalysisCallbackSuccess,
    SqsJobMessage,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_sqs_job_message_parses():
    job = SqsJobMessage.model_validate(_load("sample_job_message.json"))
    assert job.presentation_id == 456
    assert job.script is None
    assert job.slide_s3_key == "presentations/456/slides.pptx"


def test_callback_success_parses():
    callback = AnalysisCallbackSuccess.model_validate(_load("sample_callback_success.json"))
    assert callback.status.value == "DONE"
    assert callback.transcript[0].text.startswith("안녕하세요")
    assert callback.report.script_diff is not None


def test_callback_failure_parses():
    callback = AnalysisCallbackFailure.model_validate(_load("sample_callback_failure.json"))
    assert callback.status.value == "FAILED"
    assert callback.error.code.value == "STT_FAILED"
