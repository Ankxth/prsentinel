"""The test phase of a run (feature D3): generate, run on the old and the new code, report.

Runs between "code map updated" and "finish". Any problem here is reported as "no result" for that
function (never as a pass) and never fails the user's CI. Standard library only.
"""
import ast
import os
import shutil
import subprocess
import tempfile
import textwrap
import time

import client
import diff
import mutation
import pytest_runner as pr

RERUN_TIMES = 3
TIME_BUDGET_SECONDS = 600
MAX_CODE_CHARS = 20_000
MAX_REPAIR_ROUNDS = 2  # same limit as the server; attempt 0 is the first tests
MAX_FAILING = 20


def is_test_path(path):
    parts = path.replace("\\", "/").split("/")
    base = parts[-1]
    return (any(p in ("tests", "test") for p in parts[:-1]) or base.startswith("test_")
            or base.endswith("_test.py") or base == "conftest.py")


def function_source(source, name):
    """The source text of one function or method (with decorators), or None."""
    try:
        node = diff.collect_functions(source).get(name)
    except SyntaxError:
        return None
    if node is None:
        return None
    start = min([node.lineno] + [d.lineno for d in node.decorator_list])
    lines = source.splitlines()[start - 1:node.end_lineno]
    return textwrap.dedent("\n".join(lines)) + "\n"


def _read(rev, path, cwd):
    return diff._show(rev, path, cwd)


def _checkout_old(base, root):
    """A throwaway checkout of the old commit. Returns its folder, or None."""
    if base == diff.EMPTY_TREE:
        return None
    folder = tempfile.mkdtemp(prefix="prsentinel_old_")
    done = subprocess.run(["git", "worktree", "add", "--detach", folder, base], cwd=root,
                          capture_output=True, text=True)
    if done.returncode != 0:
        shutil.rmtree(folder, ignore_errors=True)
        return None
    return folder


def _remove_old(folder, root):
    if folder:
        subprocess.run(["git", "worktree", "remove", "--force", folder], cwd=root,
                       capture_output=True)
        shutil.rmtree(folder, ignore_errors=True)
        subprocess.run(["git", "worktree", "prune"], cwd=root, capture_output=True)


def _post_results(api_url, run_id, token, body, kwargs):
    return client.request_json("POST", "%s/v1/runs/%s/results" % (api_url, run_id),
                               token=token, body=body, **kwargs)


def _could_not_test(api_url, run_id, token, item, reason, kwargs):
    body = {"file": item["file"], "name": item["name"], "status": "could_not_test",
            "reason": reason[:300], "tests": []}
    _post_results(api_url, run_id, token, body, kwargs)


def build_tests(before, after):
    """Pair up each test's result on the old and the new code. `before` may be None (new function).

    A test that is missing on the old side counts as an error there, never as a pass.
    """
    tests = []
    for name, result in after["tests"].items():
        rerun = after.get("reruns", {}).get(name, {})
        tests.append({
            "name": name,
            "before": (before["tests"].get(name, pr.ERROR) if before and before["tests"]
                       else (pr.ERROR if before else pr.SKIPPED)),
            "after": result,
            "rerun_passes": rerun.get("passes", 0),
            "rerun_fails": rerun.get("fails", 0),
            "message": after["messages"].get(name),
        })
    return tests


def run(api_url, run_id, token, functions, base, root, max_functions, kwargs, log, annotate,
        rerun_times=RERUN_TIMES, budget=TIME_BUDGET_SECONDS, clock=time.monotonic):
    """Returns a dict of counts: tested, could_not_generate, could_not_test, skipped, notes."""
    summary = {"tested": 0, "could_not_generate": 0, "could_not_test": 0, "skipped": 0, "notes": []}
    wanted = [f for f in functions if f["change_type"] != "deleted" and not is_test_path(f["file"])]
    summary["skipped"] = len(functions) - len(wanted)
    if not wanted:
        return summary
    if len(wanted) > max_functions:
        summary["notes"].append("Only the first %d of %d changed functions were tested."
                                % (max_functions, len(wanted)))
        summary["skipped"] += len(wanted) - max_functions
        wanted = wanted[:max_functions]

    available, detail = pr.pytest_available()
    if not available:
        note = "pytest is not installed in this job (%s), so no tests were written or run." % detail
        annotate("warning", "PRSentinel: " + note)
        summary["notes"].append(note)
        summary["could_not_test"] = len(wanted)
        return summary

    old_root = None
    if any(f["change_type"] == "modified" for f in wanted):
        old_root = _checkout_old(base, root)
    started = clock()
    try:
        for number, item in enumerate(wanted):
            if clock() - started > budget:
                left = len(wanted) - number
                summary["notes"].append("The time limit was reached; %d function(s) were not tested." % left)
                summary["skipped"] += left
                break
            try:
                outcome = _one(api_url, run_id, token, item, number, base, root, old_root,
                               rerun_times, kwargs, log)
            except client.ApiError as err:
                annotate("warning", "PRSentinel: %s::%s: %s" % (item["file"], item["name"], err.detail))
                outcome = "could_not_test"
            except Exception as err:  # one function must never stop the others
                annotate("warning", "PRSentinel: %s::%s: %s" % (item["file"], item["name"], err))
                outcome = "could_not_test"
            summary[outcome] += 1
    finally:
        _remove_old(old_root, root)
    return summary


