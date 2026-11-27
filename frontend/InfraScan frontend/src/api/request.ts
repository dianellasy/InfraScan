import { getAccessToken, notifyUnauthorized } from "../auth/session.ts";
import { apiBaseUrl } from "./endpoints.ts";

function errorMessage(body: string, fallback: string): string {
  try {
    const parsed: unknown = JSON.parse(body);
    if (typeof parsed !== "object" || parsed === null) return body || fallback;
    if (
      "error" in parsed &&
      typeof parsed.error === "object" &&
      parsed.error !== null &&
      "message" in parsed.error
    ) {
      return String(parsed.error.message);
    }
    if ("detail" in parsed) {
      // FastAPI validation errors are a list of {loc, msg} objects.
      if (Array.isArray(parsed.detail)) {
        return parsed.detail
          .map((item: unknown) =>
            typeof item === "object" && item !== null && "msg" in item
              ? String(item.msg)
              : String(item),
          )
          .join("; ");
      }
      return String(parsed.detail);
    }
  } catch {
    // Use the response text when the server does not return JSON.
  }
  return body || fallback;
}

function isApiUrl(url: string): boolean {
  return new URL(url, apiBaseUrl).origin === new URL(apiBaseUrl).origin;
}

/**
 * `fetch` with the signed-in user's access token, sent only to the InfraScan API.
 * A 401 means the backend rejected the session, so the user is asked to sign in again.
 */
async function apiFetch(url: string, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers);
  const token = isApiUrl(url) ? await getAccessToken() : null;
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(url, { ...init, headers });
  if (response.status === 401 && isApiUrl(url)) notifyUnauthorized();
  return response;
}

async function ensureOk(response: Response): Promise<Response> {
  if (!response.ok) {
    const body = await response.text();
    throw new Error(
      `Request failed (${response.status}): ${errorMessage(body, response.statusText)}`,
    );
  }
  return response;
}

export async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await ensureOk(await apiFetch(url, init));
  return (await response.json()) as T;
}

/** Binary API resources, such as evidence images, that need the bearer token. */
export async function requestBlob(url: string, init?: RequestInit): Promise<Blob> {
  const response = await ensureOk(await apiFetch(url, init));
  return response.blob();
}

export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}
