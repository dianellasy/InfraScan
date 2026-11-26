import type { ReactNode } from "react";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AppRoutes } from "./AppRoutes.tsx";
import { focusBounds, parseRoute } from "./routing.ts";
import {
  authorizationFor,
  collection,
  detection,
  feature,
  imageResponse,
  issue,
  jsonResponse,
  requestedUrls,
  stubFetch,
} from "./test/fixtures.ts";
import { resetFakeMap } from "./test/reactLeafletMock.tsx";
import { fakeSession, setStoredSession } from "./test/supabaseMock.ts";

vi.mock("react-leaflet", () => import("./test/reactLeafletMock.tsx"));
vi.mock("react-leaflet-cluster", () => ({
  default: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}));

const ISSUE_POINT = { latitude: 33.88241, longitude: -117.8851 };
const uploaded = issue({
  issue_id: "issue-uploaded",
  status: "pending_review",
  needs_review: true,
  ...ISSUE_POINT,
  location_accuracy_m: null,
  detection_count: 1,
  evidence_thumbnail_url: "/api/v1/evidence/evidence-uploaded",
});
const supporting = detection({
  detection_id: "detection-uploaded",
  job_id: "job-1",
  issue_id: "issue-uploaded",
  evidence_media: "evidence-uploaded",
  ...ISSUE_POINT,
  observed_at: "2026-11-10T22:30:00Z",
});
const uploadedFeature = feature(
  {
    issue_id: "issue-uploaded",
    status: "pending_review",
    needs_review: true,
    detection_count: 1,
    evidence_thumbnail_url: "/api/v1/evidence/evidence-uploaded",
  },
  [ISSUE_POINT.longitude, ISSUE_POINT.latitude],
);
const laterFeature = feature(
  { issue_id: "issue-later", severity: "minor" },
  [ISSUE_POINT.longitude + 0.0005, ISSUE_POINT.latitude],
);

describe("from a completed job to the issue map and detail", () => {
  it("shows the uploaded detection's issue as a marker in the framed viewport", async () => {
    let mapFeatures = [uploadedFeature];
    const fetchMock = stubFetch((url) => {
      switch (url.pathname) {
        case "/api/v1/jobs/job-1":
          return jsonResponse({ job_id: "job-1", status: "completed", submitted_at: "2026-11-10T22:31:00Z" });
        case "/api/v1/jobs/job-1/detections":
          return jsonResponse({ items: [supporting], total: 1, page: 1, page_size: 100 });
        case "/api/v1/issues":
          return jsonResponse({ items: [uploaded], total: 1, page: 1, page_size: 100 });
        case "/api/v1/issues/map":
          return jsonResponse(collection(mapFeatures));
        case "/api/v1/issues/issue-uploaded":
          return jsonResponse(uploaded);
        case "/api/v1/issues/issue-uploaded/detections":
          return jsonResponse({ items: [supporting], total: 1, page: 1, page_size: 100 });
        case "/api/v1/evidence/evidence-uploaded":
          return imageResponse();
        default:
          throw new Error(`Unexpected request ${url}`);
      }
    });
    const user = userEvent.setup();
    setStoredSession(fakeSession({ accessToken: "token-a" }));
    window.location.hash = "#/jobs/job-1";
    render(<AppRoutes />);

    const mapLink = await screen.findByRole("link", { name: "View on issue map" });
    expect(requestedUrls(fetchMock).some((url) => url.pathname === "/api/v1/issues/map")).toBe(false);

    // Leaflet fits the requested bounds; the fake map reports them as its viewport.
    const expected = focusBounds([ISSUE_POINT])!;
    resetFakeMap(expected);
    await user.click(mapLink);

    const marker = await screen.findByTestId("issue-marker");
    expect(parseRoute(window.location.hash)).toEqual({
      name: "map", bounds: expected, issueId: "issue-uploaded",
    });
    expect(JSON.parse(screen.getByTestId("map").dataset.bounds!)).toEqual([
      [expected.south, expected.west],
      [expected.north, expected.east],
    ]);
    const mapCall = fetchMock.mock.calls.find(([input]) =>
      String(input).includes("/api/v1/issues/map"),
    ) as unknown as [string, RequestInit];
    expect(mapCall[1].cache).toBe("no-store");
    const params = new URL(String(mapCall[0])).searchParams;
    expect(Number(params.get("min_latitude"))).toBeLessThanOrEqual(ISSUE_POINT.latitude);
    expect(Number(params.get("max_latitude"))).toBeGreaterThanOrEqual(ISSUE_POINT.latitude);
    expect(Number(params.get("min_longitude"))).toBeLessThanOrEqual(ISSUE_POINT.longitude);
    expect(Number(params.get("max_longitude"))).toBeGreaterThanOrEqual(ISSUE_POINT.longitude);
    // GeoJSON [longitude, latitude] is placed at Leaflet [latitude, longitude].
    expect(marker).toHaveAttribute("data-lat", String(ISSUE_POINT.latitude));
    expect(marker).toHaveAttribute("data-lng", String(ISSUE_POINT.longitude));

    // The job's issue is preselected in the side panel; its evidence is fetched
    // with the bearer token and shown from an object URL.
    const panel = screen.getByRole("complementary", { name: "Selected issue" });
    expect((await within(panel).findByRole("img")).getAttribute("src")).toMatch(/^blob:/);
    // Fetched once for the job page's annotated image and once for the map panel.
    expect(authorizationFor(fetchMock, "/api/v1/evidence/evidence-uploaded")).toEqual([
      "Bearer token-a",
      "Bearer token-a",
    ]);

    // An open map picks up Issues created afterwards via Refresh.
    mapFeatures = [uploadedFeature, laterFeature];
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(screen.getAllByTestId("issue-marker")).toHaveLength(2));

    await user.click(within(panel).getByRole("link", { name: "Open issue details" }));
    const rows = (await screen.findAllByRole("row")).slice(1);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent("Pothole (class 3)");
    expect((await screen.findByRole("img")).getAttribute("src")).toMatch(/^blob:/);
    await user.click(within(rows[0]).getByRole("button", { name: "View" }));
    const evidence = await screen.findByRole("img", { name: /Evidence for the pothole detection/ });
    expect(evidence.getAttribute("src")).toMatch(/^blob:/);
    expect(within(rows[0]).getByRole("button", { name: "Hide" })).toHaveAttribute(
      "aria-expanded", "true",
    );
    expect(authorizationFor(fetchMock, "/api/v1/evidence/evidence-uploaded").every(
      (header) => header === "Bearer token-a",
    )).toBe(true);
    expect(screen.queryByRole("link", { name: "View" })).not.toBeInTheDocument();

    // Back on the job link, the results are restored from the API.
    await act(async () => {
      window.location.hash = "#/jobs/job-1";
    });
    expect(await screen.findByRole("link", { name: "View on issue map" })).toBeInTheDocument();
  });
});
