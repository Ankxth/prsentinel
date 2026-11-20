import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  getRunFindings,
  getRunFunctions,
  type FindingsView,
  type FindingView,
  type RunFunctionView,
  type RunView,
  type TestView,
} from "../lib/api";
import { runLink } from "../lib/route";

// While a run is going, ask the server again every few seconds. Stop after a while so a
// forgotten tab does not keep asking forever; the Refresh button still works.
const POLL_MS = 4000;
const MAX_POLL_MS = 20 * 60 * 1000;

const RUN_STATUS_TEXT: Record<string, string> = {
  running: "Running",
  completed: "Finished",
  failed: "Failed",
  could_not_run: "Could not run",
};

const FUNCTION_STATUS_TEXT: Record<string, string> = {
  pending: "Waiting for the Action",
  generated: "Tests written, not run yet",
  tested: "Tested",
  skipped: "Not tested",
  could_not_generate: "Not tested",
  could_not_test: "Not tested",
};

const KIND_ICON: Record<string, string> = {
  catches: "⚠️",
  flaky: "🎲",
  odd: "🔄",
  unreliable: "❓",
  new_fails: "❓",
  same: "➖",
};

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

function kindText(test: TestView): string {
  switch (test.kind) {
    case "catches":
      return "passes on the old code, fails on the new code";
    case "flaky":
      return `unstable: ${plural(test.rerun_passes, "rerun")} passed and ${test.rerun_fails} failed`;
    case "odd":
      return "fails on the old code but passes on the new code, so it matches the new behaviour: check that this change is intended";
    case "unreliable":
      return "fails on the old code too, so the test itself may be wrong";
    case "new_fails":
      return "fails on this new function, so check the test (there is no old code to compare)";
    default:
      return "same result on the old and the new code";
  }
}

function summaryParts(fn: RunFunctionView): string[] {
  const c = fn.counts;
  const parts: string[] = [];
  if (c.catches) parts.push(`⚠️ ${plural(c.catches, "test")} ${c.catches === 1 ? "catches" : "catch"} a change`);
  if (c.flaky) parts.push(`🎲 ${plural(c.flaky, "unstable test")}`);
  if (c.odd) parts.push(`🔄 ${plural(c.odd, "test")} match the new behaviour`);
  if (c.unreliable) parts.push(`❓ ${plural(c.unreliable, "unreliable test")}`);
  if (c.new_fails) parts.push(`❓ ${plural(c.new_fails, "failing test")} on a new function`);
  if (c.same) parts.push(`➖ ${plural(c.same, "test")} found no difference`);
  return parts;
}

function Mutation({ fn }: { fn: RunFunctionView }) {
  const m = fn.mutation;
  if (!m) return null;
  if (m.status !== "measured") {
    return <p className="small muted">Mutation check: not measured ({m.reason}).</p>;
  }
  const extras: string[] = [];
  if (m.equivalent) extras.push(`${m.equivalent} changed nothing and were left out`);
  if (m.could_not_run) extras.push(`${m.could_not_run} could not be run`);
  if (m.capped) extras.push("only the first faults were tried");
  if (m.stopped_early) extras.push("stopped early because of the time limit");
  return (
    <div className="small">
      <p>
        Mutation check: the tests caught {m.killed} of {m.killed + m.survived} small deliberate faults (
        {m.percent}%){extras.length > 0 && ` (${extras.join("; ")})`}.
      </p>
      {m.survivors.length > 0 && (
        <p>
          Faults the tests missed:{" "}
          {m.survivors.map((s, i) => (
            <span key={i}>
              {i > 0 && ", "}
              <code>{s}</code>
            </span>
          ))}
          .
        </p>
      )}
      <p className="muted">{m.note}</p>
    </div>
  );
}

