"""여러 LLM 모델 x 여러 (pptx, 오디오) 페어를 한 번에 돌려서 비교하는 스크립트.

목적: gemini-3.7-flash / claude-haiku-4-5 / gpt-5.6-sol처럼 후보 모델 여러 개를
실제 발표 음성(더미 데이터 아님)으로 한 번에 돌려서 정합성/필러/script_diff
품질을 비교한다.

비용 절약을 위해 PPTX 파싱·CLOVA STT·전달지표(그룹A 필러, Silero VAD)는 모델과
무관하므로 페어(오디오)당 딱 한 번만 실행하고, 모델별로는 LLM 호출(정합성
판정+그룹B 필러, app/llm/gateway_client.analyze_consistency)만 반복한다 —
CLOVA STT는 월 20분 무료 한도가 있어서, run_analysis()를 모델 수만큼 통째로
반복 호출하면 그만큼 STT를 반복 호출하게 돼 한도를 불필요하게 깎아먹는다.

사용법 (ai-service 폴더에서):
    .\run.ps1 scripts\test_model_comparison.py

PAIRS/MODELS 리스트를 바꿔서 원하는 조합만 골라 돌릴 수 있다. 결과는
scripts/model_comparison_results/comparison_<타임스탬프>.json 하나에 전부
저장되고, 진행되는 대로 터미널에도 요약이 출력된다.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv()

import librosa  # noqa: E402

from app.audio.delivery_metrics import compute_delivery_metrics  # noqa: E402
from app.llm.gateway_client import LlmError, analyze_consistency  # noqa: E402
from app.parsing.pptx_parser import PptxParseError, parse_pptx  # noqa: E402
from app.schemas.report import AnalysisReport  # noqa: E402
from app.stt.clova_client import SttError, transcribe  # noqa: E402

RESULTS_DIR = Path(__file__).parent / "model_comparison_results"
AUDIO_DIR = Path(__file__).parent / "test-audio"

# 월 3000크레딧 한도(AI_설계.md §3, 매월 1일 리셋) 대비 이 모델로 한 달에 몇 건
# 처리 가능한지 추정하기 위한 값. AI_설계.md의 "동일 테스트 요청 기준" 크레딧
# 소모표에서 gemini-3.5-flash-lite가 토큰당 약 0.0013크레딧이라고 측정된 걸
# 기준(anchor)으로 삼고, 다른 gemini flash 형제 모델은 그 표에 있는 크레딧
# 비율(모델크레딧 / flash-lite크레딧)을 곱해서 토큰당 단가를 근사한다.
# 주의: 이건 "같은 요청 = 같은 토큰 수"라고 가정한 근사치다(모델마다 출력 길이가
# 달라 완전히 정확하진 않음). claude/gpt-5.6-sol은 이 표에 없어서 단가를 모름 —
# 실제 차감 크레딧은 Mindlogic 콘솔의 사용 내역에서 직접 확인해야 정확하다.
MONTHLY_CREDIT_BUDGET = 3000
_FLASH_LITE_CREDIT_PER_TOKEN = 0.0013
_CREDIT_PER_REQUEST_TABLE: dict[str, float] = {
    "gemini-3.5-flash-lite": 0.11,
    "gemini-3.7-flash": 0.13,
    "gemini-3.8-flash": 0.14,
    "gemini-3.6-flash": 0.17,
    "gemini-3.5-flash": 0.30,
}
_FLASH_LITE_CREDIT_PER_REQUEST = _CREDIT_PER_REQUEST_TABLE["gemini-3.5-flash-lite"]


def _estimate_credit_per_token(model: str) -> float | None:
    ratio_base = _CREDIT_PER_REQUEST_TABLE.get(model)
    if ratio_base is None:
        return None  # 이 게이트웨이/모델 조합의 크레딧 단가를 알 수 없음(콘솔 확인 필요)
    return _FLASH_LITE_CREDIT_PER_TOKEN * (ratio_base / _FLASH_LITE_CREDIT_PER_REQUEST)

# (표시용 이름, pptx경로, 오디오경로) — 필요하면 이 리스트만 수정.
# 2026-09-12: 이번 라운드는 토큰 소모량만 비교하는 게 목적이라 test02 하나만 돌림
# (CLOVA 무료 한도 절약 겸, 같은 입력으로 모델별 토큰 차이를 보는 게 목적이라
# 페어를 늘려도 결론이 달라지지 않음).
PAIRS: list[tuple[str, Path, Path]] = [
    ("test02_집중력_포모도로", AUDIO_DIR / "test02_slides.pptx", AUDIO_DIR / "test02.m4a"),
]

# 비교할 모델 — 필요하면 이 리스트만 수정.
# gemini-3.5-flash-lite는 제외(이전 라운드에서 이미 테스트함). gemini-3.7-flash가
# 기준(방금 id=23 실측). 나머지는 AI_설계.md §3의 크레딧 표에서 gemini-3.7-flash
# (0.13)와 비슷한 가격대인 flash 계열 형제 모델들(gemini-3.8-flash 0.14,
# gemini-3.6-flash 0.17) + claude/gpt 대표 후보 2개(크레딧 표엔 없어서 실제
# 토큰 수 자체를 비교 기준으로 같이 둠). 토큰 소모량과 판정 품질(정합성/필러/
# 논리비약 등)을 같이 보고 5개 중 최종 후보를 고른다.
MODELS: list[str] = [
    "gemini-3.7-flash",
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "claude-haiku-4-5-20251001",
    "gpt-5.6-sol",
]


def _prepare_pair(pptx_path: Path, audio_path: Path):
    """모델과 무관한 부분(PPTX 파싱/STT/전달지표)을 페어당 한 번만 계산."""
    audio_duration_ms = int(librosa.get_duration(path=str(audio_path)) * 1000)
    slides = parse_pptx(pptx_path)
    stt_result = transcribe(audio_path)
    delivery = compute_delivery_metrics(stt_result, audio_path, audio_duration_ms)
    return slides, stt_result, delivery


def _run_one_model(slides, stt_result, delivery, script: str | None, model: str) -> dict:
    """캐싱된 STT/전달지표 위에서 모델별 LLM 호출(그룹B+정합성)만 다시 실행."""
    start = time.monotonic()
    try:
        llm_result = analyze_consistency(slides, stt_result, script, model=model)
    except LlmError as exc:
        return {"error": f"LlmError: {exc}"}
    elapsed = time.monotonic() - start

    fillers = delivery.fillers + llm_result.group_b_fillers
    merged_delivery = delivery.model_copy(
        update={"fillers": fillers, "filler_count": len(fillers)}
    )
    report = AnalysisReport(
        consistency=llm_result.consistency,
        off_topic=llm_result.off_topic,
        logic_gaps=llm_result.logic_gaps,
        delivery=merged_delivery,
        script_diff=llm_result.script_diff,
    )
    return {
        "elapsed_seconds": round(elapsed, 1),
        "token_usage": llm_result.token_usage,
        "report": report.model_dump(mode="json"),
    }


def main() -> None:
    RESULTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    all_results: list[dict] = []

    for pair_name, pptx_path, audio_path in PAIRS:
        if not pptx_path.exists() or not audio_path.exists():
            print(f"[건너뜀] {pair_name}: 파일 없음 ({pptx_path} / {audio_path})")
            continue

        print(f"\n[{pair_name}] PPTX 파싱 + CLOVA STT + 전달지표 계산 중(모델 무관, 1회만)...")
        try:
            slides, stt_result, delivery = _prepare_pair(pptx_path, audio_path)
        except (PptxParseError, SttError) as exc:
            print(f"  실패: {type(exc).__name__}: {exc}")
            continue

        for model in MODELS:
            print(f"  === {pair_name} x {model} LLM 호출 중... ===")
            result = _run_one_model(slides, stt_result, delivery, None, model)
            all_results.append({"pair": pair_name, "model": model, **result})
            if "error" in result:
                print(f"    실패: {result['error']}")
                continue
            r = result["report"]
            usage = result.get("token_usage") or {}
            print(
                f"    {result['elapsed_seconds']}초 | "
                f"tokens(in/out/total) {usage.get('prompt_tokens')}/"
                f"{usage.get('completion_tokens')}/{usage.get('total_tokens')} | "
                f"consistency {r['consistency']['supported_count']}/{r['consistency']['total_claims']} | "
                f"off_topic {len(r['off_topic'])} | logic_gaps {len(r['logic_gaps'])} | "
                f"filler_count {r['delivery']['filler_count']}"
            )

    out_path = RESULTS_DIR / f"comparison_{ts}.json"
    out_path.write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n전체 결과 저장: {out_path}")

    # 토큰(비용) + 판정 품질(정합성/오프토픽/논리비약/필러)을 한 표로 같이 비교.
    # test02는 대본(script)이 없어서 script_diff는 항상 null — 정답이 없는 만큼,
    # 여기서는 "모델 간 판정이 얼마나 갈리는지"를 품질 신호로 본다. 실제 내용이
    # 맞는지는 JSON의 checks[].claim/evidence_span을 사람이 눈으로 봐야 함.
    print("\n=== 모델별 토큰 + 판정 품질 + 월 예산(3000크레딧) 대비 처리 가능 건수 ===")
    header = (
        f"{'모델':<28} {'input':>7} {'output':>7} {'total':>7} "
        f"{'정합성':>8} {'오프토픽':>7} {'논리비약':>7} {'필러':>5} "
        f"{'예상크레딧/건':>12} {'월'+str(MONTHLY_CREDIT_BUDGET)+'건':>10}"
    )
    print(header)
    for item in all_results:
        if "error" in item:
            print(f"{item['model']:<28} 실패: {item['error']}")
            continue
        u = item.get("token_usage") or {}
        r = item["report"]
        c = r["consistency"]
        total_tokens = u.get("total_tokens")
        credit_per_token = _estimate_credit_per_token(item["model"])
        if credit_per_token is not None and total_tokens is not None:
            est_credit = round(total_tokens * credit_per_token, 3)
            monthly_capacity = int(MONTHLY_CREDIT_BUDGET / est_credit) if est_credit > 0 else None
            credit_str = f"{est_credit:>12}"
            capacity_str = f"{monthly_capacity:>10}건" if monthly_capacity else f"{'?':>11}"
        else:
            credit_str = f"{'단가미확인':>12}"
            capacity_str = f"{'콘솔확인':>11}"
        print(
            f"{item['model']:<28} "
            f"{str(u.get('prompt_tokens')):>7} "
            f"{str(u.get('completion_tokens')):>7} "
            f"{str(total_tokens):>7} "
            f"{c['supported_count']}/{c['total_claims']:>5} "
            f"{len(r['off_topic']):>7} "
            f"{len(r['logic_gaps']):>7} "
            f"{r['delivery']['filler_count']:>5} "
            f"{credit_str} {capacity_str}"
        )
    print(
        "\n(참고: 예상크레딧/건은 AI_설계.md 크레딧표의 gemini-3.5-flash-lite 단가"
        "(토큰당 0.0013크레딧)를 기준으로 모델 간 크레딧 비율을 곱해 근사한 값 —"
        " '같은 요청 = 같은 토큰 수'라고 가정한 추정치라 정확하지 않을 수 있음."
        " claude/gpt-5.6-sol처럼 단가를 모르는 모델은 Mindlogic 콘솔의 실제"
        " 사용 내역에서 이번 호출로 차감된 크레딧을 직접 확인하는 게 정확함.)"
    )

    # 클레임별 판정을 모델 간 나란히 비교(같은 slide_index끼리 verdict가 갈리는지
    # 눈으로 보기 위함 — 스키마가 슬라이드별 claim을 모델이 자유롭게 뽑아서 claim
    # 문구 자체는 다를 수 있음에 주의).
    print("\n=== 모델별 정합성 판정 상세 ===")
    for item in all_results:
        if "error" in item:
            continue
        print(f"\n[{item['model']}]")
        for chk in item["report"]["consistency"]["checks"]:
            claim = chk["claim"][:40]
            print(f"  slide {chk['slide_index']}: {chk['verdict']:<14} {claim}")


if __name__ == "__main__":
    main()
