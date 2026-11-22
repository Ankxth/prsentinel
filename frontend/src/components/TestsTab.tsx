import type { RunFunctionView, RunView, TestView } from "../lib/api";
import { Chip, plural, type Tone } from "./ui";

const FUNCTION_STATUS: Record<string, { text: string; tone: Tone }> = {
  pending: { text: "Waiting for the Action", tone: "mute" },
  generated: { text: "Not run yet", tone: "mute" },
  tested: { text: "Tested", tone: "ok" },
  skipped: { text: "Not tested", tone: "warn" },
  could_not_generate: { text: "Not tested", tone: "warn" },
  could_not_test: { text: "Not tested", tone: "warn" },
};

// Order matters: the most useful groups come first.
const KINDS: { kind: string; label: string; tone: Tone; desc: string }[] = [
  { kind: "catches", label: "May have broken something", tone: "bad", desc: "These tests passed before your change and fail now. Either your change broke something, or the behaviour changed on purpose." },
  { kind: "flaky", label: "Inconsistent", tone: "warn", desc: "These gave different answers when run again, so they cannot tell us anything reliable." },
  { kind: "odd", label: "Behaviour changed", tone: "info", desc: "These failed before your change but pass now. Check that the new behaviour is what you wanted." },
  { kind: "unreliable", label: "The test may be wrong", tone: "warn", desc: "These fail on your old code too, so the test itself may be faulty rather than your code." },
  { kind: "new_fails", label: "Fails on a new function", tone: "warn", desc: "This function is new, so there is nothing older to compare with. Check whether the test or the code is wrong." },
  { kind: "same", label: "Works the same as before", tone: "ok", desc: "These behave the same on your old and your new code." },
];
const SHOW_MESSAGE = new Set(["catches", "flaky", "new_fails", "unreliable"]);

// Parametrized tests have names like test_add[1-2-3]. They are shown as one line with a count.
const baseName = (name: string) => name.split("[")[0];

/** The source of one test function (with its decorators) out of the generated test file. */
function sourceOf(code: string | null, base: string): string | null {
  if (!code) return null;
  const lines = code.split("\n");
  const i = lines.findIndex((l) => new RegExp(`^(async\\s+)?def\\s+${base}\\b`).test(l));
  if (i < 0) return null;
  let k = i - 1;
  while (k >= 0 && lines[k].trim() !== "") k--;
  const block = lines.slice(k + 1, i);
  const firstDecorator = block.findIndex((l) => l.startsWith("@"));
  const start = firstDecorator >= 0 ? k + 1 + firstDecorator : i;
  let end = i + 1;
  while (end < lines.length && (lines[end].trim() === "" || /^\s/.test(lines[end]))) end++;
  while (end > start && lines[end - 1].trim() === "") end--;
  return lines.slice(start, end).join("\n");
}

function Mutation({ fn }: { fn: RunFunctionView }) {
  const m = fn.mutation;
  if (!m) return null;
  if (m.status !== "measured") {
    return <p className="small muted">Mutation check not measured: {m.reason}</p>;
  }
  const extras: string[] = [];
  if (m.equivalent) extras.push(`${m.equivalent} changed nothing and were left out`);
  if (m.could_not_run) extras.push(`${m.could_not_run} could not be run`);
  if (m.capped) extras.push("only the first faults were tried");
  if (m.stopped_early) extras.push("stopped early because of the time limit");
  return (
    <div className="group">
      <div className="group-head">
        <Chip tone={m.percent >= 80 ? "ok" : "warn"}>Mutation check {m.percent}%</Chip>
      </div>
      <p className="group-desc">
        The tests caught {m.killed} of {m.killed + m.survived} small deliberate faults
        {extras.length > 0 && ` (${extras.join("; ")})`}.
      </p>
      {m.survivors.length > 0 && (
        <p className="small">
          Faults the tests missed:{" "}
          {m.survivors.map((s, i) => (
            <span key={i}>
              {i > 0 && ", "}
              <code>{s}</code>
            </span>
          ))}
        </p>
      )}
      <p className="small muted">{m.note}</p>
    </div>
  );
}

