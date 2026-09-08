"""프론트엔드 없이 백엔드 실제 API를 직접 호출해서 SQS에 진짜 잡을 넣는 스크립트.

프론트가 아직 없어서(§AI_설계.md 남은 이슈 참고) "발표 생성 → S3 업로드 →
제출 → (분석 완료 후) 리포트 조회" 흐름을 여기서 대신 흉내 낸다. 이 스크립트가
끝나면 실제 SQS 큐에 잡 메시지가 들어가 있는 상태가 되고, 그 다음 별도 터미널에서

    .\\run.ps1 -m app.clients.sqs_consumer

를 실행하면 sqs_consumer.py가 그 잡을 실제로 집어서 S3 다운로드 → run_analysis()
→ 콜백까지 진짜로 처리하는지 종단 검증할 수 있다. 이 스크립트를 그대로 다시
실행하면(같은 presentation_id로 GET 폴링) 콜백 이후 최종 리포트까지 확인 가능.

사용법 (ai-service 디렉토리에서):
    .\\run.ps1 scripts\\test_e2e_via_backend.py [BASE_URL] [pptx경로] [오디오경로]

인자 다 생략하면 기본값(CloudFront 주소, test01_slides.pptx, test01.m4a) 사용.

주의: audio_s3_key는 백엔드가 항상 "presentations/{id}/audio.webm"로 고정해서
주는데, 테스트 파일은 test01.m4a임 — 실제로는 진짜 webm 녹음이 올라오니 문제
없지만, 이 테스트에서는 m4a 바이트를 .webm 이름으로 업로드하는 셈이라 다운로드
후 오디오 길이 계산(librosa/ffmpeg)에서 에러가 나면 이것 때문일 수 있음.

2026-09-08: 백엔드에 `POST .../submit`이 `X-Result-Token` 헤더를 필수로
요구하도록 바뀜(API_명세서.md §2.2, 커밋 "submit에 result token 추가"). 이
헤더가 없으면 Spring이 던지는 MissingRequestHeaderException이
GlobalExceptionHandler의 catch-all(Exception.class)에 걸려 그냥 500
INTERNAL_ERROR로 나온다(제대로 된 400/401이 아님) — 이전에 이 스크립트로
재현했던 submit 500이 사실 이것 때문이었을 가능성이 높음. 아래에서
1번 응답의 result_token을 그대로 재사용해서 헤더에 실어 보낸다.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx

_DEFAULT_BASE_URL = "https://doiq20sq36e37.cloudfront.net"
_TEST_AUDIO_DIR = Path(__file__).resolve().parent / "test-audio"  # scripts/test-audio/
_DEFAULT_PPTX = _TEST_AUDIO_DIR / "test01_slides.pptx"
_DEFAULT_AUDIO = _TEST_AUDIO_DIR / "test01.m4a"
_POLL_INTERVAL_SECONDS = 3
_POLL_MAX_ATTEMPTS = 5  # 워커를 별도 터미널에서 안 돌리고 있으면 계속 PROCESSING일 수 있음


def main() -> None:
    base_url = sys.argv[1] if len(sys.argv) > 1 else _DEFAULT_BASE_URL
    pptx_path = Path(sys.argv[2]) if len(sys.argv) > 2 else _DEFAULT_PPTX
    audio_path = Path(sys.argv[3]) if len(sys.argv) > 3 else _DEFAULT_AUDIO

    if not pptx_path.exists():
        print(f"PPTX 파일을 찾을 수 없음: {pptx_path}")
        sys.exit(1)
    if not audio_path.exists():
        print(f"오디오 파일을 찾을 수 없음: {audio_path}")
        sys.exit(1)

    print(f"BASE_URL={base_url}")
    print(f"PPTX={pptx_path}")
    print(f"오디오={audio_path}\n")

    # 1. 발표 생성
    print("=== 1. POST /api/presentations (발표 생성) ===")
    create_resp = httpx.post(
        f"{base_url}/api/presentations",
        json={"title": "ai-service 종단 테스트", "script": None},
        timeout=30,
    )
    print(f"status={create_resp.status_code}")
    if create_resp.status_code != 201:
        print(f"실패: {create_resp.text}")
        sys.exit(1)
    created = create_resp.json()
    print(created)

    presentation_id = created["presentation_id"]
    slide_upload_url = created["slide_upload_url"]
    audio_upload_url = created["audio_upload_url"]
    result_token = created["result_token"]
    result_url = created["result_url"]

    # 2. S3에 pptx/오디오 직접 PUT (presigned URL)
    print("\n=== 2. presigned URL로 S3에 파일 업로드 ===")
    put_slide = httpx.put(
        slide_upload_url, content=pptx_path.read_bytes(), headers={"Content-Type": "application/octet-stream"}, timeout=60
    )
    print(f"pptx PUT status={put_slide.status_code}")
    if put_slide.status_code not in (200, 204):
        print(f"pptx 업로드 실패: {put_slide.text}")
        sys.exit(1)

    put_audio = httpx.put(
        audio_upload_url, content=audio_path.read_bytes(), headers={"Content-Type": "application/octet-stream"}, timeout=60
    )
    print(f"오디오 PUT status={put_audio.status_code}")
    if put_audio.status_code not in (200, 204):
        print(f"오디오 업로드 실패: {put_audio.text}")
        sys.exit(1)

    # 3. 오디오 길이 계산 + 제출 (여기서 실제로 SQS에 잡이 push됨)
    import librosa

    audio_duration_ms = int(librosa.get_duration(path=str(audio_path)) * 1000)

    print(f"\n=== 3. POST /api/presentations/{presentation_id}/submit (SQS push) ===")
    submit_resp = httpx.post(
        f"{base_url}/api/presentations/{presentation_id}/submit",
        json={"audio_duration_ms": audio_duration_ms},
        headers={"X-Result-Token": result_token},
        timeout=30,
    )
    print(f"status={submit_resp.status_code}")
    print(submit_resp.text)

    if submit_resp.status_code != 202:
        print("\n제출 실패 — SQS에 잡이 안 들어갔을 가능성 높음.")
        sys.exit(1)

    print("\n=== SQS에 실제 잡이 들어갔음 ===")
    print(f"presentation_id={presentation_id}")
    print(f"result_url={result_url}")
    print("\n이제 다른 터미널(ai-service 디렉토리)에서 아래 실행해서 워커가 처리하는지 확인:")
    print("    .\\run.ps1 -m app.clients.sqs_consumer")
    print("워커가 콜백까지 보내고 나면(로그에 '잡 완료, 메시지 삭제' 찍힘), 이 스크립트를")
    print("다시 실행해서(같은 presentation_id는 아니고 새로 만들어지지만) 아래 4번 폴링으로")
    print("최종 리포트를 확인할 수 있음. 워커를 아직 안 돌렸으면 4번은 계속 PROCESSING만 찍힘.")

    # 4. 상태 폴링 + 리포트 조회 (§2.3, 이번에 새로 추가된 GET 엔드포인트)
    print(f"\n=== 4. GET /api/presentations/{presentation_id} (상태 폴링) ===")
    for attempt in range(1, _POLL_MAX_ATTEMPTS + 1):
        report_resp = httpx.get(
            f"{base_url}/api/presentations/{presentation_id}",
            headers={"X-Result-Token": result_token},
            timeout=30,
        )
        print(f"[{attempt}/{_POLL_MAX_ATTEMPTS}] status={report_resp.status_code}")
        if report_resp.status_code != 200:
            print(report_resp.text)
            break
        body = report_resp.json()
        print(f"  presentation_status={body.get('status')}")
        if body.get("status") in ("DONE", "FAILED"):
            print(body)
            break
        time.sleep(_POLL_INTERVAL_SECONDS)
    else:
        print("계속 PROCESSING — 워커(sqs_consumer.py)가 아직 이 잡을 처리 안 한 상태로 보임.")


if __name__ == "__main__":
    main()
