import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import App from "./App.tsx";
import type { ViewportBounds } from "./issues/types.ts";
import { parseRoute } from "./routing.ts";
import { detection, issue, jsonResponse, requestedUrls, stubFetch } from "./test/fixtures.ts";
import { browserUtcOffset, formatUtcOffset } from "./utcOffset.ts";

const accepted = { job_id: "job-1", status: "pending", submitted_at: "2026-11-20T20:30:00Z" };

const ticket = {
  upload_id: "upload-1",
  upload_url: "https://r2.example/input/user/upload-1.jpg?signature=test",
  method: "PUT",
  headers: { "Content-Type": "image/jpeg" },
  expires_in_seconds: 900,
};

/** Answers the upload ticket and the storage PUT; API calls after that get `responses` in order. */
function stubSubmission(...responses: Response[]) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input));
    if (url.pathname === "/api/v1/inference/uploads") return jsonResponse(ticket, 201);
    if (url.hostname === "r2.example") return new Response(null, { status: 200 });
    return responses.shift() ?? jsonResponse(accepted);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function chooser(): HTMLInputElement {
  return document.querySelector('input[type="file"]') as HTMLInputElement;
}

function callTo(fetchMock: ReturnType<typeof vi.fn>, matches: (url: URL) => boolean) {
  return (fetchMock.mock.calls as unknown as [string, RequestInit][]).find(([input]) =>
    matches(new URL(String(input))),
  );
}

function submittedJob(fetchMock: ReturnType<typeof vi.fn>): Record<string, unknown> {
  const call = callTo(fetchMock, (url) => url.pathname === "/api/v1/inference/jobs")!;
  return JSON.parse(String(call[1].body)) as Record<string, unknown>;
}

function submittedMetadata(fetchMock: ReturnType<typeof vi.fn>): unknown {
  return submittedJob(fetchMock).metadata;
}