function Cases({ tests, kind, code }: { tests: TestView[]; kind: string; code: string | null }) {
  const byBase = new Map<string, TestView[]>();
  for (const t of tests) {
    const key = baseName(t.name);
    byBase.set(key, [...(byBase.get(key) ?? []), t]);
  }
  return (
    <ul className="cases">
      {[...byBase.entries()].map(([base, items]) => {
        const first = items.find((t) => t.message) ?? items[0];
        const source = sourceOf(code, base);
        return (
          <li key={base}>
            <details className="case">
              <summary>
                <code>{base}</code>
                {items.length > 1 && <span className="muted small"> · {plural(items.length, "case")}</span>}
                {kind === "flaky" && (
                  <span className="muted small">
                    {" "}· {plural(first.rerun_passes, "rerun")} passed, {first.rerun_fails} failed
                  </span>
                )}
                <span className="code-hint">show test</span>
              </summary>
              {source ? <pre className="code">{source}</pre> : <p className="muted small" style={{ marginLeft: 16 }}>The test code is not available for this run.</p>}
            </details>
            {SHOW_MESSAGE.has(kind) && first.message && (
              <div className="failure">
                <code>{first.message}</code>
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function FunctionCard({ fn }: { fn: RunFunctionView }) {
  const status = FUNCTION_STATUS[fn.status] ?? { text: fn.status, tone: "mute" as Tone };
  const tested = fn.status === "tested";
  const attention = fn.counts.catches + fn.counts.flaky + fn.counts.odd + fn.counts.unreliable + fn.counts.new_fails > 0;
  return (
    <details className="fn" open={attention}>
      <summary>
        <span className="fn-title">
          <code>{fn.name}</code>
          <span className="muted small"> {fn.file} · {fn.change_type}</span>
        </span>
        <span className="chips">
          {tested ? (
            KINDS.filter((k) => (fn.counts as Record<string, number>)[k.kind] > 0).map((k) => (
              <Chip key={k.kind} tone={k.tone}>
                {(fn.counts as Record<string, number>)[k.kind]} {k.label.toLowerCase()}
              </Chip>
            ))
          ) : (
            <Chip tone={status.tone}>{status.text}</Chip>
          )}
        </span>
      </summary>
      <div className="fn-body">
        {!tested && fn.reason && (
          <p className="small muted">
            <code>{fn.reason}</code>
          </p>
        )}
        {!tested && !fn.reason && fn.status === "could_not_test" && (
          <p className="small muted">No test result was reported. This is not a pass.</p>
        )}
        {tested &&
          KINDS.map((k) => {
            const tests = fn.tests.filter((t) => t.kind === k.kind);
            if (tests.length === 0) return null;
            return (
              <div className="group" key={k.kind}>
                <div className="group-head">
                  <Chip tone={k.tone}>{k.label}</Chip>
                  <span className="muted small">{plural(tests.length, "test")}</span>
                </div>
                <p className="group-desc">{k.desc}</p>
                <Cases tests={tests} kind={k.kind} code={fn.test_code} />
              </div>
            );
          })}
        {tested && fn.repair_rounds > 0 && (
          <p className="small muted">
            The AI repaired these tests {fn.repair_rounds === 1 ? "once" : `${fn.repair_rounds} times`} after some
            failed on the old code too. Only the newest version is shown.
          </p>
        )}
        {tested && <Mutation fn={fn} />}
        {tested && fn.test_code && (
          <details className="group">
            <summary className="small" style={{ cursor: "pointer" }}>Show every test that ran for this function</summary>
            <pre className="code" style={{ marginTop: 8 }}>{fn.test_code}</pre>
          </details>
        )}
      </div>
    </details>
  );
}

export default function TestsTab({ view }: { view: RunView }) {
  if (view.functions.length === 0) {
    if (view.status === "could_not_run" || view.status === "failed") return null;
    return <p className="muted">No changed Python functions were found in this commit. Nothing to check.</p>;
  }
  return (
    <div>
      {view.functions.map((fn) => (
        <FunctionCard key={fn.id} fn={fn} />
      ))}
      {view.functions.some((f) => f.status === "tested") && <p className="small muted">{view.ai_note}</p>}
    </div>
  );
}