function FunctionCard({ fn }: { fn: RunFunctionView }) {
  const tested = fn.status === "tested";
  const parts = summaryParts(fn);
  return (
    <div className="fn-card">
      <div className="row">
        <div>
          <code>
            {fn.file}::{fn.name}
          </code>{" "}
          <span className="muted small">({fn.change_type})</span>
        </div>
        <span className={`badge fn-${fn.status}`}>{FUNCTION_STATUS_TEXT[fn.status] ?? fn.status}</span>
      </div>
      {!tested && fn.reason && (
        <p className="small muted">
          <code>{fn.reason}</code>
        </p>
      )}
      {!tested && !fn.reason && fn.status === "could_not_test" && (
        <p className="small muted">No test result was reported. This is not a pass.</p>
      )}
      {tested && (
        <>
          <p className="small">{parts.join(" · ")}</p>
          {fn.repair_rounds > 0 && (
            <p className="small muted">
              The AI repaired these tests {fn.repair_rounds === 1 ? "once" : `${fn.repair_rounds} times`}{" "}
              after some of them failed on the old code too. Only the newest version is shown.
            </p>
          )}
          <ul className="tests">
            {fn.tests.map((test) => (
              <li key={test.name}>
                {KIND_ICON[test.kind] ?? "•"} <code>{test.name}</code>: {kindText(test)}
                {test.message && (test.kind === "catches" || test.kind === "flaky" || test.kind === "new_fails" || test.kind === "unreliable") && (
                  <div className="small muted">
                    failure: <code>{test.message}</code>
                  </div>
                )}
              </li>
            ))}
          </ul>
          <Mutation fn={fn} />
        </>
      )}
    </div>
  );
}

const SEVERITY_ICON: Record<string, string> = { high: "🔴", medium: "🟠", low: "🟡", unknown: "⚪" };

const TRIAGE_LABEL: Record<string, string> = {
  likely_real: "looks real",
  likely_false_positive: "looks like a false alarm",
  unsure: "not sure",
};

function FindingRow({ f }: { f: FindingView }) {
  const where = f.file ? `${f.file}${f.line ? `:${f.line}` : ""}` : "?";
  return (
    <li>
      {SEVERITY_ICON[f.severity] ?? "⚪"} <code>{f.rule_id}</code> ({f.tool}, {f.severity}
      {f.cwe ? `, ${f.cwe}` : ""}) in <code>{where}</code>: {f.message}
      {f.triage && TRIAGE_LABEL[f.triage] && (
        <div className="small muted">
          🤖 AI: {TRIAGE_LABEL[f.triage]}
          {f.triage_reason ? ` (${f.triage_reason})` : ""} — a hint only, the finding is never hidden.
        </div>
      )}
    </li>
  );
}

function FindingsSection({ data }: { data: FindingsView }) {
  const noScanners = data.scanners.length === 0;
  const ranAny = data.scanners.some((s) => s.status === "ran");
  const fresh = data.findings.filter((f) => f.origin !== "existing");
  const old = data.findings.filter((f) => f.origin === "existing");
  const { counts } = data;

  let summary: string;
  if (counts.new > 0) {
    summary = `${counts.new} new finding(s) introduced by this commit`;
    if (counts.existing) summary += `; ${counts.existing} already existed before it`;
    if (counts.unknown) summary += `; ${counts.unknown} could not be compared with the old code`;
    summary += ".";
  } else if (counts.unknown > 0) {
    summary = `Found ${counts.unknown} finding(s), but the old code could not be scanned, so nobody can tell whether this commit introduced them.`;
  } else if (ranAny) {
    summary = `No new findings in this commit.${counts.existing ? ` ${counts.existing} finding(s) already existed before it.` : ""}`;
  } else {
    summary = "No scanner ran, so nothing was checked. This is not a pass.";
  }

  return (
    <div className="fn-card">
      <h4>Security checks</h4>
      {noScanners ? (
        <p className="small muted">No scanner results were reported for this run.</p>
      ) : (
        <>
          <ul className="tests">
            {data.scanners.map((s) => (
              <li key={s.tool}>
                <code>{s.tool}</code>{s.version ? ` ${s.version}` : ""}:{" "}
                {s.status === "ran" ? "ran" : <span className="warn">could not run</span>}
                {s.reason && <span className="muted small"> ({s.reason})</span>}
                {s.status === "ran" && !s.compared_with_base && (
                  <span className="muted small"> (the old code could not be scanned, so nothing is called new)</span>
                )}
              </li>
            ))}
          </ul>
          <p className="small">{summary}</p>
          {fresh.length > 0 && (
            <ul className="tests">
              {fresh.map((f) => (
                <FindingRow key={f.id} f={f} />
              ))}
            </ul>
          )}
          {old.length > 0 && (
            <details>
              <summary className="small">{old.length} finding(s) that already existed before this commit</summary>
              <ul className="tests">
                {old.map((f) => (
                  <FindingRow key={f.id} f={f} />
                ))}
              </ul>
            </details>
          )}
          {data.omitted > 0 && (
            <p className="small muted">{data.omitted} more finding(s) were not sent because there were too many.</p>
          )}
          <p className="small muted">{data.note}</p>
        </>
      )}
    </div>
  );
}

