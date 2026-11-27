import { endpoints } from "../api/endpoints.ts";
import { request } from "../api/request.ts";
import type { Detection as RegisterRow } from "../pages/Detections.tsx";

/** A detection as the API returns it (`DetectionDTO`). */
export type ApiDetection = {
  detection_id: string;
  job_id: string;
  classification: string;
  severity: string;
  confidence: number;
  latitude: number | null;
  longitude: number | null;
  detected_at: string;
  observed_at?: string | null;
  issue_id?: string | null;
  issue_status?: string | null;
  evidence_media?: string | null;
  frame_timestamp?: number | null;
};

type Page<T> = { items: T[]; total: number; page: number; page_size: number };

export type ReviewDecision = "accepted" | "dismissed";

/** The register shows the newest detections; the API caps a page at 100. */
export const REGISTER_LIMIT = 100;

export async function fetchDetections(signal?: AbortSignal): Promise<Page<ApiDetection>> {
  const url = new URL(endpoints.detections.url);
  url.searchParams.set("page_size", String(REGISTER_LIMIT));
  return request<Page<ApiDetection>>(url.toString(), { signal });
}

export async function fetchDetection(id: string, signal?: AbortSignal): Promise<ApiDetection> {
  return request<ApiDetection>(endpoints.detection(id).url, { signal });
}

/** Record a reviewer's decision on the Issue a detection supports. */
export async function reviewIssue(
  issueId: string,
  status: ReviewDecision,
  reviewNote: string,
): Promise<{ status: string; review_note?: string | null }> {
  const endpoint = endpoints.updateIssue(issueId);
  return request(endpoint.url, {
    method: endpoint.method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status, review_note: reviewNote }),
  });
}

const SEVERITY: Record<string, RegisterRow["severity"]> = {
  critical: "High",
  moderate: "Medium",
  minor: "Low",
};

/** Review state shown on the register, from the supporting Issue's status. */
export function reviewState(issueStatus: string | null | undefined): RegisterRow["status"] {
  if (issueStatus === "accepted" || issueStatus === "resolved") return "Validated";
  if (issueStatus === "dismissed") return "Rejected";
  return "Unreviewed";
}

export function formatLocation(latitude: number | null, longitude: number | null): string {
  return latitude != null && longitude != null
    ? `${latitude.toFixed(5)}, ${longitude.toFixed(5)}`
    : "No location";
}

export function sourceLabel(detection: ApiDetection): string {
  const kind =
    detection.frame_timestamp != null
      ? `Video frame ${detection.frame_timestamp.toFixed(1)} s`
      : "Photo";
  return `${kind} · job ${detection.job_id.slice(0, 8)}`;
}

export function toRegisterRow(detection: ApiDetection): RegisterRow {
  return {
    id: detection.detection_id,
    classification: detection.classification,
    location: formatLocation(detection.latitude, detection.longitude),
    source: sourceLabel(detection),
    confidence: detection.confidence * 100,
    severity: SEVERITY[detection.severity] ?? "Low",
    status: reviewState(detection.issue_status),
    detectedAt: detection.observed_at ?? detection.detected_at,
    evidencePath: detection.evidence_media
      ? `/api/v1/evidence/${detection.evidence_media}`
      : undefined,
  };
}
