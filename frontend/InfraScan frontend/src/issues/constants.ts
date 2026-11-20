import type {
  IssueFilters,
  IssueStatus,
  IssueTypeCode,
  ReviewReason,
  Severity,
} from "./types.ts";

export type MarkerShape = "circle" | "square" | "diamond" | "triangle";

export const ISSUE_TYPES: ReadonlyArray<{
  code: IssueTypeCode;
  label: string;
  shape: MarkerShape;
  glyph: string;
}> = [
  { code: "pothole", label: "Pothole", shape: "circle", glyph: "P" },
  { code: "alligator_crack", label: "Alligator crack", shape: "square", glyph: "A" },
  { code: "longitudinal_crack", label: "Longitudinal crack", shape: "diamond", glyph: "L" },
  { code: "transverse_crack", label: "Transverse crack", shape: "triangle", glyph: "T" },
];

export const SEVERITIES: ReadonlyArray<{
  value: Severity;
  label: string;
  color: string;
  textColor: string;
}> = [
  { value: "critical", label: "Critical", color: "#c62828", textColor: "#ffffff" },
  { value: "moderate", label: "Moderate", color: "#e8710a", textColor: "#ffffff" },
  { value: "minor", label: "Minor", color: "#f2c230", textColor: "#3b2f00" },
];

export const STATUSES: ReadonlyArray<{ value: IssueStatus; label: string }> = [
  { value: "pending_review", label: "Pending review" },
  { value: "accepted", label: "Accepted" },
  { value: "resolved", label: "Resolved" },
  { value: "dismissed", label: "Dismissed" },
];

export const REVIEW_REASON_LABELS: Record<ReviewReason, string> = {
  type_conflict: "Detections disagree on issue type",
  missing_location: "No location could be determined",
  low_confidence: "Low detection confidence",
};

export const REVIEW_INDICATOR_COLOR = "#6a1b9a";
export const UNKNOWN_SEVERITY = { label: "Unknown", color: "#78909c", textColor: "#ffffff" };

export function issueTypeInfo(code: string) {
  return (
    ISSUE_TYPES.find((type) => type.code === code) ?? {
      code,
      label: code.replaceAll("_", " "),
      shape: "circle" as MarkerShape,
      glyph: "?",
    }
  );
}

export function severityInfo(value: string) {
  return SEVERITIES.find((severity) => severity.value === value) ?? UNKNOWN_SEVERITY;
}

export function statusLabel(value: string): string {
  return STATUSES.find((status) => status.value === value)?.label ?? value.replaceAll("_", " ");
}

export function reviewReasonLabel(value: string): string {
  return REVIEW_REASON_LABELS[value as ReviewReason] ?? value.replaceAll("_", " ");
}

/**
 * Mirrors ROUTE_WAYPOINTS in backend/app/seeds/stanton_anaheim.py as
 * [latitude, longitude] pairs (Leaflet order).
 */
export const STANTON_ANAHEIM_ROUTE: ReadonlyArray<[number, number]> = [
  [33.8025, -117.9932],
  [33.8027, -117.96],
  [33.803, -117.93],
  [33.8031, -117.9146],
  [33.818, -117.9146],
  [33.8353, -117.9145],
];

export const MAP_RESULT_LIMIT = 500;
export const VIEWPORT_DEBOUNCE_MS = 350;

export const OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
export const OSM_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

export const EMPTY_FILTERS: IssueFilters = {
  issueTypes: [],
  severities: [],
  statuses: [],
  reviewRequired: false,
  lastSeenFrom: "",
  lastSeenTo: "",
};
