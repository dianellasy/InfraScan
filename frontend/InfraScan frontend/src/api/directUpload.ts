import { endpoints } from "./endpoints.ts";
import { request } from "./request.ts";

type UploadTicket = {
  upload_id: string;
  upload_url: string;
  method: "PUT";
  headers: Record<string, string>;
  expires_in_seconds: number;
};

export type UploadStage = "uploading" | "starting";

/**
 * Upload a file straight to object storage, then start an inference job for it.
 *
 * 1. The API signs a short-lived PUT URL for this exact file type and size.
 * 2. The browser PUTs the file to storage, so large dashcam videos never pass
 *    through the API (Vercel functions accept at most 4.5 MB per request).
 * 3. The API validates the stored file and queues the job.
 */
export async function uploadAndSubmit<T>(
  file: File,
  contentType: "image/jpeg" | "video/mp4",
  metadata: Record<string, string | number>,
  onStage?: (stage: UploadStage) => void,
): Promise<T> {
  onStage?.("uploading");
  const ticket = await request<UploadTicket>(endpoints.inferenceUploads.url, {
    method: endpoints.inferenceUploads.method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content_type: contentType, size_bytes: file.size }),
  });

  const stored = await fetch(ticket.upload_url, {
    method: ticket.method,
    headers: ticket.headers,
    body: file,
  });
  if (!stored.ok) {
    throw new Error(`Upload to storage failed (${stored.status}). Please try again.`);
  }

  onStage?.("starting");
  return request<T>(endpoints.inferenceJobs.url, {
    method: endpoints.inferenceJobs.method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      upload_id: ticket.upload_id,
      content_type: contentType,
      filename: file.name,
      metadata,
    }),
  });
}
