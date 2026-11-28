import { useId, useState, type FormEvent } from "react";
import infrascanLogo from "../assets/infrascan.svg";
import "./Login.css";

export type LoginProps = {
  onSignIn: (email: string, password: string) => void;
  loading?: boolean;
  error?: string | null;
};

export function Login({ onSignIn, loading = false, error }: LoginProps) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const id = useId();
  const headingId = `${id}-heading`;
  const emailId = `${id}-email`;
  const passwordId = `${id}-password`;
  const errorId = `${id}-error`;

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onSignIn(email, password);
  }

  return (
    <main className="login-page">
      <section className="login-card" aria-labelledby={headingId}>
        <div className="login-brand">
          <img src={infrascanLogo} alt="" />
          <span>InfraScan</span>
        </div>

        <div className="login-heading">
          <h1 id={headingId}>Welcome back!</h1>
          <p>Sign in to access your InfraScan account.</p>
        </div>

        <form
          className="login-form"
          onSubmit={handleSubmit}
          aria-busy={loading}
          aria-labelledby={headingId}
          aria-describedby={error ? errorId : undefined}
        >
          <div className="login-field">
            <label htmlFor={emailId}>Email address</label>
            <input
              id={emailId}
              type="email"
              placeholder="name@example.com"
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              disabled={loading}
              required
            />
          </div>

          <div className="login-field">
            <label htmlFor={passwordId}>Password</label>
            <input
              id={passwordId}
              type="password"
              placeholder="Enter your password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              disabled={loading}
              required
            />
          </div>

          {error && (
            <p className="login-error" id={errorId} role="alert">
              {error}
            </p>
          )}

          <button className="login-submit" type="submit" disabled={loading}>
            {loading ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </section>
    </main>
  );
}

export default Login;
