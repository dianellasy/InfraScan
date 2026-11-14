import { useEffect, useState, type FormEvent } from "react";
import { endpoints } from "./api/endpoints.ts";
import infrascanLogo from "./assets/infrascan.svg";
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
};

type Page<T> = {
  items: T[];
  total: number;
};

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

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    const body = await response.text();
    let message = body || response.statusText;
    try {
      const parsed: unknown = JSON.parse(body);
      if (
        typeof parsed === "object" &&
        parsed !== null &&
        "detail" in parsed
      ) {
        message = String(parsed.detail);
      }
    } catch {
      // Use the response text when the server does not return JSON.
    }
    throw new Error(`Request failed (${response.status}): ${message}`);
  }
  return (await response.json()) as T;
}

function App() {
  const [files, setFiles] = useState<File[]>([]);
  const [sourceId, setSourceId] = useState("");
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

  async function submitImages(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (files.length === 0) {
      setError("Choose at least one image to submit.");
      return;
    }

    setSubmitting(true);
    setError("");
    setJob(null);
    setDetections([]);
    setDetectionTotal(0);

    const metadata: Record<string, string | number> = {};
    if (sourceId.trim()) metadata.source_id = sourceId.trim();
    if (capturedAt) metadata.captured_at = new Date(capturedAt).toISOString();
    if (latitude.trim()) metadata.latitude = Number(latitude);
    if (longitude.trim()) metadata.longitude = Number(longitude);

    const formData = new FormData();
    files.forEach((file) => formData.append("files", file));
    formData.append("metadata", JSON.stringify(metadata));

    try {
      const acceptedJob = await request<Job>(endpoints.inferenceJobs.url, {
        method: endpoints.inferenceJobs.method,
        body: formData,
      });
      setJob(acceptedJob);
    } catch (submitError) {
      setError(
        submitError instanceof Error ? submitError.message : "Unable to submit images.",
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
      <header className="topbar">
        <a className="brand" href="#" aria-label="InfraScan home">
          <img className="brand-mark" src={infrascanLogo} alt="" />
          <span>InfraScan</span>
        </a>
        <span className="topbar-note">Road infrastructure inspection</span>
      </header>

      <section className="intro">
        <p className="eyebrow">IMAGE ANALYSIS</p>
        <h1>Inspect road conditions</h1>
        <p>Upload road images to detect and review infrastructure issues.</p>
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
              <h2>Upload images</h2>
            </div>
            <span className="step-number">01</span>
          </div>

          <form onSubmit={submitImages}>
            <label className="file-drop">
              <span className="upload-icon" aria-hidden="true">↑</span>
              <strong>Choose road images</strong>
              <span>PNG, JPG, JPEG, or WEBP · multiple files allowed</span>
              <input
                type="file"
                accept="image/*"
                multiple
                onChange={(event) => setFiles(Array.from(event.currentTarget.files ?? []))}
              />
            </label>

            {files.length > 0 && (
              <div className="selected-files" aria-live="polite">
                <strong>{files.length} image{files.length === 1 ? "" : "s"} selected</strong>
                <ul>
                  {files.map((file, index) => (
                    <li key={`${file.name}-${file.lastModified}-${index}`}>
                      <span>{file.name}</span>
                      <span>{(file.size / (1024 * 1024)).toFixed(2)} MB</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <details className="metadata-fields">
              <summary>Optional image details</summary>
              <div className="field-grid">
                <label>
                  Source ID
                  <input
                    value={sourceId}
                    onChange={(event) => setSourceId(event.target.value)}
                    placeholder="Camera or survey identifier"
                  />
                </label>
                <label>
                  Capture date and time
                  <input
                    type="datetime-local"
                    value={capturedAt}
                    onChange={(event) => setCapturedAt(event.target.value)}
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
                  />
                </label>
              </div>
            </details>

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
              Submit one or more images to start an analysis job. Its status will appear here.
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
