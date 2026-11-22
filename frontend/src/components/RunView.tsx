import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  getRun,
  getRunFindings,
  getRunFunctions,
  type FindingsView,
  type Run,
  type RunView as RunData,
} from "../lib/api";
import { closeRunPage, runLink } from "../lib/route";
import { RunStatus } from "./RepoPanel";
import SecurityTab from "./SecurityTab";
import TestsTab from "./TestsTab";
import { Skeleton, Stat, Tabs, plural } from "./ui";

// While a run is going, ask again every few seconds, and stop after a while.
const POLL_MS = 4000;
const MAX_POLL_MS = 20 * 60 * 1000;

type Tab = "tests" | "security";

export default function RunView({ runId }: { runId: string }) {
  const [run, setRun] = useState<Run | null>(null);
  const [view, setView] = useState<RunData | null>(null);
  const [findings, setFindings] = useState<FindingsView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [findingsError, setFindingsError] = useState<string | null>(null);
  const [stopped, setStopped] = useState(false);
  const [copied, setCopied] = useState(false);
  const [tab, setTab] = useState<Tab>("tests");
  const [nonce, setNonce] = useState(0);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    const startedAt = Date.now();
    setStopped(false);
    setView(null);
    setRun(null);
    setFindings(null);
    setFindingsError(null);
    getRun(runId).then((r) => !cancelled && setRun(r)).catch(() => undefined);

    async function tick() {
      let data: RunData | null = null;
      try {
        data = await getRunFunctions(runId);
        if (cancelled) return;
        setView(data);
        setError(null);
      } catch (e) {
        if (cancelled) return;
        setError((e as Error).message);
        if (e instanceof ApiError && e.status === 404) return;
      }
      if (data !== null && data.status !== "running") {
        getRun(runId).then((r) => !cancelled && setRun(r)).catch(() => undefined);
        getRunFindings(runId)
          .then((f) => !cancelled && (setFindings(f), setFindingsError(null)))
          .catch((e: Error) => !cancelled && setFindingsError(e.message));
        return;
      }
      if (Date.now() - startedAt > MAX_POLL_MS) {
        setStopped(true);
        return;
      }
      timer.current = window.setTimeout(tick, POLL_MS);
    }
    void tick();
    return () => {
      cancelled = true;
      window.clearTimeout(timer.current);
    };
  }, [runId, nonce]);

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(runLink(runId));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }

  const running = view?.status === "running";
  const fns = view?.functions ?? [];
  const tested = fns.filter((f) => f.status === "tested").length;
  const sum = (key: "catches" | "flaky" | "odd" | "unreliable" | "new_fails") =>
    fns.reduce((n, f) => n + f.counts[key], 0);
  const attention = sum("catches") + sum("flaky") + sum("odd") + sum("unreliable") + sum("new_fails");
  const newFindings = findings?.counts.new ?? 0;

  const verified = findings?.findings.filter((f) => f.origin !== "existing" && f.proof === "verified").length ?? 0;
  const worry = sum("flaky") + sum("odd") + sum("unreliable") + sum("new_fails") + newFindings;
  const breaks = sum("catches");

  let verdict: { tone: string; title: string; text: string; points: string[] } | null = null;
  if (view && !running && view.status === "completed") {
    const points: string[] = [];
    if (breaks > 0) points.push(`${plural(breaks, "test")} passed before your change and fail now (see the Tests tab).`);
    if (verified > 0) points.push(`${plural(verified, "security problem")} confirmed by a test run (see the Security tab).`);
    else if (newFindings > 0) points.push(`${plural(newFindings, "possible security problem")} introduced by this change.`);
    if (sum("flaky") + sum("odd") + sum("unreliable") + sum("new_fails") > 0) points.push("Some tests need a second look before you rely on them.");
    if (!findings && !findingsError) points.push("Security results are still loading.");
    if (breaks > 0 || verified > 0) {
      verdict = {
        tone: "bad",
        title: "This change needs attention before you continue",
        text: "Something may be broken or unsafe. If you have not merged yet, fix it first. If it is already merged, you can roll the commit back (git revert) until it is fixed.",
        points,
      };
    } else if (worry > 0) {
      verdict = {
        tone: "warn",
        title: "Worth a look, but nothing looks broken",
        text: "No test shows your change breaking existing behaviour. You can usually carry on, after checking the items below.",
        points,
      };
    } else if (findings || findingsError) {
      verdict = {
        tone: "ok",
        title: "No problems found in this change",
        text: "The generated tests did not catch anything broken and the scanners found nothing new. This is good news but not a guarantee.",
        points: [],
      };
    }
  }

  return (
    <div>
      <button className="link" onClick={closeRunPage}>&larr; Back to repositories</button>
      <div className="page-head" style={{ marginTop: 12 }}>
        <div>
          <h1>
            Run {run ? <code>{run.sha.slice(0, 7)}</code> : ""} {view && <RunStatus status={view.status} />}
          </h1>
          {run && (
            <p className="meta">
              {run.event} · {run.ref} · started {new Date(run.started_at).toLocaleString()}
              {run.finished_at && ` · finished ${new Date(run.finished_at).toLocaleTimeString()}`}
            </p>
          )}
        </div>
        <div className="actions">
          <button onClick={copyLink}>{copied ? "Copied" : "Copy link"}</button>
          <button onClick={() => setNonce(nonce + 1)}>Refresh</button>
        </div>
      </div>

      {error && <p className="error">{error}</p>}
      {!view && !error && <Skeleton lines={5} />}
      {running && !stopped && <div className="notice info">This run is still going. The page updates by itself.</div>}
      {running && stopped && <div className="notice">This page stopped updating. Press Refresh to check again.</div>}
      {view?.status === "could_not_run" && (
        <div className="notice">
          Could not run{view.reason ? `: ${view.reason}` : ""}. This is not a pass. Nothing was checked in this run.
        </div>
      )}
      {view?.status === "failed" && <div className="notice bad">The run failed{view.reason ? `: ${view.reason}` : ""}.</div>}

      {verdict && (
        <div className={`verdict ${verdict.tone}`}>
          <h2>{verdict.title}</h2>
          <p>{verdict.text}</p>
          {verdict.points.length > 0 && <ul>{verdict.points.map((p) => <li key={p}>{p}</li>)}</ul>}
          <p className="fineprint">Automatic checks help but never replace your own judgement.</p>
        </div>
      )}
      {view && (
        <>
          <div className="stats">
            <Stat label="Functions checked" value={tested} note={`of ${plural(fns.length, "changed function")}`} />
            <Stat
              label="Tests needing a look"
              value={attention}
              tone={attention > 0 ? "warn" : "ok"}
              note={attention > 0 ? "changed, unstable or unreliable" : "nothing unusual"}
            />
            <Stat
              label="New security findings"
              value={findings ? newFindings : "-"}
              tone={findings ? (newFindings > 0 ? "bad" : "ok") : undefined}
              note={findings ? `${findings.counts.existing} older` : running ? "waiting for the run" : undefined}
            />
          </div>
          <Tabs
            tabs={[
              { id: "tests" as const, label: "Tests", count: fns.length },
              { id: "security" as const, label: "Security", count: findings ? findings.findings.length : undefined },
            ]}
            active={tab}
            onChange={setTab}
          />
          {tab === "tests" && <TestsTab view={view} />}
          {tab === "security" &&
            (findings ? (
              <SecurityTab data={findings} />
            ) : findingsError ? (
              <p className="error">Security checks could not be loaded: {findingsError}</p>
            ) : (
              <p className="muted">{running ? "Security results appear when the run finishes." : "Loading…"}</p>
            ))}
        </>
      )}
    </div>
  );
}
