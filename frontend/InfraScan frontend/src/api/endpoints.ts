const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

export const apiBaseUrl = API_BASE_URL;

type HttpMethod = "GET" | "POST" | "PATCH";

export type Endpoint = {
  method: HttpMethod;
  url: string;
};

function endpoint(method: HttpMethod, path: string): Endpoint {
  return { method, url: `${API_BASE_URL}${path}` };
}

export const endpoints = {
  inferenceJobs: endpoint("POST", "/api/v1/inference/jobs"),
  issues: endpoint("GET", "/api/v1/issues"),
  createIssue: endpoint("POST", "/api/v1/issues"),
  issueReviewQueue: endpoint("GET", "/api/v1/issues/review-queue"),
  issue: (issueId: string) =>
    endpoint("GET", `/api/v1/issues/${encodeURIComponent(issueId)}`),
  updateIssue: (issueId: string) =>
    endpoint("PATCH", `/api/v1/issues/${encodeURIComponent(issueId)}`),
  jobs: endpoint("GET", "/api/v1/jobs"),
  job: (jobId: string) =>
    endpoint("GET", `/api/v1/jobs/${encodeURIComponent(jobId)}`),
  jobDetections: (jobId: string) =>
    endpoint("GET", `/api/v1/jobs/${encodeURIComponent(jobId)}/detections`),
  roads: endpoint("GET", "/api/v1/roads"),
  road: (roadId: string) =>
    endpoint("GET", `/api/v1/roads/${encodeURIComponent(roadId)}`),
  roadIssues: (roadId: string) =>
    endpoint("GET", `/api/v1/roads/${encodeURIComponent(roadId)}/issues`),
  detections: endpoint("GET", "/api/v1/detections"),
  detection: (detectionId: string) =>
    endpoint("GET", `/api/v1/detections/${encodeURIComponent(detectionId)}`),
  detectionEvidence: (detectionId: string) =>
    endpoint(
      "GET",
      `/api/v1/detections/${encodeURIComponent(detectionId)}/evidence`,
    ),
  analyticsOverview: endpoint("GET", "/api/v1/analytics/overview"),
  analyticsRoadConditions: endpoint("GET", "/api/v1/analytics/road-conditions"),
  analyticsGeographic: endpoint("GET", "/api/v1/analytics/geographic"),
  analyticsDamageDistribution: endpoint("GET", "/api/v1/analytics/damage-distribution"),
} as const;
