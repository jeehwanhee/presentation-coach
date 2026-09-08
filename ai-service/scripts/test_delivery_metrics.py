"""app/audio/delivery_metrics.py 실측 테스트 스크립트 (Silero VAD 실제 실행).

목적: compute_delivery_metrics()를 진짜 오디오 + 진짜 CLOVA 응답으로 돌려서
Silero VAD가 실제로 동작하는지, gap 임계값(GAP_CANDIDATE_THRESHOLD_MS=250ms)이
그럴듯한 결과를 내는지 확인한다. 지금까지는 로직만(monkeypatch로 VAD 대체) 테스트
했고 실제 모델 실행은 한 번도 안 해봄(AI_설계.md §5).

scripts/test_clova_upload.py로 이미 저장해둔 CLOVA 응답 JSON을 재사용한다
(다시 CLOVA를 호출하지 않음 — 크레딧/시간 절약).

사용법:
    1. pip install -r requirements.txt (silero-vad/torch/librosa 새로 추가됨,
       시간 좀 걸릴 수 있음)
    2. python scripts/test_delivery_metrics.py <오디오파일경로> [클로바결과json경로]
       클로바결과json경로 생략하면 scripts/clova_test_results/ 밑에서 가장 최근
       파일을 자동으로 씀.

예:
    python scripts/test_delivery_metrics.py scripts/test-audio/test01.m4a

결과는 scripts/delivery_metrics_test_results/ 밑에 저장되고, 터미널에도
필러/침묵/WPM/성량변화가 바로 출력된다. 필러로 잡힌 지점의 at_ms를 실제
오디오에서 그 근처 들어보면서 맞는지 확인하는 용도로 쓰면 됨.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.audio.delivery_metrics import compute_delivery_metrics  # noqa: E402
from app.stt.clova_client import _parse_response  # noqa: E402

CLOVA_RESULTS_DIR = Path(__file__).parent / "clova_test_results"
RESULTS_DIR = Path(__file__).parent / "delivery_metrics_test_results"


def _latest_clova_result() -> Path:
    candidates = sorted(CLOVA_RESULTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
    if not candidates:
        print(f"{CLOVA_RESULTS_DIR}에 저장된 CLOVA 결과 JSON이 없음 — 먼저 "
              "scripts/test_clova_upload.py를 돌려서 만들어야 함.")
        sys.exit(1)
    return candidates[-1]


def main() -> None:
    if len(sys.argv) < 2:
        print("사용법: python scripts/test_delivery_metrics.py <오디오파일경로> [클로바결과json경로]")
        sys.exit(1)

    audio_path = Path(sys.argv[1])
    if not audio_path.exists():
        print(f"오디오 파일을 찾을 수 없음: {audio_path}")
        sys.exit(1)

    clova_json_path = Path(sys.argv[2]) if len(sys.argv) > 2 else _latest_clova_result()
    print(f"오디오: {audio_path}")
    print(f"CLOVA 결과: {clova_json_path}")

    data = json.loads(clova_json_path.read_text(encoding="utf-8"))
    transcript = _parse_response(data)
    print(f"단어 수: {len(transcript.words)}, 세그먼트 수: {len(transcript.segments)}")

    import librosa

    audio_duration_ms = int(librosa.get_duration(path=str(audio_path)) * 1000)
    print(f"오디오 길이: {audio_duration_ms}ms")

    print("\n=== Silero VAD 로딩 + gap 분류 중... (첫 실행은 모델 로딩 때문에 좀 걸릴 수 있음) ===")
    delivery = compute_delivery_metrics(transcript, audio_path, audio_duration_ms)

    print("\n=== WPM ===")
    print(delivery.wpm)

    print("\n=== 침묵 ===")
    print(f"silence_total_ms={delivery.silence_total_ms}")
    for lp in delivery.long_pauses:
        print(f"  장시간 정지: [{lp.start_ms}ms~{lp.start_ms + lp.duration_ms}ms] ({lp.duration_ms}ms)")

    print(f"\n=== 그룹A 필러(Silero VAD로 감지) — {delivery.filler_count}개 ===")
    for f in delivery.fillers:
        print(f"  [{f.at_ms}ms~{f.at_ms + f.duration_ms}ms] type={f.type.value} text={f.text!r}")
    if not delivery.fillers:
        print("  없음 — GAP_CANDIDATE_THRESHOLD_MS(250ms)보다 긴 빈틈 자체가 없었거나, "
              "다 침묵으로 판정됐거나 둘 중 하나. 원본 CLOVA 결과의 word 타임스탬프도 같이 봐서 확인할 것.")

    print("\n=== 성량 변화 ===")
    print(f"relative_std={delivery.volume_variation.relative_std}")
    print(delivery.volume_variation.note)

    RESULTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = RESULTS_DIR / f"{audio_path.stem}_{ts}.json"
    out_path.write_text(
        json.dumps(_delivery_to_dict(delivery), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n전체 결과 저장: {out_path}")
    print(
        "\n참고: at_ms 지점들을 오디오 플레이어로 실제로 들어보면서 진짜 '음'/'어'가 "
        "맞는지, 놓친 필러는 없는지 확인해보면 GAP_CANDIDATE_THRESHOLD_MS/VAD_MIN_SPEECH_MS "
        "튜닝 방향을 잡을 수 있음(AI_설계.md §5)."
    )


def _delivery_to_dict(delivery) -> dict:
    return delivery.model_dump(mode="json")


if __name__ == "__main__":
    main()
