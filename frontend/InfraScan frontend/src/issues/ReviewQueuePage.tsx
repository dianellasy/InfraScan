import { useEffect, useState } from "react";
import { fetchReviewQueue } from "./api.ts";
import { reviewReasonLabel, severityInfo, statusLabel } from "./constants.ts";
import { formatDateTime } from "./format.ts";
import type { Issue, Page } from "./types.ts";
import "./issues.css";

const PAGE_SIZE = 50;

type QueueState =
  | { status: "loading" }
  | { status: "error"; error: string }
  | { status: "success"; page: Page<Issue> };

export function ReviewQueuePage() {
  const [page, setPage] = useState(1);
  const [state, setState] = useState<QueueState>({ status: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    fetchReviewQueue(page, PAGE_SIZE, controller.signal)
      .then((result) => setState({ status: "success", page: result }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setState({
          status: "error",
          error: error instanceof Error ? error.message : "Unable to load the review queue.",
        });
      });
    return () => controller.abort();
  }, [page]);

  const changePage = (next: number) => {
    setState({ status: "loading" });
    setPage(next);
  };

  return (
    <section className="review-queue-page">
      <div className="issue-map-heading">
        <div>
          <p className="eyebrow">REVIEW QUEUE</p>
          <h1>Issues needing review</h1>
          <p>Includes issues without a location, which cannot appear on the map.</p>
        </div>
        {state.status === "success" && (
          <span className="result-count">{state.page.total} issues</span>
        )}
      </div>

      {state.status === "loading" && (
        <p className="empty-state" role="status">
          Loading review queue…
        </p>
      )}
      {state.status === "error" && (
        <div className="error-banner" role="alert">
          <strong>Could not load the review queue</strong>
          <span>{state.error}</span>
        </div>
      )}
      {state.status === "success" && state.page.items.length === 0 && (
        <p className="empty-state">No issues need review.</p>
      )}
      {state.status === "success" && state.page.items.length > 0 && (
        <section className="panel results-panel">
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Issue type</th>
                  <th>Severity</th>
                  <th>Status</th>
                  <th>Review reason</th>
                  <th>Location</th>
                  <th>Detections</th>
                  <th>Last seen</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {state.page.items.map((issue) => (
                  <tr key={issue.issue_id}>
                    <td>
                      <strong>{issue.issue_type_name}</strong>
                    </td>
                    <td>
                      <span className={`severity ${issue.severity}`}>
                        {severityInfo(issue.severity).label}
                      </span>
                    </td>
                    <td>{statusLabel(issue.status)}</td>
                    <td>{issue.review_reason ? reviewReasonLabel(issue.review_reason) : "—"}</td>
                    <td>
                      {issue.latitude != null ? (
                        "On map"
                      ) : (
                        <span className="no-location-pill">No location</span>
                      )}
                    </td>
                    <td>{issue.detection_count}</td>
                    <td>{formatDateTime(issue.last_seen_at)}</td>
                    <td>
                      <a href={`#/issues/${encodeURIComponent(issue.issue_id)}`}>Open</a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {state.page.total > PAGE_SIZE && (
            <div className="pager">
              <button
                type="button"
                className="secondary-button"
                disabled={page === 1}
                onClick={() => changePage(page - 1)}
              >
                Previous
              </button>
              <span>
                Page {page} of {Math.ceil(state.page.total / PAGE_SIZE)}
              </span>
              <button
                type="button"
                className="secondary-button"
                disabled={page * PAGE_SIZE >= state.page.total}
                onClick={() => changePage(page + 1)}
              >
                Next
              </button>
            </div>
          )}
        </section>
      )}
    </section>
  );
}
