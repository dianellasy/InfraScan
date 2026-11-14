import { endpoints } from "./endpoints.ts";

export type JobGeolocation = {
  mode: string;
  assumed: boolean;
  note: string | null;
  source: { name: string | null; latitude: number; longitude: number; datum: string };
  camera: Record<string, unknown>;
  request_gps: { latitude: number; longitude: number; used: boolean } | null;
  image: { width: number; height: number } | null;
  intrinsics: Record<string, unknown> | null;
};

export type InferenceJob = {
  job_id: string;
  status: "pending" | "running" | "completed" | "failed" | string;
  submitted_at: string;
  completed_at: string | null;
  model_version: string | null;
  metadata: { captured_at: string | null; latitude: number | null; longitude: number | null } | null;
  geolocation: JobGeolocation | null;
  annotated_image_url: string | null;
};

export type Detection = {
  detection_id: string;
  classification: string;
  severity: string;
  confidence: number;
  latitude: number | null;
  longitude: number | null;
  location_status: string | null;
  location_details: {
    ground_distance_m?: number | null;
    message?: string | null;
  } | null;
  bounding_box: number[] | null;
};

type Page<T> = { items: T[]; total: number; page: number; page_size: number };

type ApiErrorBody = { error?: { code?: string; message?: string } };

async function parse<T>(response: Response): Promise<T> {
  if (response.ok) {
    return (await response.json()) as T;
  }
  let message = `${response.status} ${response.statusText}`;
  try {
    const body = (await response.json()) as ApiErrorBody;
    if (body.error?.message) {
      message = `${body.error.code ?? response.status}: ${body.error.message}`;
    }
  } catch {
    // Non-JSON error body; keep the status line.
  }
  throw new Error(message);
}

export async function submitInferenceJob(
  file: File,
  metadata: { captured_at: string; latitude: number; longitude: number },
): Promise<{ job_id: string }> {
  const form = new FormData();
  form.append("file", file);
  form.append("metadata", JSON.stringify(metadata));
  const { method, url } = endpoints.inferenceJobs;
  return parse(await fetch(url, { method, body: form }));
}

export async function getJob(jobId: string, signal?: AbortSignal): Promise<InferenceJob> {
  return parse(await fetch(endpoints.job(jobId).url, { signal }));
}

export async function getJobDetections(
  jobId: string,
  signal?: AbortSignal,
): Promise<Page<Detection>> {
  const url = `${endpoints.jobDetections(jobId).url}?page=1&page_size=100`;
  return parse(await fetch(url, { signal }));
}
