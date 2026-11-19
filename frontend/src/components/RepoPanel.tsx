import { useCallback, useEffect, useState } from "react";
import { listRuns, PUBLIC_API_URL, type Repo, type Run } from "../lib/api";
import ContextView from "./ContextView";

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

const STATUS_TEXT: Record<string, string> = {
  running: "Running",
  completed: "Finished",
  failed: "Failed",
  could_not_run: "Could not run",
};

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
      <h3>Set up</h3>
      <p className="muted small">
        Save this as <code>.github/workflows/prsentinel.yml</code> in your repository and push it.
      </p>
      <pre className="code">{text}</pre>
      <button onClick={copy}>{copied ? "Copied" : "Copy"}</button>
    </div>
  );
}

function RunsList({ repo }: { repo: Repo }) {
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRuns(await listRuns(repo.id));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [repo.id]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div>
      <div className="row">
        <h3>Recent runs</h3>
        <button onClick={load}>Refresh</button>
      </div>
      {error && <p className="error">{error}</p>}
      {runs === null && !error && <p className="muted">Loading…</p>}
      {runs?.length === 0 && (
        <p className="muted small">No runs yet. Push a commit or open a pull request.</p>
      )}
      {runs && runs.length > 0 && (
        <table className="runs">
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
              <tr key={run.id}>
                <td>
                  <span className={`badge ${run.status}`}>
                    {STATUS_TEXT[run.status] ?? run.status}
                  </span>
                  {run.summary.reason && <div className="muted small">{run.summary.reason}</div>}
                </td>
                <td>{run.event}</td>
                <td>
                  <a
                    href={`https://github.com/${repo.full_name}/commit/${run.sha}`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {run.sha.slice(0, 7)}
                  </a>
                </td>
                <td>{run.summary.changed_functions?.length ?? 0}</td>
                <td className="small">{new Date(run.started_at).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export default function RepoPanel({ repo }: { repo: Repo }) {
  return (
    <div className="panel">
      <RunsList repo={repo} />
      <ContextView repo={repo} />
      <SetupSnippet />
    </div>
  );
}
