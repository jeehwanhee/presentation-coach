import type { Consistency } from "../../types/report";

const VERDICT_LABEL: Record<string, string> = {
  SUPPORTED: "뒷받침됨",
  NOT_MENTIONED: "미언급",
  NO_BASIS: "근거없음",
};

export function ConsistencySection({ consistency }: { consistency: Consistency }) {
  return (
    <section className="report-section">
      <h3>
        발표자료 정합성{" "}
        <span className="section-summary">
          {consistency.supported_count}/{consistency.total_claims} 뒷받침됨
        </span>
      </h3>
      <ul className="consistency-list">
        {consistency.checks.map((check, i) => (
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
    </section>
  );
}
