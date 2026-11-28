import { useCallback, useEffect, useState } from "react";
import { isAbortError } from "../api/request.ts";
import { buildIssueMapUrl, fetchIssueMap } from "./api.ts";
import { VIEWPORT_DEBOUNCE_MS } from "./constants.ts";
import type { IssueFeatureCollection, IssueFilters, ViewportBounds } from "./types.ts";

export type IssueMapState = {
  status: "idle" | "loading" | "success" | "error";
  data: IssueFeatureCollection | null;
  error: string | null;
};

/**
 * Fetch Issues for the visible viewport. Requests are debounced, and a newer
 * viewport or filter selection aborts the in-flight request; a response that
 * arrives after its request was superseded is ignored.
 */
export function useIssueMap(
  viewport: ViewportBounds | null,
  filters: IssueFilters,
  debounceMs: number = VIEWPORT_DEBOUNCE_MS,
): IssueMapState & { retry: () => void } {
  const [state, setState] = useState<IssueMapState>({
    status: "idle",
    data: null,
    error: null,
  });
  const [attempt, setAttempt] = useState(0);
  const url = viewport ? buildIssueMapUrl(viewport, filters) : null;

  useEffect(() => {
    if (url === null) return;
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setState((previous) => ({ ...previous, status: "loading", error: null }));
      try {
        const data = await fetchIssueMap(url, controller.signal);
        if (!controller.signal.aborted) setState({ status: "success", data, error: null });
      } catch (error) {
        if (controller.signal.aborted || isAbortError(error)) return;
        setState((previous) => ({
          ...previous,
          status: "error",
          error: error instanceof Error ? error.message : "Unable to load issues.",
        }));
      }
    }, debounceMs);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [url, attempt, debounceMs]);

  const retry = useCallback(() => setAttempt((value) => value + 1), []);
  return { ...state, retry };
}
