import type { ScriptDiff } from "../../types/report";

export function ScriptDiffSection({ scriptDiff }: { scriptDiff: ScriptDiff }) {
  return (
    <section className="report-section">
      <h3>
        대본 비교{" "}
        <span className="section-summary">일치율 {Math.round(scriptDiff.matched_ratio * 100)}%</span>
      </h3>
      {scriptDiff.deviations.length === 0 ? (
        <p className="field-hint">대본과 실제 발화가 거의 일치했어요.</p>
      ) : (
        <ul className="diff-list">
          {scriptDiff.deviations.map((d, i) => (
            <li key={i}>
              <span className="diff-kind">{d.kind}</span>
              <p className="diff-script">대본: {d.script_text}</p>
              <p className="diff-spoken">발화: {d.spoken_text}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
