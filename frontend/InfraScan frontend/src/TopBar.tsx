import infrascanLogo from "./assets/infrascan.svg";
import { roleLabel, useAuth } from "./auth/AuthContext.ts";
import type { Route } from "./routing.ts";

const LINKS: ReadonlyArray<{ href: string; label: string; routes: Route["name"][] }> = [
  { href: "#/", label: "Upload", routes: ["upload"] },
  { href: "#/map", label: "Issue map", routes: ["map", "issue"] },
  { href: "#/review", label: "Review queue", routes: ["review"] },
];

function AccountMenu() {
  const auth = useAuth();
  if (!auth) return null;
  return (
    <div className="account-menu">
      <div className="account-identity">
        <span className="account-email">{auth.user.email}</span>
        <span className="account-role">{roleLabel(auth.user)}</span>
      </div>
      <button type="button" className="sign-out-button" onClick={() => void auth.signOut()}>
        Sign out
      </button>
    </div>
  );
}

export function TopBar({ route }: { route: Route["name"] }) {
  return (
    <header className="topbar">
      <a className="brand" href="#/" aria-label="InfraScan home">
        <img className="brand-mark" src={infrascanLogo} alt="" />
        <span>InfraScan</span>
      </a>
      <div className="topbar-end">
        <nav className="topbar-nav" aria-label="Main">
          {LINKS.map((link) => (
            <a
              key={link.href}
              href={link.href}
              aria-current={link.routes.includes(route) ? "page" : undefined}
            >
              {link.label}
            </a>
          ))}
        </nav>
        <AccountMenu />
      </div>
    </header>
  );
}
