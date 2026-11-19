import { useCallback, useEffect, useState } from "react";
import {
  getContext,
  getContextFile,
  getContextPack,
  type ContextFileDetail,
  type ContextMap,
  type ContextPackPreview,
  type Repo,
} from "../lib/api";

const STATUS_TEXT: Record<string, string> = {
  empty: "No code map yet",
  building: "Being updated",
  complete: "Ready",
};

function Freshness({ map, repo }: { map: ContextMap; repo: Repo }) {
  const short = (sha: string) => sha.slice(0, 7);
  const link = (sha: string) => (
    <a href={`https://github.com/${repo.full_name}/commit/${sha}`} target="_blank" rel="noreferrer">
      {short(sha)}
    </a>
  );
  if (!map.built_from_sha) {
    return <p className="muted small">Nothing has been uploaded yet. Run the Action once.</p>;
  }
  const behind = map.latest_run_sha && map.latest_run_sha !== map.built_from_sha;
  return (
    <p className="small">
      Built from commit {link(map.built_from_sha)}.{" "}
      {behind ? (
        <span className="warn">
          A newer run ({link(map.latest_run_sha as string)}) has not finished updating it.
        </span>
      ) : (
        <span className="muted">This is the latest run.</span>
      )}
    </p>
  );
}

function PackPreview({ repo, file, fn }: { repo: Repo; file: string; fn: string }) {
  const [pack, setPack] = useState<ContextPackPreview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setPack(null);
    setError(null);
    getContextPack(repo.id, file, fn)
      .then((p) => !cancelled && setPack(p))
      .catch((e) => !cancelled && setError((e as Error).message));
    return () => {
      cancelled = true;
    };
  }, [repo.id, file, fn]);

  if (error) return <p className="error">{error}</p>;
  if (!pack) return <p className="muted">Loading…</p>;
  return (
    <div>
      <p className="muted small">
        This is exactly what the test generator would be told about <code>{fn}</code> (about{" "}
        {pack.pack.token_estimate} tokens).
      </p>
      <pre className="code">{pack.text}</pre>
    </div>
  );
}

function FileDetail({ repo, path }: { repo: Repo; path: string }) {
  const [detail, setDetail] = useState<ContextFileDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setError(null);
    setPicked(null);
    getContextFile(repo.id, path)
      .then((d) => !cancelled && setDetail(d))
      .catch((e) => !cancelled && setError((e as Error).message));
    return () => {
      cancelled = true;
    };
  }, [repo.id, path]);

  if (error) return <p className="error">{error}</p>;
  if (!detail) return <p className="muted">Loading…</p>;
  return (
    <div className="filedetail">
      <h4>{detail.path}</h4>
      {detail.parse_error && (
        <p className="error small">Could not read this file: {detail.parse_error}</p>
      )}
      {detail.symbols.length === 0 && !detail.parse_error && (
        <p className="muted small">No functions or classes.</p>
      )}
      <ul className="symbols">
        {detail.symbols.map((s) => (
          <li key={s.name}>
            <button
              className={`link ${picked === s.name ? "active" : ""}`}
              onClick={() => setPicked(s.name)}
              title="Show what the test generator would be told about this"
            >
              {s.name}
              {s.kind === "function" ? s.signature : ""}
            </button>{" "}
            <span className="muted small">
              {s.kind}, lines {s.lines[0]}-{s.lines[1]}
              {s.doc ? ` · ${s.doc}` : ""}
            </span>
          </li>
        ))}
      </ul>
      {picked && <PackPreview repo={repo} file={detail.path} fn={picked} />}
    </div>
  );
}

export default function ContextView({ repo }: { repo: Repo }) {
  const [map, setMap] = useState<ContextMap | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [file, setFile] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setMap(await getContext(repo.id));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [repo.id]);

  useEffect(() => {
    load();
  }, [load]);

  const calls = map ? map.edges.resolved + map.edges.external + map.edges.unresolved : 0;

  return (
    <div>
      <div className="row">
        <h3>Code map</h3>
        <button onClick={load}>Refresh</button>
      </div>
      {error && <p className="error">{error}</p>}
      {map === null && !error && <p className="muted">Loading…</p>}
      {map && (
        <>
          <p className="small">
            <span className={`badge ${map.status === "complete" ? "completed" : "running"}`}>
              {STATUS_TEXT[map.status] ?? map.status}
            </span>{" "}
            {map.partial && (
              <span className="badge failed" title="Some files were left out (too many or too large)">
                Partial: {map.skipped_files} file(s) left out
              </span>
            )}
          </p>
          <Freshness map={map} repo={repo} />
          {map.file_count > 0 && (
            <>
              <p className="small">
                {map.file_count} Python files
                {map.repo_facts.test_files !== undefined && ` (${map.repo_facts.test_files} tests)`}
                {map.repo_facts.test_framework && map.repo_facts.test_framework !== "unknown"
                  ? `, ${map.repo_facts.test_framework}`
                  : ""}
                . {calls} calls found: {map.edges.resolved} point to code in this repo,{" "}
                {map.edges.external} to built-ins or libraries, {map.edges.unresolved} could not be
                matched to one place (for example a method called on a variable). Those are shown
                as unknown, never guessed.
              </p>
              <table className="runs">
                <thead>
                  <tr>
                    <th>File</th>
                    <th>Symbols</th>
                    <th>Calls</th>
                  </tr>
                </thead>
                <tbody>
                  {map.files.map((f) => (
                    <tr key={f.path}>
                      <td>
                        <button
                          className={`link ${file === f.path ? "active" : ""}`}
                          onClick={() => setFile(f.path)}
                        >
                          {f.path}
                        </button>{" "}
                        {f.is_test && <span className="badge">test</span>}
                        {f.parse_error && <span className="badge failed">unreadable</span>}
                      </td>
                      <td>{f.symbols}</td>
                      <td>{f.calls}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {map.file_count > map.files.length && (
                <p className="muted small">
                  Showing the first {map.files.length} of {map.file_count} files.
                </p>
              )}
            </>
          )}
          {file && <FileDetail repo={repo} path={file} />}
        </>
      )}
    </div>
  );
}
