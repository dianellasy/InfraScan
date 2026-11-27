import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { detection, issue, jsonResponse, stubFetch } from "../test/fixtures.ts";
import { IssueDetailPage } from "./IssueDetailPage.tsx";
import { ReviewQueuePage } from "./ReviewQueuePage.tsx";

const conflictIssue = issue({
  issue_id: "issue-conflict",
  severity: "moderate",
  status: "pending_review",
  review_reason: "type_conflict",
  needs_review: true,
  detection_count: 3,
});

const conflictDetections = [
  detection({ detection_id: "d1", issue_id: "issue-conflict" }),
  detection({
    detection_id: "d2",
    issue_id: "issue-conflict",
    classification: "Alligator crack",
    issue_type: 2,
    predicted_issue_type_code: "alligator_crack",
    confidence: 0.69,
  }),
  detection({ detection_id: "d3", issue_id: "issue-conflict", confidence: 0.66 }),
];

describe("IssueDetailPage", () => {
  it("lists supporting detections with their raw predicted classes", async () => {
    stubFetch((url) =>
      url.pathname.endsWith("/detections")
        ? jsonResponse({ items: conflictDetections, total: 3, page: 1, page_size: 100 })
        : jsonResponse(conflictIssue),
    );
    render(<IssueDetailPage issueId="issue-conflict" />);

    expect(await screen.findByRole("heading", { name: "Pothole" })).toBeInTheDocument();
    expect(screen.getByText("Needs review")).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent(
      "Detections disagree on issue type. Supporting detections predicted Pothole ×2, Alligator crack ×1",
    );

    const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);
    expect(rows.map((row) => within(row).getAllByRole("cell")[1].textContent)).toEqual([
      "Pothole (class 3)",
      "Alligator crack (class 2)",
      "Pothole (class 3)",
    ]);
    expect(screen.getAllByText("Differs from issue")).toHaveLength(1);
    expect(within(rows[1]).getByText("Differs from issue")).toBeInTheDocument();
    expect(screen.getByText("3 of 3 detections")).toBeInTheDocument();
  });

  it("shows loading and error states", async () => {
    stubFetch(() => jsonResponse({ error: { code: "ISSUE_NOT_FOUND", message: "The issue was not found." } }, 404));
    render(<IssueDetailPage issueId="missing" />);

    expect(screen.getByRole("status")).toHaveTextContent("Loading issue…");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The issue was not found.");
  });
});

describe("ReviewQueuePage", () => {
  it("lists unlocated Issues that cannot appear on the map", async () => {
    stubFetch(() =>
      jsonResponse({
        items: [
          conflictIssue,
          issue({
            issue_id: "issue-unlocated",
            latitude: null,
            longitude: null,
            location_accuracy_m: null,
            status: "pending_review",
            review_reason: "missing_location",
            needs_review: true,
          }),
        ],
        total: 2,
        page: 1,
        page_size: 50,
      }),
    );
    render(<ReviewQueuePage />);

    const rows = (await screen.findAllByRole("row")).slice(1);
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("On map")).toBeInTheDocument();
    expect(within(rows[1]).getByText("No location")).toBeInTheDocument();
    expect(within(rows[1]).getByText("No location could be determined")).toBeInTheDocument();
    expect(within(rows[1]).getByRole("link", { name: "Open" })).toHaveAttribute(
      "href",
      "#/issues/issue-unlocated",
    );
  });
});