def _one(api_url, run_id, token, item, number, base, root, old_root, rerun_times, kwargs, log):
    path, name, change = item["file"], item["name"], item["change_type"]
    new_source = _read("HEAD", path, root)
    new_code = function_source(new_source, name) if new_source else None
    if not new_code:
        return "skipped"
    old_code = None
    if change == "modified":
        old_source = _read(base, path, root)
        old_code = function_source(old_source, name) if old_source else None
        if not old_code:
            return "skipped"
    if len(new_code) > MAX_CODE_CHARS or len(old_code or "") > MAX_CODE_CHARS:
        return "skipped"

    generated = client.request_json(
        "POST", "%s/v1/runs/%s/generate" % (api_url, run_id), token=token,
        body={"file": path, "name": name, "old_code": old_code, "new_code": new_code},
        timeout=120, **kwargs)
    if generated.get("status") != "generated":
        log("PRSentinel: %s::%s: %s" % (path, name, generated.get("reason")))
        return "could_not_generate"

    code, file_name = generated["test_code"], "test_prs_%d.py" % number
    before, after, problem = _run_both(root, old_root, code, file_name, change, rerun_times)
    if problem:
        _could_not_test(api_url, run_id, token, item, problem, kwargs)
        return "could_not_test"
    tests = build_tests(before, after)
    reply = _post_results(api_url, run_id, token, {
        "file": path, "name": name, "status": "tested", "attempt": 0, "tests": tests}, kwargs)
    log("PRSentinel: %s::%s: %s" % (path, name, ", ".join(
        "%s=%s" % (t["name"], t["label"]) for t in reply.get("tests", [])[:5])))
    if reply.get("status") != "tested":
        return "could_not_test"
    if change == "modified" and old_code:
        try:  # a repair problem must never take away the results that were already reported
            code, tests = _repair(api_url, run_id, token, item, old_code, new_code, code, file_name,
                                  tests, reply, root, old_root, rerun_times, kwargs, log)
        except Exception as err:
            log("PRSentinel: %s::%s: the repair stopped: %s" % (path, name, err))
        if old_root is not None:
            try:  # the mutation check is extra information: a problem here changes nothing else
                _mutation(api_url, run_id, token, item, code, file_name, old_root, kwargs, log)
            except Exception as err:
                log("PRSentinel: %s::%s: the mutation check stopped: %s" % (path, name, err))
    return "tested"


def _mutation(api_url, run_id, token, item, code, file_name, old_root, kwargs, log):
    """Run the mutation check on the final tests and report the counts to the server."""
    result = mutation.run_mutation(old_root, item["file"], item["name"], code, file_name)
    body = {"file": item["file"], "name": item["name"], "status": result["status"],
            "reason": result["reason"] or None, "found": result["found"],
            "capped": result["capped"], "stopped_early": result["stopped_early"],
            "killed": result["killed"], "survived": result["survived"],
            "equivalent": result["equivalent"], "timed_out": result["timed_out"],
            "could_not_run": result["could_not_run"], "survivors": result["survivors"]}
    client.request_json("POST", "%s/v1/runs/%s/mutation" % (api_url, run_id), token=token,
                        body=body, **kwargs)
    if result["status"] == "measured":
        log("PRSentinel: %s::%s: mutation check: %d killed, %d survived, %d equivalent"
            % (item["file"], item["name"], result["killed"], result["survived"],
               result["equivalent"]))
    else:
        log("PRSentinel: %s::%s: mutation check not measured: %s"
            % (item["file"], item["name"], result["reason"]))


