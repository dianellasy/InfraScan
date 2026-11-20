import { describe, expect, it } from "vitest";
import { buildIssueMapUrl, isDateRangeValid, normalizeViewport } from "./api.ts";
import { EMPTY_FILTERS } from "./constants.ts";

describe("buildIssueMapUrl", () => {
  const viewport = { south: 33.79, west: -118.0, north: 33.84, east: -117.9 };

  it("maps south/west/north/east to the API's latitude and longitude bounds", () => {
    const url = new URL(buildIssueMapUrl(viewport, EMPTY_FILTERS));
    expect(url.pathname).toBe("/api/v1/issues/map");
    expect(url.searchParams.get("min_latitude")).toBe("33.79");
    expect(url.searchParams.get("max_latitude")).toBe("33.84");
    expect(url.searchParams.get("min_longitude")).toBe("-118");
    expect(url.searchParams.get("max_longitude")).toBe("-117.9");
    expect(url.searchParams.get("limit")).toBe("500");
  });

  it("repeats multi-value filters in a stable order and converts dates to UTC instants", () => {
    const url = new URL(
      buildIssueMapUrl(viewport, {
        issueTypes: ["transverse_crack", "pothole"],
        severities: ["minor", "critical"],
        statuses: ["pending_review"],
        reviewRequired: true,
        lastSeenFrom: "2026-10-01",
        lastSeenTo: "2026-10-31",
      }),
    );
    expect(url.searchParams.getAll("issue_type")).toEqual(["pothole", "transverse_crack"]);
    expect(url.searchParams.getAll("severity")).toEqual(["critical", "minor"]);
    expect(url.searchParams.getAll("status")).toEqual(["pending_review"]);
    expect(url.searchParams.get("review_required")).toBe("true");
    expect(url.searchParams.get("last_seen_after")).toBe(new Date("2026-10-01T00:00:00").toISOString());
    expect(url.searchParams.get("last_seen_before")).toBe(
      new Date("2026-10-31T23:59:59.999").toISOString(),
    );
  });

  it("omits unset filters", () => {
    const url = new URL(buildIssueMapUrl(viewport, EMPTY_FILTERS));
    for (const name of ["issue_type", "severity", "status", "review_required", "last_seen_after"]) {
      expect(url.searchParams.has(name)).toBe(false);
    }
  });
});

describe("normalizeViewport", () => {
  it("rounds outward so edge markers stay inside the requested box", () => {
    expect(
      normalizeViewport({ south: 33.123456, west: -117.123456, north: 33.654321, east: -117.000001 }),
    ).toEqual({ south: 33.12345, west: -117.12346, north: 33.65433, east: -117 });
  });

  it("clamps to valid coordinates and collapses a wrapped world view", () => {
    expect(normalizeViewport({ south: -95, west: -400, north: 95, east: 400 })).toEqual({
      south: -90,
      west: -180,
      north: 90,
      east: 180,
    });
    expect(normalizeViewport({ south: 10, west: 170, north: 20, east: 190 })).toEqual({
      south: 10,
      west: 170,
      north: 20,
      east: 180,
    });
  });
});

describe("isDateRangeValid", () => {
  it("rejects a start date after the end date", () => {
    expect(isDateRangeValid({ ...EMPTY_FILTERS, lastSeenFrom: "2026-10-02", lastSeenTo: "2026-10-01" })).toBe(false);
    expect(isDateRangeValid({ ...EMPTY_FILTERS, lastSeenFrom: "2026-10-01", lastSeenTo: "2026-10-01" })).toBe(true);
    expect(isDateRangeValid({ ...EMPTY_FILTERS, lastSeenFrom: "2026-10-01" })).toBe(true);
  });
});
