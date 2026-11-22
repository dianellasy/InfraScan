import { describe, expect, it, vi } from "vitest";
import { onUnauthorized } from "../auth/session.ts";
import { jsonResponse, stubFetch } from "../test/fixtures.ts";
import { fakeSession, setStoredSession } from "../test/supabaseMock.ts";
import { endpoints } from "./endpoints.ts";
import { request } from "./request.ts";

function authorization(fetchMock: ReturnType<typeof stubFetch>, call = 0): string | null {
  return new Headers(fetchMock.mock.calls[call][1]?.headers).get("Authorization");
}

describe("request", () => {
  it("adds the bearer token and keeps the caller's options", async () => {
    setStoredSession(fakeSession({ accessToken: "token-a" }));
    const fetchMock = stubFetch(() => jsonResponse({ ok: true }));
    const controller = new AbortController();

    await request(endpoints.issues.url, {
      signal: controller.signal,
      cache: "no-store",
      headers: { Accept: "application/json" },
    });

    const init = fetchMock.mock.calls[0][1]!;
    expect(authorization(fetchMock)).toBe("Bearer token-a");
    expect(new Headers(init.headers).get("Accept")).toBe("application/json");
    expect(init.signal).toBe(controller.signal);
    expect(init.cache).toBe("no-store");
  });

  it("sends no Authorization header without a session", async () => {
    const fetchMock = stubFetch(() => jsonResponse({}));
    await request(endpoints.issues.url);
    expect(authorization(fetchMock)).toBeNull();
  });

  it("never sends the token to another origin", async () => {
    setStoredSession(fakeSession({ accessToken: "token-a" }));
    const fetchMock = stubFetch(() => jsonResponse({}));
    await request("https://tiles.example.org/data.json");
    expect(authorization(fetchMock)).toBeNull();
  });

  it("signals reauthentication on 401 and still rejects", async () => {
    setStoredSession(fakeSession());
    stubFetch(() =>
      jsonResponse({ error: { code: "INVALID_TOKEN", message: "The access token is invalid or expired." } }, 401),
    );
    const listener = vi.fn();
    const stop = onUnauthorized(listener);

    await expect(request(endpoints.issues.url)).rejects.toThrow(
      "Request failed (401): The access token is invalid or expired.",
    );
    expect(listener).toHaveBeenCalledOnce();
    stop();
  });

  it("does not treat 403 or 404 as an expired session", async () => {
    setStoredSession(fakeSession());
    const listener = vi.fn();
    const stop = onUnauthorized(listener);
    for (const status of [403, 404]) {
      stubFetch(() => jsonResponse({ error: { code: "X", message: "nope" } }, status));
      await expect(request(endpoints.issues.url)).rejects.toThrow(`(${status})`);
    }
    expect(listener).not.toHaveBeenCalled();
    stop();
  });
});
