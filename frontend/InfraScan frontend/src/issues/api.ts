import { endpoints } from "../api/endpoints.ts";
import { request } from "../api/request.ts";
import { MAP_RESULT_LIMIT } from "./constants.ts";
import type {
  Detection,
  Issue,
  IssueFeatureCollection,
  IssueFilters,
  Page,
  ViewportBounds,
} from "./types.ts";

const COORDINATE_PRECISION = 1e5;
// Absorbs float error such as 33.84 * 1e5 = 3384000.0000000005 before rounding.
const ROUNDING_TOLERANCE = 1e-6;

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

/**
 * Normalize Leaflet bounds into a valid API bounding box: clamp to the
 * world, collapse a fully wrapped view to [-180, 180], and round outward so
 * markers on the visible edge are never dropped.
 */
export function normalizeViewport(bounds: ViewportBounds): ViewportBounds {
  const outwardDown = (v: number) =>
    Math.floor(v * COORDINATE_PRECISION + ROUNDING_TOLERANCE) / COORDINATE_PRECISION;
  const outwardUp = (v: number) =>
    Math.ceil(v * COORDINATE_PRECISION - ROUNDING_TOLERANCE) / COORDINATE_PRECISION;
  const fullWidth = bounds.east - bounds.west >= 360;
  return {
    south: clamp(outwardDown(bounds.south), -90, 90),
    west: fullWidth ? -180 : clamp(outwardDown(bounds.west), -180, 180),
    north: clamp(outwardUp(bounds.north), -90, 90),
    east: fullWidth ? 180 : clamp(outwardUp(bounds.east), -180, 180),
  };
}

export function isDateRangeValid(filters: IssueFilters): boolean {
  return !filters.lastSeenFrom || !filters.lastSeenTo || filters.lastSeenFrom <= filters.lastSeenTo;
}

function startOfLocalDay(date: string): string {
  return new Date(`${date}T00:00:00`).toISOString();
}

function endOfLocalDay(date: string): string {
  return new Date(`${date}T23:59:59.999`).toISOString();
}

export function buildIssueMapUrl(
  viewport: ViewportBounds,
  filters: IssueFilters,
  limit: number = MAP_RESULT_LIMIT,
): string {
  const url = new URL(endpoints.issueMap.url);
  const params = url.searchParams;
  params.set("min_latitude", String(viewport.south));
  params.set("min_longitude", String(viewport.west));
  params.set("max_latitude", String(viewport.north));
  params.set("max_longitude", String(viewport.east));
  params.set("limit", String(limit));
  // Sorted so equivalent filter selections produce the same request URL.
  for (const value of [...filters.issueTypes].sort()) params.append("issue_type", value);
  for (const value of [...filters.severities].sort()) params.append("severity", value);
  for (const value of [...filters.statuses].sort()) params.append("status", value);
  if (filters.reviewRequired) params.set("review_required", "true");
  if (filters.lastSeenFrom) params.set("last_seen_after", startOfLocalDay(filters.lastSeenFrom));
  if (filters.lastSeenTo) params.set("last_seen_before", endOfLocalDay(filters.lastSeenTo));
  return url.toString();
}

export function fetchIssueMap(url: string, signal?: AbortSignal) {
  // Never reuse a cached response: new jobs keep adding and updating Issues.
  return request<IssueFeatureCollection>(url, { signal, cache: "no-store" });
}

/** Issues supported by at least one detection from `jobId` (located and unlocated). */
export function fetchJobIssues(jobId: string, signal?: AbortSignal) {
  const url = new URL(endpoints.issues.url);
  url.searchParams.set("job_id", jobId);
  url.searchParams.set("page_size", "100");
  return request<Page<Issue>>(url.toString(), { signal, cache: "no-store" });
}

export function fetchIssue(issueId: string, signal?: AbortSignal) {
  return request<Issue>(endpoints.issue(issueId).url, { signal });
}

export function fetchIssueDetections(issueId: string, signal?: AbortSignal) {
  const url = new URL(endpoints.issueDetections(issueId).url);
  url.searchParams.set("page_size", "100");
  return request<Page<Detection>>(url.toString(), { signal });
}

export function fetchReviewQueue(page: number, pageSize: number, signal?: AbortSignal) {
  const url = new URL(endpoints.issueReviewQueue.url);
  url.searchParams.set("page", String(page));
  url.searchParams.set("page_size", String(pageSize));
  return request<Page<Issue>>(url.toString(), { signal });
}
