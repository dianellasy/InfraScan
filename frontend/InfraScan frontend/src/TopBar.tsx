import infrascanLogo from "./assets/infrascan.svg";
import type { Route } from "./routing.ts";

const LINKS: ReadonlyArray<{ href: string; label: string; routes: Route["name"][] }> = [
  { href: "#/", label: "Upload", routes: ["upload"] },
  { href: "#/map", label: "Issue map", routes: ["map", "issue"] },
  { href: "#/review", label: "Review queue", routes: ["review"] },
];

export function TopBar({ route }: { route: Route["name"] }) {
  return (
    <header className="topbar">
      <a className="brand" href="#/" aria-label="InfraScan home">
        <img className="brand-mark" src={infrascanLogo} alt="" />
        <span>InfraScan</span>
      </a>
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
    </header>
  );
}
