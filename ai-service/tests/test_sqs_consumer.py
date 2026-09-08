"""app/clients/sqs_consumer.py 단위 테스트.

실제 SQS/S3/콜백 HTTP 호출은 안 한다 — S3/파이프라인/HTTP는 전부 monkeypatch로
가짜 함수로 대체하고, 검증하려는 건 (1) S3 실패/파이프라인 예외를 올바른
ErrorCode로 매핑하는지, (2) 콜백 성공/재시도/전체실패 흐름, (3) 콜백 성공 시에만
SQS 메시지를 삭제하는지 세 가지.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.clients import s3_client as s3c
from app.clients import sqs_consumer as sc
from app.llm.gateway_client import LlmError
from app.parsing.pptx_parser import PptxParseError
from app.pipeline.run_analysis import AnalysisOutput
from app.schemas.job import (
    AnalysisCallbackFailure,
    AnalysisCallbackSuccess,
    ErrorCode,
    SqsJobMessage,
)
from app.schemas.report import AnalysisReport, Consistency, Delivery, VolumeVariation
from app.stt.clova_client import SttError


def _job(**overrides) -> SqsJobMessage:
    defaults = dict(
        presentation_id=456,
        slide_s3_key="presentations/456/slides.pptx",
        audio_s3_key="presentations/456/audio.webm",
        script=None,
        callback_url="https://backend.example.com/api/internal/analysis-results",
    )
    defaults.update(overrides)
    return SqsJobMessage(**defaults)


def _fake_output() -> AnalysisOutput:
    report = AnalysisReport(
        consistency=Consistency(checks=[], supported_count=0, total_claims=0),
        off_topic=[],
        logic_gaps=[],
        delivery=Delivery(
            wpm=100.0,
            silence_total_ms=0,
            long_pauses=[],
            fillers=[],
            filler_count=0,
            volume_variation=VolumeVariation(relative_std=0.0, note="테스트"),
        ),
        script_diff=None,
    )
    return AnalysisOutput(transcript=[], report=report)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    # 콜백 재시도 백오프 때문에 테스트가 느려지지 않게.
    monkeypatch.setattr(sc.time, "sleep", lambda seconds: None)


class TestProcessJob:
    def test_slide_download_failure_maps_to_pptx_parse_failed(self, monkeypatch, tmp_path):
        def boom(key, dest):
            raise s3c.S3Error("가짜 S3 실패")

        monkeypatch.setattr(sc, "download_object", boom)

        result = sc._process_job(_job(), tmp_path)

        assert isinstance(result, AnalysisCallbackFailure)
        assert result.error.code == ErrorCode.PPTX_PARSE_FAILED

    def test_audio_download_failure_maps_to_audio_not_found(self, monkeypatch, tmp_path):
        def fake_download(key, dest):
            if "audio" in key:
                raise s3c.S3Error("가짜 S3 실패")
            dest.write_bytes(b"pptx")

        monkeypatch.setattr(sc, "download_object", fake_download)

        result = sc._process_job(_job(), tmp_path)

        assert isinstance(result, AnalysisCallbackFailure)
        assert result.error.code == ErrorCode.AUDIO_NOT_FOUND

    def test_audio_duration_calc_failure_maps_to_audio_not_found(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sc, "download_object", lambda key, dest: dest.write_bytes(b"x"))
        monkeypatch.setattr("librosa.get_duration", lambda path: (_ for _ in ()).throw(RuntimeError("깨진 오디오")))

        result = sc._process_job(_job(), tmp_path)

        assert isinstance(result, AnalysisCallbackFailure)
        assert result.error.code == ErrorCode.AUDIO_NOT_FOUND

    @pytest.mark.parametrize(
        "exc,expected_code",
        [
            (PptxParseError("깨진 pptx"), ErrorCode.PPTX_PARSE_FAILED),
            (SttError("CLOVA 실패"), ErrorCode.STT_FAILED),
            (LlmError("게이트웨이 실패"), ErrorCode.LLM_FAILED),
            (RuntimeError("알 수 없는 실패"), ErrorCode.TIMEOUT),
        ],
    )
    def test_run_analysis_exception_maps_to_correct_error_code(self, monkeypatch, tmp_path, exc, expected_code):
        monkeypatch.setattr(sc, "download_object", lambda key, dest: dest.write_bytes(b"x"))
        monkeypatch.setattr("librosa.get_duration", lambda path: 30.0)

        def boom(analysis_input):
            raise exc

        monkeypatch.setattr(sc, "run_analysis", boom)

        result = sc._process_job(_job(), tmp_path)

        assert isinstance(result, AnalysisCallbackFailure)
        assert result.error.code == expected_code

    def test_happy_path_returns_success_with_transcript_and_report(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sc, "download_object", lambda key, dest: dest.write_bytes(b"x"))
        monkeypatch.setattr("librosa.get_duration", lambda path: 30.0)
        monkeypatch.setattr(sc, "run_analysis", lambda analysis_input: _fake_output())

        result = sc._process_job(_job(presentation_id=789), tmp_path)

        assert isinstance(result, AnalysisCallbackSuccess)
        assert result.presentation_id == 789
        assert result.transcript == []


class TestPostCallback:
    def test_missing_worker_secret_returns_false_without_request(self, monkeypatch):
        monkeypatch.delenv("WORKER_SECRET", raising=False)
        calls = []
        monkeypatch.setattr(sc.httpx, "post", lambda *a, **kw: calls.append(1))

        ok = sc._post_callback("https://x/callback", _fake_failure())

        assert ok is False
        assert calls == []

    def test_first_attempt_200_returns_true(self, monkeypatch):
        monkeypatch.setenv("WORKER_SECRET", "s3cr3t")
        calls = []

        def fake_post(url, json, headers, timeout):
            calls.append((url, headers))
            return _Resp(200)

        monkeypatch.setattr(sc.httpx, "post", fake_post)

        ok = sc._post_callback("https://x/callback", _fake_failure())

        assert ok is True
        assert len(calls) == 1
        assert calls[0][1] == {"X-Worker-Secret": "s3cr3t"}

    def test_retries_after_non_200_then_succeeds(self, monkeypatch):
        monkeypatch.setenv("WORKER_SECRET", "s3cr3t")
        responses = iter([_Resp(500), _Resp(200)])
        monkeypatch.setattr(sc.httpx, "post", lambda *a, **kw: next(responses))

        ok = sc._post_callback("https://x/callback", _fake_failure())

        assert ok is True

    def test_all_attempts_fail_returns_false(self, monkeypatch):
        monkeypatch.setenv("WORKER_SECRET", "s3cr3t")
        monkeypatch.setattr(sc.httpx, "post", lambda *a, **kw: _Resp(500))

        ok = sc._post_callback("https://x/callback", _fake_failure())

        assert ok is False

    def test_network_exception_counts_as_failed_attempt_and_keeps_retrying(self, monkeypatch):
        monkeypatch.setenv("WORKER_SECRET", "s3cr3t")
        import httpx as real_httpx

        responses = iter([real_httpx.ConnectError("연결 실패"), _Resp(200)])

        def fake_post(*a, **kw):
            r = next(responses)
            if isinstance(r, Exception):
                raise r
            return r

        monkeypatch.setattr(sc.httpx, "post", fake_post)

        ok = sc._post_callback("https://x/callback", _fake_failure())

        assert ok is True


class TestHandleMessage:
    def test_unparseable_body_does_not_delete_message(self, monkeypatch):
        sqs = _FakeSqs()
        sc._handle_message(sqs, "queue-url", {"Body": "이건 json이 아님", "ReceiptHandle": "rh-1"})

        assert sqs.deleted == []

    def test_successful_job_and_callback_deletes_message(self, monkeypatch):
        monkeypatch.setattr(sc, "_process_job", lambda job, tmp_dir: _fake_failure())
        monkeypatch.setattr(sc, "_post_callback", lambda url, result: True)
        sqs = _FakeSqs()
        message = {"Body": _job().model_dump_json(), "ReceiptHandle": "rh-2"}

        sc._handle_message(sqs, "queue-url", message)

        assert sqs.deleted == [("queue-url", "rh-2")]

    def test_failed_callback_does_not_delete_message(self, monkeypatch):
        monkeypatch.setattr(sc, "_process_job", lambda job, tmp_dir: _fake_failure())
        monkeypatch.setattr(sc, "_post_callback", lambda url, result: False)
        sqs = _FakeSqs()
        message = {"Body": _job().model_dump_json(), "ReceiptHandle": "rh-3"}

        sc._handle_message(sqs, "queue-url", message)

        assert sqs.deleted == []


def _fake_failure() -> AnalysisCallbackFailure:
    from app.schemas.job import ErrorDetail

    return AnalysisCallbackFailure(
        presentation_id=456, error=ErrorDetail(code=ErrorCode.STT_FAILED, message="테스트")
    )


class _Resp:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text or f"status={status_code}"


class _FakeSqs:
    def __init__(self):
        self.deleted: list[tuple[str, str]] = []

    def delete_message(self, QueueUrl: str, ReceiptHandle: str) -> None:
        self.deleted.append((QueueUrl, ReceiptHandle))
