"""run_analysis() 전체 파이프라인 실측 테스트 스크립트.

PPTX∥STT → 전달지표∥LLM 두 병렬 단계를 포함한 파이프라인 전체를 진짜 파일로
끝까지 돌려본다. S3/SQS 없이 로컬 파일 경로만으로 검증(AI_설계.md §4).

사용법:
    python scripts/test_run_analysis.py <pptx파일경로> <오디오파일경로> [대본txt파일경로]

예:
    python scripts/test_run_analysis.py scripts/test-audio/test01_slides.pptx scripts/test-audio/test01.m4a

대본 인자는 선택 — 주면 script_diff까지 채워짐, 안 주면 script_diff는 null로 나옴.

결과는 scripts/run_analysis_test_results/ 밑에 저장되고, 터미널에도 전체
리포트가 보기 좋게 출력된다. 각 단계(PPTX 파싱/CLOVA/VAD/LLM)가 실제로 얼마나
걸렸는지, 병렬 실행 덕분에 전체 시간이 얼마나 절약됐는지도 같이 보여준다.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv()  # .env의 CLOVA_INVOKE_URL/CLOVA_SECRET_KEY, LLM_GATEWAY_API_KEY 등을
# os.environ에 채워 넣음 — clova_client.py/gateway_client.py는 자체적으로
# .env를 로드하지 않고 os.environ만 읽으므로, 여기서 먼저 로드해야 함.

RESULTS_DIR = Path(__file__).parent / "run_analysis_test_results"


def main() -> None:
    if len(sys.argv) < 3:
        print("사용법: python scripts/test_run_analysis.py <pptx경로> <오디오경로> [대본txt경로]")
        sys.exit(1)

    pptx_path = Path(sys.argv[1])
    audio_path = Path(sys.argv[2])
    script_path = Path(sys.argv[3]) if len(sys.argv) > 3 else None

    if not pptx_path.exists():
        print(f"PPTX 파일을 찾을 수 없음: {pptx_path}")
        sys.exit(1)
    if not audio_path.exists():
        print(f"오디오 파일을 찾을 수 없음: {audio_path}")
        sys.exit(1)

    script = script_path.read_text(encoding="utf-8").strip() if script_path else None

    import librosa

    audio_duration_ms = int(librosa.get_duration(path=str(audio_path)) * 1000)
    print(f"PPTX: {pptx_path}")
    print(f"오디오: {audio_path} ({audio_duration_ms}ms)")
    print(f"대본: {'있음 (' + str(script_path) + ')' if script else '없음'}")

    from app.llm.gateway_client import LlmError
    from app.parsing.pptx_parser import PptxParseError
    from app.pipeline.run_analysis import AnalysisInput, run_analysis
    from app.stt.clova_client import SttError

    analysis_input = AnalysisInput(
        pptx_path=pptx_path,
        audio_path=audio_path,
        audio_duration_ms=audio_duration_ms,
        script=script,
    )

    print("\n=== 파이프라인 실행 중... (PPTX∥STT → VAD∥LLM, 몇십 초 걸릴 수 있음) ===")
    start = time.monotonic()
    try:
        output = run_analysis(analysis_input)
    except (PptxParseError, SttError, LlmError) as exc:
        print(f"\n파이프라인 실패: {type(exc).__name__}: {exc}")
        sys.exit(1)
    elapsed = time.monotonic() - start
    print(f"총 소요 시간: {elapsed:.1f}초")

    report = output.report

    print("\n=== transcript (§3.2 콜백 필드) ===")
    for seg in output.transcript:
        print(f"  [{seg.start_ms}~{seg.end_ms}ms] {seg.text}")

    print("\n=== consistency ===")
    print(f"supported_count={report.consistency.supported_count} / total_claims={report.consistency.total_claims}")
    for c in report.consistency.checks:
        print(f"  [slide {c.slide_index}] {c.verdict.value} — {c.claim!r}")

    print("\n=== off_topic ===")
    for ot in report.off_topic:
        print(f"  [{ot.start_ms}~{ot.end_ms}ms] {ot.text!r} — {ot.reason}")

    print("\n=== logic_gaps ===")
    for lg in report.logic_gaps:
        print(f"  [{lg.at_ms}ms] {lg.text!r} — {lg.note}")

    print("\n=== delivery ===")
    print(f"wpm={report.delivery.wpm}, silence_total_ms={report.delivery.silence_total_ms}")
    print(f"filler_count={report.delivery.filler_count} (그룹A+그룹B 합산)")
    for f in report.delivery.fillers:
        print(f"  [{f.at_ms}ms] type={f.type.value} text={f.text!r}")
    print(f"volume_variation: {report.delivery.volume_variation.relative_std}")

    print("\n=== script_diff ===")
    if report.script_diff is None:
        print("  없음(대본 미제공)")
    else:
        print(f"  matched_ratio={report.script_diff.matched_ratio}")
        for d in report.script_diff.deviations:
            print(f"  [{d.at_ms}ms][{d.kind.value}] 대본={d.script_text!r} / 발화={d.spoken_text!r}")

    RESULTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = RESULTS_DIR / f"{audio_path.stem}_{ts}.json"
    out_path.write_text(
        json.dumps(
            {
                "transcript": [s.model_dump(mode="json") for s in output.transcript],
                "report": report.model_dump(mode="json"),
                "elapsed_seconds": round(elapsed, 1),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n전체 결과 저장: {out_path}")


if __name__ == "__main__":
    main()
