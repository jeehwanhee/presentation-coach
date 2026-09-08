"""LLM 게이트웨이(건국대 API Gateway, Mindlogic) 실측 테스트 스크립트.

목적: app/llm/gateway_client.py의 analyze_consistency()가 실제 게이트웨이에
붙는지, 특히 strict 모드 JSON 스키마(중첩 객체 + optional 필드)를 게이트웨이가
정말로 그대로 강제해주는지 확인한다(AI_설계.md §3, §5 "실제 게이트웨이 호출
검증" 항목). 더미 슬라이드 3개 + 더미 발화(그룹B 후보 "그"를 정상 용법/필러
용법 두 군데에 일부러 섞어 넣음)로 돌려서, LLM이 스키마를 지키는지·그룹B
판단이 그럴듯한지 눈으로 확인하는 용도.

사용법:
    1. .env에 LLM_GATEWAY_API_KEY 채우기 (LLM_GATEWAY_BASE_URL/MODEL은
       .env.example 기본값 그대로 둬도 됨)
    2. pip install -r requirements.txt
    3. python scripts/test_llm_gateway.py [모델명]
       (모델명 생략하면 app/llm/gateway_client.DEFAULT_MODEL 사용)

결과는 scripts/llm_test_results/ 밑에 저장되고, 터미널에도 요약이 바로 출력된다.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# 프로젝트 루트를 sys.path에 넣어야 app.* 임포트가 됨 (ai-service/ 밑에서 실행 가정)
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.llm.gateway_client import DEFAULT_MODEL, LlmError, analyze_consistency  # noqa: E402
from app.parsing.pptx_parser import Slide  # noqa: E402
from app.schemas.job import TranscriptSegment  # noqa: E402
from app.stt.clova_client import TranscriptResult, WordTiming  # noqa: E402

RESULTS_DIR = Path(__file__).parent / "llm_test_results"


def _dummy_slides() -> list[Slide]:
    return [
        Slide(slide_index=0, text="발표 주제: 우리 서비스는 사용자 이탈률을 30% 낮췄습니다."),
        Slide(slide_index=1, text="핵심 기능: 실시간 알림과 맞춤 추천을 제공합니다."),
        Slide(slide_index=2, text="다음 분기 계획: 해외 시장 진출을 준비 중입니다."),
    ]


def _dummy_transcript() -> TranscriptResult:
    # 일부러 "그"를 두 번 넣음 — 하나는 지시대명사(정상 용법), 하나는 필러성 사용.
    # LLM이 그룹B 후보를 정상/필러로 구분하는지 확인하는 게 이 스크립트의 핵심.
    words_by_segment = [
        ["안녕하세요", "저희", "서비스", "소개", "시작하겠습니다"],
        ["저희", "서비스는", "그", "사용자", "이탈률을", "30퍼센트", "낮췄습니다"],  # "그"=지시대명사(정상)
        ["그리고", "음", "그", "그게", "저희가", "제일", "자신있는", "부분인데요"],  # "그"=필러성
        ["실시간", "알림", "기능도", "있습니다"],
        ["다음", "분기에는", "해외", "진출도", "준비하고", "있어요"],
    ]
    words: list[WordTiming] = []
    segments: list[TranscriptSegment] = []
    t = 0
    for seg_words in words_by_segment:
        seg_start = t
        seg_texts = []
        for w in seg_words:
            start = t
            end = t + 400
            words.append(WordTiming(text=w, start_ms=start, end_ms=end))
            seg_texts.append(w)
            t = end + 100  # 단어 사이 자연스러운 간격
        segments.append(
            TranscriptSegment(start_ms=seg_start, end_ms=t - 100, text=" ".join(seg_texts))
        )
        t += 300  # 세그먼트 사이 간격

    full_text = " ".join(w.text for w in words)
    return TranscriptResult(full_text=full_text, words=words, segments=segments)


def _dummy_script() -> str:
    return (
        "안녕하세요, 저희 서비스 소개를 시작하겠습니다. "
        "저희 서비스는 사용자 이탈률을 30퍼센트 낮췄습니다. "
        "실시간 알림 기능도 있습니다. "
        "다음 분기에는 해외 진출도 준비하고 있습니다."
    )


def main() -> None:
    model = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MODEL
    print(f"모델: {model}")

    slides = _dummy_slides()
    transcript = _dummy_transcript()
    script = _dummy_script()

    print("\n=== 호출 중... ===")
    try:
        result = analyze_consistency(slides, transcript, script, model=model)
    except LlmError as exc:
        print(f"\nLLM 호출 실패: {exc}")
        sys.exit(1)

    print("\n=== consistency ===")
    print(f"supported_count={result.consistency.supported_count} / total_claims={result.consistency.total_claims}")
    for c in result.consistency.checks:
        print(f"  [slide {c.slide_index}] {c.verdict.value} — {c.claim!r} (evidence_at_ms={c.evidence_at_ms})")

    print("\n=== off_topic ===")
    for ot in result.off_topic:
        print(f"  [{ot.start_ms}~{ot.end_ms}ms] {ot.text!r} — {ot.reason}")

    print("\n=== logic_gaps ===")
    for lg in result.logic_gaps:
        print(f"  [{lg.at_ms}ms] {lg.text!r} — {lg.note}")

    print("\n=== script_diff ===")
    if result.script_diff is None:
        print("  없음(대본 미제공 또는 LLM이 null 반환)")
    else:
        print(f"  matched_ratio={result.script_diff.matched_ratio}")
        for d in result.script_diff.deviations:
            print(f"  [{d.at_ms}ms][{d.kind.value}] 대본={d.script_text!r} / 발화={d.spoken_text!r}")

    print("\n=== 그룹B 채움말 판단 (핵심 확인 포인트) ===")
    if not result.group_b_fillers:
        print("  필러로 판단된 것 없음")
    for f in result.group_b_fillers:
        print(f"  [{f.at_ms}ms] type={f.type.value} text={f.text!r} duration={f.duration_ms}ms")
    print(
        "  (기대: 두 번째 세그먼트의 '그'=지시대명사는 필러 아님, "
        "세 번째 세그먼트의 '그'/'그게'는 필러로 잡히면 이상적)"
    )

    RESULTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = RESULTS_DIR / f"{model}_{ts}.json"
    out_path.write_text(
        json.dumps(_result_to_dict(result), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n전체 결과 저장: {out_path}")


def _result_to_dict(result) -> dict:
    return {
        "consistency": {
            "supported_count": result.consistency.supported_count,
            "total_claims": result.consistency.total_claims,
            "checks": [c.model_dump(mode="json") for c in result.consistency.checks],
        },
        "off_topic": [o.model_dump(mode="json") for o in result.off_topic],
        "logic_gaps": [g.model_dump(mode="json") for g in result.logic_gaps],
        "script_diff": result.script_diff.model_dump(mode="json") if result.script_diff else None,
        "group_b_fillers": [f.model_dump(mode="json") for f in result.group_b_fillers],
    }


if __name__ == "__main__":
    main()
