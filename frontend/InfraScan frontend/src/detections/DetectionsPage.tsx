import { useEffect, useState } from "react";
import { isPrivileged, useAuth } from "../auth/AuthContext.ts";
import Detections, { type Detection } from "../pages/Detections.tsx";
import { detectionHref } from "../routing.ts";
import { REGISTER_LIMIT, fetchDetections, toRegisterRow } from "./api.ts";

type LoadState =
  | { status: "loading" }
  | { status: "error"; error: string }
  | { status: "loaded"; rows: Detection[]; total: number; activeTickets: number };

/** Loads the detection register from the API and renders Eileen's register design. */
export function DetectionsPage() {
  const auth = useAuth();
  const allowed = !auth || isPrivileged(auth.user);
  const [state, setState] = useState<LoadState>({ status: "loading" });

  useEffect(() => {
    if (!allowed) return;
    const controller = new AbortController();
    fetchDetections(controller.signal)
      .then((page) => {
        const rows = page.items.map(toRegisterRow);
        // Accepted Issues are confirmed and waiting for repair.
        const activeTickets = new Set(
          page.items.filter((d) => d.issue_status === "accepted").map((d) => d.issue_id),
        ).size;
        setState({ status: "loaded", rows, total: page.total, activeTickets });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setState({
          status: "error",
          error: error instanceof Error ? error.message : "Unable to load detections.",
        });
      });
    return () => controller.abort();
  }, [allowed]);

  if (!allowed) {
    return (
      <p className="empty-state" role="status">
        The detection register is available to municipal administrators and developers.
      </p>
    );
  }
  if (state.status === "loading") {
    return (
      <p className="empty-state" role="status">
        Loading detections…
      </p>
    );
  }
  if (state.status === "error") {
    return (
      <div className="error-banner" role="alert">
        <strong>Could not load detections</strong>
        <span>{state.error}</span>
      </div>
    );
  }
  return (
    <>
      <Detections
        detections={state.rows}
        activeTickets={state.activeTickets}
        onReview={(id) => {
          window.location.hash = detectionHref(id);
        }}
      />
      {state.total > REGISTER_LIMIT && (
        <p className="empty-state">
          Showing the newest {REGISTER_LIMIT} of {state.total} detections.
        </p>
      )}
    </>
  );
}
