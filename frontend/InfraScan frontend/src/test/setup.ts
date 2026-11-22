import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";
import { resetFakeSupabase } from "./supabaseMock.ts";

// Every test uses the in-memory Supabase client; none may reach the real service.
vi.mock("../auth/supabaseClient.ts", () => import("./supabaseMock.ts"));

beforeEach(() => {
  // jsdom implements neither; evidence images are shown from object URLs.
  let next = 0;
  URL.createObjectURL = vi.fn(() => `blob:infrascan-test/${++next}`);
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  cleanup();
  window.location.hash = "";
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  resetFakeSupabase();
});
