import type { Session } from "@supabase/supabase-js";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { AppRoutes } from "./AppRoutes.tsx";
import { authorizationFor, jsonResponse, stubFetch } from "./test/fixtures.ts";
import {
  VALID_PASSWORD,
  emitAuthChange,
  fakeAuth,
  fakeSession,
  setStoredSession,
} from "./test/supabaseMock.ts";

const completedJob = { job_id: "job-1", status: "completed", submitted_at: "2026-11-10T22:31:00Z" };

function jobServer() {
  return stubFetch((url) => {
    switch (url.pathname) {
      case "/api/v1/jobs/job-1":
        return jsonResponse(completedJob);
      case "/api/v1/jobs/job-1/detections":
        return jsonResponse({ items: [], total: 0, page: 1, page_size: 100 });
      case "/api/v1/issues":
        return jsonResponse({ items: [], total: 0, page: 1, page_size: 100 });
      default:
        throw new Error(`Unexpected request ${url}`);
    }
  });
}

async function signIn(password = VALID_PASSWORD) {
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText("Email address"), "citizen.a@example.test");
  await user.type(screen.getByLabelText("Password"), password);
  await user.click(screen.getByRole("button", { name: "Sign in" }));
  return user;
}

describe("authentication gate", () => {
  it("shows only the sign-in page to an unauthenticated user", async () => {
    const fetchMock = stubFetch(() => jsonResponse({}));
    window.location.hash = "#/map";
    render(<AppRoutes />);

    expect(await screen.findByRole("heading", { name: "Welcome back!" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Main" })).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("signs in with email and password and renders the application", async () => {
    stubFetch(() => jsonResponse({}));
    window.location.hash = "#/";
    render(<AppRoutes />);

    await signIn();

    expect(
      await screen.findByRole("heading", { name: "Inspect road conditions" }),
    ).toBeInTheDocument();
    expect(fakeAuth.signInWithPassword).toHaveBeenCalledWith({
      email: "citizen.a@example.test",
      password: VALID_PASSWORD,
    });
    expect(screen.getByText("citizen.a@example.test")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  it("reports rejected credentials without signing in", async () => {
    window.location.hash = "#/";
    render(<AppRoutes />);

    await signIn("wrong-password");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The email address or password is incorrect.",
    );
    expect(screen.queryByRole("heading", { name: "Inspect road conditions" })).not.toBeInTheDocument();
  });

  it("restores a stored session on reload after a loading state", async () => {
    let finishRestore: (value: { data: { session: Session }; error: null }) => void = () => {};
    fakeAuth.getSession.mockImplementationOnce(
      () => new Promise((resolve) => (finishRestore = resolve)),
    );
    const session = fakeSession({ role: "city_engineer" });
    setStoredSession(session);
    jobServer();
    window.location.hash = "#/jobs/job-1";
    render(<AppRoutes />);

    expect(screen.getByRole("status")).toHaveTextContent("Restoring your session…");
    expect(screen.queryByRole("heading", { name: "Welcome back!" })).not.toBeInTheDocument();

    await act(async () => finishRestore({ data: { session }, error: null }));

    expect(await screen.findByText("job-1")).toBeInTheDocument();
    expect(screen.getByText("City engineer")).toBeInTheDocument();
    expect(fakeAuth.signInWithPassword).not.toHaveBeenCalled();
  });

  it("keeps a job deep link through sign-in", async () => {
    jobServer();
    window.location.hash = "#/jobs/job-1";
    render(<AppRoutes />);

    await signIn();

    expect(await screen.findByText("job-1")).toBeInTheDocument();
    expect(window.location.hash).toBe("#/jobs/job-1");
  });

  it("sends the current access token with API requests", async () => {
    setStoredSession(fakeSession({ accessToken: "token-a" }));
    const fetchMock = jobServer();
    window.location.hash = "#/jobs/job-1";
    render(<AppRoutes />);

    await screen.findByText("This job did not create or update any issues.");
    expect(authorizationFor(fetchMock, "/api/v1/jobs/job-1")).toEqual(["Bearer token-a"]);
    expect(authorizationFor(fetchMock, "/api/v1/jobs/job-1/detections")).toEqual([
      "Bearer token-a",
    ]);
    expect(authorizationFor(fetchMock, "/api/v1/issues")).toEqual(["Bearer token-a"]);
  });

  it("uses the refreshed token after supabase-js rotates it", async () => {
    const original = fakeSession({ accessToken: "token-a" });
    setStoredSession(original);
    const fetchMock = stubFetch(() => jsonResponse({ items: [], total: 0, page: 1, page_size: 50 }));
    window.location.hash = "#/review";
    render(<AppRoutes />);
    await waitFor(() =>
      expect(authorizationFor(fetchMock, "/api/v1/issues/review-queue")).toEqual(["Bearer token-a"]),
    );

    await act(async () =>
      emitAuthChange("TOKEN_REFRESHED", { ...original, access_token: "token-b" }),
    );
    await act(async () => {
      window.location.hash = "#/";
    });
    await screen.findByRole("heading", { name: "Inspect road conditions" });
    await act(async () => {
      window.location.hash = "#/review";
    });

    await waitFor(() =>
      expect(authorizationFor(fetchMock, "/api/v1/issues/review-queue").at(-1)).toBe(
        "Bearer token-b",
      ),
    );
  });

  it("signs out back to the sign-in page", async () => {
    setStoredSession(fakeSession());
    jobServer();
    window.location.hash = "#/jobs/job-1";
    render(<AppRoutes />);
    await screen.findByText("job-1");

    await userEvent.setup().click(screen.getByRole("button", { name: "Sign out" }));

    expect(await screen.findByRole("heading", { name: "Welcome back!" })).toBeInTheDocument();
    expect(fakeAuth.signOut).toHaveBeenCalled();
    expect(window.location.hash).toBe("#/");
    // The stored session is gone, so a reload does not restore it.
    expect((await fakeAuth.getSession()).data.session).toBeNull();
  });

  it("falls back to a local sign-out when revoking the session fails", async () => {
    setStoredSession(fakeSession());
    jobServer();
    fakeAuth.signOut.mockResolvedValueOnce({ error: { message: "network down" } } as never);
    window.location.hash = "#/jobs/job-1";
    render(<AppRoutes />);
    await screen.findByText("job-1");

    await userEvent.setup().click(screen.getByRole("button", { name: "Sign out" }));

    await waitFor(() => expect(fakeAuth.signOut).toHaveBeenLastCalledWith({ scope: "local" }));
    expect(screen.getByRole("heading", { name: "Welcome back!" })).toBeInTheDocument();
  });

  it("asks the user to sign in again when the API rejects the session", async () => {
    setStoredSession(fakeSession());
    stubFetch(() =>
      jsonResponse(
        { error: { code: "INVALID_TOKEN", message: "The access token is invalid or expired." } },
        401,
      ),
    );
    window.location.hash = "#/jobs/job-1";
    render(<AppRoutes />);

    expect(await screen.findByRole("heading", { name: "Welcome back!" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Your session has expired");
    expect(fakeAuth.signOut).toHaveBeenCalledWith({ scope: "local" });
    // The deep link survives, so signing in again returns to the job.
    expect(window.location.hash).toBe("#/jobs/job-1");
  });
});
