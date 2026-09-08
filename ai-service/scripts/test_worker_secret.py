"""WORKER_SECRET이 실제 배포된 백엔드와 맞는지 안전하게 확인하는 스크립트.

존재하지 않는 presentation_id(999999999)로 콜백을 보내본다 — 실제 데이터는
전혀 건드리지 않음. 백엔드 로직(PresentationErrorCode.java) 기준:
  - 시크릿이 틀리면        -> 403 INVALID_WORKER_SECRET
  - 시크릿은 맞는데 id 없음 -> 404 PRESENTATION_NOT_FOUND (정상! 시크릿 검증 통과 의미)

사용법:
    python scripts/test_worker_secret.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv()

import os

import httpx

from app.schemas.job import AnalysisCallbackFailure, ErrorCode, ErrorDetail

_FAKE_PRESENTATION_ID = 999999999  # 실존 안 할 값


def main() -> None:
    callback_url = os.environ.get("CALLBACK_URL", "")
    secret = os.environ.get("WORKER_SECRET", "")

    if not callback_url:
        print("CALLBACK_URL이 .env에 없음")
        sys.exit(1)
    if not secret:
        print("WORKER_SECRET이 .env에 없음")
        sys.exit(1)

    payload = AnalysisCallbackFailure(
        presentation_id=_FAKE_PRESENTATION_ID,
        error=ErrorDetail(code=ErrorCode.TIMEOUT, message="WORKER_SECRET 검증용 더미 요청"),
    )
    body = payload.model_dump(mode="json")
    headers = {"X-Worker-Secret": secret}

    print(f"POST {callback_url}")
    print(f"presentation_id={_FAKE_PRESENTATION_ID} (실존 안 하는 값 — 실제 데이터 영향 없음)")

    resp = httpx.post(callback_url, json=body, headers=headers, timeout=15)

    print(f"\nstatus={resp.status_code}")
    print(f"body={resp.text}")

    if resp.status_code == 403 or "INVALID_WORKER_SECRET" in resp.text:
        print("\n결과: WORKER_SECRET이 틀림 (403/INVALID_WORKER_SECRET) — 값 다시 확인 필요")
    elif resp.status_code == 404 or "PRESENTATION_NOT_FOUND" in resp.text:
        print("\n결과: WORKER_SECRET 검증 통과! (404는 id가 없어서 나는 정상 응답)")
    else:
        print(f"\n결과: 예상 못 한 응답 — 위 status/body 확인 필요")


if __name__ == "__main__":
    main()
