import { useEffect, useState, type FormEvent } from "react";
import { endpoints } from "./api/endpoints.ts";
import { uploadAndSubmit, type UploadStage } from "./api/directUpload.ts";
import { request } from "./api/request.ts";
import { EvidenceImage } from "./issues/EvidenceImage.tsx";
import { fetchJobIssues } from "./issues/api.ts";
import { reviewReasonLabel, severityInfo, statusLabel } from "./issues/constants.ts";
import type { Issue } from "./issues/types.ts";
import { focusBounds, issueHref, jobHref, mapHref } from "./routing.ts";
import { TopBar } from "./TopBar.tsx";
import { UTC_OFFSET_OPTIONS, browserUtcOffset, formatUtcOffset } from "./utcOffset.ts";
import { readPhotoMetadata, toDateTimeLocal } from "./photoMetadata.ts";
import "./App.css";

type Job = {
  job_id: string;
  status: string;
  /** Absent until the first status response when the page is opened from a job link. */
  submitted_at?: string;
  progress?: number | null;
  error?: string | null;
};

type Detection = {
  detection_id: string;
  classification: string;
  severity: string;
  confidence: number;
  latitude: number | null;
  longitude: number | null;
  detected_at: string;
  observed_at?: string | null;
  frame_timestamp?: number | null;
  issue_id?: string | null;
  /** Annotated evidence image (served by `/api/v1/evidence/<id>`). */
  evidence_media?: string | null;
};

/** Unique evidence images in detection order (one per photo, one per video frame). */
function evidenceIds(detections: Detection[], limit = 6): string[] {
  const ids: string[] = [];
  for (const detection of detections) {
    const id = detection.evidence_media;
    if (id && !ids.includes(id)) ids.push(id);
    if (ids.length >= limit) break;
  }
  return ids;
}

