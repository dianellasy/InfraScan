import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { AppRoutes } from "../AppRoutes.tsx";
import DetectionReview from "../pages/DetectionReview.tsx";
import { parseRoute } from "../routing.ts";
import { fakeSession, setStoredSession } from "../test/supabaseMock.ts";
import { jsonResponse, stubFetch } from "../test/fixtures.ts";
import { type ApiDetection, reviewState, toRegisterRow } from "./api.ts";
import { DetectionsPage } from "./DetectionsPage.tsx";

function apiDetection(overrides: Partial<ApiDetection> = {}): ApiDetection {
  return {
    detection_id: "d0000001-aaaa-4bbb-8ccc-000000000001",
    job_id: "a1b2c3d4-0000-4000-8000-000000000001",
    classification: "Pothole",
    severity: "critical",
    confidence: 0.873,
    latitude: 33.8823,
    longitude: -117.8851,
    detected_at: "2026-11-20T20:31:00Z",
    observed_at: "2026-11-20T20:30:00Z",
    issue_id: "issue-1",
    issue_status: "pending_review",
    evidence_media: "evidence-1",
    frame_timestamp: null,
    ...overrides,
  };
}

const imageResponse = () => new Response(new Blob(["jpeg"], { type: "image/jpeg" }));

describe("register rows from the API", () => {
  it("maps severity, confidence, review state, location and source", () => {
    expect(toRegisterRow(apiDetection())).toEqual({
      id: "d0000001-aaaa-4bbb-8ccc-000000000001",
      classification: "Pothole",
      location: "33.88230, -117.88510",
      source: "Photo · job a1b2c3d4",
      confidence: 87.3,
      severity: "High",
      status: "Unreviewed",
      detectedAt: "2026-11-20T20:30:00Z",
      evidencePath: "/api/v1/evidence/evidence-1",
    });
    const frame = toRegisterRow(
      apiDetection({ frame_timestamp: 3.24, latitude: null, longitude: null, evidence_media: null }),
    );
    expect(frame.source).toBe("Video frame 3.2 s · job a1b2c3d4");
    expect(frame.location).toBe("No location");
    expect(frame.evidencePath).toBeUndefined();
  });

  it("derives the review state from the issue status", () => {
    expect(reviewState("accepted")).toBe("Validated");
    expect(reviewState("resolved")).toBe("Validated");
    expect(reviewState("dismissed")).toBe("Rejected");
    expect(reviewState("pending_review")).toBe("Unreviewed");
    expect(reviewState(null)).toBe("Unreviewed");
  });
});

