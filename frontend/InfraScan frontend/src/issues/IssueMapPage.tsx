import { useCallback, useMemo, useState } from "react";
import type { LatLngTuple } from "leaflet";
import { isDateRangeValid } from "./api.ts";
import { EMPTY_FILTERS, STANTON_ANAHEIM_ROUTE, VIEWPORT_DEBOUNCE_MS } from "./constants.ts";
import { IssueFiltersPanel } from "./IssueFiltersPanel.tsx";
import { IssueLegend } from "./IssueLegend.tsx";
import { IssueMap } from "./IssueMap.tsx";
import { IssueSummaryPanel } from "./IssueSummaryPanel.tsx";
import { useIssueMap } from "./useIssueMap.ts";
import type { IssueFeature, IssueFilters, ViewportBounds } from "./types.ts";
import "./issues.css";

const DEFAULT_BOUNDS: LatLngTuple[] = STANTON_ANAHEIM_ROUTE.map(([lat, lon]) => [lat, lon]);

type IssueMapPageProps = {
  debounceMs?: number;
  /** Area to show first (for example, around a job's Issues); defaults to the seed route. */
  initialBounds?: ViewportBounds;
  /** Issue to select once the map has loaded it. */
  focusIssueId?: string;
};

export function IssueMapPage({
  debounceMs = VIEWPORT_DEBOUNCE_MS,
  initialBounds,
  focusIssueId,
}: IssueMapPageProps) {
  const [viewport, setViewport] = useState<ViewportBounds | null>(null);
  const [filters, setFilters] = useState<IssueFilters>(EMPTY_FILTERS);
  const [selected, setSelected] = useState<IssueFeature | null>(null);
  const [focusPending, setFocusPending] = useState(focusIssueId !== undefined);

  const mapBounds = useMemo<LatLngTuple[]>(
    () =>
      initialBounds
        ? [
            [initialBounds.south, initialBounds.west],
            [initialBounds.north, initialBounds.east],
          ]
        : DEFAULT_BOUNDS,
    [initialBounds],
  );
  const requestFilters = useMemo(
    () => (isDateRangeValid(filters) ? filters : { ...filters, lastSeenFrom: "", lastSeenTo: "" }),
    [filters],
  );
  const { status, data, error, retry } = useIssueMap(viewport, requestFilters, debounceMs);
  const features = useMemo(() => data?.features ?? [], [data]);
  const shown =
    selected ??
    (focusPending ? (features.find((feature) => feature.id === focusIssueId) ?? null) : null);

  const selectIssue = useCallback(
    (issueId: string) => {
      setFocusPending(false);
      setSelected(features.find((feature) => feature.id === issueId) ?? null);
    },
    [features],
  );
  const clearSelection = useCallback(() => {
    setFocusPending(false);
    setSelected(null);
  }, []);

  return (
    <section className="issue-map-page">
      <div className="issue-map-heading">
        <div>
          <p className="eyebrow">ISSUE MAP</p>
          <h1>Road issues</h1>
          <p>One marker per canonical issue. Repeated detections of the same issue share a marker.</p>
        </div>
        <div className="issue-map-actions">
          {data && (
            <span className="result-count" aria-live="polite">
              {data.meta.total} {data.meta.total === 1 ? "issue" : "issues"} in view
            </span>
          )}
          <button
            type="button"
            className="secondary-button"
            onClick={retry}
            disabled={viewport === null || status === "loading"}
          >
            Refresh
          </button>
        </div>
      </div>

      <div className="issue-map-layout">
        <IssueFiltersPanel value={filters} onChange={setFilters} />

        <div className="issue-map-stage">
          <IssueMap
            features={features}
            selectedIssueId={shown?.id ?? null}
            initialBounds={mapBounds}
            onSelectIssue={selectIssue}
            onViewportChange={setViewport}
          />
          <div className="map-overlays">
            {status === "loading" && (
              <p className="map-status" role="status">
                Loading issues…
              </p>
            )}
            {status === "error" && (
              <div className="map-status error" role="alert">
                <span>Could not load issues. {error}</span>
                <button type="button" className="secondary-button" onClick={retry}>
                  Retry
                </button>
              </div>
            )}
            {status === "success" && features.length === 0 && (
              <p className="map-status" role="status">
                No issues match these filters in this area.
              </p>
            )}
            {data?.meta.truncated && (
              <p className="map-status warning" role="status">
                Showing {data.meta.returned} of {data.meta.total} issues in view. Zoom in or narrow
                the filters to see the rest.
              </p>
            )}
          </div>
          <IssueLegend />
        </div>

        <IssueSummaryPanel feature={shown} onClose={clearSelection} />
      </div>
    </section>
  );
}
