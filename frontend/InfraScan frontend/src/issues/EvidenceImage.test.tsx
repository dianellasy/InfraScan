import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { apiUrl } from "../api/endpoints.ts";
import {
  authorizationFor,
  imageResponse,
  jsonResponse,
  requestedUrls,
  stubFetch,
} from "../test/fixtures.ts";
import { fakeSession, setStoredSession } from "../test/supabaseMock.ts";
import { EvidenceImage } from "./EvidenceImage.tsx";

describe("EvidenceImage", () => {
  it("fetches protected evidence with the bearer token and shows it from an object URL", async () => {
    setStoredSession(fakeSession({ accessToken: "token-a" }));
    const fetchMock = stubFetch(() => imageResponse());
    render(<EvidenceImage path="/api/v1/evidence/evidence-1" alt="Evidence" />);

    expect(screen.getByRole("status")).toHaveTextContent("Loading evidence…");
    const image = await screen.findByRole("img", { name: "Evidence" });

    expect(image.getAttribute("src")).toMatch(/^blob:/);
    expect(image.getAttribute("src")).not.toContain("/api/v1/evidence");
    expect(requestedUrls(fetchMock).map(String)).toEqual([apiUrl("/api/v1/evidence/evidence-1")]);
    expect(authorizationFor(fetchMock, "/api/v1/evidence/evidence-1")).toEqual(["Bearer token-a"]);
    // The token never travels in the URL.
    expect(requestedUrls(fetchMock)[0].search).toBe("");
    expect(URL.createObjectURL).toHaveBeenCalledOnce();
  });

  it("revokes the object URL when the evidence changes and on unmount", async () => {
    setStoredSession(fakeSession());
    stubFetch(() => imageResponse());
    const { rerender, unmount } = render(
      <EvidenceImage path="/api/v1/evidence/evidence-1" alt="Evidence" />,
    );
    const first = (await screen.findByRole("img")).getAttribute("src");

    rerender(<EvidenceImage path="/api/v1/evidence/evidence-2" alt="Evidence" />);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith(first);
    const second = await screen.findByRole("img");
    expect(second.getAttribute("src")).not.toBe(first);

    unmount();
    expect(URL.revokeObjectURL).toHaveBeenLastCalledWith(second.getAttribute("src"));
    expect(URL.revokeObjectURL).toHaveBeenCalledTimes(2);
  });

  it("shows a placeholder, not a broken image, when evidence is not accessible", async () => {
    setStoredSession(fakeSession());
    stubFetch(() =>
      jsonResponse(
        { error: { code: "EVIDENCE_NOT_FOUND", message: "The evidence image was not found." } },
        404,
      ),
    );
    render(<EvidenceImage path="/api/v1/evidence/someone-elses" alt="Evidence" />);

    expect(await screen.findByText("Evidence image unavailable")).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });
});
