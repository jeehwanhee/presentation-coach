"""분석 파이프라인 오케스트레이션.

S3/SQS/FastAPI를 전혀 몰라도 되는 순수 함수로 설계 — 로컬 파일 경로만 받아서
report_json을 조립해 반환한다. 인프라 레이어(clients/sqs_consumer.py)는 이
함수를 감싸기만 하면 된다. 개발 중에는 이 함수를 CLI나 main.py의 디버그
엔드포인트로 직접 호출해서 검증한다 (S3 버킷명이 없어도 진행 가능).

실제 서비스에서는 이 함수 호출 한 번(run_analysis)이 PPTX 파싱→STT→전달지표
→LLM→리포트 조립까지 전부 대신 처리한다 — SQS 워커가 잡 메시지를 받으면
자동으로 이 함수를 호출하는 구조라, 사람이 중간 단계를 하나씩 실행할 필요가
없다(scripts/test_*.py들은 어디까지나 개발 중 각 부품을 개별 검증하던 용도).

## 왜 스레드로 일부만 병렬 실행하는가

네 단계 사이의 실제 의존관계는 이렇다:

    1단계(동시 실행): parse_pptx(pptx)      transcribe(audio)
                            ↓ slides              ↓ stt_result
    2단계(동시 실행): compute_delivery_metrics(stt_result, ...)
                       analyze_consistency(slides, stt_result, ...)

- PPTX 파싱과 STT는 서로의 결과를 전혀 필요로 하지 않아서 동시에 돌릴 수 있다.
- 전달지표(Silero VAD)와 LLM 호출은 둘 다 STT 결과만 있으면 되고 서로는
  필요 없어서(그룹A/그룹B는 run_analysis에서 나중에 합침, app/schemas/report.py
  참고) 동시에 돌릴 수 있다. 하지만 이 두 단계는 STT가 끝나야 시작할 수 있다 —
  VAD는 CLOVA가 준 단어 타임스탬프 사이 gap을 봐야 하고, LLM도 발화 전문이
  있어야 정합성을 판단할 수 있어서 STT 없이는 시작 자체가 불가능하다
  (AI_설계.md §4).

ThreadPoolExecutor를 쓰는 이유: LLM 호출은 네트워크 I/O(대기 중 GIL 해제),
Silero VAD는 PyTorch 텐서 연산(연산 중 GIL 해제) — 둘 다 스레드로 겹쳐 돌리면
실질적인 이득이 있고, asyncio로 바꾸거나 멀티프로세스를 쓸 만큼 복잡한 작업이
아니라 스레드 두 개로 충분하다.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from app.audio.delivery_metrics import compute_delivery_metrics
from app.llm.gateway_client import analyze_consistency
from app.parsing.pptx_parser import parse_pptx
from app.schemas.job import TranscriptSegment
from app.schemas.report import AnalysisReport
from app.stt.clova_client import transcribe


@dataclass
class AnalysisInput:
    pptx_path: Path
    audio_path: Path
    audio_duration_ms: int
    script: str | None = None


@dataclass
class AnalysisOutput:
    transcript: list[TranscriptSegment]
    report: AnalysisReport


def run_analysis(input: AnalysisInput) -> AnalysisOutput:
    """전체 파이프라인 실행: PPTX 파싱∥STT → 전달지표∥LLM 정합 검사 → 리포트 조립.

    각 단계 실패 시 해당 예외(PptxParseError/SttError/LlmError)를 그대로 올려서
    clients/sqs_consumer.py가 §1 error.code로 매핑해 콜백을 보내게 한다. 두
    단계 중 하나만 실패해도(ThreadPoolExecutor.result()가 그 예외를 그대로
    다시 던짐) 전체를 실패로 취급한다 — §3.2 "부분 실패도 전체 실패로"와 동일한
    all-or-nothing 원칙.
    """
    # 1단계: PPTX 파싱과 STT는 서로 무관 — 동시 실행.
    with ThreadPoolExecutor(max_workers=2) as pool:
        slides_future = pool.submit(parse_pptx, input.pptx_path)
        stt_future = pool.submit(transcribe, input.audio_path)
        slides = slides_future.result()
        stt_result = stt_future.result()

    # 2단계: 전달지표(그룹A 필러 포함)와 LLM 정합 판정(그룹B 필러 포함)은
    # 둘 다 stt_result만 있으면 되고 서로는 필요 없음 — 동시 실행.
    with ThreadPoolExecutor(max_workers=2) as pool:
        delivery_future = pool.submit(
            compute_delivery_metrics, stt_result, input.audio_path, input.audio_duration_ms
        )
        llm_future = pool.submit(analyze_consistency, slides, stt_result, input.script)
        delivery = delivery_future.result()
        llm_result = llm_future.result()

    # 그룹A(VAD, delivery.fillers)와 그룹B(LLM 문맥판단, llm_result.group_b_fillers)
    # 채움말을 합쳐야 최종 fillers 전체 목록이 된다 (AI_설계.md §2).
    fillers = delivery.fillers + llm_result.group_b_fillers
    delivery = delivery.model_copy(update={"fillers": fillers, "filler_count": len(fillers)})

    report = AnalysisReport(
        consistency=llm_result.consistency,
        off_topic=llm_result.off_topic,
        logic_gaps=llm_result.logic_gaps,
        delivery=delivery,
        script_diff=llm_result.script_diff,
    )

    # stt_result.segments는 §3.2 TranscriptSegment와 타입이 같아 변환 불필요.
    return AnalysisOutput(transcript=stt_result.segments, report=report)
