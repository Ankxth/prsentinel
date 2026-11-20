"""Proof agent in the user's own runner (feature E3). Standard library only.

For up to 3 new Bandit command-injection findings (B602, B604, B605) the server's AI writes a
small test, one round at a time. Here each test is checked (proof_linter), run three times in a
scrubbed child process with a harmless marker string, and the outcome is sent back. The server
settles the finding or asks for a better test, at most 5 tries per finding.

Only a function's own source is sent (never a file that looks like it holds a secret). Tests run
here, never on the server. Any problem in this step is logged and changes nothing else.
"""
import ast
import json
import os
import re
import secrets
import shutil

import client
import proof_linter
import pytest_runner
from scanners import SECRET_LIKE, TRIAGE_MAX_FILE_BYTES, clip

RULES = ("B602", "B604", "B605")
MAX_FINDINGS = 3
MAX_ROUNDS = 6          # the server stops at 5 tries; one more call settles the last result
RUNS_PER_TEST = 3
MAX_SOURCE = 6000
RUN_TIMEOUT = 30
HARNESS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prs_harness.py")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def module_name(rel):
    """The dotted module a file can be imported as, or None."""
    parts = rel.replace("\\", "/").split("/")
    if not parts or not parts[-1].endswith(".py"):
        return None
    parts[-1] = parts[-1][:-3]
    if parts[0] == "src" and len(parts) > 1:
        parts = parts[1:]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts or not all(_IDENT.match(p) for p in parts):
        return None
    return ".".join(parts)


def extract_function(root, rel, line):
    """{"file","name","source","import_line","class_name"} for the top-level function that holds
    `line`, or None when that is not possible or not safe (decorated, nested, a method, too big,
    outside the repository, or it looks like it holds a secret)."""
    module = module_name(rel or "")
    if not module or not isinstance(line, int):
        return None
    base = os.path.realpath(root)
    path = os.path.realpath(os.path.join(base, rel))
    if not path.startswith(base + os.sep):
        return None
    try:
        if os.path.getsize(path) > TRIAGE_MAX_FILE_BYTES:
            return None
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
        tree = ast.parse(text)
    except (OSError, SyntaxError, ValueError):
        return None
    for node in tree.body:
        if (isinstance(node, ast.FunctionDef) and not node.decorator_list
                and node.lineno <= line <= (node.end_lineno or node.lineno)):
            source = "\n".join(text.splitlines()[node.lineno - 1:node.end_lineno])
            if len(source) > MAX_SOURCE or SECRET_LIKE.search(source):
                return None
            return {"file": rel, "name": node.name, "source": source,
                    "import_line": "from %s import %s" % (module, node.name), "class_name": None}
    return None


def candidates(findings, root, all_findings=None):
    """The new command-injection findings worth a proof attempt: [(fingerprint, function)]."""
    secret_files = {f["file"] for f in (all_findings or findings) if f["tool"] == "detect-secrets"}
    out = []
    for f in sorted(findings, key=lambda x: (x.get("file") or "", x.get("line") or 0)):
        if len(out) >= MAX_FINDINGS:
            break
        if (f["tool"] != "bandit" or f["rule_id"] not in RULES
                or f["origin"] not in ("new", "unknown") or f["file"] in secret_files):
            continue
        function = extract_function(root, f["file"], f["line"])
        if function:
            out.append((f["fingerprint"], function))
    return out


def _read_hit(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return bool(json.load(handle).get("dangerous"))
    except (OSError, ValueError, AttributeError):
        return False


def execute(root, code, function, attempt, python=None):
    """Check and run one test. Returns the body of the "result" the server expects."""
    errors = proof_linter.lint(code, function["import_line"])
    if errors:
        return {"attempt": attempt, "lint_ok": False, "lint_errors": [clip(e, 200) for e in errors],
                "outcomes": [], "output": ""}
    folder = os.path.join(root, pytest_runner.TEST_DIR)
    os.makedirs(folder, exist_ok=True)
    outcomes, notes = [], []
    try:
        shutil.copyfile(HARNESS_FILE, os.path.join(folder, "prs_harness.py"))
        test_path = os.path.join(folder, "test_proof_%d.py" % attempt)
        with open(test_path, "w", encoding="utf-8") as handle:
            handle.write(code)
        for index in range(1, RUNS_PER_TEST + 1):
            result_file = os.path.join(folder, "proof-result-%d-%d.json" % (attempt, index))
            env = {"PRS_CANARY": "PRS" + secrets.token_hex(8), "PRS_RESULT_FILE": result_file}
            done = pytest_runner.run_file(root, test_path, RUN_TIMEOUT, index, python, env)
            failed = [n for n, r in done["tests"].items() if r in ("failed", "error")]
            if done["status"] == pytest_runner.TIMEOUT:
                outcomes.append("timeout")
                notes.append(done["error"])
            elif done["status"] != pytest_runner.OK:
                outcomes.append("error")
                notes.append(done["error"])
            elif failed and _read_hit(result_file):
                outcomes.append("canary")
            elif failed:
                outcomes.append("error")
                notes.append(" ".join(done["messages"].get(n, "") for n in failed))
            else:
                outcomes.append("clean")
                notes.append("the test passed and no marker reached a shell command")
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return {"attempt": attempt, "lint_ok": True, "lint_errors": [], "outcomes": outcomes,
            "output": clip(" | ".join(n for n in notes[:1] if n), 1500)}


def prove(api_url, run_id, token, fingerprint, function, root, kwargs, python=None):
    """Run the back-and-forth for one finding. Returns the server's final {verdict, reason}."""
    url = "%s/v1/runs/%s/findings/%s/agent/step" % (api_url, run_id, fingerprint)
    result = None
    for _ in range(MAX_ROUNDS):
        reply = client.request_json("POST", url, token=token,
                                    body={"function": function, "result": result}, **kwargs)
        if reply.get("action") != "run_test":
            return {"verdict": reply.get("verdict"), "reason": reply.get("reason")}
        result = execute(root, reply["test_code"], function, reply["attempt"], python)
    return {"verdict": "not_reproduced", "reason": "Stopped after too many rounds."}


def run(api_url, run_id, token, findings, root, kwargs, log, all_findings=None, python=None):
    """Try to reproduce the new command-injection findings. Returns {fingerprint: verdict}."""
    picked = candidates(findings, root, all_findings)
    if not picked:
        return {}
    ok, why = pytest_runner.pytest_available(python)
    if not ok:
        log("PRSentinel: no proof tests were tried because pytest is not available (%s)." % clip(why, 100))
        return {}
    verdicts = {}
    for fingerprint, function in picked:
        try:
            final = prove(api_url, run_id, token, fingerprint, function, root, kwargs, python)
        except client.ApiError as err:
            log("PRSentinel: proof attempt stopped (%s)." % clip(err.detail, 120))
            continue
        verdicts[fingerprint] = final["verdict"]
        log("PRSentinel: %s(): %s." % (function["name"], clip(final["reason"] or final["verdict"], 160)))
    return verdicts