export default function RunPanel({ runId, onClose }: { runId: string; onClose: () => void }) {
  const [view, setView] = useState<RunView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [stopped, setStopped] = useState(false);
  const [copied, setCopied] = useState(false);
  const [findings, setFindings] = useState<FindingsView | null>(null);
  const [findingsError, setFindingsError] = useState<string | null>(null);
  const startedAt = useRef(Date.now());
  const timer = useRef<number | undefined>(undefined);

  const load = useCallback(async (): Promise<RunView | null> => {
    try {
      const data = await getRunFunctions(runId);
      setView(data);
      setError(null);
      if (data.status !== "running") {
        getRunFindings(runId).then(setFindings).catch(() => undefined);
      }
      return data;
    } catch (e) {
      setError((e as Error).message);
      // A missing run will not appear by waiting; anything else may be a sleeping server.
      return e instanceof ApiError && e.status === 404 ? null : view;
    }
  }, [runId, view]);

  useEffect(() => {
    let cancelled = false;
    startedAt.current = Date.now();
    setStopped(false);
    setView(null);
    setFindings(null);
    setFindingsError(null);

    async function tick() {
      let data: RunView | null = null;
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
      const going = data === null || data.status === "running";
      if (!going) return;
      if (Date.now() - startedAt.current > MAX_POLL_MS) {
        setStopped(true);
        return;
      }
      timer.current = window.setTimeout(tick, POLL_MS);
    }

    tick();
    return () => {
      cancelled = true;
      window.clearTimeout(timer.current);
    };
  }, [runId]);

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(runLink(runId));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }

  // Scanner results arrive just before a run finishes, so ask once the run is no longer going.
  const runStatus = view?.status;
  useEffect(() => {
    if (!runStatus || runStatus === "running") return;
    let cancelled = false;
    getRunFindings(runId)
      .then((data) => {
        if (!cancelled) {
          setFindings(data);
          setFindingsError(null);
        }
      })
      .catch((e: Error) => {
        if (!cancelled) setFindingsError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, [runId, runStatus]);

  const running = view?.status === "running";
  return (
    <div className="run-view">
      <div className="row">
        <h3>
          Run details{" "}
          {view && <span className={`badge ${view.status}`}>{RUN_STATUS_TEXT[view.status] ?? view.status}</span>}
        </h3>
        <div className="actions">
          <button onClick={copyLink}>{copied ? "Copied" : "Copy link"}</button>
          <button onClick={load}>Refresh</button>
          <button onClick={onClose}>Close</button>
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      {!view && !error && <p className="muted">Loading…</p>}
      {running && !stopped && (
        <p className="small muted">This run is still going. The page updates by itself.</p>
      )}
      {running && stopped && (
        <p className="small warn">This page stopped updating. Press Refresh to check again.</p>
      )}
      {view && view.status === "could_not_run" && (
        <p className="warn">
          Could not run{view.reason ? `: ${view.reason}` : ""}. This is not a pass. Nothing was checked in this run.
        </p>
      )}
      {view && view.status === "failed" && (
        <p className="error">The run failed{view.reason ? `: ${view.reason}` : ""}.</p>
      )}
      {view && view.functions.length === 0 && view.status !== "could_not_run" && view.status !== "failed" && (
        <p className="muted small">No changed Python functions were found in this commit. Nothing to check.</p>
      )}
      {view?.functions.map((fn) => <FunctionCard key={fn.id} fn={fn} />)}
      {view && view.functions.some((f) => f.status === "tested") && (
        <p className="small muted">{view.ai_note}</p>
      )}
      {findings && <FindingsSection data={findings} />}
      {findingsError && <p className="small error">Security checks could not be loaded: {findingsError}</p>}
    </div>
  );
}
