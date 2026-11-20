import { useEffect, useState, type FormEvent } from "react";
import { endpoints } from "./api/endpoints.ts";
import { request } from "./api/request.ts";
import { TopBar } from "./TopBar.tsx";
import "./App.css";

type Job = {
  job_id: string;
  status: string;
  submitted_at: string;
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
  frame_timestamp?: number | null;
};

type Page<T> = {
  items: T[];
  total: number;
};

const MAX_FILE_SIZE = 5_000_000;
const MAX_VIDEO_SIZE = 1_000_000_000;
const mediaLimit = (file: File) => /\.mp4$/i.test(file.name) ? MAX_VIDEO_SIZE : MAX_FILE_SIZE;

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

function App() {
  const [file, setFile] = useState<File | null>(null);
  const [capturedAt, setCapturedAt] = useState("");
  const [latitude, setLatitude] = useState("");
  const [longitude, setLongitude] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [detections, setDetections] = useState<Detection[]>([]);
  const [detectionTotal, setDetectionTotal] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [loadingDetections, setLoadingDetections] = useState(false);
  const [error, setError] = useState("");
  const activeJobId = job?.job_id;

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
            const page = await request<Page<Detection>>(
              detectionsUrl.toString(),
            );
            if (active) {
              setDetections(page.items);
              setDetectionTotal(page.total);
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

    setSubmitting(true);
    setError("");
    setJob(null);
    setDetections([]);
    setDetectionTotal(0);

    const metadata = {
      captured_at: capturedDate.toISOString(),
      latitude: latitudeNumber,
      longitude: longitudeNumber,
    };

    const formData = new FormData();
    formData.append("file", file);
    formData.append("metadata", JSON.stringify(metadata));

    try {
      const acceptedJob = await request<Job>(endpoints.inferenceJobs.url, {
        method: endpoints.inferenceJobs.method,
        body: formData,
      });
      setJob(acceptedJob);
    } catch (submitError) {
      setError(
        submitError instanceof Error ? submitError.message : "Unable to submit media.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  const statusLabel = job
    ? job.status.replaceAll("_", " ")
    : "No job submitted";

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
              <span>JPEG: 5 MB, up to 4032 × 3024. MP4/H.264: 1 GB, 1080p, 30 fps, 10 minutes. Video locations require a GPS track.</span>
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

            <div className="metadata-fields">
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
            </div>

            <button className="primary-button" type="submit" disabled={submitting}>
              {submitting ? "Submitting…" : "Submit for analysis"}
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
              {statusLabel}
            </span>
          </div>
          {job ? (
            <div className="job-details">
              <div>
                <span className="detail-label">Job ID</span>
                <code>{job.job_id}</code>
              </div>
              <div>
                <span className="detail-label">Submitted</span>
                <span>{new Date(job.submitted_at).toLocaleString()}</span>
              </div>
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
              <table>
                <thead>
                  <tr>
                    <th>Classification</th>
                    <th>Severity</th>
                    <th>Confidence</th>
                    <th>Location</th>
                    <th>Video time</th>
                    <th>Detected</th>
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
                      <td>{detection.frame_timestamp == null ? "—" : `${detection.frame_timestamp.toFixed(2)} s`}</td>
                      <td>{new Date(detection.detected_at).toLocaleString()}</td>
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
