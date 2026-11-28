import L from "leaflet";
import { issueTypeInfo, severityInfo, type MarkerShape } from "./constants.ts";
import type { IssueSummary } from "./types.ts";

export const MARKER_SIZE = 30;

const SHAPE_SVG: Record<MarkerShape, string> = {
  circle: '<circle cx="15" cy="15" r="12" />',
  square: '<rect x="3.5" y="3.5" width="23" height="23" rx="3" />',
  diamond: '<polygon points="15,1.5 28.5,15 15,28.5 1.5,15" />',
  triangle: '<polygon points="15,2 28.5,27 1.5,27" />',
};

/** Glyph baseline per shape, lowered for the triangle's wider base. */
const GLYPH_Y: Record<MarkerShape, number> = { circle: 19, square: 19, diamond: 19, triangle: 23 };

export type MarkerSymbolOptions = {
  shape: MarkerShape;
  fill: string;
  textColor: string;
  glyph: string;
  selected?: boolean;
};

export function markerSymbolSvg({ shape, fill, textColor, glyph, selected }: MarkerSymbolOptions) {
  const stroke = selected ? "#0b1f3a" : "#ffffff";
  const strokeWidth = selected ? 3 : 2;
  return (
    `<svg viewBox="0 0 30 30" width="${MARKER_SIZE}" height="${MARKER_SIZE}" aria-hidden="true">` +
    `<g fill="${fill}" stroke="${stroke}" stroke-width="${strokeWidth}">${SHAPE_SVG[shape]}</g>` +
    `<text x="15" y="${GLYPH_Y[shape]}" text-anchor="middle" font-size="11" font-weight="700" ` +
    `fill="${textColor}" font-family="system-ui, sans-serif">${glyph}</text></svg>`
  );
}

export function issueMarkerHtml(issue: IssueSummary, selected: boolean): string {
  const type = issueTypeInfo(issue.issue_type_code);
  const severity = severityInfo(issue.severity);
  const classes = ["issue-marker", `severity-${issue.severity}`, `type-${type.shape}`];
  if (selected) classes.push("is-selected");
  if (issue.needs_review) classes.push("needs-review");
  const badge = issue.needs_review
    ? '<span class="issue-marker-review" data-review-indicator="true">!</span>'
    : "";
  return (
    `<div class="${classes.join(" ")}" aria-hidden="true">` +
    markerSymbolSvg({
      shape: type.shape,
      fill: severity.color,
      textColor: severity.textColor,
      glyph: type.glyph,
      selected,
    }) +
    `${badge}</div>`
  );
}

export function issueMarkerTitle(issue: IssueSummary): string {
  const parts = [
    issueTypeInfo(issue.issue_type_code).label,
    `${severityInfo(issue.severity).label} severity`,
  ];
  if (issue.needs_review) parts.push("needs review");
  return parts.join(", ");
}

export function createIssueIcon(issue: IssueSummary, selected: boolean): L.DivIcon {
  return L.divIcon({
    className: "issue-marker-icon",
    html: issueMarkerHtml(issue, selected),
    iconSize: [MARKER_SIZE, MARKER_SIZE],
    iconAnchor: [MARKER_SIZE / 2, MARKER_SIZE / 2],
  });
}
