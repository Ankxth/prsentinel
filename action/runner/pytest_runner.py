"""Run a generated pytest file inside the user's own environment. Standard library only.

Ported from v1 `test_runner.py` (child environment allow-list, junit parsing, reruns), changed so
the tests run against the REAL repository (the working tree they are given), not a copied module.

Safety, in plain words:
* The AI-written test runs as a child process with a brand new environment built from an allow-list.
  The GitHub identity token, the GitHub token and every other secret are never copied in, because
  nothing that is not on the list gets through.
* The test file is written into a throwaway folder inside the tree under test and removed afterwards.
* It runs on the user's own GitHub runner, never on our server.
"""
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

PASSED, FAILED, ERROR, SKIPPED = "passed", "failed", "error", "skipped"
OK, TIMEOUT, BROKEN = "OK", "TIMEOUT", "ERROR"

TEST_DIR = ".prsentinel_tests"
RUN_INDEX_ENV = "PRSENTINEL_RUN_INDEX"
MAX_MESSAGE_CHARS = 1500
DEFAULT_TIMEOUT = 60

ENV_ALLOW_POSIX = (
    "PATH", "HOME", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL", "LC_CTYPE", "PYTHONPATH",
    "PYTHONHOME", "VIRTUAL_ENV", "SHELL", "TZ", "LD_LIBRARY_PATH",
)
ENV_ALLOW_WINDOWS = (
    "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "TEMP", "TMP",
    "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
    "APPDATA", "LOCALAPPDATA",
)


def child_env(source=None, root=None, run_index=None):
    """A fresh environment for the child: only allow-listed names, never a secret."""
    source = os.environ if source is None else source
    allowed = {n.lower() for n in (ENV_ALLOW_WINDOWS if os.name == "nt" else ENV_ALLOW_POSIX)}
    env = {k: v for k, v in source.items() if k.lower() in allowed}
    paths = []
    if root:
        paths.append(root)
        if os.path.isdir(os.path.join(root, "src")):
            paths.append(os.path.join(root, "src"))
    if env.get("PYTHONPATH"):
        paths.append(env["PYTHONPATH"])
    if paths:
        env["PYTHONPATH"] = os.pathsep.join(paths)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["CI"] = "true"
    if run_index is not None:
        env[RUN_INDEX_ENV] = str(run_index)
    return env


def pytest_available(python=None):
    """(True, version text) when `python -m pytest` works in this environment."""
    try:
        done = subprocess.run([python or sys.executable, "-m", "pytest", "--version"],
                              capture_output=True, text=True, timeout=60, env=child_env())
    except (OSError, subprocess.TimeoutExpired) as err:
        return False, str(err)
    text = (done.stdout + done.stderr).strip()
    return done.returncode == 0, text.splitlines()[0] if text else "no output"


def _short(text):
    text = (text or "").replace("\x00", "")
    return text[:MAX_MESSAGE_CHARS]


def _error_line(text):
    """The most useful single line of a long error: the last "E ..." line, else the last line."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    marked = [ln for ln in lines if ln.startswith("E ")]
    pick = (marked or lines or ["collection failure"])[-1]
    return _short(pick[1:].strip() if pick.startswith("E ") else pick)[:300]


def parse_junit(xml_path, stem):
    """Return {"tests": {name: result}, "messages": {name: text}, "file_error": text}."""
    try:
        tree = ET.parse(xml_path)
    except (ET.ParseError, OSError) as err:
        return {"tests": {}, "messages": {}, "file_error": "no usable report (%s)" % err}
    tests, messages = {}, {}
    for case in tree.iter("testcase"):
        name = case.get("name", "")
        classname = case.get("classname", "") or ""
        error = case.find("error")
        # A file that cannot be imported is one "test" named after the module (dotted path).
        if error is not None and not classname and (name == stem or name.endswith("." + stem)):
            return {"tests": {}, "messages": {}, "file_error": _error_line(error.text or error.get("message"))}
        index = classname.rfind(stem)
        rest = classname[index + len(stem):].lstrip(".") if index >= 0 else classname
        full = "%s::%s" % (rest, name) if rest else name
        node, result = None, PASSED
        if case.find("failure") is not None:
            node, result = case.find("failure"), FAILED
        elif error is not None:
            node, result = error, ERROR
        elif case.find("skipped") is not None:
            result = SKIPPED
        tests[full] = result
        if node is not None:
            messages[full] = _short(node.get("message") or node.text)
    return {"tests": tests, "messages": messages, "file_error": ""}


def run_file(root, test_path, timeout=DEFAULT_TIMEOUT, run_index=None, python=None):
    """Run one test file with `root` as the project folder.

    Returns {"status": OK | TIMEOUT | ERROR, "tests": {name: result}, "messages": {name: text},
    "error": text}. ERROR with no tests means the file could not even be loaded.
    """
    report = os.path.join(os.path.dirname(test_path), "report-%s.xml" % (run_index or 0))
    command = [python or sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
               "-o", "addopts=", "--rootdir", root, "--junitxml", report, test_path]
    empty = {"tests": {}, "messages": {}}
    try:
        done = subprocess.run(command, cwd=root, env=child_env(root=root, run_index=run_index),
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {**empty, "status": TIMEOUT, "error": "took longer than %ss and was stopped" % timeout}
    except OSError as err:
        return {**empty, "status": BROKEN, "error": "could not start pytest: %s" % err}
    if not os.path.isfile(report):
        return {**empty, "status": BROKEN, "error": "pytest produced no report"}
    stem = os.path.splitext(os.path.basename(test_path))[0]
    parsed = parse_junit(report, stem)
    if not parsed["tests"]:
        detail = parsed["file_error"] or _short(done.stdout)[-300:] or "pytest collected no tests"
        return {**empty, "status": BROKEN, "error": detail}
    return {"status": OK, "tests": parsed["tests"], "messages": parsed["messages"], "error": ""}


def run_in_tree(root, code, file_name, timeout=DEFAULT_TIMEOUT, rerun_times=0, python=None):
    """Write `code` into a throwaway folder inside `root`, run it, remove the folder.

    With rerun_times > 0 and at least one failing test, the file is run again that many times and
    the result also carries "reruns": {test name: {"passes": n, "fails": n}} for the failing tests.
    """
    folder = os.path.join(root, TEST_DIR)
    os.makedirs(folder, exist_ok=True)
    test_path = os.path.join(folder, file_name)
    try:
        with open(test_path, "w", encoding="utf-8") as handle:
            handle.write(code)
        first = run_file(root, test_path, timeout, None, python)
        first["reruns"] = {}
        failing = [n for n, r in first["tests"].items() if r in (FAILED, ERROR)]
        if first["status"] == OK and failing and rerun_times:
            counts = {n: {"passes": 0, "fails": 0} for n in failing}
            for index in range(1, rerun_times + 1):
                again = run_file(root, test_path, timeout, index, python)
                for name in failing:
                    result = again["tests"].get(name)
                    key = "passes" if result in (PASSED, SKIPPED) else "fails"
                    counts[name][key] += 1
            first["reruns"] = counts
        return first
    finally:
        shutil.rmtree(folder, ignore_errors=True)
