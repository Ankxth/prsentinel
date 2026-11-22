import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { listRuns, PUBLIC_API_URL, type Repo, type Run } from "../lib/api";
import { readCache, writeCache } from "../lib/cache";
import { openRunPage } from "../lib/route";
import { Chip, Skeleton, Tabs, type Tone } from "./ui";

// The code map is only needed when someone opens that tab.
const ContextView = lazy(() => import("./ContextView"));

function workflowText(): string {
  return `name: prsentinel

on:
  pull_request:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  id-token: write
  contents: read
  pull-requests: write

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: Ankxth/prsentinel/action@main
        with:
          api-url: ${PUBLIC_API_URL}
`;
}

const STATUS: Record<string, { text: string; tone: Tone }> = {
  running: { text: "Running", tone: "info" },
  completed: { text: "Finished", tone: "ok" },
  failed: { text: "Failed", tone: "bad" },
  could_not_run: { text: "Could not run", tone: "warn" },
};

export function RunStatus({ status }: { status: string }) {
  const s = STATUS[status] ?? { text: status, tone: "mute" as Tone };
  return <Chip tone={s.tone}>{s.text}</Chip>;
}

function SetupSnippet() {
  const [copied, setCopied] = useState(false);
  const text = workflowText();
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }
  return (
    <div>
      <p className="muted small">
        Save this as <code>.github/workflows/prsentinel.yml</code> in your repository and push it.
      </p>
      <pre className="code">{text}</pre>
      <button onClick={copy}>{copied ? "Copied" : "Copy"}</button>
    </div>
  );
}

function RunsList({ repo }: { repo: Repo }) {
  const key = `runs.${repo.id}`;
  const [runs, setRuns] = useState<Run[] | null>(() => readCache<Run[]>(key));
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const fresh = await listRuns(repo.id);
      setRuns(fresh);
      writeCache(key, fresh);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [repo.id, key]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div>
      <div className="row" style={{ marginBottom: 8 }}>
        <span className="muted small">Most recent first</span>
        <button onClick={load}>Refresh</button>
      </div>
      {error && <p className="error">{error}</p>}
      {runs === null && !error && <Skeleton lines={3} />}
      {runs?.length === 0 && (
        <p className="muted small">No runs yet. Push a commit or open a pull request.</p>
      )}
      {runs && runs.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Status</th>
                <th>Event</th>
                <th>Commit</th>
                <th>Functions</th>
                <th>Started</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.id} className="clickable" onClick={() => openRunPage(run.id)}>
                  <td>
                    <RunStatus status={run.status} />
                    {run.summary.reason && <div className="muted small">{run.summary.reason}</div>}
                  </td>
                  <td>{run.event}</td>
                  <td onClick={(e) => e.stopPropagation()}>
                    <a href={`https://github.com/${repo.full_name}/commit/${run.sha}`} target="_blank" rel="noreferrer">
                      <code>{run.sha.slice(0, 7)}</code>
                    </a>
                  </td>
                  <td>{run.summary.changed_functions?.length ?? 0}</td>
                  <td className="small">{new Date(run.started_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

type SubTab = "runs" | "map" | "setup";

export default function RepoPanel({ repo }: { repo: Repo }) {
  const [tab, setTab] = useState<SubTab>("runs");
  return (
    <div className="panel">
      <Tabs
        tabs={[
          { id: "runs" as const, label: "Runs" },
          { id: "map" as const, label: "Code map" },
          { id: "setup" as const, label: "Setup" },
        ]}
        active={tab}
        onChange={setTab}
      />
      {tab === "runs" && <RunsList repo={repo} />}
      {tab === "map" && (
        <Suspense fallback={<Skeleton lines={3} />}>
          <ContextView repo={repo} />
        </Suspense>
      )}
      {tab === "setup" && <SetupSnippet />}
    </div>
  );
}
