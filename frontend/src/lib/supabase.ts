import { createClient, type SupabaseClient } from "@supabase/supabase-js";

const url = import.meta.env.VITE_SUPABASE_URL as string | undefined;
const key = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY as string | undefined;

/** null when the .env.local values are missing, so the app can show a clear message. */
export const supabase: SupabaseClient | null =
  url && key ? createClient(url, key) : null;
