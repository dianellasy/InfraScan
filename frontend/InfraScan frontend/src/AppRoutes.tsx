import App from "./App.tsx";
import { IssueDetailPage } from "./issues/IssueDetailPage.tsx";
import { IssueMapPage } from "./issues/IssueMapPage.tsx";
import { ReviewQueuePage } from "./issues/ReviewQueuePage.tsx";
import { useHashRoute } from "./routing.ts";
import { TopBar } from "./TopBar.tsx";
import "./App.css";

export function AppRoutes() {
  const route = useHashRoute();
  if (route.name === "upload") return <App />;
  return (
    <main className="app-shell wide">
      <TopBar route={route.name} />
      {route.name === "map" && <IssueMapPage />}
      {route.name === "review" && <ReviewQueuePage />}
      {route.name === "issue" && <IssueDetailPage key={route.issueId} issueId={route.issueId} />}
      <footer>InfraScan · Road damage detection</footer>
    </main>
  );
}
