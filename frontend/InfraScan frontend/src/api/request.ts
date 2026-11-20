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

export async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    const body = await response.text();
    throw new Error(
      `Request failed (${response.status}): ${errorMessage(body, response.statusText)}`,
    );
  }
  return (await response.json()) as T;
}

export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}