describe("detection register page", () => {
  it("loads detections and opens the review page", async () => {
    const fetchMock = stubFetch((url) => {
      if (url.pathname === "/api/v1/detections") {
        return jsonResponse({
          items: [
            apiDetection(),
            apiDetection({
              detection_id: "d0000002-aaaa-4bbb-8ccc-000000000002",
              severity: "minor",
              issue_id: "issue-2",
              issue_status: "accepted",
            }),
          ],
          total: 2,
          page: 1,
          page_size: 100,
        });
      }
      if (url.pathname === "/api/v1/evidence/evidence-1") return imageResponse();
      throw new Error(`Unexpected request ${url}`);
    });
    const user = userEvent.setup();
    render(<DetectionsPage />);

    const table = await screen.findByRole("table");
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("d0000001");
    expect(rows[0]).toHaveTextContent("87.3%");
    expect(rows[1]).toHaveTextContent("Validated");
    // One accepted issue is an active ticket.
    expect(screen.getByText("Active Tickets").parentElement).toHaveTextContent("1");
    const requested = new URL(String(fetchMock.mock.calls[0][0]));
    expect(requested.searchParams.get("page_size")).toBe("100");

    await user.click(within(rows[0]).getByRole("button", { name: "Review" }));
    expect(parseRoute(window.location.hash)).toEqual({
      name: "detection",
      detectionId: "d0000001-aaaa-4bbb-8ccc-000000000001",
    });
  });

  it("explains the register is for administrators to other roles", async () => {
    const fetchMock = stubFetch(() => {
      throw new Error("no requests expected");
    });
    setStoredSession(fakeSession({ role: "citizen" }));
    window.location.hash = "#/detections";
    render(<AppRoutes />);

    expect(
      await screen.findByText(/available to municipal administrators and developers/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Detections" })).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("shows the Detections link to administrators", async () => {
    stubFetch((url) => {
      if (url.pathname === "/api/v1/detections") {
        return jsonResponse({ items: [], total: 0, page: 1, page_size: 100 });
      }
      throw new Error(`Unexpected request ${url}`);
    });
    setStoredSession(fakeSession({ role: "municipal_administrator" }));
    window.location.hash = "#/detections";
    render(<AppRoutes />);

    expect(await screen.findByRole("link", { name: "Detections" })).toHaveAttribute(
      "aria-current", "page",
    );
    expect(await screen.findByText("No detections yet")).toBeInTheDocument();
  });
});

describe("detection review page", () => {
  it("shows the detection and saves a decision on its issue", async () => {
    const fetchMock = stubFetch((url, init) => {
      if (url.pathname === "/api/v1/detections/d1") {
        return jsonResponse(apiDetection({ detection_id: "d1" }));
      }
      if (url.pathname === "/api/v1/evidence/evidence-1") return imageResponse();
      if (url.pathname === "/api/v1/issues/issue-1" && init?.method === "PATCH") {
        return jsonResponse({ status: "dismissed", review_note: "Shadow, not a pothole." });
      }
      throw new Error(`Unexpected request ${init?.method ?? "GET"} ${url}`);
    });
    const user = userEvent.setup();
    render(<DetectionReview detectionId="d1" />);

    expect(await screen.findByText("87.3%")).toBeInTheDocument();
    expect(screen.getByText("High")).toBeInTheDocument();
    expect(screen.getByText("Unreviewed")).toBeInTheDocument();
    expect((await screen.findByRole("img")).getAttribute("src")).toMatch(/^blob:/);
    expect(screen.getByRole("link", { name: "→ Open issue" })).toHaveAttribute(
      "href", "#/issues/issue-1",
    );

    await user.type(screen.getByLabelText("Reviewer notes"), "Shadow, not a pothole.");
    await user.click(screen.getByRole("button", { name: "✕ Reject detection" }));

    expect(await screen.findByText(/Saved: the issue is now dismissed/)).toBeInTheDocument();
    expect(screen.getByText("Rejected")).toBeInTheDocument();
    const patch = fetchMock.mock.calls.find(([, init]) => init?.method === "PATCH")!;
    expect(JSON.parse(String(patch[1]!.body))).toEqual({
      status: "dismissed",
      review_note: "Shadow, not a pothole.",
    });
  });

  it("disables decisions for a detection without an issue", async () => {
    stubFetch((url) => {
      if (url.pathname === "/api/v1/detections/d2") {
        return jsonResponse(
          apiDetection({ detection_id: "d2", issue_id: null, issue_status: null, evidence_media: null }),
        );
      }
      throw new Error(`Unexpected request ${url}`);
    });
    render(<DetectionReview detectionId="d2" />);

    expect(await screen.findByText(/not linked to an issue/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "✓ Validate detection" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "✕ Reject detection" })).toBeDisabled();
  });

  it("reports a failed save", async () => {
    stubFetch((url, init) => {
      if (url.pathname === "/api/v1/detections/d1") return jsonResponse(apiDetection());
      if (url.pathname === "/api/v1/evidence/evidence-1") return imageResponse();
      if (init?.method === "PATCH") {
        return jsonResponse({ error: { code: "FORBIDDEN", message: "Not allowed." } }, 403);
      }
      throw new Error(`Unexpected request ${url}`);
    });
    const user = userEvent.setup();
    render(<DetectionReview detectionId="d1" />);
    await user.click(await screen.findByRole("button", { name: "✓ Validate detection" }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Request failed (403): Not allowed."),
    );
    expect(screen.getByText("Unreviewed")).toBeInTheDocument();
  });
});

describe("routes", () => {
  it("parses overview, register and review links", () => {
    expect(parseRoute("#/overview")).toEqual({ name: "overview" });
    expect(parseRoute("#/detections")).toEqual({ name: "detections" });
    expect(parseRoute("#/detections/abc%201")).toEqual({ name: "detection", detectionId: "abc 1" });
  });
});
