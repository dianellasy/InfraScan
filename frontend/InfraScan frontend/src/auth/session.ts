import { supabase } from "./supabaseClient.ts";

/** The current access token; supabase-js refreshes it first when it is about to expire. */
export async function getAccessToken(): Promise<string | null> {
  if (!supabase) return null;
  const { data, error } = await supabase.auth.getSession();
  if (error) return null;
  return data.session?.access_token ?? null;
}

type Listener = () => void;
const unauthorizedListeners = new Set<Listener>();

/** Called by the API helper when the backend rejects the session (HTTP 401). */
export function notifyUnauthorized(): void {
  for (const listener of unauthorizedListeners) listener();
}

export function onUnauthorized(listener: Listener): () => void {
  unauthorizedListeners.add(listener);
  return () => {
    unauthorizedListeners.delete(listener);
  };
}
