"""app/clients/s3_client.py 단위 테스트.

실제 S3 호출은 하지 않는다 — `_get_client()`가 반환하는 boto3 클라이언트를
가짜 객체로 교체해서 download_object()의 분기(버킷명 미설정/성공/404/기타
ClientError)만 검증한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from app.clients import s3_client as s3c


class FakeS3Client:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.calls: list[tuple[str, str, str]] = []

    def download_file(self, bucket: str, key: str, dest: str) -> None:
        self.calls.append((bucket, key, dest))
        if self.error:
            raise self.error
        Path(dest).write_bytes(b"fake-bytes")


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "테스트 에러"}}, "GetObject")


class TestDownloadObject:
    def test_missing_bucket_env_raises_s3error(self, monkeypatch, tmp_path):
        monkeypatch.delenv("S3_BUCKET", raising=False)

        with pytest.raises(s3c.S3Error, match="S3_BUCKET"):
            s3c.download_object("presentations/1/audio.webm", tmp_path / "out.bin")

    def test_success_calls_download_file_with_bucket_key_dest(self, monkeypatch, tmp_path):
        monkeypatch.setenv("S3_BUCKET", "my-bucket")
        fake = FakeS3Client()
        monkeypatch.setattr(s3c, "_get_client", lambda: fake)
        dest = tmp_path / "nested" / "audio.webm"

        result = s3c.download_object("presentations/1/audio.webm", dest)

        assert result == dest
        assert fake.calls == [("my-bucket", "presentations/1/audio.webm", str(dest))]
        assert dest.exists()  # 부모 디렉토리(nested/)도 자동 생성돼야 함

    def test_404_raises_s3error_with_object_not_found_message(self, monkeypatch, tmp_path):
        monkeypatch.setenv("S3_BUCKET", "my-bucket")
        monkeypatch.setattr(s3c, "_get_client", lambda: FakeS3Client(error=_client_error("404")))

        with pytest.raises(s3c.S3Error, match="객체 없음"):
            s3c.download_object("presentations/1/missing.pptx", tmp_path / "out.bin")

    def test_no_such_key_also_treated_as_not_found(self, monkeypatch, tmp_path):
        monkeypatch.setenv("S3_BUCKET", "my-bucket")
        monkeypatch.setattr(s3c, "_get_client", lambda: FakeS3Client(error=_client_error("NoSuchKey")))

        with pytest.raises(s3c.S3Error, match="객체 없음"):
            s3c.download_object("k", tmp_path / "out.bin")

    def test_other_client_error_raises_generic_s3error(self, monkeypatch, tmp_path):
        monkeypatch.setenv("S3_BUCKET", "my-bucket")
        monkeypatch.setattr(s3c, "_get_client", lambda: FakeS3Client(error=_client_error("AccessDenied")))

        with pytest.raises(s3c.S3Error, match="다운로드 실패"):
            s3c.download_object("k", tmp_path / "out.bin")
