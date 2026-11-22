import { vi } from "vitest";
import type {
  Detection,
  Issue,
  IssueFeature,
  IssueFeatureCollection,
  IssueSummary,
  LonLat,
} from "../issues/types.ts";

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export function summary(overrides: Partial<IssueSummary> = {}): IssueSummary {
  return {
    issue_id: "issue-pothole",
    issue_type_code: "pothole",
    issue_type_name: "Pothole",
    severity: "critical",
    status: "accepted",
    review_reason: null,
    needs_review: false,
    location_accuracy_m: 6.5,
    first_seen_at: "2026-10-05T16:02:00Z",
    last_seen_at: "2026-10-19T16:02:00Z",
    detection_count: 3,
    evidence_thumbnail_url: "/api/v1/evidence/evidence-1",
    ...overrides,
  };
}

export function feature(
  overrides: Partial<IssueSummary> = {},
  coordinates: LonLat | null = [-117.98, 33.8026],
): IssueFeature {
  const properties = summary(overrides);
  return {
    type: "Feature",
    id: properties.issue_id,
    geometry: coordinates ? { type: "Point", coordinates } : null,
    properties,
  };
}

export function collection(
  features: IssueFeature[],
  meta: Partial<IssueFeatureCollection["meta"]> = {},
): IssueFeatureCollection {
  return {
    type: "FeatureCollection",
    bbox: [-118, 33.79, -117.9, 33.84],
    features,
    meta: {
      limit: 500,
      returned: features.length,
      total: features.length,
      truncated: false,
      ...meta,
    },
  };
}

export function issue(overrides: Partial<Issue> = {}): Issue {
  return {
    ...summary(),
    latitude: 33.8026,
    longitude: -117.98,
    created_at: "2026-10-05T16:05:00Z",
    updated_at: "2026-10-19T16:05:00Z",
    ...overrides,
  };
}

export function detection(overrides: Partial<Detection> = {}): Detection {
  return {
    detection_id: "detection-1",
    job_id: "job-1",
    classification: "Pothole",
    severity: "critical",
    confidence: 0.91,
    latitude: 33.8026,
    longitude: -117.98,
    detected_at: "2026-10-05T16:02:00Z",
    observed_at: "2026-10-05T16:01:00Z",
    frame_timestamp: null,
    issue_id: "issue-pothole",
    issue_type: 3,
    predicted_issue_type_code: "pothole",
    evidence_media: "evidence-1",
    ...overrides,
  };
}

export function imageResponse(body = "jpeg-bytes"): Response {
  // A string body: Node's Response cannot read jsdom's Blob.
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "image/jpeg" },
  });
}

export function stubFetch(handler: (url: URL) => Response | Promise<Response>) {
  const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(
    (input) => Promise.resolve(handler(new URL(String(input)))),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

export function requestedUrls(fetchMock: ReturnType<typeof stubFetch>): URL[] {
  return fetchMock.mock.calls.map(([input]) => new URL(String(input)));
}

/** The Authorization header of each request to `pathname`. */
export function authorizationFor(
  fetchMock: ReturnType<typeof stubFetch>,
  pathname: string,
): Array<string | null> {
  return fetchMock.mock.calls
    .filter(([input]) => new URL(String(input)).pathname === pathname)
    .map(([, init]) => new Headers(init?.headers).get("Authorization"));
}
