import { Fragment, useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import type { Session } from "@supabase/supabase-js";
import { Login } from "../pages/Login.tsx";
import { AuthContext, type AuthContextValue } from "./AuthContext.ts";
import { onUnauthorized } from "./session.ts";
import { supabase, supabaseConfigError } from "./supabaseClient.ts";
import "./auth.css";

type GateState =
  | { status: "restoring" }
  | { status: "signed-out"; notice: string | null }
  | { status: "signed-in"; session: Session };

const SESSION_EXPIRED = "Your session has expired. Sign in again to continue.";

function signInErrorMessage(error: { message?: string; code?: string; status?: number }): string {
  if (error.code === "invalid_credentials" || error.status === 400) {
    return "The email address or password is incorrect.";
  }
  if (error.code === "email_not_confirmed") {
    return "Confirm your email address before signing in.";
  }
  return error.message || "Unable to sign in. Try again.";
}

/** Renders `children` only for a signed-in Supabase user; otherwise the sign-in page. */
export function AuthGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<GateState>(() =>
    supabase ? { status: "restoring" } : { status: "signed-out", notice: null },
  );
  const [signingIn, setSigningIn] = useState(false);
  const [signInError, setSignInError] = useState<string | null>(null);

  useEffect(() => {
    if (!supabase) return;
    let active = true;
    const apply = (session: Session | null) => {
      if (!active) return;
      setState((current) => {
        if (session) return { status: "signed-in", session };
        return current.status === "signed-out" ? current : { status: "signed-out", notice: null };
      });
    };

    void supabase.auth.getSession().then(({ data }) => apply(data.session));
    // Only records state: calling Supabase from inside this callback can deadlock.
    const { data } = supabase.auth.onAuthStateChange((_event, session) => apply(session));
    const stopListening = onUnauthorized(() => {
      if (!active) return;
      setState({ status: "signed-out", notice: SESSION_EXPIRED });
      void supabase?.auth.signOut({ scope: "local" });
    });
    return () => {
      active = false;
      data.subscription.unsubscribe();
      stopListening();
    };
  }, []);

  const signIn = useCallback(async (email: string, password: string) => {
    if (!supabase) return;
    setSigningIn(true);
    setSignInError(null);
    try {
      const { data, error } = await supabase.auth.signInWithPassword({ email, password });
      if (error) {
        setSignInError(signInErrorMessage(error));
      } else if (data.session) {
        setState({ status: "signed-in", session: data.session });
      }
    } catch {
      setSignInError("Unable to reach the sign-in service. Check your connection and try again.");
    } finally {
      setSigningIn(false);
    }
  }, []);

  const signOut = useCallback(async () => {
    // A job or Issue link from this session should not carry over to the next user.
    window.location.hash = "#/";
    setState({ status: "signed-out", notice: null });
    if (!supabase) return;
    try {
      // A failed server-side revocation would otherwise leave the session stored.
      const { error } = await supabase.auth.signOut();
      if (error) await supabase.auth.signOut({ scope: "local" });
    } catch {
      await supabase.auth.signOut({ scope: "local" });
    }
  }, []);

  const session = state.status === "signed-in" ? state.session : null;
  const context = useMemo<AuthContextValue | null>(
    () => (session ? { user: session.user, signOut } : null),
    [session, signOut],
  );

  if (supabaseConfigError !== null) {
    return (
      <main className="auth-status" role="alert">
        <h1>Sign-in is not configured</h1>
        <p>{supabaseConfigError}</p>
      </main>
    );
  }
  if (state.status === "restoring") {
    return (
      <main className="auth-status" aria-busy="true">
        <p role="status">Restoring your session…</p>
      </main>
    );
  }
  if (context === null) {
    const notice = state.status === "signed-out" ? state.notice : null;
    return <Login onSignIn={signIn} loading={signingIn} error={signInError ?? notice} />;
  }
  return (
    <AuthContext.Provider value={context}>
      {/* Keyed so switching accounts never shows the previous user's page state. */}
      <Fragment key={context.user.id}>{children}</Fragment>
    </AuthContext.Provider>
  );
}

export default AuthGate;
