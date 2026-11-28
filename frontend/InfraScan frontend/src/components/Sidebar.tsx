import infrascanLogo from '../assets/infrascan.svg';

// reuse existing acc controls and Supabase sign-out action
import { AccountMenu } from '../TopBar.tsx';

// reuse the existing auth and role system from backend
// routes are determined by the role of the user, which is stored in the JWT token
import {
    isPrivileged,
    useAuth,
} from "../auth/AuthContext.ts";

import type { Route } from "../routing.ts";

import "./Sidebar.css";

// each sidebar item has label, icon, and route
type SidebarItem = {
    label: string;
    href: string;
    routes: Route["name"][]; // array of routes that can see this item
    privileged?: boolean; // if true, only privileged users can see this item
};

// use the existing route system
const NAV_ITEMS: SidebarItem[] = [
    {
        label: "Overview",
        href: "#/overview",
        routes: ["overview"],
    },
    {
        label: "Detections",
        href: "#/detections",
        routes: ["detections", "detection"],
        privileged: true,
    },
    {
        label: "Upload",
        href: "#/",
        routes: ["upload"],
    },
    {
        label: "Issue Map",
        href: "#/map",
        routes: ["map", "issue"],
    },
    {
        label: "Review queue",
        href: "#/review",
        routes: ["review"],
    },
];

type SidebarProps = {
    route: Route["name"];
};

export function Sidebar({ route }: SidebarProps) {

    // reads the curr user from existing auth context
    const auth = useAuth();

    // keep role-based access control with TopBar
    // auth should be available in actual app, but if not, show all items
    const visibleItems = NAV_ITEMS.filter(
        (item) =>
            !item.privileged ||
            (auth !== null && isPrivileged(auth.user))
);

return (
    <aside className="workspace-sidebar">

        {/* Project Branding */}
        <a
            href="#/overview"
            className="sidebar-brand"
            aria-label="InfraScan home"
        >
            <img src={infrascanLogo} alt="" />
            <span>InfraScan</span>
        </a>

        {/*  DISPLAY ONLY: Workspace Information */}
        <section className="sidebar-workspace">
            <span className="workspace-name">
                MUNICIPAL WORKSPACE
            </span>

        {/* Navigation Items */}
        <strong>Fullerton, CA</strong>
        </section>

        {/* Application Navigation */}
        <nav
            className="sidebar-navigation"
            aria-label="Workspace navigation"
        >
            <p className="sidebar-caption">OPERATIONS</p>

            {visibleItems.map((item) => {
                // the curr route determines which item is active
                const isActive = item.routes.includes(route);

                return (
                    <a
                        key={item.href}
                        href={item.href}
                        className={
                            isActive
                                ? "sidebar-link active"
                                : "sidebar-link"
                        }
                        aria-current={isActive ? "page" : undefined}
                    >
                        {item.label}
                    </a>
                );
            })}
        </nav>

        {/* User Account Section */}
        <div className="sidebar-footer">

        {/* From the TopBar to SideBar */}
        <AccountMenu />

      </div>
    </aside>
  );
}
