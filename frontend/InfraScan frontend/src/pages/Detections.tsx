
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import infrascanLogo from "../assets/infrascan.svg";
import "../App.css";
import "./Detections.css";

export type Detection = {
  id: string;
  classification: string;
  location: string;
  source: string;
  confidence: number;
  severity: "High" | "Medium" | "Low";
  status: "Unreviewed" | "Validated" | "Rejected";
  detectedAt: string;
};

type DetectionsProps = {
  detections?: Detection[];
  activeTickets?: number;
  onReview?: (id: string) => void;
};

const PAGE_SIZE = 5;

export default function Detections({
  detections = [],
  activeTickets = 0,
  onReview,
}: DetectionsProps) {
  const navigate = useNavigate();

  const [search, setSearch] = useState("");
  const [classification, setClassification] = useState("All");
  const [severity, setSeverity] = useState("All");
  const [status, setStatus] = useState("All");
  const [source, setSource] = useState("All");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [page, setPage] = useState(1);

  const classifications = useMemo(
    () => [...new Set(detections.map((d) => d.classification))],
    [detections]
  );

  const sources = useMemo(
    () => [...new Set(detections.map((d) => d.source))],
    [detections]
  );

  const filteredDetections = useMemo(() => {
    const query = search.toLowerCase().trim();

    return detections.filter((d) => {
      const matchesSearch = [
        d.id,
        d.classification,
        d.location,
        d.source,
      ].some((value) => value.toLowerCase().includes(query));

      return (
        matchesSearch &&
        (classification === "All" ||
          d.classification === classification) &&
        (severity === "All" || d.severity === severity) &&
        (status === "All" || d.status === status) &&
        (source === "All" || d.source === source)
      );
    });
  }, [detections, search, classification, severity, status, source]);

  useEffect(() => {
    setPage(1);
  }, [search, classification, severity, status, source]);

  const totalPages = Math.max(
    1,
    Math.ceil(filteredDetections.length / PAGE_SIZE)
  );

  const currentPage = Math.min(page, totalPages);

  const visibleDetections = filteredDetections.slice(
    (currentPage - 1) * PAGE_SIZE,
    currentPage * PAGE_SIZE
  );

  const selectedDetection =
    detections.find((d) => d.id === selectedId) ??
    filteredDetections[0] ??
    null;

  const resetFilters = () => {
    setSearch("");
    setClassification("All");
    setSeverity("All");
    setStatus("All");
    setSource("All");
    setPage(1);
  };

  const today = new Date().toDateString();

  const detectionsToday = detections.filter(
    (d) => new Date(d.detectedAt).toDateString() === today
  ).length;

  const awaitingValidation = detections.filter(
    (d) => d.status === "Unreviewed"
  ).length;

  const highSeverity = detections.filter(
    (d) => d.severity === "High" && d.status !== "Rejected"
  ).length;

  const stats = [
    {
      label: "Detections Today",
      value: detectionsToday,
      description: "Detected road issues today",
    },
    {
      label: "Awaiting Validation",
      value: awaitingValidation,
      description: "Unreviewed detections",
    },
    {
      label: "High-Severity Issues",
      value: highSeverity,
      description: "High-priority road issues",
    },
    {
      label: "Active Tickets",
      value: activeTickets,
      description: "Open and in-progress tickets",
    },
  ];

  const hasDetections = detections.length > 0;

  return (
    <div className="detections-page">
      {/* InfraScan header */}
      <header className="topbar">
        <a
          className="brand"
          href="/overview"
          aria-label="InfraScan home"
        >
          <img
            className="brand-mark"
            src={infrascanLogo}
            alt=""
          />
          <span>InfraScan</span>
        </a>

        <span className="topbar-note">
          Road infrastructure inspection
        </span>
      </header>

      {/* Page navigation */}
      <nav
        className="page-navigation"
        aria-label="Page navigation"
      >
        <button
          type="button"
          className="navigation-button"
          onClick={() => navigate(-1)}
        >
          <span aria-hidden="true">←</span>
          Back
        </button>

        <button
          type="button"
          className="navigation-button"
          onClick={() => navigate("/overview")}
        >
          <span aria-hidden="true">→</span>
          Overview
        </button>

        <button
          type="button"
          className="navigation-button"
          onClick={() => navigate("/upload")}
        >
          <span aria-hidden="true">→</span>
          Upload
        </button>
      </nav>

      {/* Page heading */}
      <div className="detections-header">
        <div>
          <p className="eyebrow">ROAD ISSUE MONITORING</p>
          <h1>Detections</h1>
          <p>
            Search model-generated issues, inspect evidence,
            and prioritize human review.
          </p>
        </div>
      </div>

      {/* Data notice */}
      <div className="demo-notice">
        {hasDetections
          ? "Review detected road issues below. Validation decisions require human review."
          : "No detection records are currently loaded. Results will appear here once analysis data is connected."}
      </div>

      {/* Summary cards */}
      <div className="detections-stats">
        {stats.map((stat) => (
          <div className="stat-card" key={stat.label}>
            <p>{stat.label}</p>
            <h2>{stat.value}</h2>
            <span>{stat.description}</span>
          </div>
        ))}
      </div>

      {/* Detection register and preview */}
      <div className="detections-layout">
        <section className="detection-register">
          <div className="register-heading">
            <div>
              <h2>Detection register</h2>
              <p>Browse and filter detected road issues.</p>
            </div>
            <span>{filteredDetections.length} records</span>
          </div>

          {/* Review status tabs */}
          <div className="detection-tabs">
            <button
              type="button"
              className={status === "All" ? "active" : ""}
              onClick={() => setStatus("All")}
            >
              All detections
            </button>

            <button
              type="button"
              className={status === "Unreviewed" ? "active" : ""}
              onClick={() => setStatus("Unreviewed")}
            >
              Awaiting validation
            </button>

            <button
              type="button"
              className={status === "Validated" ? "active" : ""}
              onClick={() => setStatus("Validated")}
            >
              Validated
            </button>

            <button
              type="button"
              className={status === "Rejected" ? "active" : ""}
              onClick={() => setStatus("Rejected")}
            >
              Rejected
            </button>
          </div>

          {/* Search and filters */}
          <div className="detection-controls">
            <input
              type="search"
              placeholder="Search detection ID, street, or camera source"
              aria-label="Search detections"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />

            <div className="detection-filters">
              <select
                aria-label="Filter by classification"
                value={classification}
                onChange={(e) => setClassification(e.target.value)}
              >
                <option value="All">All classifications</option>
                {classifications.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>

              <select
                aria-label="Filter by severity"
                value={severity}
                onChange={(e) => setSeverity(e.target.value)}
              >
                <option value="All">All severities</option>
                <option value="High">High</option>
                <option value="Medium">Medium</option>
                <option value="Low">Low</option>
              </select>

              <select
                aria-label="Filter by review status"
                value={status}
                onChange={(e) => setStatus(e.target.value)}
              >
                <option value="All">All review states</option>
                <option value="Unreviewed">Unreviewed</option>
                <option value="Validated">Validated</option>
                <option value="Rejected">Rejected</option>
              </select>

              <select
                aria-label="Filter by source"
                value={source}
                onChange={(e) => setSource(e.target.value)}
              >
                <option value="All">All sources</option>
                {sources.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>

              <button
                type="button"
                className="reset-filters"
                onClick={resetFilters}
              >
                Reset
              </button>
            </div>
          </div>

          {/* Detection table */}
          <div className="detection-table-wrapper">
            <table className="detection-table">
              <thead>
                <tr>
                  <th>Detection</th>
                  <th>Classification</th>
                  <th>Location / Source</th>
                  <th>Conf.</th>
                  <th>Severity</th>
                  <th>Validation</th>
                  <th>Action</th>
                </tr>
              </thead>

              <tbody>
                {visibleDetections.map((d) => (
                  <tr
                    key={d.id}
                    className={
                      selectedDetection?.id === d.id
                        ? "selected-row"
                        : ""
                    }
                    onClick={() => setSelectedId(d.id)}
                  >
                    <td>
                      <strong>{d.id}</strong>
                      <small>{d.detectedAt}</small>
                    </td>

                    <td>{d.classification}</td>

                    <td>
                      {d.location}
                      <small>{d.source}</small>
                    </td>

                    <td>{d.confidence.toFixed(1)}%</td>

                    <td>
                      <span
                        className={`detection-severity ${d.severity.toLowerCase()}`}
                      >
                        {d.severity}
                      </span>
                    </td>

                    <td>
                      <span
                        className={`detection-status ${d.status.toLowerCase()}`}
                      >
                        {d.status}
                      </span>
                    </td>

                    <td>
                      <button
                        type="button"
                        className="table-review-button"
                        disabled={!onReview}
                        onClick={(e) => {
                          e.stopPropagation();
                          setSelectedId(d.id);
                          onReview?.(d.id);
                        }}
                      >
                        Review
                      </button>
                    </td>
                  </tr>
                ))}

                {visibleDetections.length === 0 && (
                  <tr>
                    <td colSpan={7} className="no-detections">
                      <div className="detections-empty-state">
                        <h3>
                          {!hasDetections
                            ? "No detections yet"
                            : "No matching detections"}
                        </h3>

                        <p>
                          {!hasDetections
                            ? "Detected road issues will appear here once an analysis has been completed and its results are loaded."
                            : "Try adjusting your search or filters."}
                        </p>
                      </div>
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          <div className="detection-pagination">
            <span>
              Showing{" "}
              {filteredDetections.length === 0
                ? 0
                : (currentPage - 1) * PAGE_SIZE + 1}
              {"–"}
              {Math.min(
                currentPage * PAGE_SIZE,
                filteredDetections.length
              )}{" "}
              of {filteredDetections.length}
            </span>

            <div>
              <button
                type="button"
                disabled={currentPage === 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              >
                Previous
              </button>

              <span>
                {currentPage} of {totalPages}
              </span>

              <button
                type="button"
                disabled={currentPage === totalPages}
                onClick={() =>
                  setPage((p) => Math.min(totalPages, p + 1))
                }
              >
                Next
              </button>
            </div>
          </div>
        </section>

        {/* Selected detection preview */}
        {hasDetections && selectedDetection && (
          <aside className="detection-preview">
            <div className="preview-heading">
              <h2>Selected detection</h2>
              <span>{selectedDetection.id}</span>
            </div>

            <div className="preview-image">
              <span>Evidence preview</span>
              <p>Evidence image not connected yet</p>
            </div>

            <div className="preview-details">
              <span
                className={`detection-status ${selectedDetection.status.toLowerCase()}`}
              >
                {selectedDetection.status}
              </span>

              <h3>{selectedDetection.classification}</h3>

              <p>
                <strong>Model confidence</strong>
                {selectedDetection.confidence.toFixed(1)}%
              </p>

              <p>
                <strong>Severity</strong>
                {selectedDetection.severity}
              </p>

              <p>
                <strong>Detection location</strong>
                {selectedDetection.location}
              </p>

              <p>
                <strong>Camera source</strong>
                {selectedDetection.source}
              </p>

              <p>
                <strong>Detected at</strong>
                {selectedDetection.detectedAt}
              </p>
            </div>

            <button
              type="button"
              className="open-review-button"
              disabled={!onReview}
              onClick={() => onReview?.(selectedDetection.id)}
            >
              Review detection
            </button>

            {!onReview && (
              <p className="preview-note">
                Detection Review navigation will be connected next.
              </p>
            )}
          </aside>
        )}
      </div>

      {/* Footer note */}
      <div className="detection-footer-note">
        Confidence supports review. Severity determines repair priority.
        Only validated issues should become maintenance tickets.
      </div>
    </div>
  );
}
