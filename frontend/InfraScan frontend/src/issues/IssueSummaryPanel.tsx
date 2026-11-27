import { reviewReasonLabel, severityInfo, statusLabel } from "./constants.ts";
import { EvidenceImage } from "./EvidenceImage.tsx";
import { formatCoordinates, formatDateTime } from "./format.ts";
import type { IssueFeature } from "./types.ts";

type IssueSummaryPanelProps = {
  feature: IssueFeature | null;
  onClose: () => void;
};

export function IssueSummaryPanel({ feature, onClose }: IssueSummaryPanelProps) {
  if (feature === null) {
    return (
      <aside className="panel issue-panel" aria-label="Selected issue">
        <p className="eyebrow">SELECTED ISSUE</p>
        <p className="empty-state">Select a marker to see the issue’s details.</p>
      </aside>
    );
  }

  const issue = feature.properties;
  const coordinates = feature.geometry?.coordinates;
  return (
    <aside className="panel issue-panel" aria-label="Selected issue">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">SELECTED ISSUE</p>
          <h2>{issue.issue_type_name}</h2>
        </div>
        <button type="button" className="dismiss-button" onClick={onClose} aria-label="Close issue summary">
          ✕
        </button>
      </div>

      {issue.needs_review && (
        <p className="review-callout">
          <span className="legend-review-badge" aria-hidden="true">!</span>
          <span>
            <strong>Needs review.</strong>{" "}
            {issue.review_reason ? reviewReasonLabel(issue.review_reason) : statusLabel(issue.status)}
          </span>
        </p>
      )}

      {issue.evidence_thumbnail_url ? (
        <EvidenceImage
          path={issue.evidence_thumbnail_url}
          alt={`Representative evidence for this ${issue.issue_type_name.toLowerCase()}`}
        />
      ) : (
        <div className="evidence-placeholder">No evidence image</div>
      )}

      <dl className="issue-facts">
        <div>
          <dt>Issue type</dt>
          <dd>{issue.issue_type_name}</dd>
        </div>
        <div>
          <dt>Severity</dt>
          <dd>
            <span className={`severity ${issue.severity}`}>{severityInfo(issue.severity).label}</span>
          </dd>
        </div>
        <div>
          <dt>Status</dt>
          <dd>{statusLabel(issue.status)}</dd>
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
        {issue.review_reason && (
          <div>
            <dt>Review reason</dt>
            <dd>{reviewReasonLabel(issue.review_reason)}</dd>
          </div>
        )}
        {coordinates && (
          <div>
            <dt>Location</dt>
            <dd>
              {formatCoordinates(coordinates[1], coordinates[0])}
              {issue.location_accuracy_m != null && ` (±${issue.location_accuracy_m} m)`}
            </dd>
          </div>
        )}
      </dl>

      <a className="primary-button link-button" href={`#/issues/${encodeURIComponent(issue.issue_id)}`}>
        Open issue details
      </a>
    </aside>
  );
}
