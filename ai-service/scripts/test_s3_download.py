"""S3_BUCKET/자격증명이 실제로 동작하는지 확인하는 스크립트.

test_e2e_via_backend.py의 2번 단계(presigned URL 업로드)까지는 이미 성공했으므로,
S3엔 presentations/{id}/slides.pptx, .../audio.webm가 실제로 존재한다. submit이
막혀서 SQS는 못 타지만, 우리 쪽 app/clients/s3_client.py의 download_object()가
그 파일들을 실제로 읽어올 수 있는지는 백엔드/SQS와 무관하게 바로 검증 가능하다.

사용법:
    python scripts/test_s3_download.py <presentation_id>
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv()

from app.clients.s3_client import S3Error, download_object

_DOWNLOAD_DIR = Path(__file__).parent / "s3_download_test_results"


def main() -> None:
    if len(sys.argv) < 2:
        print("사용법: python scripts/test_s3_download.py <presentation_id>")
        sys.exit(1)

    presentation_id = sys.argv[1]
    slide_key = f"presentations/{presentation_id}/slides.pptx"
    audio_key = f"presentations/{presentation_id}/audio.webm"

    _DOWNLOAD_DIR.mkdir(exist_ok=True)
    slide_dest = _DOWNLOAD_DIR / f"{presentation_id}_slides.pptx"
    audio_dest = _DOWNLOAD_DIR / f"{presentation_id}_audio.webm"

    for label, key, dest in [("슬라이드", slide_key, slide_dest), ("오디오", audio_key, audio_dest)]:
        print(f"=== {label} 다운로드: s3://{key} ===")
        try:
            download_object(key, dest)
        except S3Error as exc:
            print(f"실패: {exc}")
            sys.exit(1)
        size = dest.stat().st_size
        print(f"성공: {dest} ({size:,} bytes)\n")

    print("S3_BUCKET 설정 + boto3 자격증명(coach-ai IAM) 다 정상 동작 확인됨.")


if __name__ == "__main__":
    main()
