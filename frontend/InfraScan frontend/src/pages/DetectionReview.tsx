
import { useState } from "react";
import infrascanLogo from "../assets/infrascan.svg";
import "../App.css";
import "./DetectionReview.css";

export default function DetectionReview() {
  const navigate = (to: string | number) => {
    if (to === -1) window.history.back();
    else window.location.hash = `#${to}`;
  };

  const [decision, setDecision] = useState<
    "Unreviewed" | "Validated" | "Rejected"
  >("Unreviewed");

  const [notes, setNotes] = useState("");

  return (
    <main className="app-shell">
      {/* InfraScan header */}
      <header className="topbar">
        <a
          className="brand"
          href="#/overview"
          aria-label="InfraScan home"
        >
          <img
            className="brand-mark"
            src={infrascanLogo}
            alt=""
          />
          <span>InfraScan</span>
        </a>

        <span className="topbar-note">
          Road infrastructure inspection
        </span>
      </header>

      {/* Page navigation */}
      <nav
        className="page-navigation"
        aria-label="Page navigation"
      >
        <button
          type="button"
          className="navigation-button"
          onClick={() => navigate("/detections")}
        >
          ← Back to Detections
        </button>

        <button
          type="button"
          className="navigation-button"
          onClick={() => navigate("/overview")}
        >
          → Overview
        </button>
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

      <div className="review-layout">
        {/* Detection evidence */}
        <section className="review-panel">
          <div className="review-panel-heading">
            <div>
              <p className="eyebrow">EVIDENCE</p>
              <h2>Detection image</h2>
            </div>
            <span className="review-tag">Preview</span>
          </div>

          <div className="review-evidence-placeholder">
            <span className="review-evidence-icon">▧</span>
            <strong>No evidence image available</strong>
            <p>
              The original road image and detection
              bounding box will appear here once connected.
            </p>
          </div>

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
            <span
              className={`review-status ${decision.toLowerCase()}`}
            >
              {decision}
            </span>
          </div>

          <div className="review-details">
            <div>
              <span>Detection ID</span>
              <strong>Not connected</strong>
            </div>

            <div>
              <span>Classification</span>
              <strong>Not available</strong>
            </div>

            <div>
              <span>Severity</span>
              <strong>—</strong>
            </div>

            <div>
              <span>Model confidence</span>
              <strong>—</strong>
            </div>

            <div>
              <span>Location</span>
              <strong>Not available</strong>
            </div>

            <div>
              <span>Detection date</span>
              <strong>—</strong>
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
          road infrastructure issue.
        </p>

        <label className="review-notes-label" htmlFor="review-notes">
          Reviewer notes
        </label>

        <textarea
          id="review-notes"
          className="review-notes"
          placeholder="Add observations or reasons for your decision..."
          value={notes}
          onChange={(event) => setNotes(event.target.value)}
          rows={4}
        />

        <div className="review-actions">
          <button
            type="button"
            className="review-validate-button"
            onClick={() => setDecision("Validated")}
          >
            ✓ Validate detection
          </button>

          <button
            type="button"
            className="review-reject-button"
            onClick={() => setDecision("Rejected")}
          >
            ✕ Reject detection
          </button>

          <button
            type="button"
            className="review-cancel-button"
            onClick={() => navigate("/detections")}
          >
            Back to detections
          </button>
        </div>

        {decision !== "Unreviewed" && (
          <p className="review-confirmation" role="status">
            Detection marked as {decision.toLowerCase()}.
            This is a frontend-only preview and has not
            been saved to the backend.
          </p>
        )}
      </section>

      <footer>InfraScan · Road damage detection</footer>
    </main>
  );
}
