import { useEffect, useState } from "react";
import { isPrivileged, useAuth } from "../auth/AuthContext.ts";
import {
  type ApiDetection,
  type ReviewDecision,
  fetchDetection,
  formatLocation,
  reviewIssue,
  reviewState,
  sourceLabel,
} from "../detections/api.ts";
import { EvidenceImage } from "../issues/EvidenceImage.tsx";
import { issueHref } from "../routing.ts";
import "../App.css";
import "./DetectionReview.css";

type Decision = "Unreviewed" | "Validated" | "Rejected";

type LoadState =
  | { status: "loading" }
  | { status: "error"; error: string }
  | { status: "loaded"; detection: ApiDetection };

const DECISION_STATUS: Record<Exclude<Decision, "Unreviewed">, ReviewDecision> = {
  Validated: "accepted",
  Rejected: "dismissed",
};

const SEVERITY_LABELS: Record<string, string> = {
  critical: "High",
  moderate: "Medium",
  minor: "Low",
};

/**
 * Inspect one detection and record a decision on the Issue it supports.
 * Validating accepts the Issue, rejecting dismisses it; both leave the review queue.
 */
export default function DetectionReview({ detectionId }: { detectionId: string }) {
  const auth = useAuth();
  const allowed = !auth || isPrivileged(auth.user);
  const [state, setState] = useState<LoadState>({ status: "loading" });
  const [decision, setDecision] = useState<Decision>("Unreviewed");
  const [notes, setNotes] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState("");

  useEffect(() => {
    if (!allowed) return;
    const controller = new AbortController();
    fetchDetection(detectionId, controller.signal)
      .then((detection) => {
        setState({ status: "loaded", detection });
        setDecision(reviewState(detection.issue_status));
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setState({
          status: "error",
          error: error instanceof Error ? error.message : "Unable to load this detection.",
        });
      });
    return () => controller.abort();
  }, [allowed, detectionId]);

  const detection = state.status === "loaded" ? state.detection : null;
  const issueId = detection?.issue_id ?? null;

  async function decide(next: Exclude<Decision, "Unreviewed">) {
    if (!issueId) return;
    setSaving(true);
    setSaveError("");
    setSaved(false);
    try {
      await reviewIssue(issueId, DECISION_STATUS[next], notes);
      setDecision(next);
      setSaved(true);
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : "Unable to save the decision.");
    } finally {
      setSaving(false);
    }
  }

  if (!allowed) {
    return (
      <p className="empty-state" role="status">
        Detection review is available to municipal administrators and developers.
      </p>
    );
  }

  return (
    <>
      {/* Page navigation */}
      <nav className="page-navigation" aria-label="Page navigation">
        <a className="navigation-button" href="#/detections">
          ← Back to Detections
        </a>
        {issueId && (
          <a className="navigation-button" href={issueHref(issueId)}>
            → Open issue
          </a>
        )}
      </nav>

      {/* Page heading */}
      <section className="review-heading">
        <p className="eyebrow">ROAD ISSUE MONITORING</p>
        <h1>Detection Review</h1>
        <p>
          Inspect detection evidence, verify road conditions,
          and record a validation decision.
        </p>
      </section>

      {state.status === "error" && (
        <div className="error-banner" role="alert">
          <strong>Could not load this detection</strong>
          <span>{state.error}</span>
        </div>
      )}

      <div className="review-layout">
        {/* Detection evidence */}
        <section className="review-panel">
          <div className="review-panel-heading">
            <div>
              <p className="eyebrow">EVIDENCE</p>
              <h2>Detection image</h2>
            </div>
            <span className="review-tag">Model output</span>
          </div>

          {detection?.evidence_media ? (
            <EvidenceImage
              path={`/api/v1/evidence/${detection.evidence_media}`}
              alt={`${detection.classification} with the model's detection boxes`}
              className="review-evidence-image"
            />
          ) : (
            <div className="review-evidence-placeholder">
              <span className="review-evidence-icon">▧</span>
              <strong>
                {state.status === "loading" ? "Loading…" : "No evidence image available"}
              </strong>
              <p>The annotated road image appears here when the detection has one.</p>
            </div>
          )}

          <p className="review-help">
            Review the evidence before confirming whether
            the detected road issue is valid.
          </p>
        </section>

        {/* Detection details */}
        <section className="review-panel">
          <div className="review-panel-heading">
            <div>
              <p className="eyebrow">INSPECTION DETAILS</p>
              <h2>Detection information</h2>
            </div>

            <span className={`review-status ${decision.toLowerCase()}`}>
              {decision}
            </span>
          </div>

          <div className="review-details">
            <div>
              <span>Detection ID</span>
              <strong title={detectionId}>{detectionId.slice(0, 8)}</strong>
            </div>
            <div>
              <span>Classification</span>
              <strong>{detection?.classification ?? "—"}</strong>
            </div>
            <div>
              <span>Severity</span>
              <strong>
                {detection ? (SEVERITY_LABELS[detection.severity] ?? detection.severity) : "—"}
              </strong>
            </div>
            <div>
              <span>Model confidence</span>
              <strong>{detection ? `${(detection.confidence * 100).toFixed(1)}%` : "—"}</strong>
            </div>
            <div>
              <span>Location</span>
              <strong>
                {detection ? formatLocation(detection.latitude, detection.longitude) : "—"}
              </strong>
            </div>
            <div>
              <span>Source</span>
              <strong>{detection ? sourceLabel(detection) : "—"}</strong>
            </div>
            <div>
              <span>Detection date</span>
              <strong>
                {detection
                  ? new Date(detection.observed_at ?? detection.detected_at).toLocaleString()
                  : "—"}
              </strong>
            </div>
          </div>
        </section>
      </div>

      {/* Validation section */}
      <section className="review-panel review-validation">
        <div className="review-panel-heading">
          <div>
            <p className="eyebrow">HUMAN VALIDATION</p>
            <h2>Review decision</h2>
          </div>
        </div>

        <p className="review-description">
          Confirm whether this detection represents a real
          road infrastructure issue. The decision applies to the issue it belongs to.
        </p>

        <label className="review-notes-label" htmlFor="review-notes">
          Reviewer notes
        </label>

        <textarea
          id="review-notes"
          className="review-notes"
          placeholder="Add observations or reasons for your decision..."
          value={notes}
          maxLength={2000}
          onChange={(event) => setNotes(event.target.value)}
          rows={4}
        />

        <div className="review-actions">
          <button
            type="button"
            className="review-validate-button"
            disabled={!issueId || saving}
            onClick={() => void decide("Validated")}
          >
            ✓ Validate detection
          </button>

          <button
            type="button"
            className="review-reject-button"
            disabled={!issueId || saving}
            onClick={() => void decide("Rejected")}
          >
            ✕ Reject detection
          </button>

          <a className="review-cancel-button" href="#/detections">
            Back to detections
          </a>
        </div>

        {detection && !issueId && (
          <p className="review-confirmation" role="status">
            This detection is not linked to an issue, so there is nothing to review.
          </p>
        )}
        {saved && (
          <p className="review-confirmation" role="status">
            Saved: the issue is now {decision === "Validated" ? "accepted" : "dismissed"}.
          </p>
        )}
        {saveError && (
          <div className="error-banner" role="alert">
            <strong>Could not save the decision</strong>
            <span>{saveError}</span>
          </div>
        )}
      </section>
    </>
  );
}
