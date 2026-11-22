import { createContext, useContext } from "react";
import type { User } from "@supabase/supabase-js";

export type AuthContextValue = {
  user: User;
  signOut: () => Promise<void>;
};

export const AuthContext = createContext<AuthContextValue | null>(null);

/** The signed-in user, or null outside `AuthGate` (for example, in isolated page tests). */
export function useAuth(): AuthContextValue | null {
  return useContext(AuthContext);
}

const ROLE_LABELS: Record<string, string> = {
  citizen: "Citizen",
  maintenance_crew: "Maintenance crew",
  city_engineer: "City engineer",
  municipal_administrator: "Municipal administrator",
  developer: "Developer",
};

/**
 * Display only: the backend enforces roles from the verified token. Like the
 * backend, reads the signed `app_metadata` and treats unknown roles as citizen.
 */
export function roleLabel(user: User): string {
  const role: unknown = user.app_metadata?.role;
  return (typeof role === "string" && ROLE_LABELS[role]) || ROLE_LABELS.citizen;
}
