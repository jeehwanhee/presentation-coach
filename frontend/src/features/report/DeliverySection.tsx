import type { Delivery } from "../../types/report";

function formatMs(ms: number): string {
  const totalSeconds = Math.round(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}분 ${seconds}초`;
}

export function DeliverySection({ delivery }: { delivery: Delivery }) {
  return (
    <section className="report-section">
      <h3>전달력</h3>
      <div className="delivery-stats">
        <div className="stat">
          <span className="stat-value">{Math.round(delivery.wpm)}</span>
          <span className="stat-label">분당 단어 수(WPM)</span>
        </div>
        <div className="stat">
          <span className="stat-value">{formatMs(delivery.silence_total_ms)}</span>
          <span className="stat-label">총 침묵 시간</span>
        </div>
        <div className="stat">
          <span className="stat-value">{delivery.filler_count}</span>
          <span className="stat-label">채움말 횟수</span>
        </div>
        <div className="stat">
          <span className="stat-value">{delivery.long_pauses.length}</span>
          <span className="stat-label">긴 침묵 구간</span>
        </div>
      </div>
      <p className="field-hint">{delivery.volume_variation.note}</p>
    </section>
  );
}
