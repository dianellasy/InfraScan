import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { isSupabaseConfigured, supabase } from "../supabase";
import type { Session } from "@supabase/supabase-js";

type AuthGateProps = {
  children: ReactNode;
};

export default function AuthGate({ children }: AuthGateProps) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  const [mode, setMode] = useState<"sign-in" | "sign-up">("sign-in");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!supabase) {
      setLoading(false);
      return;
    }

    void supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setLoading(false);
    });
    const { data } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      setSession(nextSession);
    });
    return () => data.subscription.unsubscribe();
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!supabase) return;
    setLoading(true);
    setError("");

    try {
      if (mode === "sign-up") {
        const { error: signUpError } = await supabase.auth.signUp({
          email,
          password,
          options: {
            data: { role: "citizen" },
          },
        });
        if (signUpError) throw signUpError;
      } else {
        const { error: signInError } = await supabase.auth.signInWithPassword({
          email,
          password,
        });
        if (signInError) throw signInError;
      }
    } catch (authError) {
      setError(
        authError instanceof Error
          ? authError.message
          : "Unable to authenticate.",
      );
    } finally {
      setLoading(false);
    }
  }

  if (!isSupabaseConfigured) {
    return (
      <main className="auth-shell">
        <section className="auth-card">
          <p className="eyebrow">AUTHENTICATION SETUP</p>
          <h1>Configure Supabase</h1>
          <p>
            Add <code>VITE_SUPABASE_URL</code> and{" "}
            <code>VITE_SUPABASE_ANON_KEY</code> to the frontend environment,
            plus <code>SUPABASE_URL</code> to the backend environment.
          </p>
        </section>
      </main>
    );
  }

  if (loading) {
    return (
      <main className="auth-shell">
        <p>Loading InfraScan…</p>
      </main>
    );
  }

  if (!session) {
    return (
      <main className="auth-shell">
        <section className="auth-card">
          <p className="eyebrow">INFRASCAN ACCOUNT</p>
          <h1>{mode === "sign-in" ? "Sign in" : "Create citizen account"}</h1>
          <form onSubmit={submit}>
            <label>
              Email
              <input
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                required
                autoComplete="email"
              />
            </label>
            <label>
              Password
              <input
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                required
                minLength={6}
                autoComplete={mode === "sign-in" ? "current-password" : "new-password"}
              />
            </label>
            {error && <div className="auth-error" role="alert">{error}</div>}
            <button className="primary-button" type="submit" disabled={loading}>
              {mode === "sign-in" ? "Sign in" : "Create account"}
            </button>
          </form>
          <button
            type="button"
            className="auth-switch"
            onClick={() => {
              setError("");
              setMode(mode === "sign-in" ? "sign-up" : "sign-in");
            }}
          >
            {mode === "sign-in"
              ? "New citizen? Create an account"
              : "Already have an account? Sign in"}
          </button>
        </section>
      </main>
    );
  }

  return (
    <>
      <div className="account-bar">
        <span>{session.user.email}</span>
        <button
          type="button"
          className="auth-switch"
          onClick={() => void supabase?.auth.signOut()}
        >
          Sign out
        </button>
      </div>
      {children}
    </>
  );
}
