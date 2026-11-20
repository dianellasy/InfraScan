import { useSyncExternalStore } from "react";

export type Route =
  | { name: "upload" }
  | { name: "map" }
  | { name: "review" }
  | { name: "issue"; issueId: string };

export function parseRoute(hash: string): Route {
  const path = hash.replace(/^#/, "") || "/";
  if (path === "/map") return { name: "map" };
  if (path === "/review") return { name: "review" };
  const issue = /^\/issues\/([^/]+)$/.exec(path);
  if (issue) return { name: "issue", issueId: decodeURIComponent(issue[1]) };
  return { name: "upload" };
}

function subscribe(onChange: () => void) {
  window.addEventListener("hashchange", onChange);
  return () => window.removeEventListener("hashchange", onChange);
}

export function useHashRoute(): Route {
  const hash = useSyncExternalStore(subscribe, () => window.location.hash);
  return parseRoute(hash);
}
