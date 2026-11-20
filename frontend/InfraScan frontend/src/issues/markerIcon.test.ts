import { describe, expect, it } from "vitest";
import { summary } from "../test/fixtures.ts";
import { SEVERITIES } from "./constants.ts";
import { createIssueIcon, issueMarkerHtml } from "./markerIcon.ts";

function parse(html: string): HTMLElement {
  const container = document.createElement("div");
  container.innerHTML = html;
  return container.firstElementChild as HTMLElement;
}

describe("issue marker icons", () => {
  it.each([
    ["pothole", "circle"],
    ["alligator_crack", "square"],
    ["longitudinal_crack", "diamond"],
    ["transverse_crack", "triangle"],
  ] as const)("uses a distinct shape for %s", (code, shape) => {
    const marker = parse(issueMarkerHtml(summary({ issue_type_code: code }), false));
    expect(marker).toHaveClass(`type-${shape}`);
  });

  it.each(SEVERITIES.map((s) => [s.value, s.color] as const))(
    "fills %s markers with the severity color",
    (severity, color) => {
      const marker = parse(issueMarkerHtml(summary({ severity }), false));
      expect(marker.querySelector("g")).toHaveAttribute("fill", color);
    },
  );

  it("adds a review indicator only to Issues that need review", () => {
    const review = parse(
      issueMarkerHtml(summary({ needs_review: true, review_reason: "type_conflict" }), false),
    );
    const plain = parse(issueMarkerHtml(summary(), false));
    expect(review.querySelector("[data-review-indicator]")).not.toBeNull();
    expect(plain.querySelector("[data-review-indicator]")).toBeNull();
  });

  it("highlights the selected marker", () => {
    expect(parse(issueMarkerHtml(summary(), true))).toHaveClass("is-selected");
    expect(parse(issueMarkerHtml(summary(), false))).not.toHaveClass("is-selected");
  });

  it("hides decorative glyphs so the marker title is its accessible name", () => {
    const marker = parse(issueMarkerHtml(summary({ needs_review: true }), false));
    expect(marker).toHaveAttribute("aria-hidden", "true");
  });

  it("anchors the icon at its center", () => {
    const icon = createIssueIcon(summary(), false);
    expect(icon.options.iconAnchor).toEqual([15, 15]);
  });
});
