"""app/stt/clova_client.py 단위 테스트.

실제 CLOVA API 호출은 여기서 하지 않는다(scripts/test_clova_upload.py가 실측용).
_parse_response()는 실제 응답 형태를 흉내낸 fixture로, transcribe()의 나머지
분기(설정 누락/파일 없음/네트워크 오류/HTTP 에러/응답 파싱 실패)는 monkeypatch로
httpx.post를 대체해서 검증한다.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.stt import clova_client as cc

# 실제 CLOVA /recognizer/upload 응답을 축약한 fixture (AI_설계.md §1.1 실측 형태 기준).
SAMPLE_RESPONSE = {
    "text": "안녕하세요 발표를 시작하겠습니다",
    "segments": [
        {
            "start": 0,
            "end": 1200,
            "text": "안녕하세요",
            "words": [[0, 1200, "안녕하세요"]],
        },
        {
            "start": 2100,
            "end": 3800,
            "text": "발표를 시작하겠습니다",
            "words": [[2100, 2600, "발표를"], [2700, 3800, "시작하겠습니다"]],
        },
    ],
}


class TestParseResponse:
    def test_full_text_and_segments(self):
        result = cc._parse_response(SAMPLE_RESPONSE)

        assert result.full_text == "안녕하세요 발표를 시작하겠습니다"
        assert len(result.segments) == 2
        assert result.segments[0].start_ms == 0
        assert result.segments[0].end_ms == 1200
        assert result.segments[0].text == "안녕하세요"

    def test_words_flattened_across_segments(self):
        result = cc._parse_response(SAMPLE_RESPONSE)

        assert [w.text for w in result.words] == ["안녕하세요", "발표를", "시작하겠습니다"]
        assert result.words[1].start_ms == 2100
        assert result.words[1].end_ms == 2600

    def test_missing_text_field_defaults_empty(self):
        result = cc._parse_response({"segments": []})
        assert result.full_text == ""
        assert result.words == []
        assert result.segments == []

    def test_segment_without_start_end_is_skipped(self):
        data = {"text": "x", "segments": [{"text": "broken"}]}
        result = cc._parse_response(data)
        assert result.segments == []

    def test_malformed_word_entries_are_skipped(self):
        data = {
            "text": "x",
            "segments": [
                {
                    "start": 0,
                    "end": 100,
                    "text": "x",
                    "words": [[0, 50], "not-a-list", [50, 100, "ok"]],
                }
            ],
        }
        result = cc._parse_response(data)
        assert [w.text for w in result.words] == ["ok"]


class TestTranscribeGuards:
    def test_missing_env_raises(self, monkeypatch, tmp_path):
        monkeypatch.delenv("CLOVA_INVOKE_URL", raising=False)
        monkeypatch.delenv("CLOVA_SECRET_KEY", raising=False)
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"fake")

        with pytest.raises(cc.SttError, match="CLOVA_INVOKE_URL"):
            cc.transcribe(audio)

    def test_missing_file_raises(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CLOVA_INVOKE_URL", "https://example.com")
        monkeypatch.setenv("CLOVA_SECRET_KEY", "secret")

        with pytest.raises(cc.SttError, match="찾을 수 없음"):
            cc.transcribe(tmp_path / "missing.wav")


class TestTranscribeHttp:
    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch):
        monkeypatch.setenv("CLOVA_INVOKE_URL", "https://example.com/")
        monkeypatch.setenv("CLOVA_SECRET_KEY", "secret")

    @pytest.fixture
    def audio_path(self, tmp_path: Path) -> Path:
        p = tmp_path / "a.wav"
        p.write_bytes(b"fake-audio-bytes")
        return p

    def test_success_returns_transcript_result(self, monkeypatch, audio_path):
        def fake_post(url, headers=None, files=None, timeout=None):
            assert url == "https://example.com/recognizer/upload"  # trailing slash 제거 확인
            assert headers["X-CLOVASPEECH-API-KEY"] == "secret"
            return httpx.Response(200, json=SAMPLE_RESPONSE)

        monkeypatch.setattr(httpx, "post", fake_post)

        result = cc.transcribe(audio_path)

        assert result.full_text == SAMPLE_RESPONSE["text"]
        assert len(result.words) == 3

    def test_non_200_raises_stt_error(self, monkeypatch, audio_path):
        def fake_post(url, headers=None, files=None, timeout=None):
            return httpx.Response(400, text='{"error":"speaker detect is off"}')

        monkeypatch.setattr(httpx, "post", fake_post)

        with pytest.raises(cc.SttError, match="status=400"):
            cc.transcribe(audio_path)

    def test_network_error_raises_stt_error(self, monkeypatch, audio_path):
        def fake_post(url, headers=None, files=None, timeout=None):
            raise httpx.ConnectError("boom")

        monkeypatch.setattr(httpx, "post", fake_post)

        with pytest.raises(cc.SttError, match="CLOVA 요청 실패"):
            cc.transcribe(audio_path)

    def test_invalid_json_response_raises_stt_error(self, monkeypatch, audio_path):
        def fake_post(url, headers=None, files=None, timeout=None):
            return httpx.Response(200, text="not json")

        monkeypatch.setattr(httpx, "post", fake_post)

        with pytest.raises(cc.SttError, match="응답 파싱 실패"):
            cc.transcribe(audio_path)
