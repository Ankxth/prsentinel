import { useState } from "react";
import { ApiError, getFindingAttempts, type FindingsView, type FindingView, type ProofAttempts } from "../lib/api";
import { explain, SEVERITY_PLAIN } from "../lib/plain";
import { Chip, type Tone } from "./ui";

const SEVERITY_TONE: Record<string, Tone> = { high: "bad", medium: "warn", low: "info", unknown: "mute" };

const OUTCOME_TEXT: Record<string, string> = {
  canary: "the harmless marker reached the shell",
  clean: "the marker did not reach the shell",
  error: "the test failed to run",
  timeout: "the test timed out",
};

function AgentWork({ runId, findingId }: { runId: string; findingId: string }) {
  const [data, setData] = useState<ProofAttempts | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  function toggle() {
    setOpen(!open);
    if (!data && !error) {
      getFindingAttempts(runId, findingId)
        .then(setData)
        .catch((e) => setError(e instanceof ApiError ? e.message : "Could not load."));
    }
  }
  return (
    <div className="agent">
      <button type="button" className="link" onClick={toggle}>
        {open ? "Hide the tests the agent tried" : "Show the tests the agent tried"}
      </button>
      {open && error && <p className="error">{error}</p>}
      {open && data && <p className="muted small">{data.how_to_read}</p>}
      {open && data && (
        <ol>
          {data.attempts.map((a) => (
            <li key={a.attempt}>
              <pre>{a.test_code}</pre>
              {a.lint_ok === false ? (
                <p className="muted small">The safety checker rejected this test: {a.lint_errors.join("; ")}</p>
              ) : a.outcomes.length > 0 ? (
                <p className="muted small">
                  Run three times: {a.outcomes.map((o) => OUTCOME_TEXT[o] ?? o).join("; ")}
                  {a.output ? `. ${a.output}` : ""}
                </p>
              ) : (
                <p className="muted small">This test was not run.</p>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function evidence(f: FindingView): { text: string; tone: Tone } | null {
  if (f.proof === "verified") return { text: "Confirmed by a test run", tone: "bad" };
  if (f.proof === "needs_review") return { text: "Test results were mixed", tone: "warn" };
  if (f.proof === "not_reproduced") return { text: "Could not confirm (this does not mean it is safe)", tone: "mute" };
  if (f.triage === "likely_real") return { text: "AI thinks this is real", tone: "warn" };
  if (f.triage === "likely_false_positive") return { text: "AI thinks this is a false alarm", tone: "ok" };
  return null;
}

function Qa({ q, children }: { q: string; children: React.ReactNode }) {
  return (
    <div className="qa">
      <div className="qa-q">{q}</div>
      {children}
    </div>
  );
}

function Finding({ f, runId }: { f: FindingView; runId: string }) {
  const info = explain(f.tool, f.rule_id, f.message);
  const where = f.file ? `${f.file}${f.line ? `, line ${f.line}` : ""}` : "unknown location";
  const ev = evidence(f);
  return (
    <div className="issue">
      <div className="issue-head">
        <div className="chips">
          <Chip tone={SEVERITY_TONE[f.severity] ?? "mute"}>{SEVERITY_PLAIN[f.severity] ?? "Not rated"}</Chip>
          {ev && <Chip tone={ev.tone}>{ev.text}</Chip>}
        </div>
        <h3>{info.title}</h3>
        <div className="issue-where">
          Found in <code>{where}</code>
        </div>
      </div>
      <div className="issue-body">
        <Qa q="What this means">
          <p>{info.what}</p>
        </Qa>
        {f.proof_explanation && (
          <Qa q="What our test run showed">
            <p>{f.proof_explanation}</p>
          </Qa>
        )}
        <Qa q="What you can do">
          <p>{f.proof_next_step ?? info.fix}</p>
        </Qa>
        {f.triage_reason && f.triage !== "likely_false_positive" && (
          <Qa q="What the AI thinks">
            <p>{f.triage_reason}</p>
          </Qa>
        )}
        {f.triage === "likely_false_positive" && (
          <Qa q="What the AI thinks">
            <p>
              This may be a false alarm{f.triage_reason ? `: ${f.triage_reason}` : "."} It is shown anyway, because
              the AI can be wrong.
            </p>
          </Qa>
        )}
        <details className="tech">
          <summary>Technical details</summary>
          <dl className="tech-grid">
            <dt>Scanner</dt>
            <dd>{f.tool}</dd>
            <dt>Rule</dt>
            <dd><code>{f.rule_id}</code>{f.cwe ? ` (${f.cwe})` : ""}</dd>
            <dt>Scanner message</dt>
            <dd>{f.message}</dd>
            {f.proof_reason && (<><dt>Test run result</dt><dd>{f.proof_reason}</dd></>)}
          </dl>
          {f.proof && f.proof !== "not_attempted" && <AgentWork runId={runId} findingId={f.id} />}
        </details>
      </div>
    </div>
  );
}

export default function SecurityTab({ data }: { data: FindingsView }) {
  const ranAny = data.scanners.some((s) => s.status === "ran");
  const fresh = data.findings.filter((f) => f.origin !== "existing");
  const old = data.findings.filter((f) => f.origin === "existing");
  const { counts } = data;

  let tone: "ok" | "warn" | "bad" = "ok";
  let title: string;
  let body: string;
  if (data.scanners.length === 0 || !ranAny) {
    tone = "warn";
    title = "Security was not checked";
    body = "No scanner ran for this change, so nothing was checked. This is not a pass.";
  } else if (counts.new > 0) {
    tone = "bad";
    title = counts.new === 1 ? "1 new security problem" : `${counts.new} new security problems`;
    body = "These appeared with this change. They are listed below, most serious first, with what each one means and what to do.";
  } else if (counts.unknown > 0) {
    tone = "warn";
    title = "We could not tell what is new";
    body = `${counts.unknown} problem(s) were found, but the older version of the code could not be scanned for comparison.`;
  } else {
    title = "No new security problems";
    body = counts.existing
      ? `This change did not add any. ${counts.existing} older problem(s) were already in your code before it.`
      : "The scanners found nothing risky in this change.";
  }

  return (
    <div>
      <div className={`verdict ${tone}`}>
        <h2>{title}</h2>
        <p>{body}</p>
        {data.scanners.length > 0 && (
          <div className="chips" style={{ marginTop: 12 }}>
            {data.scanners.map((s) => (
              <span key={s.tool} title={s.reason ?? undefined}>
                <Chip tone={s.status === "ran" ? "ok" : "warn"}>
                  {s.tool}: {s.status === "ran" ? "checked" : "could not run"}
                </Chip>
              </span>
            ))}
          </div>
        )}
      </div>

      {fresh.length > 0 && <h3 style={{ margin: "24px 0 12px" }}>New in this change</h3>}
      {fresh.map((f) => <Finding key={f.id} f={f} runId={data.run_id} />)}

      {old.length > 0 && (
        <details className="fn" style={{ marginTop: 20 }}>
          <summary>
            <span className="fn-title">Already in your code before this change ({old.length})</span>
            <span className="muted small">not caused by this change</span>
          </summary>
          <div className="fn-body">
            {old.map((f) => <Finding key={f.id} f={f} runId={data.run_id} />)}
          </div>
        </details>
      )}
      {data.omitted > 0 && <p className="small muted">{data.omitted} more problem(s) were not sent because there were too many.</p>}
      <p className="small muted" style={{ marginTop: 16 }}>{data.note}</p>
    </div>
  );
}
