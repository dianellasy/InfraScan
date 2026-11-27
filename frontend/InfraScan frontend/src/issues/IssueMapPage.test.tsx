import type { ReactNode } from "react";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  authorizationFor,
  collection,
  feature,
  imageResponse,
  jsonResponse,
  requestedUrls,
  stubFetch,
} from "../test/fixtures.ts";
import { moveFakeMap, resetFakeMap } from "../test/reactLeafletMock.tsx";
import { fakeSession, setStoredSession } from "../test/supabaseMock.ts";
import { formatDateTime } from "./format.ts";
import { IssueMapPage } from "./IssueMapPage.tsx";

vi.mock("react-leaflet", () => import("../test/reactLeafletMock.tsx"));
vi.mock("react-leaflet-cluster", () => ({
  default: ({ children }: { children: ReactNode }) => (
    <div data-testid="cluster-group">{children}</div>
  ),
}));

const conflict = feature(
  {
    issue_id: "issue-conflict",
    severity: "moderate",
    status: "pending_review",
    review_reason: "type_conflict",
    needs_review: true,
    detection_count: 3,
    evidence_thumbnail_url: "/api/v1/evidence/evidence-conflict",
  },
  [-117.95, 33.8028],
);
const repeatedPothole = feature({ issue_id: "issue-repeated", detection_count: 4 });
const crack = feature(
  {
    issue_id: "issue-crack",
    issue_type_code: "longitudinal_crack",
    issue_type_name: "Longitudinal crack",
    severity: "minor",
    detection_count: 1,
  },
  [-117.97, 33.8027],
);
const unlocated = feature({ issue_id: "issue-unlocated" }, null);

function renderPage() {
  return render(<IssueMapPage debounceMs={0} />);
}

function lastRequest(fetchMock: ReturnType<typeof stubFetch>): URL {
  return requestedUrls(fetchMock).at(-1)!;
}

beforeEach(() => {
  resetFakeMap({ south: 33.79, west: -118.0, north: 33.84, east: -117.9 });
});

