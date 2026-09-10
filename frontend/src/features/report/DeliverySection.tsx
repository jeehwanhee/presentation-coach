import type { Delivery } from "../../types/report";

function formatMs(ms: number): string {
  const totalSeconds = Math.round(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}분 ${seconds}초`;
}

// WPM은 이미 "분당"으로 정규화된 값이라 일반적인 발표 기준으로 등급을 매길 수 있음.
// (일반적인 발표 가이드라인 기준 — 개인차·언어 특성에 따라 다를 수 있는 참고값)
function wpmRating(wpm: number): { label: string; tone: "low" | "good" | "high" } {
  if (wpm < 100) return { label: "느림", tone: "low" };
  if (wpm > 160) return { label: "빠름", tone: "high" };
  return { label: "적정", tone: "good" };
}

export function DeliverySection({ delivery }: { delivery: Delivery }) {
  const rating = wpmRating(delivery.wpm);

  return (
    <section className="report-section">
      <h3>전달력</h3>
      <div className="delivery-stats">
        <div className="stat">
          <span className="stat-value">
            {Math.round(delivery.wpm)}
            <span className={`stat-rating tone-${rating.tone}`}>{rating.label}</span>
          </span>
          <span className="stat-label">분당 단어 수(WPM)</span>
        </div>
        <div className="stat">
          <span className="stat-value">{formatMs(delivery.silence_total_ms)}</span>
          <span className="stat-label">총 침묵 시간 · 낮을수록 좋아요</span>
        </div>
        <div className="stat">
          <span className="stat-value">{delivery.filler_count}</span>
          <span className="stat-label">채움말 횟수 · 낮을수록 좋아요</span>
        </div>
        <div className="stat">
          <span className="stat-value">{delivery.long_pauses.length}</span>
          <span className="stat-label">긴 침묵 구간 · 낮을수록 좋아요</span>
        </div>
      </div>
      <p className="field-hint">
        발표 전체 길이에 따라 침묵·채움말·긴 침묵 구간의 적정 횟수는 달라질 수 있어요. WPM만
        일반적인 발표 기준(분당 100~160단어)으로 판단했어요.
      </p>
      <p className="field-hint">{delivery.volume_variation.note}</p>
    </section>
  );
}