def _run_both(root, old_root, code, file_name, change, rerun_times):
    """Run the tests on the new code, then on the old code. Returns (before, after, problem)."""
    after = pr.run_in_tree(root, code, file_name, rerun_times=rerun_times)
    if after["status"] != pr.OK:
        return None, None, "The generated tests could not run on the new code: %s" % after["error"]
    before = None
    if change == "modified":
        if old_root is None:
            return None, None, "The old version of the code could not be checked out."
        before = pr.run_in_tree(old_root, code, file_name)
        if before["status"] == pr.TIMEOUT:
            return None, None, "The generated tests timed out on the old code."
        # A file that cannot load on the old code means its tests have no old result: errors.
        before = {"tests": before["tests"]} if before["status"] == pr.OK else {"tests": {}}
    return before, after, None


def _label(before, after):
    """The same rule as the server's `label_for`; the label the server stores is the real one."""
    failed = (pr.FAILED, pr.ERROR)
    if before in failed and after in failed:
        return "TEST_WRONG_ON_BEFORE"
    if before in failed:
        return "ODD"
    if after in failed:
        return "CATCHES_CHANGE"
    return "NO_SIGNAL"


def lost_catches(previous, repaired):
    """Names of tests that caught the change before the repair and do not catch it after it.

    A repair may only fix the tests that were wrong on the old code. If it removed or weakened a
    test that caught the change, it is not accepted.
    """
    now = {t["name"]: _label(t["before"], t["after"]) for t in repaired}
    return [t["name"] for t in previous
            if _label(t["before"], t["after"]) == "CATCHES_CHANGE"
            and now.get(t["name"]) != "CATCHES_CHANGE"]


def _repair(api_url, run_id, token, item, old_code, new_code, code, file_name, tests, reply,
            root, old_root, rerun_times, kwargs, log):
    """Ask the server to fix the tests that fail on the old code too, up to MAX_REPAIR_ROUNDS."""
    path, name = item["file"], item["name"]
    labels = {t["name"]: t["label"] for t in reply.get("tests", [])}
    for attempt in range(1, MAX_REPAIR_ROUNDS + 1):
        failing = [t for t in tests if labels.get(t["name"]) == "TEST_WRONG_ON_BEFORE"]
        if not failing:
            return code, tests
        try:
            fixed = client.request_json(
                "POST", "%s/v1/runs/%s/repair" % (api_url, run_id), token=token, timeout=120,
                body={"file": path, "name": name, "attempt": attempt, "old_code": old_code,
                      "new_code": new_code, "test_code": code,
                      "failing": [{"name": t["name"], "before": t["before"], "after": t["after"],
                                   "message": t["message"]} for t in failing[:MAX_FAILING]]},
                **kwargs)
        except client.ApiError as err:
            log("PRSentinel: %s::%s: repair not possible: %s" % (path, name, err.detail))
            return code, tests
        if fixed.get("status") != "generated":
            log("PRSentinel: %s::%s: repair not possible: %s" % (path, name, fixed.get("reason")))
            return code, tests
        before, after, problem = _run_both(root, old_root, fixed["test_code"], file_name,
                                           "modified", rerun_times)
        if problem:
            log("PRSentinel: %s::%s: repair not used: %s" % (path, name, problem))
            return code, tests
        repaired = build_tests(before, after)
        lost = lost_catches(tests, repaired)
        if lost:
            log("PRSentinel: %s::%s: repair not used, it changed a test that caught the change (%s)"
                % (path, name, ", ".join(lost[:3])))
            return code, tests
        again = _post_results(api_url, run_id, token, {
            "file": path, "name": name, "status": "tested", "attempt": attempt,
            "tests": repaired, "test_code": fixed["test_code"]}, kwargs)
        code, tests = fixed["test_code"], repaired
        labels = {t["name"]: t["label"] for t in again.get("tests", [])}
        log("PRSentinel: %s::%s: repaired (round %d): %s" % (path, name, attempt, ", ".join(
            "%s=%s" % (t["name"], t["label"]) for t in again.get("tests", [])[:5])))
    return code, tests
