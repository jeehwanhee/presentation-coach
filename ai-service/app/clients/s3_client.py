"""S3 클라이언트 — presentations/{id}/slides.pptx, .../audio.webm 다운로드 전용.

ai-service는 S3에 쓰지 않는다(읽기 전용, 필요한 IAM 권한은 s3:GetObject만).
버킷명은 환경변수(S3_BUCKET)로 주입 — .env.example 참고. 자격증명은 boto3 기본
프로바이더 체인을 그대로 사용한다 — backend의 S3Config.java/SqsConfig.java와
동일한 방식(DefaultCredentialsProvider): EC2에 배포하면 인스턴스 IAM role을
자동으로 집어오고, 로컬 개발 중엔 `aws configure`로 잡아둔 `coach-ai` IAM 유저
프로필을 사용한다(AI_설계.md §5 참고).
"""

from __future__ import annotations

import os
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

_BUCKET_ENV = "S3_BUCKET"
_REGION_ENV = "AWS_REGION"
_DEFAULT_REGION = "ap-northeast-2"

_client = None  # 지연 생성 — 모듈 임포트 시점에 자격증명/리전이 없어도 에러 안 나게.


class S3Error(Exception):
    """S3 다운로드 실패. 호출부(sqs_consumer.py)가 job에 따라
    ErrorCode.PPTX_PARSE_FAILED 또는 ErrorCode.AUDIO_NOT_FOUND로 매핑한다."""


def _get_client():
    global _client
    if _client is None:
        region = os.environ.get(_REGION_ENV, _DEFAULT_REGION)
        _client = boto3.client("s3", region_name=region)
    return _client


def download_object(s3_key: str, dest_path: Path) -> Path:
    """S3_BUCKET에서 s3_key 객체를 dest_path로 내려받는다.

    Raises:
        S3Error: S3_BUCKET 미설정, 객체 없음(404), 권한 없음, 네트워크 오류 등.
    """
    bucket = os.environ.get(_BUCKET_ENV, "")
    if not bucket:
        raise S3Error(f"{_BUCKET_ENV}이 설정되지 않음(.env 확인)")

    dest_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        _get_client().download_file(bucket, s3_key, str(dest_path))
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey"):
            raise S3Error(f"S3 객체 없음: s3://{bucket}/{s3_key}") from exc
        raise S3Error(f"S3 다운로드 실패 (key={s3_key}): {exc}") from exc
    except BotoCoreError as exc:
        raise S3Error(f"S3 요청 실패 (key={s3_key}): {exc}") from exc

    return dest_path
