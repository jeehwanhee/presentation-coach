"""CLOVA Speech '로컬 파일 인식'(/recognizer/upload) 실측 테스트 스크립트.

목적: word-level 타임스탬프가 나오는지, "음"/"어"/"그" 같은 채움말이
전사 텍스트에서 살아남는지 확인 (AI_설계.md §1 최우선 검증 항목).
noiseFiltering을 명시적으로 false로 보내서 콘솔 "테스트" 탭 기본값과
다른 결과가 나오는지 비교하는 게 이 스크립트의 핵심 목적.

사용법:
    1. .env에 CLOVA_INVOKE_URL, CLOVA_SECRET_KEY 채우기
       (NCP 콘솔 > CLOVA Speech > 도메인 상세 > 인증 정보에서 확인.
        CLOVA_INVOKE_URL은 콘솔에 표시된 Invoke URL을 그대로 붙여넣으면 됨,
        끝에 /recognizer/upload는 이 스크립트가 자동으로 붙임)
    2. pip install -r requirements.txt
    3. python scripts/test_clova_upload.py <오디오파일경로>

결과 JSON은 scripts/clova_test_results/ 밑에 저장되고, 터미널에도
전체 텍스트 + 세그먼트별 word 타임스탬프 + 채움말 생존 여부를 바로 출력한다.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

INVOKE_URL = os.environ.get("CLOVA_INVOKE_URL", "").rstrip("/")
SECRET_KEY = os.environ.get("CLOVA_SECRET_KEY", "")

RESULTS_DIR = Path(__file__).parent / "clova_test_results"

# 확인하고 싶은 채움말 후보. "그"는 "그래서"/"그러면" 같은 단어에도 걸리니
# 아래 출력에서 앞뒤 문맥(세그먼트 원문)을 같이 보고 판단할 것.
FILLER_CANDIDATES = ["음", "어", "그니까", "그"]


def main() -> None:
    if not INVOKE_URL or not SECRET_KEY:
        print("CLOVA_INVOKE_URL / CLOVA_SECRET_KEY를 .env에 채워주세요.")
        sys.exit(1)

    if len(sys.argv) < 2:
        print("사용법: python scripts/test_clova_upload.py <오디오파일경로>")
        sys.exit(1)

    audio_path = Path(sys.argv[1])
    if not audio_path.exists():
        print(f"파일을 찾을 수 없습니다: {audio_path}")
        sys.exit(1)

    params = {
        "language": "ko-KR",
        "completion": "sync",
        "wordAlignment": True,
        "fullText": True,
        # 핵심 검증 대상: 이걸 꺼도 "음"/"어"가 여전히 사라지면
        # 정규화(후처리) 문제일 가능성이 큼 -> VAD 기반 플랜B 검토 필요.
        "noiseFiltering": False,
        # diarization.enable 기본값이 true라서, 도메인 생성 시 "화자 인식"을
        # 미사용으로 설정해뒀다면 반드시 false로 명시해야 함 (안 그러면
        # "speaker detect is off" 400 에러 남 - 도메인 설정과 요청이 충돌).
        "diarization": {"enable": False},
    }

    url = f"{INVOKE_URL}/recognizer/upload"
    headers = {"X-CLOVASPEECH-API-KEY": SECRET_KEY}

    print(f"POST {url}")
    print(f"params: {json.dumps(params, ensure_ascii=False)}")

    with open(audio_path, "rb") as f:
        files = {
            "media": (audio_path.name, f, "application/octet-stream"),
            "params": (None, json.dumps(params), "application/json"),
        }
        resp = httpx.post(url, headers=headers, files=files, timeout=120)

    print(f"status: {resp.status_code}")

    RESULTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = RESULTS_DIR / f"{audio_path.stem}_{ts}.json"

    if resp.status_code != 200:
        print(resp.text)
        out_path.write_text(resp.text, encoding="utf-8")
        print(f"(실패 응답도 저장함: {out_path})")
        sys.exit(1)

    data = resp.json()
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n전체 응답 저장: {out_path}")

    print("\n=== 전체 텍스트 ===")
    print(data.get("text", "(text 필드 없음)"))

    print("\n=== 세그먼트별 word-level 타임스탬프 ===")
    for seg in data.get("segments", []):
        words = seg.get("words", [])
        print(f"[{seg.get('start')}ms~{seg.get('end')}ms] {seg.get('text')}")
        for w in words:
            print(f"    {w}")

    full_text = data.get("text", "")
    found = [f for f in FILLER_CANDIDATES if f in full_text]
    print("\n=== 채움말 생존 여부 (단순 문자열 포함 검사, 참고용) ===")
    print(f"발견됨: {found if found else '없음 - 전부 제거된 것으로 보임'}")
    print("(주의: '그'는 '그래서' 등에도 걸리니 위 세그먼트 원문으로 실제 문맥을 같이 확인할 것)")


if __name__ == "__main__":
    main()
