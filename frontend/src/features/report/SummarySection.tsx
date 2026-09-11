import type { AnalysisReport } from "../../types/report";
import { getDeliveryRatings } from "./DeliverySection";

interface SummarySectionProps {
  report: AnalysisReport;
  audioDurationMs: number | null;
}

export function SummarySection({ report, audioDurationMs }: SummarySectionProps) {
  const { supported_count, total_claims } = report.consistency;
  const ratings = getDeliveryRatings(report.delivery, audioDurationMs);

  return (
    <section className="report-section summary-section">
      <h3>요약</h3>
      <ul className="summary-list">
        <li>
          <span className="summary-label">발표자료 정합성</span>
          <span className="summary-value">
            {supported_count}/{total_claims} 뒷받침됨
          </span>
        </li>
        <li>
          <span className="summary-label">주제 이탈</span>
          <span className="summary-value">{report.off_topic.length}건</span>
        </li>
        <li>
          <span className="summary-label">논리 비약</span>
          <span className="summary-value">{report.logic_gaps.length}건</span>
        </li>
        {report.script_diff && (
          <li>
            <span className="summary-label">대본 일치율</span>
            <span className="summary-value">{Math.round(report.script_diff.matched_ratio * 100)}%</span>
          </li>
        )}
      </ul>
      <div className="summary-badges">
        {ratings.map((r) => (
          <span key={r.metric} className="summary-badge">
            {r.metric}
            <span className={`stat-rating tone-${r.tone}`}>{r.label}</span>
          </span>
        ))}
      </div>
    </section>
  );
}
