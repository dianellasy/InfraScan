import App from "./App.tsx";
import { AuthGate } from "./auth/AuthGate.tsx";
import { IssueDetailPage } from "./issues/IssueDetailPage.tsx";
import { IssueMapPage } from "./issues/IssueMapPage.tsx";
import { ReviewQueuePage } from "./issues/ReviewQueuePage.tsx";
import { mapHref, useHashRoute } from "./routing.ts";
import { TopBar } from "./TopBar.tsx";
import "./App.css";

function SignedInRoutes() {
  const route = useHashRoute();

  // Keyed so a new submission, or returning to a job, starts from fresh state.
  if (route.name === "upload") return <App key={route.jobId ?? "new"} jobId={route.jobId} />;
  return (
    <main className="app-shell wide">
      <TopBar route={route.name} />
      {route.name === "map" && (
        <IssueMapPage
          key={mapHref(route.bounds, route.issueId)}
          initialBounds={route.bounds}
          focusIssueId={route.issueId}
        />
      )}
      {route.name === "review" && <ReviewQueuePage />}
      {route.name === "issue" && <IssueDetailPage key={route.issueId} issueId={route.issueId} />}
      <footer>InfraScan · Road damage detection</footer>
    </main>
  );
}

/** Every route, including job and Issue deep links, requires a signed-in user. */
export function AppRoutes() {
  return (
    <AuthGate>
      <SignedInRoutes />
    </AuthGate>
  );
}
