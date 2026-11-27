import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Login } from "./Login.tsx";

describe("Login", () => {
  it("passes the entered credentials to the sign-in extension point", async () => {
    const user = userEvent.setup();
    const onSignIn = vi.fn();
    render(<Login onSignIn={onSignIn} />);

    await user.type(screen.getByLabelText("Email address"), "inspector@example.com");
    await user.type(screen.getByLabelText("Password"), "secret");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(onSignIn).toHaveBeenCalledOnce();
    expect(onSignIn).toHaveBeenCalledWith("inspector@example.com", "secret");
  });

  it("exposes loading and error states accessibly", () => {
    const onSignIn = vi.fn();
    render(<Login onSignIn={onSignIn} loading error="Unable to sign in." />);

    expect(screen.getByRole("alert")).toHaveTextContent("Unable to sign in.");
    expect(screen.getByRole("form")).toHaveAttribute("aria-busy", "true");
    expect(screen.getByLabelText("Email address")).toBeDisabled();
    expect(screen.getByLabelText("Password")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Signing in…" })).toBeDisabled();
  });
});
