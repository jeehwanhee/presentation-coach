import { useState } from "react";
import type { Consistency } from "../../types/report";

const VERDICT_LABEL: Record<string, string> = {
  SUPPORTED: "뒷받침됨",
  NOT_MENTIONED: "미언급",
  NO_BASIS: "근거없음",
};

const COLLAPSED_COUNT = 5;

export function ConsistencySection({ consistency }: { consistency: Consistency }) {
  const [expanded, setExpanded] = useState(false);

  const hasMore = consistency.checks.length > COLLAPSED_COUNT;
  const visibleChecks = expanded ? consistency.checks : consistency.checks.slice(0, COLLAPSED_COUNT);

  return (
    <section className="report-section">
      <h3>
        발표자료 정합성{" "}
        <span className="section-summary">
          {consistency.supported_count}/{consistency.total_claims} 뒷받침됨
        </span>
      </h3>
      <ul className="consistency-list">
        {visibleChecks.map((check, i) => (
          <li key={i} className={`verdict-${check.verdict}`}>
            <div className="verdict-row">
              <span className="verdict-badge">{VERDICT_LABEL[check.verdict] ?? check.verdict}</span>
              <span className="slide-tag">슬라이드 {check.slide_index + 1}</span>
            </div>
            <p className="claim">{check.claim}</p>
            {check.evidence_span && <p className="evidence">"{check.evidence_span}"</p>}
          </li>
        ))}
      </ul>
      {hasMore && (
        <button type="button" className="show-more-btn" onClick={() => setExpanded((v) => !v)}>
          {expanded ? "접기 ▲" : `${consistency.checks.length - COLLAPSED_COUNT}개 더보기 ▼`}
        </button>
      )}
    </section>
  );
}
