import { describe, expect, it } from "vitest";
import { focusBounds, mapHref, parseRoute } from "./routing.ts";

describe("routing", () => {
  it("round-trips map links with bounds and a selected issue", () => {
    const bounds = { south: 33.88, west: -117.89, north: 33.89, east: -117.88 };
    expect(parseRoute(mapHref(bounds, "issue 1"))).toEqual({
      name: "map", bounds, issueId: "issue 1",
    });
    expect(parseRoute("#/map")).toEqual({ name: "map", bounds: undefined, issueId: undefined });
  });

  it("ignores malformed or swapped bounds", () => {
    for (const value of ["1,2,3", "a,b,c,d", "34,-117,33,-118", "0,0,91,1"]) {
      expect(parseRoute(`#/map?bounds=${value}`)).toMatchObject({ name: "map", bounds: undefined });
    }
  });

  it("parses job links as the upload page", () => {
    expect(parseRoute("#/jobs/job%201")).toEqual({ name: "upload", jobId: "job 1" });
    expect(parseRoute("#/")).toEqual({ name: "upload" });
  });

  it("frames points with padding, rounding outward", () => {
    expect(focusBounds([])).toBeUndefined();
    const bounds = focusBounds([
      { latitude: 33.8, longitude: -117.9 },
      { latitude: 33.81, longitude: -117.88 },
    ])!;
    expect(bounds.south).toBeLessThan(33.8);
    expect(bounds.north).toBeGreaterThan(33.81);
    expect(bounds.west).toBeLessThan(-117.9);
    expect(bounds.east).toBeGreaterThan(-117.88);
  });
});
