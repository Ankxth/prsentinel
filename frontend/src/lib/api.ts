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
export const deleteAccount = () =>
  apiFetch<{ data_deleted: boolean; login_removed: boolean }>("/v1/me", { method: "DELETE" });

/** Address the user's GitHub workflow should call. Set VITE_PUBLIC_API_URL to override. */
export const PUBLIC_API_URL: string =
  import.meta.env.VITE_PUBLIC_API_URL ?? "https://prsentinel-21ho.onrender.com";

export type Run = {
  id: string;
  repo_id: string;
  sha: string;
  ref: string;
  event: string;
  status: "running" | "completed" | "failed" | "could_not_run" | string;
  started_at: string;
  finished_at: string | null;
  summary: {
    changed_files?: number;
    changed_functions?: { file: string; name: string; change_type: string }[];
    reason?: string | null;
  };
};

export const getRun = (runId: string) => apiFetch<Run>(`/v1/runs/${runId}`);

export const listRuns = (repoId: string) => apiFetch<Run[]>(`/v1/repos/${repoId}/runs`);

export type TestView = {
  name: string;
  /** catches | flaky | odd | unreliable | new_fails | same (the same buckets as the PR comment) */
  kind: "catches" | "flaky" | "odd" | "unreliable" | "new_fails" | "same" | string;
  label: string;
  diagnosis: string | null;
  before: string;
  after: string;
  rerun_passes: number;
  rerun_fails: number;
  /** Failure text from the repo's own test run: untrusted, show as plain text. */
  message: string | null;
};

export type MutationView =
  | { status: "not_measured"; reason: string }
  | {
      status: "measured";
      killed: number;
      survived: number;
      percent: number;
      equivalent: number;
      could_not_run: number;
      capped: boolean;
      stopped_early: boolean;
      survivors: string[];
      note: string;
    };

export type RunFunctionView = {
  id: string;
  file: string;
  name: string;
  change_type: string;
  status: string;
  reason: string | null;
  counts: Record<string, number>;
  repair_rounds: number;
  tests: TestView[];
  test_code: string | null;
  mutation: MutationView | null;
};

export type RunView = {
  run_id: string;
  status: "running" | "completed" | "failed" | "could_not_run" | string;
  reason: string | null;
  ai_note: string;
  functions: RunFunctionView[];
};

export const getRunFunctions = (runId: string) => apiFetch<RunView>(`/v1/runs/${runId}/functions`);

export type ScannerView = {
  tool: string;
  status: "ran" | "could_not_run" | string;
  /** Why a scanner could not run, or a note about what it skipped. Untrusted text. */
  reason: string | null;
  version: string | null;
  /** false means the old code could not be scanned, so nothing could be called new. */
  compared_with_base: boolean;
  new: number;
  existing: number;
  unknown: number;
};

export type FindingView = {
  id: string;
  tool: string;
  rule_id: string;
  severity: "high" | "medium" | "low" | "unknown" | string;
  confidence: string;
  file: string | null;
  line: number | null;
  /** Scanner text: untrusted, show as plain text. */
  message: string;
  cwe: string | null;
  /** new (introduced by this commit) | existing | unknown (could not be compared) */
  origin: "new" | "existing" | "unknown" | string;
  /** AI opinion: a hint only, never hides a finding. null when none was given. */
  triage: "likely_real" | "likely_false_positive" | "unsure" | string | null;
  /** Model text: untrusted, show as plain text. */
  triage_reason: string | null;
  /** Proof agent: verified | not_reproduced | needs_review | not_attempted | null. */
  proof: "verified" | "not_reproduced" | "needs_review" | "not_attempted" | string | null;
  /** Server text: show as plain text. */
  proof_reason: string | null;
  /** AI text after a verified result: untrusted, plain text. */
  proof_explanation: string | null;
  /** Fixed advice from the server, shown when a proof was verified or mixed. */
  proof_next_step: string | null;
};

export type FindingsView = {
  run_id: string;
  status: string;
  scanners: ScannerView[];
  counts: { new: number; existing: number; unknown: number };
  omitted: number;
  note: string;
  findings: FindingView[];
};

export type ProofAttempt = {
  attempt: number;
  /** AI-written test text: untrusted, show as plain text, never run in the browser. */
  test_code: string;
  lint_ok: boolean | null;
  lint_errors: string[];
  outcomes: string[];
  output: string | null;
  model: string | null;
};

export type ProofAttempts = {
  finding_id: string;
  proof: string | null;
  proof_reason: string | null;
  proof_explanation: string | null;
  how_to_read: string;
  attempts: ProofAttempt[];
};

export const getFindingAttempts = (runId: string, findingId: string) =>
  apiFetch<ProofAttempts>(`/v1/runs/${runId}/findings/${findingId}/attempts`);

export const getRunFindings = (runId: string) => apiFetch<FindingsView>(`/v1/runs/${runId}/findings`);

export type ContextFile = {
  path: string;
  is_test: boolean;
  parse_error: string | null;
  symbols: number;
  calls: number;
};

export type ContextMap = {
  status: "empty" | "building" | "complete" | string;
  built_from_sha: string | null;
  latest_run_sha: string | null;
  partial: boolean;
  total_files: number;
  skipped_files: number;
  repo_facts: { test_framework?: string; python_files?: number; test_files?: number };
  file_count: number;
  edges: { resolved: number; external: number; unresolved: number };
  files: ContextFile[];
};

export type ContextSymbol = {
  name: string;
  kind: "function" | "class" | string;
  signature: string;
  doc: string;
  lines: number[];
};

export type ContextFileDetail = {
  path: string;
  is_test: boolean;
  parse_error: string | null;
  symbols: ContextSymbol[];
  /** AI summary; `summary_current` is false when the file changed after it was written. */
  summary: string | null;
  summary_current: boolean;
};

export type FileSummary = {
  path: string;
  summary: string;
  current: boolean;
  generated: boolean;
};

export type ContextPackPreview = {
  found: boolean;
  text: string;
  pack: { token_estimate: number; budget: number; truncated: Record<string, number> };
};

export const getContext = (repoId: string) => apiFetch<ContextMap>(`/v1/repos/${repoId}/context`);
export const getContextFile = (repoId: string, path: string) =>
  apiFetch<ContextFileDetail>(`/v1/repos/${repoId}/context/file?path=${encodeURIComponent(path)}`);
export const getContextPack = (repoId: string, file: string, fn: string, budget = 3000) =>
  apiFetch<ContextPackPreview>(
    `/v1/repos/${repoId}/context/pack?file=${encodeURIComponent(file)}` +
      `&function=${encodeURIComponent(fn)}&budget=${budget}`,
  );
/** Writes the file's AI summary (or returns the stored one if the file has not changed). */
export const summarizeFile = (repoId: string, path: string) =>
  apiFetch<FileSummary>(
    `/v1/repos/${repoId}/context/file/summary?path=${encodeURIComponent(path)}`,
    { method: "POST" },
  );
