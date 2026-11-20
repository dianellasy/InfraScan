import { useEffect, useState } from "react";
import { apiUrl } from "../api/endpoints.ts";
import { focusBounds, mapHref } from "../routing.ts";
import { fetchIssue, fetchIssueDetections } from "./api.ts";
import { issueTypeInfo, reviewReasonLabel, severityInfo, statusLabel } from "./constants.ts";
import { formatCoordinates, formatDateTime } from "./format.ts";
import { IssueSymbol } from "./IssueSymbol.tsx";
import type { Detection, Issue, Page } from "./types.ts";
import "./issues.css";

type DetailState =
  | { status: "loading" }
  | { status: "error"; error: string }
  | { status: "success"; issue: Issue; detections: Page<Detection> };

/** Count of supporting detections per predicted canonical type, most common first. */
function predictedTypeCounts(detections: Detection[]): Array<[string, number]> {
  const counts = new Map<string, number>();
  for (const detection of detections) {
    const key = detection.predicted_issue_type_code ?? detection.classification;
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1]);
}

/** Render with `key={issueId}` so state resets when the issue changes. */
export function IssueDetailPage({ issueId }: { issueId: string }) {
  const [state, setState] = useState<DetailState>({ status: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      fetchIssue(issueId, controller.signal),
      fetchIssueDetections(issueId, controller.signal),
    ])
      .then(([issue, detections]) => setState({ status: "success", issue, detections }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setState({
          status: "error",
          error: error instanceof Error ? error.message : "Unable to load the issue.",
        });
      });
    return () => controller.abort();
  }, [issueId]);

  return (
    <section className="issue-detail-page">
      <a className="back-link" href="#/map">
        ← Back to issue map
      </a>
      {state.status === "loading" && (
        <p className="empty-state" role="status">
          Loading issue…
        </p>
      )}
      {state.status === "error" && (
        <div className="error-banner" role="alert">
          <strong>Could not load this issue</strong>
          <span>{state.error}</span>
        </div>
      )}
      {state.status === "success" && (
        <IssueDetail issue={state.issue} detections={state.detections} />
      )}
    </section>
  );
}

function IssueDetail({ issue, detections }: { issue: Issue; detections: Page<Detection> }) {
  const typeCounts = predictedTypeCounts(detections.items);
  const hasConflict = typeCounts.length > 1;
  return (
    <>
      <div className="issue-detail-heading">
        <IssueSymbol
          issueType={issue.issue_type_code}
          fill={severityInfo(issue.severity).color}
          textColor={severityInfo(issue.severity).textColor}
        />
        <div>
          <p className="eyebrow">ISSUE</p>
          <h1>{issue.issue_type_name}</h1>
          <div className="pill-row">
            <span className={`severity ${issue.severity}`}>{severityInfo(issue.severity).label}</span>
            <span className={`status-pill ${issue.status}`}>{statusLabel(issue.status)}</span>
            {issue.needs_review && <span className="review-pill">Needs review</span>}
          </div>
        </div>
      </div>

      {(issue.review_reason || hasConflict) && (
        <div className="review-callout" role="note">
          <span className="legend-review-badge" aria-hidden="true">!</span>
          <span>
            {issue.review_reason && <strong>{reviewReasonLabel(issue.review_reason)}. </strong>}
            {hasConflict && (
              <>
                Supporting detections predicted{" "}
                {typeCounts
                  .map(([code, count]) => `${issueTypeInfo(code).label} ×${count}`)
                  .join(", ")}
                . They are kept as one issue until reviewed.
              </>
            )}
          </span>
        </div>
      )}

      <div className="issue-detail-grid">
        <section className="panel issue-detail-facts">
          <dl className="issue-facts">
            <div>
              <dt>Canonical type</dt>
              <dd>{issue.issue_type_name}</dd>
            </div>
            <div>
              <dt>Supporting detections</dt>
              <dd>{issue.detection_count}</dd>
            </div>
            <div>
              <dt>First seen</dt>
              <dd>{formatDateTime(issue.first_seen_at)}</dd>
            </div>
            <div>
              <dt>Last seen</dt>
              <dd>{formatDateTime(issue.last_seen_at)}</dd>
            </div>
            <div>
              <dt>Location</dt>
              <dd>
                {issue.latitude != null && issue.longitude != null ? (
                  <>
                    {formatCoordinates(issue.latitude, issue.longitude)}
                    {issue.location_accuracy_m != null && ` (±${issue.location_accuracy_m} m)`}{" "}
                    <a
                      href={mapHref(
                        focusBounds([{ latitude: issue.latitude, longitude: issue.longitude }]),
                        issue.issue_id,
                      )}
                    >
                      Show on map
                    </a>
                  </>
                ) : (
                  "No location (not shown on the map)"
                )}
              </dd>
            </div>
            <div>
              <dt>Issue ID</dt>
              <dd>
                <code>{issue.issue_id}</code>
              </dd>
            </div>
          </dl>
        </section>
        <section className="panel issue-detail-evidence">
          {issue.evidence_thumbnail_url ? (
            <img
              className="evidence-thumbnail"
              src={apiUrl(issue.evidence_thumbnail_url)}
              alt={`Representative evidence for this ${issue.issue_type_name.toLowerCase()}`}
            />
          ) : (
            <div className="evidence-placeholder">No evidence image</div>
          )}
        </section>
      </div>

      <section className="panel results-panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">SUPPORTING DETECTIONS</p>
            <h2>Raw model predictions</h2>
          </div>
          <span className="result-count">
            {detections.items.length} of {detections.total} detections
          </span>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Observed</th>
                <th>Predicted class</th>
                <th>Predicted type</th>
                <th>Confidence</th>
                <th>Severity</th>
                <th>Location</th>
                <th>Evidence</th>
              </tr>
            </thead>
            <tbody>
              {detections.items.map((detection) => {
                const differs =
                  detection.predicted_issue_type_code !== null &&
                  detection.predicted_issue_type_code !== issue.issue_type_code;
                return (
                  <tr key={detection.detection_id} className={differs ? "type-differs" : undefined}>
                    <td>
                      {detection.observed_at ? formatDateTime(detection.observed_at) : "Unknown"}
                      {detection.frame_timestamp != null && (
                        <span className="class-id"> (video {detection.frame_timestamp.toFixed(2)} s)</span>
                      )}
                    </td>
                    <td>
                      <strong>{detection.classification}</strong>
                      {detection.issue_type != null && (
                        <span className="class-id"> (class {detection.issue_type})</span>
                      )}
                    </td>
                    <td>
                      {detection.predicted_issue_type_code
                        ? issueTypeInfo(detection.predicted_issue_type_code).label
                        : "Unmapped"}
                      {differs && <span className="differs-badge">Differs from issue</span>}
                    </td>
                    <td>{(detection.confidence * 100).toFixed(1)}%</td>
                    <td>
                      <span className={`severity ${detection.severity}`}>
                        {severityInfo(detection.severity).label}
                      </span>
                    </td>
                    <td>
                      {detection.latitude != null && detection.longitude != null
                        ? formatCoordinates(detection.latitude, detection.longitude)
                        : "—"}
                    </td>
                    <td>
                      {detection.evidence_media ? (
                        <a
                          href={apiUrl(`/api/v1/evidence/${encodeURIComponent(detection.evidence_media)}`)}
                          target="_blank"
                          rel="noreferrer"
                        >
                          View
                        </a>
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
