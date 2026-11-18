import { supabase } from "./supabase";

export const API_URL: string = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** FastAPI sends detail as text, or as a list for form errors. Turn both into one sentence. */
function readDetail(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { msg?: string };
      if (first?.msg) return first.msg.replace(/^Value error, /, "");
    }
  }
  return fallback;
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const { data } = (await supabase?.auth.getSession()) ?? { data: { session: null } };
  const token = data.session?.access_token;

  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body) headers.set("Content-Type", "application/json");

  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, { ...init, headers });
  } catch {
    throw new ApiError(0, "Cannot reach the server. Is the backend running?");
  }

  if (response.status === 204) return undefined as T;

  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    // not JSON
  }
  if (!response.ok) {
    throw new ApiError(response.status, readDetail(body, `Request failed (${response.status}).`));
  }
  return body as T;
}

export type Me = {
  id: string;
  github_login: string;
  avatar_url: string | null;
  is_admin: boolean;
  created_at: string;
};

export type Repo = {
  id: string;
  full_name: string;
  github_id: number;
  default_branch: string;
  is_public: boolean;
  llm_opt_in: boolean;
  created_at: string;
};

export const getMe = () => apiFetch<Me>("/v1/me");
export const listRepos = () => apiFetch<Repo[]>("/v1/repos");
export const addRepo = (full_name: string) =>
  apiFetch<Repo>("/v1/repos", {
    method: "POST",
    body: JSON.stringify({ full_name, llm_opt_in: true }),
  });
export const deleteRepo = (id: string) => apiFetch<void>(`/v1/repos/${id}`, { method: "DELETE" });