describe("upload form", () => {
  it("accepts JPEG and MP4 files", () => {
    render(<App />);
    expect(chooser()).toHaveAttribute("accept", expect.stringContaining(".mp4"));
    expect(chooser()).toHaveAttribute("accept", expect.stringContaining(".jpeg"));
  });

  it("requires capture time and coordinates for JPEG", async () => {
    const user = userEvent.setup();
    const fetchMock = stubSubmission();
    render(<App />);
    await user.upload(chooser(), new File(["jpeg"], "road.jpg", { type: "image/jpeg" }));

    expect(screen.getByLabelText("Latitude")).toBeRequired();
    expect(screen.queryByLabelText("Dashcam time-zone setting")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("Capture date and time"), "2026-11-10T14:30");
    await user.type(screen.getByLabelText("Latitude"), "33.8823");
    await user.type(screen.getByLabelText("Longitude"), "-117.8851");
    await user.click(screen.getByRole("button", { name: "Submit for analysis" }));

    await waitFor(() => expect(callTo(fetchMock, (url) => url.pathname === "/api/v1/inference/jobs")).toBeDefined());
    expect(submittedMetadata(fetchMock)).toMatchObject({ latitude: 33.8823, longitude: -117.8851 });
  });

  it("uploads the file straight to storage, then starts the job by upload ID", async () => {
    const user = userEvent.setup();
    const fetchMock = stubSubmission();
    render(<App />);
    const photo = new File(["jpeg-bytes"], "road.jpg", { type: "image/jpeg" });
    await user.upload(chooser(), photo);
    await user.type(screen.getByLabelText("Capture date and time"), "2026-11-10T14:30");
    await user.type(screen.getByLabelText("Latitude"), "33.8823");
    await user.type(screen.getByLabelText("Longitude"), "-117.8851");
    await user.click(screen.getByRole("button", { name: "Submit for analysis" }));
    await waitFor(() => expect(window.location.hash).toBe("#/jobs/job-1"));

    const paths = (fetchMock.mock.calls as unknown as [string][]).map(([input]) => new URL(String(input)));
    expect(paths.slice(0, 3).map((url) => url.hostname === "r2.example" ? "storage" : url.pathname)).toEqual([
      "/api/v1/inference/uploads", "storage", "/api/v1/inference/jobs",
    ]);
    const ticketRequest = callTo(fetchMock, (url) => url.pathname === "/api/v1/inference/uploads")!;
    expect(JSON.parse(String(ticketRequest[1].body))).toEqual({ content_type: "image/jpeg", size_bytes: photo.size });
    const put = callTo(fetchMock, (url) => url.hostname === "r2.example")!;
    expect(put[0]).toBe(ticket.upload_url);
    expect(put[1].method).toBe("PUT");
    expect(put[1].body).toBe(photo);
    // The signed URL is the credential; the user's token never goes to storage.
    expect(new Headers(put[1].headers).get("Authorization")).toBeNull();
    expect(new Headers(put[1].headers).get("Content-Type")).toBe("image/jpeg");
    expect(submittedJob(fetchMock)).toMatchObject({
      upload_id: "upload-1", content_type: "image/jpeg", filename: "road.jpg",
    });
  });

  it("reports a failed storage upload without starting a job", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input));
      if (url.pathname === "/api/v1/inference/uploads") return jsonResponse(ticket, 201);
      return new Response("denied", { status: 403 });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await user.upload(chooser(), new File(["mp4"], "clip.mp4", { type: "video/mp4" }));
    await user.click(screen.getByRole("button", { name: "Submit for analysis" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Upload to storage failed (403)");
    expect(callTo(fetchMock, (url) => url.pathname === "/api/v1/inference/jobs")).toBeUndefined();
  });

  it("uses embedded GPS and the camera offset for MP4", async () => {
    const user = userEvent.setup();
    const fetchMock = stubSubmission();
    render(<App />);
    await user.upload(
      chooser(),
      new File(["mp4"], "20261120123000_000001.MP4", { type: "video/mp4" }),
    );

    expect(screen.queryByLabelText("Latitude")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Capture date and time")).not.toBeInTheDocument();
    const note = screen.getByRole("note");
    expect(note).toHaveTextContent("embedded A119 Mini 2 GPS");
    for (const setting of ["MP4", "H.264", "2560 × 1440", "1920 × 1080", "30 fps", "GPS on", "10 minutes"]) {
      expect(note).toHaveTextContent(setting);
    }

    await user.selectOptions(screen.getByLabelText("Dashcam time-zone setting"), "UTC−08:00");
    await user.click(screen.getByRole("button", { name: "Submit for analysis" }));
    await waitFor(() => expect(callTo(fetchMock, (url) => url.pathname === "/api/v1/inference/jobs")).toBeDefined());
    expect(submittedMetadata(fetchMock)).toEqual({ camera_utc_offset_minutes: -480 });
    expect(submittedJob(fetchMock).content_type).toBe("video/mp4");
  });

  it("rejects an MP4 over 1 GB before uploading", async () => {
    const user = userEvent.setup();
    const fetchMock = stubSubmission();
    render(<App />);
    const big = new File(["x"], "clip.mp4", { type: "video/mp4" });
    Object.defineProperty(big, "size", { value: 1_000_000_001 });
    await user.upload(chooser(), big);
    expect(screen.getByRole("alert")).toHaveTextContent("MP4 limit: 1 GB");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("shows backend validation errors", async () => {
    const user = userEvent.setup();
    stubSubmission(
      jsonResponse(
        { error: { code: "MISSING_GPS_TRACK", message: "The video has no usable embedded A119 Mini 2 GPS fix.", field: "file" } },
        422,
      ),
    );
    render(<App />);
    await user.upload(chooser(), new File(["mp4"], "clip.mp4", { type: "video/mp4" }));
    await user.click(screen.getByRole("button", { name: "Submit for analysis" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Request failed (422): The video has no usable embedded A119 Mini 2 GPS fix.",
    );
  });

  it("shows frame timestamps for video detections and leaves them empty for images", async () => {
    const user = userEvent.setup();
    const detection = {
      classification: "Pothole",
      severity: "high",
      confidence: 0.9,
      latitude: 33.8,
      longitude: -117.9,
      detected_at: "2026-11-20T20:31:00Z",
    };
    stubSubmission(
      jsonResponse(accepted),
      jsonResponse({ ...accepted, status: "completed" }),
      jsonResponse({
        items: [
          { ...detection, detection_id: "d1", frame_timestamp: 3.2 },
          { ...detection, detection_id: "d2", frame_timestamp: null },
        ],
        total: 2,
      }),
      jsonResponse({ items: [], total: 0, page: 1, page_size: 100 }),
    );
    render(<App />);
    await user.upload(chooser(), new File(["mp4"], "clip.mp4", { type: "video/mp4" }));
    await user.click(screen.getByRole("button", { name: "Submit for analysis" }));

    expect(await screen.findByText("3.20 s")).toBeInTheDocument();
    const rows = within(screen.getByRole("table", { name: "Detections" })).getAllByRole("row");
    expect(rows[2].children[4]).toHaveTextContent(/^$/);
  });
});

describe("job results and issues", () => {
  const locatedIssue = issue({
    issue_id: "issue-located",
    status: "pending_review",
    needs_review: true,
    latitude: 33.88241,
    longitude: -117.8851,
    location_accuracy_m: null,
    detection_count: 1,
  });
  const unlocatedIssue = issue({
    issue_id: "issue-unlocated",
    severity: "minor",
    status: "pending_review",
    review_reason: "missing_location",
    needs_review: true,
    latitude: null,
    longitude: null,
    location_accuracy_m: null,
  });

  function completedJobServer(issues = [locatedIssue, unlocatedIssue]) {
    return stubFetch((url) => {
      if (url.pathname === "/api/v1/inference/uploads") return jsonResponse(ticket, 201);
      if (url.hostname === "r2.example") return new Response(null, { status: 200 });
      if (url.pathname === "/api/v1/inference/jobs") return jsonResponse(accepted, 202);
      if (url.pathname === "/api/v1/jobs/job-1") return jsonResponse({ ...accepted, status: "completed" });
      if (url.pathname === "/api/v1/jobs/job-1/detections") {
        return jsonResponse({
          items: [
            detection({ detection_id: "d1", job_id: "job-1", issue_id: "issue-located",
              observed_at: "2026-11-10T22:30:00Z" }),
            detection({ detection_id: "d2", job_id: "job-1", issue_id: null, issue_type: 9,
              predicted_issue_type_code: null, classification: "class_9" }),
          ],
          total: 2,
        });
      }
      if (url.pathname === "/api/v1/issues") {
        return jsonResponse({ items: issues, total: issues.length, page: 1, page_size: 100 });
      }
      throw new Error(`Unexpected request ${url}`);
    });
  }

  it("links a completed job to its issues, the map, and the review queue", async () => {
    const fetchMock = completedJobServer();
    const user = userEvent.setup();
    render(<App />);
    await user.upload(chooser(), new File(["jpeg"], "road.jpg", { type: "image/jpeg" }));
    await user.type(screen.getByLabelText("Capture date and time"), "2026-11-10T14:30");
    await user.type(screen.getByLabelText("Latitude"), "33.8823");
    await user.type(screen.getByLabelText("Longitude"), "-117.8851");
    await user.click(screen.getByRole("button", { name: "Submit for analysis" }));

    const issueTable = await screen.findByRole("table", { name: "Linked issues" });
    expect(window.location.hash).toBe("#/jobs/job-1");
    const issuesRequest = requestedUrls(fetchMock).find((url) => url.pathname === "/api/v1/issues")!;
    expect(issuesRequest.searchParams.get("job_id")).toBe("job-1");

    const rows = within(issueTable).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByRole("link", { name: "Open issue" })).toHaveAttribute(
      "href", "#/issues/issue-located",
    );
    expect(rows[1]).toHaveTextContent("No location could be determined");
    expect(rows[1]).toHaveTextContent("No location");
    expect(screen.getByText(/1 on the map, 1 without a location/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open review queue" })).toHaveAttribute("href", "#/review");

    // The map link frames the located Issue and preselects it.
    const mapLink = screen.getByRole("link", { name: "View on issue map" });
    const route = parseRoute(mapLink.getAttribute("href")!);
    expect(route).toMatchObject({ name: "map", issueId: "issue-located" });
    const bounds = (route as { bounds: ViewportBounds }).bounds;
    expect(bounds.south).toBeLessThan(33.88241);
    expect(bounds.north).toBeGreaterThan(33.88241);
    expect(bounds.west).toBeLessThan(-117.8851);
    expect(bounds.east).toBeGreaterThan(-117.8851);
    expect(bounds.north - bounds.south).toBeLessThan(0.01);

    const detectionRows = within(screen.getByRole("table", { name: "Detections" }))
      .getAllByRole("row").slice(1);
    expect(within(detectionRows[0]).getByRole("link", { name: "Open issue" })).toHaveAttribute(
      "href", "#/issues/issue-located",
    );
    expect(detectionRows[1]).toHaveTextContent("Not linked");
    expect(detectionRows[0].children[5]).toHaveTextContent(
      new Date("2026-11-10T22:30:00Z").toLocaleString(),
    );
  });

  it("restores a job's results from its link", async () => {
    completedJobServer([]);
    render(<App jobId="job-1" />);
    expect(await screen.findByText("This job did not create or update any issues.")).toBeInTheDocument();
    expect(screen.getByText("job-1")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "View on issue map" })).not.toBeInTheDocument();
  });
});

describe("UTC offsets", () => {
  it("formats offsets and defaults from the browser", () => {
    expect(formatUtcOffset(-480)).toBe("UTC−08:00");
    expect(formatUtcOffset(330)).toBe("UTC+05:30");
    expect(formatUtcOffset(0)).toBe("UTC+00:00");
    const pacific = { getTimezoneOffset: () => 480 } as Date;
    expect(browserUtcOffset(pacific)).toBe(-480);
  });
});
