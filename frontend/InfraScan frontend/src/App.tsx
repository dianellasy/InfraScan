import { useEffect, useState } from "react";
import type { FormEvent } from "react";

import "./App.css";
import { apiBaseUrl } from "./api/endpoints.ts";
import {
  getJob,
  getJobDetections,
  submitInferenceJob,
} from "./api/inference.ts";
import type { Detection, InferenceJob } from "./api/inference.ts";

const POLL_INTERVAL_MS = 1500;
const TERMINAL_STATUSES = new Set(["completed", "failed"]);

function initialJobId(): string {
  return new URLSearchParams(window.location.search).get("job") ?? "";
}

function rememberJobId(jobId: string) {
  const url = new URL(window.location.href);
  url.searchParams.set("job", jobId);
  window.history.replaceState(null, "", url);
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener("abort", () => {
      clearTimeout(timer);
      resolve();
    });
  });
}

function describeLocation(detection: Detection): string {
  if (detection.latitude !== null && detection.longitude !== null) {
    return `${detection.latitude.toFixed(7)}, ${detection.longitude.toFixed(7)}`;
  }
  if (detection.location_status === null) {
    return "Not calculated";
  }
  return `No location (${detection.location_status.replaceAll("_", " ")})`;
}

function App() {
  const [jobId, setJobId] = useState(initialJobId);
  const [jobIdInput, setJobIdInput] = useState(initialJobId);
  const [job, setJob] = useState<InferenceJob | null>(null);
  const [detections, setDetections] = useState<Detection[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [latitude, setLatitude] = useState("33.8823");
  const [longitude, setLongitude] = useState("-117.8851");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!jobId) return;
    const controller = new AbortController();
    const { signal } = controller;

    (async () => {
      try {
        let current = await getJob(jobId, signal);
        setJob(current);
        while (!TERMINAL_STATUSES.has(current.status) && !signal.aborted) {
          await sleep(POLL_INTERVAL_MS, signal);
          if (signal.aborted) return;
          current = await getJob(jobId, signal);
          setJob(current);
        }
        if (current.status === "completed") {
          const page = await getJobDetections(jobId, signal);
          setDetections(page.items);
        }
      } catch (err) {
        if (!signal.aborted) {
          setError(err instanceof Error ? err.message : String(err));
        }
      }
    })();

    return () => controller.abort();
  }, [jobId]);

  const openJob = (id: string) => {
    setJob(null);
    setDetections([]);
    setError(null);
    setJobId(id);
    setJobIdInput(id);
    rememberJobId(id);
  };

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!file) return;
    setSubmitting(true);
    setError(null);
    try {
      const accepted = await submitInferenceJob(file, {
        captured_at: new Date().toISOString(),
        latitude: Number(latitude),
        longitude: Number(longitude),
      });
      openJob(accepted.job_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  const onOpen = (event: FormEvent) => {
    event.preventDefault();
    const id = jobIdInput.trim();
    if (id) openJob(id);
  };

  const geolocation = job?.geolocation ?? null;

  return (
    <main className="app">
      <h1>InfraScan</h1>

      <section className="panel">
        <form onSubmit={onSubmit} className="row">
          <label>
            JPEG image
            <input
              type="file"
              accept="image/jpeg"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </label>
          <label>
            Latitude
            <input value={latitude} onChange={(e) => setLatitude(e.target.value)} size={10} />
          </label>
          <label>
            Longitude
            <input value={longitude} onChange={(e) => setLongitude(e.target.value)} size={10} />
          </label>
          <button type="submit" disabled={!file || submitting}>
            {submitting ? "Submitting…" : "Submit for inference"}
          </button>
        </form>
        <p className="hint">
          While POC geolocation is enabled on the API, submitted GPS is ignored and every
          image is treated as captured at London Bridge.
        </p>
        <form onSubmit={onOpen} className="row">
          <label>
            Job ID
            <input
              value={jobIdInput}
              onChange={(e) => setJobIdInput(e.target.value)}
              size={38}
              placeholder="e.g. from the seed command"
            />
          </label>
          <button type="submit">Open job</button>
        </form>
      </section>

      {error && <p className="error">{error}</p>}

      {job && (
        <section className="panel">
          <h2>
            Job <code>{job.job_id}</code>: <span className={`status ${job.status}`}>{job.status}</span>
          </h2>
          {!TERMINAL_STATUSES.has(job.status) && <p>Waiting for the inference worker…</p>}
          {job.status === "failed" && <p className="error">Inference failed for this job.</p>}

          {geolocation?.assumed && (
            <div className="demo-banner">
              <strong>Demo geolocation (assumed, not measured).</strong> Source:{" "}
              {geolocation.source.name} ({geolocation.source.latitude.toFixed(6)},{" "}
              {geolocation.source.longitude.toFixed(6)}). {geolocation.note}
            </div>
          )}

          {job.annotated_image_url && (
            <img
              className="annotated"
              src={job.annotated_image_url}
              alt={`Annotated detections for job ${job.job_id}`}
            />
          )}

          {job.status === "completed" && (
            <>
              <h3>Detections ({detections.length})</h3>
              {detections.length === 0 ? (
                <p>No road damage detected.</p>
              ) : (
                <table>
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Classification</th>
                      <th>Confidence</th>
                      <th>Severity</th>
                      <th>GPS ({geolocation?.assumed ? "demo estimate" : "estimate"})</th>
                      <th>Distance</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detections.map((d, index) => (
                      <tr key={d.detection_id}>
                        <td>{index + 1}</td>
                        <td>{d.classification}</td>
                        <td>{(d.confidence * 100).toFixed(1)}%</td>
                        <td>{d.severity}</td>
                        <td title={d.location_details?.message ?? undefined}>
                          {describeLocation(d)}
                        </td>
                        <td>
                          {d.latitude !== null && d.location_details?.ground_distance_m != null
                            ? `${d.location_details.ground_distance_m.toFixed(1)} m`
                            : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </>
          )}
        </section>
      )}

      <footer className="hint">API: {apiBaseUrl || "same origin"}</footer>
    </main>
  );
}

export default App;