describe("IssueMapPage", () => {
  it("renders exactly one marker per located Issue and none for unlocated Issues", async () => {
    stubFetch(() => jsonResponse(collection([repeatedPothole, conflict, crack, unlocated])));
    renderPage();

    const markers = await screen.findAllByTestId("issue-marker");
    expect(markers).toHaveLength(3);
    expect(markers.map((marker) => marker.getAttribute("aria-label"))).toEqual([
      "Pothole, Critical severity",
      "Pothole, Moderate severity, needs review",
      "Longitudinal crack, Minor severity",
    ]);
    // GeoJSON [longitude, latitude] becomes Leaflet [latitude, longitude].
    expect(markers[1]).toHaveAttribute("data-lat", "33.8028");
    expect(markers[1]).toHaveAttribute("data-lng", "-117.95");
  });

  it("requests the visible viewport and refreshes after the map moves", async () => {
    const fetchMock = stubFetch(() => jsonResponse(collection([repeatedPothole])));
    renderPage();
    await screen.findAllByTestId("issue-marker");

    const initial = lastRequest(fetchMock);
    expect(initial.pathname).toBe("/api/v1/issues/map");
    expect(Object.fromEntries(initial.searchParams)).toEqual({
      min_latitude: "33.79",
      min_longitude: "-118",
      max_latitude: "33.84",
      max_longitude: "-117.9",
      limit: "500",
    });

    act(() => moveFakeMap({ south: 33.81, west: -117.95, north: 33.83, east: -117.91 }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const moved = lastRequest(fetchMock).searchParams;
    expect(moved.get("min_latitude")).toBe("33.81");
    expect(moved.get("min_longitude")).toBe("-117.95");
    expect(moved.get("max_latitude")).toBe("33.83");
    expect(moved.get("max_longitude")).toBe("-117.91");
  });

  it("shows the selected Issue's details in the side panel", async () => {
    setStoredSession(fakeSession({ accessToken: "token-a" }));
    const fetchMock = stubFetch((url) =>
      url.pathname.startsWith("/api/v1/evidence/")
        ? imageResponse()
        : jsonResponse(collection([repeatedPothole, conflict])),
    );
    const user = userEvent.setup();
    renderPage();

    const markers = await screen.findAllByTestId("issue-marker");
    await user.click(markers[1]);

    const panel = screen.getByRole("complementary", { name: "Selected issue" });
    expect(within(panel).getByRole("heading", { name: "Pothole" })).toBeInTheDocument();
    const facts = (term: string) =>
      within(panel).getByText(term, { selector: "dt" }).nextElementSibling;
    expect(facts("Issue type")).toHaveTextContent("Pothole");
    expect(facts("Severity")).toHaveTextContent("Moderate");
    expect(facts("Status")).toHaveTextContent("Pending review");
    expect(facts("Supporting detections")).toHaveTextContent("3");
    expect(facts("First seen")).toHaveTextContent(formatDateTime(conflict.properties.first_seen_at));
    expect(facts("Last seen")).toHaveTextContent(formatDateTime(conflict.properties.last_seen_at));
    expect(facts("Review reason")).toHaveTextContent("Detections disagree on issue type");
    // Protected evidence is fetched with the token, never loaded by <img src> directly.
    expect((await within(panel).findByRole("img")).getAttribute("src")).toMatch(/^blob:/);
    expect(authorizationFor(fetchMock, "/api/v1/evidence/evidence-conflict")).toEqual([
      "Bearer token-a",
    ]);
    expect(within(panel).getByRole("link", { name: "Open issue details" })).toHaveAttribute(
      "href",
      "#/issues/issue-conflict",
    );

    // The selected marker is highlighted; the others are not.
    const selectedMarkers = screen
      .getAllByTestId("issue-marker")
      .filter((marker) => marker.querySelector(".issue-marker.is-selected"));
    expect(selectedMarkers).toHaveLength(1);
    expect(selectedMarkers[0]).toHaveAttribute("data-lng", "-117.95");
  });

  it("marks a cross-type conflict Issue with a review indicator and explains it in the legend", async () => {
    stubFetch(() => jsonResponse(collection([repeatedPothole, conflict])));
    renderPage();

    const [plain, conflicted] = await screen.findAllByTestId("issue-marker");
    expect(conflicted.querySelector("[data-review-indicator]")).not.toBeNull();
    expect(plain.querySelector("[data-review-indicator]")).toBeNull();

    const legend = screen.getByText("Legend").closest("details")!;
    expect(within(legend).getByText(/Needs review/)).toBeInTheDocument();
    for (const label of ["Critical", "Moderate", "Minor", "Pothole", "Alligator crack",
      "Longitudinal crack", "Transverse crack", "Selected issue"]) {
      expect(within(legend).getByText(label)).toBeInTheDocument();
    }
  });

  it("sends filters to the server and renders the server's result", async () => {
    const fetchMock = stubFetch((url) =>
      jsonResponse(
        collection(url.searchParams.has("severity") ? [conflict] : [repeatedPothole, conflict, crack]),
      ),
    );
    const user = userEvent.setup();
    renderPage();
    expect(await screen.findAllByTestId("issue-marker")).toHaveLength(3);

    const filters = screen.getByRole("form", { name: "Issue filters" });
    await user.click(within(filters).getByRole("checkbox", { name: "Moderate" }));
    await waitFor(() => expect(screen.getAllByTestId("issue-marker")).toHaveLength(1));
    expect(lastRequest(fetchMock).searchParams.getAll("severity")).toEqual(["moderate"]);

    await user.click(within(filters).getByRole("checkbox", { name: "Pothole" }));
    await user.click(within(filters).getByRole("checkbox", { name: "Pending review" }));
    await user.click(within(filters).getByRole("checkbox", { name: "Review required only" }));
    await waitFor(() => {
      const params = lastRequest(fetchMock).searchParams;
      expect(params.getAll("issue_type")).toEqual(["pothole"]);
      expect(params.getAll("status")).toEqual(["pending_review"]);
      expect(params.get("review_required")).toBe("true");
      expect(params.getAll("severity")).toEqual(["moderate"]);
      expect(params.get("min_latitude")).toBe("33.79");
    });

    await user.type(within(filters).getByLabelText("From"), "2026-10-01");
    await waitFor(() =>
      expect(lastRequest(fetchMock).searchParams.get("last_seen_after")).toBe(
        new Date("2026-10-01T00:00:00").toISOString(),
      ),
    );

    await user.click(within(filters).getByRole("button", { name: "Clear filters" }));
    await waitFor(() =>
      expect([...lastRequest(fetchMock).searchParams.keys()].sort()).toEqual([
        "limit", "max_latitude", "max_longitude", "min_latitude", "min_longitude",
      ]),
    );
  });

  it("shows a loading state while the request is pending", async () => {
    let respond: (response: Response) => void = () => {};
    stubFetch(() => new Promise<Response>((resolve) => (respond = resolve)));
    renderPage();

    expect(await screen.findByRole("status")).toHaveTextContent("Loading issues…");
    await act(async () => respond(jsonResponse(collection([repeatedPothole]))));
    expect(screen.queryByText("Loading issues…")).not.toBeInTheDocument();
    expect(screen.getAllByTestId("issue-marker")).toHaveLength(1);
  });

  it("shows an error state and retries", async () => {
    let fail = true;
    const fetchMock = stubFetch(() =>
      fail
        ? jsonResponse({ error: { code: "INTERNAL_ERROR", message: "Database unavailable" } }, 503)
        : jsonResponse(collection([repeatedPothole])),
    );
    const user = userEvent.setup();
    renderPage();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not load issues.");
    expect(alert).toHaveTextContent("Database unavailable");

    fail = false;
    await user.click(within(alert).getByRole("button", { name: "Retry" }));
    expect(await screen.findAllByTestId("issue-marker")).toHaveLength(1);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("shows an empty state when no Issues match", async () => {
    stubFetch(() => jsonResponse(collection([])));
    renderPage();

    expect(await screen.findByText("No issues match these filters in this area.")).toBeInTheDocument();
    expect(screen.queryByTestId("issue-marker")).not.toBeInTheDocument();
  });

  it("reports when the viewport has more Issues than were returned", async () => {
    stubFetch(() =>
      jsonResponse(collection([repeatedPothole], { limit: 1, total: 7, truncated: true })),
    );
    renderPage();

    expect(await screen.findByText(/Showing 1 of 7 issues in view/)).toBeInTheDocument();
    expect(screen.getByText("7 issues in view")).toBeInTheDocument();
  });

  it("uses OpenStreetMap tiles with attribution", async () => {
    stubFetch(() => jsonResponse(collection([])));
    renderPage();

    const tiles = await screen.findByTestId("tile-layer");
    expect(tiles.dataset.url).toContain("tile.openstreetmap.org");
    expect(tiles.dataset.attribution).toContain("OpenStreetMap");
    expect(tiles.dataset.attribution).toContain("https://www.openstreetmap.org/copyright");
  });
});
