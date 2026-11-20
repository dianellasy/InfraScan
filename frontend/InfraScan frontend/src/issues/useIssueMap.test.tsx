import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { collection, feature, jsonResponse } from "../test/fixtures.ts";
import { EMPTY_FILTERS } from "./constants.ts";
import type { ViewportBounds } from "./types.ts";
import { useIssueMap } from "./useIssueMap.ts";

const DEBOUNCE_MS = 300;
const first: ViewportBounds = { south: 33.7, west: -118.1, north: 33.8, east: -118 };
const second: ViewportBounds = { south: 33.75, west: -118.05, north: 33.85, east: -117.95 };
const third: ViewportBounds = { south: 33.8, west: -118, north: 33.9, east: -117.9 };

type Pending = { url: URL; signal: AbortSignal; respond: (response: Response) => void };

function stubControllableFetch() {
  const pending: Pending[] = [];
  const fetchMock = vi.fn(
    (input: RequestInfo | URL, init?: RequestInit) =>
      new Promise<Response>((resolve) => {
        pending.push({ url: new URL(String(input)), signal: init!.signal!, respond: resolve });
      }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, pending };
}

function renderMapHook(viewport: ViewportBounds) {
  return renderHook(
    ({ bounds }: { bounds: ViewportBounds }) => useIssueMap(bounds, EMPTY_FILTERS, DEBOUNCE_MS),
    { initialProps: { bounds: viewport } },
  );
}

describe("useIssueMap", () => {
  it("debounces rapid viewport changes into one request for the latest viewport", async () => {
    vi.useFakeTimers();
    const { fetchMock, pending } = stubControllableFetch();
    const { rerender } = renderMapHook(first);

    act(() => vi.advanceTimersByTime(DEBOUNCE_MS - 50));
    rerender({ bounds: second });
    act(() => vi.advanceTimersByTime(DEBOUNCE_MS - 50));
    rerender({ bounds: third });
    expect(fetchMock).not.toHaveBeenCalled();

    act(() => vi.advanceTimersByTime(DEBOUNCE_MS));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(pending[0].url.searchParams.get("min_latitude")).toBe("33.8");
    expect(pending[0].url.searchParams.get("max_longitude")).toBe("-117.9");
  });

  it("aborts superseded requests and ignores their late responses", async () => {
    vi.useFakeTimers();
    const { pending } = stubControllableFetch();
    const { result, rerender } = renderMapHook(first);

    act(() => vi.advanceTimersByTime(DEBOUNCE_MS));
    expect(result.current.status).toBe("loading");
    rerender({ bounds: second });
    expect(pending[0].signal.aborted).toBe(true);
    act(() => vi.advanceTimersByTime(DEBOUNCE_MS));
    expect(pending).toHaveLength(2);

    const fresh = collection([feature({ issue_id: "fresh" })]);
    await act(async () => pending[1].respond(jsonResponse(fresh)));
    await act(async () =>
      pending[0].respond(jsonResponse(collection([feature({ issue_id: "stale" })]))),
    );

    expect(result.current.status).toBe("success");
    expect(result.current.data?.features.map((f) => f.id)).toEqual(["fresh"]);
  });

  it("does not request anything before the map reports a viewport", () => {
    const { fetchMock } = stubControllableFetch();
    const { result } = renderHook(() => useIssueMap(null, EMPTY_FILTERS, 0));
    expect(result.current.status).toBe("idle");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
