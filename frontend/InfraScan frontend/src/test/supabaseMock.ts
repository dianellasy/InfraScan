import type { AuthChangeEvent, Session, User } from "@supabase/supabase-js";
import { vi } from "vitest";

/**
 * In-memory stand-in for `src/auth/supabaseClient.ts` (mocked in `setup.ts`), so no
 * test contacts Supabase. `setStoredSession` simulates a session restored from storage.
 */
type Listener = (event: AuthChangeEvent, session: Session | null) => void;

let storedSession: Session | null = null;
const listeners = new Set<Listener>();

export const VALID_PASSWORD = "correct-horse-battery";

export function fakeSession({
  id = "user-a",
  email = "citizen.a@example.test",
  accessToken = `access-token-${id}`,
  role,
}: { id?: string; email?: string; accessToken?: string; role?: string } = {}): Session {
  const user = {
    id,
    email,
    aud: "authenticated",
    role: "authenticated",
    created_at: "2026-11-01T00:00:00Z",
    app_metadata: role ? { provider: "email", role } : { provider: "email" },
    user_metadata: {},
  } as User;
  return {
    access_token: accessToken,
    refresh_token: `refresh-${id}`,
    token_type: "bearer",
    expires_in: 3600,
    expires_at: Math.floor(Date.now() / 1000) + 3600,
    user,
  };
}

function emit(event: AuthChangeEvent, session: Session | null) {
  storedSession = session;
  for (const listener of listeners) listener(event, session);
}

const defaults = {
  getSession: async () => ({ data: { session: storedSession }, error: null }),
  onAuthStateChange: (listener: Listener) => {
    listeners.add(listener);
    return { data: { subscription: { unsubscribe: () => listeners.delete(listener) } } };
  },
  signInWithPassword: async ({ email, password }: { email: string; password: string }) => {
    if (password !== VALID_PASSWORD) {
      return {
        data: { session: null, user: null },
        error: { message: "Invalid login credentials", code: "invalid_credentials", status: 400 },
      };
    }
    const session = fakeSession({ email });
    emit("SIGNED_IN", session);
    return { data: { session, user: session.user }, error: null };
  },
  signOut: async () => {
    emit("SIGNED_OUT", null);
    return { error: null };
  },
};

export const fakeAuth = {
  getSession: vi.fn(defaults.getSession),
  onAuthStateChange: vi.fn(defaults.onAuthStateChange),
  signInWithPassword: vi.fn(defaults.signInWithPassword),
  signOut: vi.fn(defaults.signOut),
};

export const supabase = { auth: fakeAuth };
export const supabaseConfigError: string | null = null;

export function setStoredSession(session: Session | null) {
  storedSession = session;
}

/** Simulates supabase-js replacing the session, e.g. after a token refresh. */
export function emitAuthChange(event: AuthChangeEvent, session: Session | null) {
  emit(event, session);
}

export function resetFakeSupabase() {
  storedSession = null;
  listeners.clear();
  for (const [name, implementation] of Object.entries(defaults)) {
    const mock = fakeAuth[name as keyof typeof fakeAuth] as ReturnType<typeof vi.fn>;
    mock.mockReset();
    mock.mockImplementation(implementation);
  }
}
