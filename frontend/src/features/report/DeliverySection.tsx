import type { Delivery } from "../../types/report";

type Tone = "good" | "mid" | "bad";

interface Rating {
  label: string;
  tone: Tone;
}

function formatMs(ms: number): string {
  const totalSeconds = Math.round(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}분 ${seconds}초`;
}

/**
 * 발표 코칭 자료 기준 참고값 (2026-09 조사):
 * - WPM 120~160이 적정 구간, 100 미만은 청중 이탈, 180 초과는 이해도 저하
 *   (National Communication Association 기준 인용, virtualspeech.com 등 다수 출처)
 * - 채움말: 평균 화자 분당 5회, 전문 발표자 분당 1~2회, 분당 10회 넘으면 확실히 거슬림
 *   (Quantified Communications, Carleton Univ. 커뮤니케이션 연구)
 * - "긴 침묵"(long_pauses)은 어색한 공백을 가리키는 우리 정의라, TED 발표자의
 *   "분당 5회 전략적 멈춤"(의도된 임팩트용) 통계는 그대로 못 씀 — 별도로 보수적인
 *   기준을 잡음.
 * - 침묵 비율은 리서치에서 직접적인 기준을 못 찾아 일반적인 판단으로 잡음.
 * 전부 "일반적인 가이드라인" 수준 참고값이라 발표 주제·상황에 따라 다를 수 있음.
 */
const RATE_THRESHOLDS = {
  fillersPerMin: { good: 2, mid: 5 },
  longPausesPerMin: { good: 1, mid: 2 },
  silenceRatio: { good: 0.15, mid: 0.25 }, // 총 침묵시간 / 전체 길이
};

function wpmRating(wpm: number): Rating {
  if (wpm >= 120 && wpm <= 160) return { label: "적절", tone: "good" };
  if (wpm >= 100 && wpm <= 180) return { label: "보통", tone: "mid" };
  return { label: "개선 필요", tone: "bad" };
}

function rateByThreshold(value: number, good: number, mid: number): Rating {
  if (value <= good) return { label: "적절", tone: "good" };
  if (value <= mid) return { label: "보통", tone: "mid" };
  return { label: "개선 필요", tone: "bad" };
}

interface DeliverySectionProps {
  delivery: Delivery;
  audioDurationMs: number | null;
}

export function DeliverySection({ delivery, audioDurationMs }: DeliverySectionProps) {
  const durationMin = audioDurationMs ? audioDurationMs / 60_000 : null;

  const wpm = wpmRating(delivery.wpm);
  const fillerRating = durationMin
    ? rateByThreshold(delivery.filler_count / durationMin, RATE_THRESHOLDS.fillersPerMin.good, RATE_THRESHOLDS.fillersPerMin.mid)
    : null;
  const pauseRating = durationMin
    ? rateByThreshold(
        delivery.long_pauses.length / durationMin,
        RATE_THRESHOLDS.longPausesPerMin.good,
        RATE_THRESHOLDS.longPausesPerMin.mid,
      )
    : null;
  const silenceRating = audioDurationMs
    ? rateByThreshold(
        delivery.silence_total_ms / audioDurationMs,
        RATE_THRESHOLDS.silenceRatio.good,
        RATE_THRESHOLDS.silenceRatio.mid,
      )
    : null;

  return (
    <section className="report-section">
      <h3>전달력</h3>
      <div className="delivery-stats">
        <div className="stat">
          <span className="stat-value">
            {Math.round(delivery.wpm)}
            <span className={`stat-rating tone-${wpm.tone}`}>{wpm.label}</span>
          </span>
          <span className="stat-label">분당 단어 수(WPM)</span>
        </div>
        <div className="stat">
          <span className="stat-value">
            {formatMs(delivery.silence_total_ms)}
            {silenceRating && <span className={`stat-rating tone-${silenceRating.tone}`}>{silenceRating.label}</span>}
          </span>
          <span className="stat-label">총 침묵 시간</span>
        </div>
        <div className="stat">
          <span className="stat-value">
            {delivery.filler_count}
            {fillerRating && <span className={`stat-rating tone-${fillerRating.tone}`}>{fillerRating.label}</span>}
          </span>
          <span className="stat-label">채움말 횟수</span>
        </div>
        <div className="stat">
          <span className="stat-value">
            {delivery.long_pauses.length}
            {pauseRating && <span className={`stat-rating tone-${pauseRating.tone}`}>{pauseRating.label}</span>}
          </span>
          <span className="stat-label">긴 침묵 구간</span>
        </div>
      </div>
      <p className="field-hint">
        발표 길이를 반영해 분당 비율로 평가했어요. 일반적인 발표 가이드라인 기준 참고값이라
        주제·상황에 따라 실제 적정 수준은 다를 수 있어요.
      </p>
      <p className="field-hint">{delivery.volume_variation.note}</p>
    </section>
  );
}
