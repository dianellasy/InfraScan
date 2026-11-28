import { useEffect, useState } from "react";
import { apiUrl } from "../api/endpoints.ts";
import { requestBlob } from "../api/request.ts";

type EvidenceState =
  | { path: string; status: "loading" }
  | { path: string; status: "error" }
  | { path: string; status: "loaded"; objectUrl: string };

/**
 * Evidence requires the bearer token, which a plain `<img src>` cannot send, so
 * the image is fetched with the token and shown from a short-lived object URL.
 * The URL is revoked when the evidence changes or the component unmounts.
 */
function useEvidenceObjectUrl(path: string): EvidenceState {
  const [state, setState] = useState<EvidenceState>({ path, status: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    let objectUrl: string | null = null;
    requestBlob(apiUrl(path), { signal: controller.signal })
      .then((blob) => {
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setState({ path, status: "loaded", objectUrl });
      })
      .catch(() => {
        if (!controller.signal.aborted) setState({ path, status: "error" });
      });
    return () => {
      controller.abort();
      if (objectUrl !== null) URL.revokeObjectURL(objectUrl);
    };
  }, [path]);

  // Never show the previous evidence while the new one loads.
  return state.path === path ? state : { path, status: "loading" };
}

type EvidenceImageProps = {
  /** API-relative evidence path, e.g. `/api/v1/evidence/<media_id>`. */
  path: string;
  alt: string;
  className?: string;
};

export function EvidenceImage({ path, alt, className = "evidence-thumbnail" }: EvidenceImageProps) {
  const evidence = useEvidenceObjectUrl(path);
  if (evidence.status === "loaded") {
    return <img className={className} src={evidence.objectUrl} alt={alt} />;
  }
  return (
    <div className="evidence-placeholder" role="status">
      {evidence.status === "loading" ? "Loading evidence…" : "Evidence image unavailable"}
    </div>
  );
}
