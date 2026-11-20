import { ISSUE_TYPES, SEVERITIES } from "./constants.ts";
import { IssueSymbol } from "./IssueSymbol.tsx";

export function IssueLegend() {
  return (
    <details className="issue-legend" open>
      <summary>Legend</summary>
      <div className="legend-groups">
        <section>
          <h3>Color: severity</h3>
          <ul>
            {SEVERITIES.map((severity) => (
              <li key={severity.value}>
                <span className="severity-dot" style={{ background: severity.color }} aria-hidden="true" />
                {severity.label}
              </li>
            ))}
          </ul>
        </section>
        <section>
          <h3>Shape: issue type</h3>
          <ul>
            {ISSUE_TYPES.map((type) => (
              <li key={type.code}>
                <IssueSymbol issueType={type.code} />
                {type.label}
              </li>
            ))}
          </ul>
        </section>
        <section>
          <h3>Indicators</h3>
          <ul>
            <li>
              <span className="legend-review-badge" aria-hidden="true">!</span>
              Needs review (pending review, type conflict, or other review reason)
            </li>
            <li>
              <span className="legend-selected" aria-hidden="true" />
              Selected issue
            </li>
            <li>
              <span className="legend-cluster" aria-hidden="true">3</span>
              Group of nearby issues; zoom in or click to expand
            </li>
          </ul>
        </section>
      </div>
    </details>
  );
}
