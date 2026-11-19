"""Security scanners in the user's own runner (feature E1). Standard library only.

Three scanners look at the code, all of them without running it:
  * Bandit          risky code (shell=True, pickle, weak hashes, ...)
  * pip-audit       dependencies with known vulnerabilities (pinned requirements files)
  * detect-secrets  passwords and keys committed to the repository

They are installed into a throwaway virtual environment outside the user's repository, so they
never touch the user's own packages. Each one is run on the NEW code and on the OLD code (the
base commit); a finding that is not in the old results is "new" in this commit, one that is there
is "existing". If the old code cannot be scanned, nothing is called new: it is "unknown".

Findings are matched by a fingerprint (a hash of tool, rule, file and the text of the line, plus a
counter for repeats), so moving code around does not make an old finding look new. A line that was
edited does count as new. Only the fingerprint, rule, place and a short message are sent to the
server. No source code and no secret value ever leave the runner.

A scanner that fails is reported as "could not run" with a reason. That is never a pass.
Any problem in this step changes nothing else: the run still completes.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

import client
import diff
import testing_phase
from pytest_runner import child_env

# Pinned so a new release cannot change results between two runs.
PINS = (
    ("bandit", "bandit==1.9.4"),
    ("pip-audit", "pip-audit==2.10.1"),
    ("detect-secrets", "detect-secrets==1.5.0"),
)
TOOLS = tuple(name for name, _ in PINS)

VENV_TIMEOUT = 120
INSTALL_TIMEOUT = 240
SCAN_TIMEOUT = 150
TOTAL_BUDGET_SECONDS = 600
MAX_SEND = 500
MAX_MESSAGE = 300
MAX_REQUIREMENT_FILES = 5
MAX_WALK_DEPTH = 3

SKIP_DIRS = {".git", ".venv", "venv", "env", "node_modules", ".tox", "build", "dist",
             ".prsentinel_tests", "site-packages", "__pycache__", ".mypy_cache", ".pytest_cache"}
# Bandit flags every `assert` in test files, which is noise, so test code is left out.
BANDIT_EXCLUDE = ",".join([
    "./.git", "./.venv", "./venv", "./env", "./node_modules", "./.tox", "./build", "./dist",
    "./.prsentinel_tests", "./tests", "./test", "*/tests/*", "*/test/*", "*/test_*.py",
    "*/*_test.py", "*/conftest.py",
])
SECRETS_EXCLUDE = (r"(^|/)(\.git|\.venv|venv|env|node_modules|\.tox|\.prsentinel_tests)/"
                   r"|(^|/)(package-lock\.json|yarn\.lock|poetry\.lock|Pipfile\.lock|uv\.lock)$")

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "unknown": 3}
ORIGIN_ORDER = {"new": 0, "unknown": 1, "existing": 2}
REQUIREMENTS_NAME = re.compile(r"^requirements[\w.\-]*\.txt$", re.IGNORECASE)


class ScanError(Exception):
    """A scanner could not produce results. The text is a short reason that is safe to show."""


class NoDependencies(ScanError):
    """The tree has no dependency files at all (a clear answer, unlike a failure)."""


# ------------------------------------------------------------------------------ small helpers

def clip(text, limit=MAX_MESSAGE):
    return " ".join(str(text or "").split())[:limit]


def tail(text, limit=200):
    cleaned = " ".join(str(text or "").split())
    return cleaned[-limit:] if cleaned else "no details"


def level(value):
    value = str(value or "").lower()
    return value if value in ("high", "medium", "low") else "unknown"


def rel_path(name, root):
    """A path relative to `root`, with forward slashes."""
    name = str(name or "")
    if os.path.isabs(name):
        try:
            name = os.path.relpath(name, root)
        except ValueError:
            pass
    name = name.replace("\\", "/")
    while name.startswith("./"):
        name = name[2:]
    return name


def source_line(root, path, line):
    """The text of one line, with whitespace collapsed, or None if it cannot be read."""
    if not path or not line:
        return None
    try:
        with open(os.path.join(root, path), encoding="utf-8", errors="replace") as handle:
            for number, text in enumerate(handle, 1):
                if number == line:
                    return " ".join(text.split())[:300]
    except OSError:
        return None
    return None


def default_run_cmd(cmd, cwd, timeout):
    """Run a command with a scrubbed environment (no tokens, no secrets)."""
    env = child_env()
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PIP_NO_INPUT"] = "1"
    try:
        return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ScanError("it took longer than %d seconds" % timeout)
    except OSError as err:
        raise ScanError("it could not be started (%s)" % err.__class__.__name__)


def fingerprint(tool, rule, path, key_text, index):
    raw = "\x1f".join([tool, rule, path or "", key_text or "", str(index)])
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()


def add_fingerprints(findings):
    """Give each finding a stable fingerprint. Repeats of the same thing get a counter, counted
    in file order, so adding code elsewhere in the file does not change them."""
    seen = {}
    for item in sorted(findings, key=lambda f: (f["file"] or "", f["line"] or 0, f["rule_id"])):
        key = (item["tool"], item["rule_id"], item["file"], item["key_text"])
        index = seen.get(key, 0)
        seen[key] = index + 1
        item["fingerprint"] = fingerprint(item["tool"], item["rule_id"], item["file"],
                                          item["key_text"], index)
    return findings


def mark_origins(new_findings, base_findings, compared):
    """Say which findings are new, which already existed, and which cannot be told."""
    old = {f["fingerprint"] for f in (base_findings or [])}
    for item in new_findings:
        if not compared:
            item["origin"] = "unknown"
        else:
            item["origin"] = "existing" if item["fingerprint"] in old else "new"
    return new_findings


# ------------------------------------------------------------------------------ the scanners

def parse_bandit(stdout, root):
    try:
        data = json.loads(stdout)
    except ValueError:
        raise ScanError("its output could not be read")
    found = []
    for item in data.get("results", []):
        path = rel_path(item.get("filename"), root)
        line = item.get("line_number") if isinstance(item.get("line_number"), int) else None
        cwe = (item.get("issue_cwe") or {}).get("id")
        found.append({
            "tool": "bandit", "rule_id": clip(item.get("test_id") or "bandit", 60),
            "severity": level(item.get("issue_severity")),
            "confidence": level(item.get("issue_confidence")),
            "file": path, "line": line, "message": clip(item.get("issue_text")),
            "cwe": ("CWE-%s" % cwe) if cwe else None,
            "key_text": source_line(root, path, line) or clip(item.get("issue_text")),
        })
    return add_fingerprints(found)


def scan_bandit(py, root, run_cmd):
    cmd = [py, "-m", "bandit", "-r", ".", "-f", "json", "-q", "-x", BANDIT_EXCLUDE]
    done = run_cmd(cmd, root, SCAN_TIMEOUT)
    # Bandit exits with 1 when it finds something; that is normal.
    if done.returncode not in (0, 1) or not (done.stdout or "").strip():
        raise ScanError("it failed (%s)" % tail(done.stderr))
    return parse_bandit(done.stdout, root), None


def parse_secrets(stdout, root):
    try:
        data = json.loads(stdout)
    except ValueError:
        raise ScanError("its output could not be read")
    found = []
    for path, entries in (data.get("results") or {}).items():
        for entry in entries:
            kind = clip(entry.get("type") or "secret", 60)
            line = entry.get("line_number") if isinstance(entry.get("line_number"), int) else None
            found.append({
                "tool": "detect-secrets", "rule_id": kind, "severity": "medium",
                "confidence": "unknown", "file": rel_path(path, root), "line": line,
                # The secret itself is never read into a message. Only a hash of it identifies it.
                "message": "Possible hard-coded secret (%s). Its value is not stored by PRSentinel." % kind,
                "cwe": "CWE-798", "key_text": str(entry.get("hashed_secret") or ""),
            })
    return add_fingerprints(found)


def scan_secrets(py, root, run_cmd):
    cmd = [py, "-m", "detect_secrets", "scan", "--all-files", "--exclude-files", SECRETS_EXCLUDE]
    # Run from the resolved (long) path: on Windows a short 8.3 name such as ANKITH~1 makes
    # detect-secrets skip every file.
    done = run_cmd(cmd, os.path.realpath(root), SCAN_TIMEOUT)
    if done.returncode != 0 or not (done.stdout or "").strip():
        raise ScanError("it failed (%s)" % tail(done.stderr))
    return parse_secrets(done.stdout, root), None


def find_requirement_files(root):
    """Paths (relative, forward slashes) of requirements*.txt files, shallowest first."""
    found = []
    base_depth = root.rstrip(os.sep).count(os.sep)
    for folder, dirs, files in os.walk(root):
        depth = folder.rstrip(os.sep).count(os.sep) - base_depth
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and depth < MAX_WALK_DEPTH)
        for name in sorted(files):
            if REQUIREMENTS_NAME.match(name):
                found.append(rel_path(os.path.join(folder, name), root))
    found.sort(key=lambda p: (p.count("/"), p))
    return found[:MAX_REQUIREMENT_FILES]


def _pin_line(root, req_path, name):
    wanted = re.sub(r"[-_.]+", "-", name).lower()
    try:
        with open(os.path.join(root, req_path), encoding="utf-8", errors="replace") as handle:
            for number, text in enumerate(handle, 1):
                match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", text)
                if match and re.sub(r"[-_.]+", "-", match.group(1)).lower() == wanted:
                    return number
    except OSError:
        pass
    return None


def parse_pip_audit(stdout, req_path, root):
    try:
        data = json.loads(stdout)
    except ValueError:
        raise ScanError("its output could not be read")
    found = []
    seen = set()
    for dep in data.get("dependencies", []):
        name, version = str(dep.get("name") or "?"), str(dep.get("version") or "?")
        for vuln in dep.get("vulns", []):
            vuln_id = clip(vuln.get("id") or "unknown", 60)
            if (name, version, vuln_id) in seen:
                continue
            seen.add((name, version, vuln_id))
            aliases = [a for a in vuln.get("aliases", []) if str(a).startswith("CVE-")]
            fixes = [str(v) for v in vuln.get("fix_versions", [])]
            message = "%s==%s has a known vulnerability (%s%s); %s" % (
                name, version, vuln_id, ", " + aliases[0] if aliases else "",
                "fixed in " + ", ".join(fixes[:3]) if fixes else "no fixed version is listed")
            found.append({
                "tool": "pip-audit", "rule_id": vuln_id, "severity": "unknown",
                "confidence": "high", "file": req_path, "line": _pin_line(root, req_path, name),
                "message": clip(message), "cwe": None, "key_text": "%s==%s" % (name, version),
            })
    return found


PIN_LINE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._\-]*(?:\[[^\]]*\])?)\s*==\s*([A-Za-z0-9][A-Za-z0-9._!+\-]*)")
DEPENDENCY_LINE = re.compile(r"^\s*[A-Za-z0-9][A-Za-z0-9._\-]*")


def split_requirements(text):
    """Split a requirements file into lines pinned with == (these can be audited) and a count of
    other dependency lines (unpinned: pip-audit cannot tell which version will be installed)."""
    pinned, unpinned = [], 0
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-") or "://" in line:
            continue
        match = PIN_LINE.match(line)
        if match:
            pinned.append("%s==%s" % (match.group(1), match.group(2)))
        elif DEPENDENCY_LINE.match(line):
            unpinned += 1
    return pinned, unpinned


def scan_pip_audit(py, root, run_cmd):
    files = find_requirement_files(root)
    if not files:
        raise NoDependencies("no requirements*.txt file was found, so there were no dependencies to audit")
    found, checked, skipped, unpinned_total = [], 0, [], 0
    scratch = tempfile.mkdtemp(prefix="prsentinel_req_")
    try:
        for number, path in enumerate(files):
            try:
                with open(os.path.join(root, path), encoding="utf-8", errors="replace") as handle:
                    pinned, unpinned = split_requirements(handle.read())
            except OSError:
                skipped.append("%s (could not be read)" % path)
                continue
            unpinned_total += unpinned
            if not pinned:
                skipped.append("%s (no dependency is pinned with ==)" % path)
                continue
            # Only the pinned lines are audited, from a copy outside the repository.
            copy = os.path.join(scratch, "req%d.txt" % number)
            with open(copy, "w", encoding="utf-8") as handle:
                handle.write("\n".join(pinned) + "\n")
            cmd = [py, "-m", "pip_audit", "-r", copy, "--no-deps", "--disable-pip", "-f", "json",
                   "--progress-spinner", "off"]
            done = run_cmd(cmd, root, SCAN_TIMEOUT)
            if done.returncode in (0, 1) and (done.stdout or "").strip():
                found += parse_pip_audit(done.stdout, path, root)
                checked += 1
            else:
                skipped.append("%s (%s)" % (path, tail(done.stderr, 80)))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    if checked == 0:
        raise ScanError("no requirements file could be audited: " + "; ".join(skipped))
    notes = []
    if skipped:
        notes.append("checked %d of %d requirements file(s); skipped: %s"
                     % (checked, len(files), "; ".join(skipped)))
    if unpinned_total:
        notes.append("%d dependency line(s) without == were not checked" % unpinned_total)
    return add_fingerprints(found), "; ".join(notes) or None


SCANNERS = {"bandit": scan_bandit, "pip-audit": scan_pip_audit, "detect-secrets": scan_secrets}


# ------------------------------------------------------------------------------ the whole step

def tool_python(work):
    return os.path.join(work, "venv", "Scripts" if os.name == "nt" else "bin",
                        "python.exe" if os.name == "nt" else "python")


def install_tools(work, run_cmd):
    """Make a throwaway virtual environment and install the pinned scanners. Returns python path."""
    created = run_cmd([sys.executable, "-m", "venv", os.path.join(work, "venv")], work, VENV_TIMEOUT)
    if created.returncode != 0:
        raise ScanError("could not create an environment for the scanners (%s)" % tail(created.stderr))
    py = tool_python(work)
    cmd = [py, "-m", "pip", "install", "--quiet", "--disable-pip-version-check"]
    cmd += [pin for _, pin in PINS]
    installed = run_cmd(cmd, work, INSTALL_TIMEOUT)
    if installed.returncode != 0:
        raise ScanError("could not install the scanners (%s)" % tail(installed.stderr))
    return py


def version_of(tool):
    return dict(PINS)[tool].split("==", 1)[1]


def _sort_key(item):
    return (ORIGIN_ORDER.get(item["origin"], 1), SEVERITY_ORDER.get(item["severity"], 3),
            item["file"] or "", item["line"] or 0, item["rule_id"])


def collect(py, root, old_root, base_is_empty, run_cmd, started, budget, log):
    """Run every scanner on the new code (and the old code). Returns (scanner rows, findings)."""
    rows, everything = [], []
    for tool in TOOLS:
        row = {"tool": tool, "status": "could_not_run", "reason": None,
               "version": version_of(tool), "compared_with_base": False}
        rows.append(row)
        if time.monotonic() - started > budget:
            row["reason"] = "the time budget for scanning was used up"
            continue
        try:
            found, note = SCANNERS[tool](py, root, run_cmd)
        except ScanError as err:
            row["reason"] = clip(str(err), 280)
            log("PRSentinel: %s could not run: %s" % (tool, row["reason"]))
            continue
        row["status"], row["reason"] = "ran", clip(note, 280) if note else None

        base_found, compared = [], False
        if base_is_empty:
            compared = True  # nothing existed before this commit, so everything is new
        elif old_root:
            try:
                base_found, _ = SCANNERS[tool](py, old_root, run_cmd)
                compared = True
            except NoDependencies:
                compared = True  # the old code had no dependency files, so every dependency is new
            except ScanError as err:
                log("PRSentinel: %s could not scan the old code (%s); nothing is called new."
                    % (tool, clip(str(err), 120)))
        row["compared_with_base"] = compared
        everything += mark_origins(found, base_found, compared)
        log("PRSentinel: %s found %d issue(s)." % (tool, len(found)))
    return rows, everything


def build_body(rows, findings):
    findings = sorted(findings, key=_sort_key)
    sent = findings[:MAX_SEND]
    return {
        "scanners": rows,
        "findings": [{
            "tool": f["tool"], "rule_id": f["rule_id"], "severity": f["severity"],
            "confidence": f["confidence"], "file": f["file"], "line": f["line"],
            "message": f["message"], "cwe": f["cwe"], "fingerprint": f["fingerprint"],
            "origin": f["origin"],
        } for f in sent],
        "omitted": len(findings) - len(sent),
    }


def run(api_url, run_id, token, base, root, kwargs, log, annotate, run_cmd=None, tools_python=None,
        budget=TOTAL_BUDGET_SECONDS):
    """Scan, compare with the old code, and report to the server. Returns the body that was sent."""
    run_cmd = run_cmd or default_run_cmd
    started = time.monotonic()
    work = tempfile.mkdtemp(prefix="prsentinel_scan_")
    old_root = None
    try:
        try:
            py = tools_python or install_tools(work, run_cmd)
        except ScanError as err:
            reason = clip(str(err), 280)
            annotate("warning", "PRSentinel scanners did not run: %s" % reason)
            body = {"scanners": [{"tool": t, "status": "could_not_run", "reason": reason,
                                  "version": version_of(t), "compared_with_base": False}
                                 for t in TOOLS], "findings": [], "omitted": 0}
        else:
            base_is_empty = base == diff.EMPTY_TREE
            if not base_is_empty:
                old_root = testing_phase._checkout_old(base, root)
            rows, findings = collect(py, root, old_root, base_is_empty, run_cmd, started, budget, log)
            body = build_body(rows, findings)
        client.request_json("POST", "%s/v1/runs/%s/findings" % (api_url, run_id), token=token,
                            body=body, **kwargs)
        return body
    finally:
        testing_phase._remove_old(old_root, root)
        shutil.rmtree(work, ignore_errors=True)
