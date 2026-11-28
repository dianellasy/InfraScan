import infrascanLogo from "./assets/infrascan.svg";
import { isPrivileged, roleLabel, useAuth } from "./auth/AuthContext.ts";
import type { Route } from "./routing.ts";

type NavLink = {
  href: string;
  label: string;
  routes: Route["name"][];
  privileged?: boolean;
};

const LINKS: ReadonlyArray<NavLink> = [
  { href: "#/overview", label: "Overview", routes: ["overview"] },
  { href: "#/", label: "Upload", routes: ["upload"] },
  { href: "#/map", label: "Issue map", routes: ["map", "issue"] },
  { href: "#/review", label: "Review queue", routes: ["review"] },
  {
    href: "#/detections",
    label: "Detections",
    routes: ["detections", "detection"],
    privileged: true,
  },
];

export function AccountMenu() {
  const auth = useAuth();
  if (!auth) return null;
  return (
    <div className="account-menu">
      <div className="account-identity">
        <span className="account-email">{auth.user.email}</span>
        <span className="account-role">{roleLabel(auth.user)}</span>
      </div>
      <button
        type="button"
        className="sign-out-button"
        onClick={() => void auth.signOut()}
      >
        Sign out
      </button>
    </div>
  );
}

// controls which parts of the top bar are shown, depending on the route and user role
type TopBarProps = {
  route: Route["name"];
  showAccount?: boolean;
  showNavigation?: boolean;
};

export function TopBar({
  route,
  showAccount = true,
  showNavigation = true,
}: TopBarProps) {
  const auth = useAuth();
  // Outside AuthGate (isolated page tests) every link is shown.
  const links = LINKS.filter(
    (link) => !link.privileged || !auth || isPrivileged(auth.user),
  );
  return (
    <header className="topbar">
      <a className="brand" href="#/overview" aria-label="InfraScan home">
        <img className="brand-mark" src={infrascanLogo} alt="" />
        <span>InfraScan</span>
      </a>
      <div className="topbar-end">
        {showNavigation && (
          <nav className="topbar-nav" aria-label="Main">
            {links.map((link) => (
              <a
                key={link.href}
                href={link.href}
                aria-current={link.routes.includes(route) ? "page" : undefined}
              >
                {link.label}
              </a>
            ))}
          </nav>
        )}
        {showAccount && <AccountMenu />}
      </div>
    </header>
  );
}
