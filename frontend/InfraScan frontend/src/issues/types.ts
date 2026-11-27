export type Severity = "minor" | "moderate" | "critical";
export type IssueStatus = "pending_review" | "accepted" | "resolved" | "dismissed";
export type IssueTypeCode =
  | "longitudinal_crack"
  | "transverse_crack"
  | "alligator_crack"
  | "pothole";
export type ReviewReason = "type_conflict" | "missing_location" | "low_confidence";

export type IssueSummary = {
  issue_id: string;
  issue_type_code: IssueTypeCode;
  issue_type_name: string;
  severity: Severity;
  status: IssueStatus;
  review_reason: ReviewReason | null;
  needs_review: boolean;
  location_accuracy_m: number | null;
  first_seen_at: string;
  last_seen_at: string;
  detection_count: number;
  /** Path relative to the API origin. */
  evidence_thumbnail_url: string | null;
};

export type Issue = IssueSummary & {
  latitude: number | null;
  longitude: number | null;
  created_at: string;
  updated_at: string;
};

/** GeoJSON Point coordinates are [longitude, latitude]. */
export type LonLat = [longitude: number, latitude: number];

export type IssueFeature = {
  type: "Feature";
  id: string;
  geometry: { type: "Point"; coordinates: LonLat } | null;
  properties: IssueSummary;
};

export type IssueFeatureCollection = {
  type: "FeatureCollection";
  bbox: [west: number, south: number, east: number, north: number];
  features: IssueFeature[];
  meta: { limit: number; returned: number; total: number; truncated: boolean };
};

export type Detection = {
  detection_id: string;
  job_id: string;
  classification: string;
  severity: Severity;
  confidence: number;
  latitude: number | null;
  longitude: number | null;
  /** When inference finished. */
  detected_at: string;
  /** When the source image or video frame was captured; null for some historical video rows. */
  observed_at: string | null;
  /** Seconds into the source video; null for still images. */
  frame_timestamp: number | null;
  issue_id: string | null;
  /** Raw model class ID as predicted. */
  issue_type: number | null;
  predicted_issue_type_code: IssueTypeCode | null;
  evidence_media: string | null;
};

export type Page<T> = {
  items: T[];
  total: number;
  page: number;
  page_size: number;
};

export type ViewportBounds = {
  south: number;
  west: number;
  north: number;
  east: number;
};

export type IssueFilters = {
  issueTypes: IssueTypeCode[];
  severities: Severity[];
  statuses: IssueStatus[];
  reviewRequired: boolean;
  /** Local calendar dates as YYYY-MM-DD, or "" when unset. */
  lastSeenFrom: string;
  lastSeenTo: string;
};
