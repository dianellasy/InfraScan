import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { AppRoutes } from "./AppRoutes.tsx";

describe("AppRoutes login entry", () => {
  it("opens the upload page after both login fields are filled", async () => {
    const user = userEvent.setup();
    window.location.hash = "#/";
    render(<AppRoutes />);

    expect(screen.getByRole("heading", { name: "Welcome back!" })).toBeInTheDocument();

    await user.type(screen.getByLabelText("Email address"), "inspector@example.com");
    await user.type(screen.getByLabelText("Password"), "anything");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(
      screen.getByRole("heading", { name: "Inspect road conditions" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Welcome back!" })).not.toBeInTheDocument();
  });
});
