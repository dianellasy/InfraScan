import { createClient, type SupabaseClient } from "@supabase/supabase-js";

/**
 * Supabase is used only for sign-in. The browser receives only the project URL
 * and the publishable key; secret and service-role keys must never reach Vite.
 */
const supabaseUrl: string | undefined = import.meta.env.VITE_SUPABASE_URL;
const publishableKey: string | undefined = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY;

function isServerOnlyKey(key: string): boolean {
  if (key.startsWith("sb_secret_")) return true;
  // Legacy JWT-format keys: refuse the service_role key outright.
  const payload = key.split(".")[1];
  if (!payload) return false;
  try {
    const claims: unknown = JSON.parse(atob(payload.replace(/-/g, "+").replace(/_/g, "/")));
    return typeof claims === "object" && claims !== null && "role" in claims && claims.role === "service_role";
  } catch {
    return false;
  }
}

function configurationError(): string | null {
  if (!supabaseUrl || !publishableKey) {
    return "Set VITE_SUPABASE_URL and VITE_SUPABASE_PUBLISHABLE_KEY in the frontend environment.";
  }
  if (isServerOnlyKey(publishableKey)) {
    return "VITE_SUPABASE_PUBLISHABLE_KEY is a secret key. Use the project's publishable key.";
  }
  return null;
}

export const supabaseConfigError = configurationError();

export const supabase: SupabaseClient | null =
  supabaseConfigError === null
    ? createClient(supabaseUrl!, publishableKey!, {
        auth: {
          persistSession: true,
          autoRefreshToken: true,
          // Email/password only; the app's hash routes are not auth redirects.
          detectSessionInUrl: false,
        },
      })
    : null;