/** "3 × Pothole, 1 × Alligator crack" style summary, most frequent first. */
function classSummary(detections: Detection[]): Array<[string, number]> {
  const counts = new Map<string, number>();
  for (const detection of detections) {
    counts.set(detection.classification, (counts.get(detection.classification) ?? 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1]);
}

type Page<T> = {
  items: T[];
  total: number;
};

type LocatedIssue = Issue & { latitude: number; longitude: number };

function isLocated(issue: Issue): issue is LocatedIssue {
  return issue.latitude != null && issue.longitude != null;
}

const MAX_FILE_SIZE = 5_000_000;
const MAX_VIDEO_SIZE = 1_000_000_000;
// Browser-side convenience only; the server detects the type from the bytes.
const isVideoFile = (file: File) => /\.mp4$/i.test(file.name) || file.type === "video/mp4";
const mediaLimit = (file: File) => isVideoFile(file) ? MAX_VIDEO_SIZE : MAX_FILE_SIZE;

function isTerminal(status: string) {
  return ["completed", "complete", "succeeded", "success", "done", "failed", "error", "cancelled"].includes(
    status.toLowerCase(),
  );
}

function isSuccessful(status: string) {
  return ["completed", "complete", "succeeded", "success", "done"].includes(
    status.toLowerCase(),
  );
}

/** Render with `key={jobId}`: `jobId` (from `#/jobs/<id>`) only seeds the initial state. */
function App({ jobId }: { jobId?: string }) {
  const [file, setFile] = useState<File | null>(null);
  const [capturedAt, setCapturedAt] = useState("");
  const [latitude, setLatitude] = useState("");
  const [longitude, setLongitude] = useState("");
  const [cameraUtcOffset, setCameraUtcOffset] = useState(() => browserUtcOffset());
  const [job, setJob] = useState<Job | null>(() =>
    jobId ? { job_id: jobId, status: "pending" } : null,
  );
  const [detections, setDetections] = useState<Detection[]>([]);
  const [detectionTotal, setDetectionTotal] = useState(0);
  const [jobIssues, setJobIssues] = useState<Page<Issue> | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [uploadStage, setUploadStage] = useState<UploadStage | null>(null);
  const [loadingDetections, setLoadingDetections] = useState(false);
  const [error, setError] = useState("");
  const [metadataNote, setMetadataNote] = useState("");
  const [locating, setLocating] = useState(false);
  const activeJobId = job?.job_id;

  /** Fill capture time and GPS from the photo's EXIF, when it has them. */
  async function fillFromPhoto(selectedFile: File) {
    setMetadataNote("");
    if (isVideoFile(selectedFile)) return;
    const meta = await readPhotoMetadata(selectedFile);
    const found: string[] = [];
    if (meta.capturedAt) {
      setCapturedAt(meta.capturedAt);
      found.push("capture time");
    }
    if (meta.latitude !== undefined && meta.longitude !== undefined) {
      setLatitude(meta.latitude.toFixed(6));
      setLongitude(meta.longitude.toFixed(6));
      found.push("GPS location");
    }
    setMetadataNote(
      found.length
        ? `Filled ${found.join(" and ")} from the photo. Check before submitting.`
        : "This photo has no capture time or GPS. Enter them below or use your current location.",
    );
  }

  function useCurrentLocation() {
    if (!navigator.geolocation) {
      setError("This browser can't share its location. Enter the coordinates instead.");
      return;
    }
    setLocating(true);
    navigator.geolocation.getCurrentPosition(
      (position) => {
        setLatitude(position.coords.latitude.toFixed(6));
        setLongitude(position.coords.longitude.toFixed(6));
        if (!capturedAt) setCapturedAt(toDateTimeLocal(new Date()));
        setLocating(false);
      },
      () => {
        setError("Couldn't get your location. Enter the coordinates instead.");
        setLocating(false);
      },
      { enableHighAccuracy: true, timeout: 10000 },
    );
  }
  const videoSelected = file !== null && isVideoFile(file);

  useEffect(() => {
    if (!activeJobId) return;

    let active = true;
    let timeoutId: number | undefined;

    const poll = async () => {
      try {
        const updatedJob = await request<Job>(endpoints.job(activeJobId).url);
        if (!active) return;
        setJob(updatedJob);

        if (isTerminal(updatedJob.status)) {
          if (isSuccessful(updatedJob.status)) {
            setLoadingDetections(true);
            const detectionsUrl = new URL(
              endpoints.jobDetections(activeJobId).url,
            );
            detectionsUrl.searchParams.set("page_size", "100");
            // Issue links commit with the job, so a completed job's Issues are final.
            const [page, issues] = await Promise.all([
              request<Page<Detection>>(detectionsUrl.toString()),
              fetchJobIssues(activeJobId),
            ]);
            if (active) {
              setDetections(page.items);
              setDetectionTotal(page.total);
              setJobIssues(issues);
            }
            if (active) setLoadingDetections(false);
          } else if (updatedJob.error) {
            setError(updatedJob.error);
          }
          return;
        }

        timeoutId = window.setTimeout(poll, 2000);
      } catch (pollError) {
        if (active) {
          setError(
            pollError instanceof Error ? pollError.message : "Unable to check job status.",
          );
        }
      } finally {
        if (active) setLoadingDetections(false);
      }
    };

    void poll();
    return () => {
      active = false;
      if (timeoutId !== undefined) window.clearTimeout(timeoutId);
    };
  }, [activeJobId]);

  async function submitMedia(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) {
      setError("Choose a JPEG image or MP4 video to submit.");
      return;
    }
    if (!/\.(jpe?g|mp4)$/i.test(file.name)) {
      setError("Choose a .jpg, .jpeg, or .mp4 file.");
      return;
    }
    if (file.size > mediaLimit(file)) {
      setError("JPEG limit: 5 MB. MP4 limit: 1 GB.");
      return;
    }

    let metadata: Record<string, string | number>;
    if (isVideoFile(file)) {
      metadata = { camera_utc_offset_minutes: cameraUtcOffset };
    } else {
      if (!capturedAt) {
        setError("Enter the capture date and time.");
        return;
      }
      if (!latitude.trim()) {
        setError("Enter the latitude.");
        return;
      }
      if (!longitude.trim()) {
        setError("Enter the longitude.");
        return;
      }

      const capturedDate = new Date(capturedAt);
      const latitudeNumber = Number(latitude);
      const longitudeNumber = Number(longitude);
      if (Number.isNaN(capturedDate.getTime())) {
        setError("Enter a valid capture date and time.");
        return;
      }
      if (!Number.isFinite(latitudeNumber) || latitudeNumber < -90 || latitudeNumber > 90) {
        setError("Latitude must be a number from -90 to 90.");
        return;
      }
      if (!Number.isFinite(longitudeNumber) || longitudeNumber < -180 || longitudeNumber > 180) {
        setError("Longitude must be a number from -180 to 180.");
        return;
      }
      metadata = {
        captured_at: capturedDate.toISOString(),
        latitude: latitudeNumber,
        longitude: longitudeNumber,
      };
    }

    setSubmitting(true);
    setError("");
    setJob(null);
    setDetections([]);
    setDetectionTotal(0);
    setJobIssues(null);

    try {
      const acceptedJob = await uploadAndSubmit<Job>(
        file,
        isVideoFile(file) ? "video/mp4" : "image/jpeg",
        metadata,
        setUploadStage,
      );
      setJob(acceptedJob);
      // A job link keeps the results reachable after visiting the map or an Issue.
      window.location.hash = jobHref(acceptedJob.job_id);
    } catch (submitError) {
      setError(
        submitError instanceof Error ? submitError.message : "Unable to submit media.",
      );
    } finally {
      setSubmitting(false);
      setUploadStage(null);
    }
  }

  const jobStatusLabel = job
    ? job.status.replaceAll("_", " ")
    : "No job submitted";
  const locatedIssues = jobIssues?.items.filter(isLocated) ?? [];
  const unlocatedIssueCount = (jobIssues?.items.length ?? 0) - locatedIssues.length;
  const jobMapHref = locatedIssues.length
    ? mapHref(focusBounds(locatedIssues), locatedIssues[0].issue_id)
    : null;

  return (
    <main className="app-shell">
      <TopBar route="upload" />

      <section className="intro">
        <p className="eyebrow">IMAGE ANALYSIS</p>
        <h1>Inspect road conditions</h1>
        <p>Upload road images or videos to detect and review infrastructure issues.</p>
      </section>

      {error && (
        <div className="error-banner" role="alert">
          <strong>Something went wrong</strong>
          <span>{error}</span>
          <button type="button" className="dismiss-button" onClick={() => setError("")}>
            Dismiss
          </button>
        </div>
      )}

      <div className="content-grid">
        <section className="panel upload-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">NEW SUBMISSION</p>
              <h2>Upload media</h2>
            </div>
            <span className="step-number">01</span>
          </div>

          <form onSubmit={submitMedia}>
            <label className="file-drop">
              <span className="upload-icon" aria-hidden="true">↑</span>
              <strong>Choose a road image or video</strong>
              <span>JPEG: 5 MB, up to 4032 × 3024. MP4 from a VIOFO A119 Mini 2: 1 GB, 10 minutes, with embedded GPS.</span>
              <input
                type="file"
                accept=".jpg,.jpeg,.mp4,image/jpeg,video/mp4"
                onChange={(event) => {
                  const selectedFile = event.currentTarget.files?.[0] ?? null;
                  if (selectedFile && selectedFile.size > mediaLimit(selectedFile)) {
                    setFile(null);
                    setError("JPEG limit: 5 MB. MP4 limit: 1 GB.");
                    event.currentTarget.value = "";
                    return;
                  }
                  setFile(selectedFile);
                  setError("");
                  if (selectedFile) void fillFromPhoto(selectedFile);
                }}
              />
            </label>

            {file && (
              <div className="selected-files" aria-live="polite">
                <strong>Media selected</strong>
                <ul>
                  <li>
                    <span>{file.name}</span>
                    <span>{file.size.toLocaleString()} bytes</span>
                  </li>
                </ul>
              </div>
            )}

            {videoSelected ? (
              <div className="metadata-fields">
                <p className="video-note" role="note">
                  Capture time and location come from the clip's embedded A119 Mini 2 GPS
                  track, so no coordinates are needed. Required camera settings: MP4, H.264,
                  2560 × 1440 (1440P) or 1920 × 1080 (1080P), 30 fps, GPS on, loop recording of
                  10 minutes or less.
                </p>
                <div className="field-grid">
                  <label>
                    Dashcam time-zone setting
                    <select
                      value={cameraUtcOffset}
                      onChange={(event) => setCameraUtcOffset(Number(event.target.value))}
                    >
                      {UTC_OFFSET_OPTIONS.map((minutes) => (
                        <option key={minutes} value={minutes}>
                          {formatUtcOffset(minutes)}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                <p className="field-hint">
                  Match the time zone set on the camera. The A119 records local time and does
                  not adjust for daylight saving time on its own.
                </p>
              </div>
            ) : (
              <div className="metadata-fields">
                {metadataNote && (
                  <p className="field-hint" role="status">
                    {metadataNote}
                  </p>
                )}
                <div className="field-grid">
                  <label>
                    Capture date and time
                    <input
                      type="datetime-local"
                      value={capturedAt}
                      onChange={(event) => setCapturedAt(event.target.value)}
                      required
                    />
                  </label>
                  <label>
                    Latitude
                    <input
                      type="number"
                      min="-90"
                      max="90"
                      step="any"
                      value={latitude}
                      onChange={(event) => setLatitude(event.target.value)}
                      placeholder="e.g. 37.7749"
                      required
                    />
                  </label>
                  <label>
                    Longitude
                    <input
                      type="number"
                      min="-180"
                      max="180"
                      step="any"
                      value={longitude}
                      onChange={(event) => setLongitude(event.target.value)}
                      placeholder="e.g. -122.4194"
                      required
                    />
                  </label>
                </div>
                <button
                  type="button"
                  className="secondary-button"
                  onClick={useCurrentLocation}
                  disabled={locating}
                >
                  {locating ? "Getting location…" : "Use my current location"}
                </button>
              </div>
            )}

            <button className="primary-button" type="submit" disabled={submitting}>
              {!submitting
                ? "Submit for analysis"
                : uploadStage === "starting"
                  ? "Starting analysis…"
                  : "Uploading…"}
            </button>
          </form>
        </section>

        <section className="panel job-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">ANALYSIS</p>
              <h2>Job status</h2>
            </div>
            <span className={`status-pill ${job ? job.status.toLowerCase() : "idle"}`}>
              {jobStatusLabel}
            </span>
          </div>
          {job ? (
            <div className="job-details">
              <div>
                <span className="detail-label">Job ID</span>
                <code>{job.job_id}</code>
              </div>
              {job.submitted_at && (
                <div>
                  <span className="detail-label">Submitted</span>
                  <span>{new Date(job.submitted_at).toLocaleString()}</span>
                </div>
              )}
              {job.progress != null && (
                <div className="progress-row">
                  <span className="detail-label">Progress</span>
                  <progress value={job.progress} max="100">{job.progress}%</progress>
                  <span>{job.progress}%</span>
                </div>
              )}
              {!isTerminal(job.status) && (
                <p className="polling-note">Checking for updates automatically…</p>
              )}
            </div>
          ) : (
            <p className="empty-state">
              Submit a JPEG image or MP4 video to start an analysis job. Its status will appear here.
            </p>
          )}
        </section>
      </div>

      {job && isSuccessful(job.status) && detections.length > 0 && (
        <section className="panel results-panel" aria-labelledby="annotated-heading">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">MODEL OUTPUT</p>
              <h2 id="annotated-heading">Annotated image</h2>
            </div>
            <span className="result-count">
              {detectionTotal} {detectionTotal === 1 ? "detection" : "detections"}
            </span>
          </div>
          <div className="class-summary">
            {classSummary(detections).map(([label, count]) => (
              <span key={label} className="class-chip">
                {count} × {label}
              </span>
            ))}
          </div>
          <div className="annotated-grid">
            {evidenceIds(detections).map((id) => (
              <EvidenceImage
                key={id}
                path={`/api/v1/evidence/${id}`}
                alt="Road image with the model's detection boxes drawn on it"
                className="annotated-image"
              />
            ))}
          </div>
        </section>
      )}

      {job && isSuccessful(job.status) && jobIssues && (
        <section className="panel results-panel" aria-labelledby="job-issues-heading">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">ISSUES</p>
              <h2 id="job-issues-heading">Linked issues</h2>
            </div>
            <span className="result-count">
              {jobIssues.total} {jobIssues.total === 1 ? "issue" : "issues"}
            </span>
          </div>
          {jobIssues.total === 0 ? (
            <p className="empty-state">This job did not create or update any issues.</p>
          ) : (
            <>
              <p className="job-issues-summary">
                Each detection was added to an existing issue nearby or started a new one.{" "}
                {locatedIssues.length} on the map
                {unlocatedIssueCount > 0 &&
                  `, ${unlocatedIssueCount} without a location (review queue only)`}
                .
              </p>
              <div className="job-issue-actions">
                {jobMapHref && (
                  <a className="primary-button" href={jobMapHref}>
                    View on issue map
                  </a>
                )}
                {unlocatedIssueCount > 0 && <a href="#/review">Open review queue</a>}
              </div>
              <div className="table-scroll">
                <table aria-label="Linked issues">
                  <thead>
                    <tr>
                      <th>Issue type</th>
                      <th>Severity</th>
                      <th>Status</th>
                      <th>Location</th>
                      <th>Detections</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {jobIssues.items.map((issue) => (
                      <tr key={issue.issue_id}>
                        <td>
                          <strong>{issue.issue_type_name}</strong>
                        </td>
                        <td>
                          <span className={`severity ${issue.severity}`}>
                            {severityInfo(issue.severity).label}
                          </span>
                        </td>
                        <td>
                          {statusLabel(issue.status)}
                          {issue.review_reason && ` · ${reviewReasonLabel(issue.review_reason)}`}
                        </td>
                        <td>
                          {isLocated(issue)
                            ? `${issue.latitude.toFixed(5)}, ${issue.longitude.toFixed(5)}`
                            : "No location"}
                        </td>
                        <td>{issue.detection_count}</td>
                        <td>
                          <a href={issueHref(issue.issue_id)}>Open issue</a>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {jobIssues.total > jobIssues.items.length && (
                <p className="field-hint">
                  Showing {jobIssues.items.length} of {jobIssues.total} issues.
                </p>
              )}
            </>
          )}
        </section>
      )}

      {job && isTerminal(job.status) && isSuccessful(job.status) && (
        <section className="panel results-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">JOB OUTPUT</p>
              <h2>Detection results</h2>
            </div>
            <span className="result-count">
              {loadingDetections
                ? "Loading…"
                : `${detections.length} of ${detectionTotal} detections`}
            </span>
          </div>

          {loadingDetections ? (
            <p className="empty-state">Loading detections…</p>
          ) : detections.length === 0 ? (
            <p className="empty-state">Analysis completed with no detections.</p>
          ) : (
            <div className="table-scroll">
              <table aria-label="Detections">
                <thead>
                  <tr>
                    <th>Classification</th>
                    <th>Severity</th>
                    <th>Confidence</th>
                    <th>Location</th>
                    <th>Video time</th>
                    <th>Captured</th>
                    <th>Issue</th>
                  </tr>
                </thead>
                <tbody>
                  {detections.map((detection) => (
                    <tr key={detection.detection_id}>
                      <td>
                        <strong>{detection.classification.replaceAll("_", " ")}</strong>
                      </td>
                      <td>
                        <span className={`severity ${detection.severity.toLowerCase()}`}>
                          {detection.severity}
                        </span>
                      </td>
                      <td>{(detection.confidence * 100).toFixed(1)}%</td>
                      <td>
                        {detection.latitude != null && detection.longitude != null
                          ? `${detection.latitude.toFixed(5)}, ${detection.longitude.toFixed(5)}`
                          : "—"}
                      </td>
                      <td>{detection.frame_timestamp == null ? "" : `${detection.frame_timestamp.toFixed(2)} s`}</td>
                      <td>
                        {detection.observed_at
                          ? new Date(detection.observed_at).toLocaleString()
                          : "—"}
                      </td>
                      <td>
                        {detection.issue_id ? (
                          <a href={issueHref(detection.issue_id)}>Open issue</a>
                        ) : (
                          "Not linked"
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      <footer>InfraScan · Road damage detection</footer>
    </main>
  );
}

export default App;
