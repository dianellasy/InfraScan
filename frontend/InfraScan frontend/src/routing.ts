import { useSyncExternalStore } from "react";
import { normalizeViewport } from "./issues/api.ts";
import type { ViewportBounds } from "./issues/types.ts";

export type Route =
  | { name: "upload"; jobId?: string }
  | { name: "map"; bounds?: ViewportBounds; issueId?: string }
  | { name: "review" }
  | { name: "issue"; issueId: string };

/** Around a lone point the map would zoom all the way in; keep about 150 m of context. */
const MIN_FOCUS_PADDING_DEG = 0.0015;

function parseBounds(value: string | null): ViewportBounds | undefined {
  const parts = value?.split(",").map(Number) ?? [];
  if (parts.length !== 4 || !parts.every(Number.isFinite)) return undefined;
  const [south, west, north, east] = parts;
  const valid =
    -90 <= south && south <= north && north <= 90 && -180 <= west && west <= east && east <= 180;
  return valid ? { south, west, north, east } : undefined;
}

export function parseRoute(hash: string): Route {
  const [path, query = ""] = (hash.replace(/^#/, "") || "/").split("?", 2);
  const params = new URLSearchParams(query);
  if (path === "/map") {
    return {
      name: "map",
      bounds: parseBounds(params.get("bounds")),
      issueId: params.get("issue") || undefined,
    };
  }
  if (path === "/review") return { name: "review" };
  const issue = /^\/issues\/([^/]+)$/.exec(path);
  if (issue) return { name: "issue", issueId: decodeURIComponent(issue[1]) };
  const job = /^\/jobs\/([^/]+)$/.exec(path);
  if (job) return { name: "upload", jobId: decodeURIComponent(job[1]) };
  return { name: "upload" };
}

export function jobHref(jobId: string): string {
  return `#/jobs/${encodeURIComponent(jobId)}`;
}

export function issueHref(issueId: string): string {
  return `#/issues/${encodeURIComponent(issueId)}`;
}

/** Map link, optionally framing `bounds` and preselecting `issueId`. */
export function mapHref(bounds?: ViewportBounds, issueId?: string): string {
  const params = new URLSearchParams();
  if (bounds) params.set("bounds", [bounds.south, bounds.west, bounds.north, bounds.east].join(","));
  if (issueId) params.set("issue", issueId);
  const query = params.toString();
  return query ? `#/map?${query}` : "#/map";
}

/** A padded viewport containing every point, or undefined when there are none. */
export function focusBounds(
  points: ReadonlyArray<{ latitude: number; longitude: number }>,
): ViewportBounds | undefined {
  if (points.length === 0) return undefined;
  const latitudes = points.map((point) => point.latitude);
  const longitudes = points.map((point) => point.longitude);
  const south = Math.min(...latitudes);
  const north = Math.max(...latitudes);
  const west = Math.min(...longitudes);
  const east = Math.max(...longitudes);
  const latPad = Math.max((north - south) * 0.1, MIN_FOCUS_PADDING_DEG);
  const lonPad = Math.max((east - west) * 0.1, MIN_FOCUS_PADDING_DEG);
  return normalizeViewport({
    south: south - latPad,
    west: west - lonPad,
    north: north + latPad,
    east: east + lonPad,
  });
}

function subscribe(onChange: () => void) {
  window.addEventListener("hashchange", onChange);
  return () => window.removeEventListener("hashchange", onChange);
}

export function useHashRoute(): Route {
  const hash = useSyncExternalStore(subscribe, () => window.location.hash);
  return parseRoute(hash);
}
