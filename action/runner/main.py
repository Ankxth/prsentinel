"""PRSentinel Action runner (v0): find changed functions, start a run, finish it, comment.

Standard library only, so nothing has to be installed on the user's runner.
Exit code is 0 unless the `fail-on-error` input is true and something went wrong.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import client  # noqa: E402
import comment  # noqa: E402
import context_sync  # noqa: E402
import diff  # noqa: E402

CONTRACT_VERSION = 1
SERVER_WAIT_SECONDS = 150  # free hosting needs about a minute to wake up
ZERO_SHA = "0" * 40


def log(message):
    print(message, flush=True)


def annotate(level, message):
    """GitHub turns these lines into visible annotations on the run page."""
    log("::%s::%s" % (level, message.replace("\n", " ")))


def write_summary(env, text):
    path = env.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")


def _has_commit(sha):
    return subprocess.run(["git", "cat-file", "-e", sha + "^{commit}"],
                          capture_output=True).returncode == 0


def _ensure_commit(sha):
    """Make sure `sha` exists locally, fetching it (shallow) when the checkout was shallow."""
    if _has_commit(sha):
        return True
    subprocess.run(["git", "fetch", "--no-tags", "--depth=1", "origin", sha],
                   capture_output=True)
    return _has_commit(sha)


def _parent_or_empty():
    return "HEAD~1" if _has_commit("HEAD~1") else diff.EMPTY_TREE


def pick_base(event_name, event):
    """Decide which commit to compare the new code against."""
    if event_name == "pull_request":
        sha = event["pull_request"]["base"]["sha"]
        return sha if _ensure_commit(sha) else _parent_or_empty()
    if event_name == "push":
        sha = event.get("before") or ZERO_SHA
        if sha != ZERO_SHA and _ensure_commit(sha):
            return sha
        return _parent_or_empty()
    return _parent_or_empty()


def is_fork_pr(event_name, event):
    if event_name != "pull_request":
        return False
    pr = event.get("pull_request", {})
    head = (pr.get("head", {}).get("repo") or {}).get("full_name")
    base = (pr.get("base", {}).get("repo") or {}).get("full_name")
    return head != base


def run(env):
    """Do the whole job. Returns (exit_code)."""
    api_url = (env.get("PRSENTINEL_API_URL") or "").rstrip("/")
    audience = env.get("PRSENTINEL_AUDIENCE") or "prsentinel"
    gh_token = env.get("PRSENTINEL_GITHUB_TOKEN") or ""
    fail_on_error = (env.get("PRSENTINEL_FAIL_ON_ERROR") or "false").lower() == "true"
    event_name = env.get("GITHUB_EVENT_NAME", "")
    wait = int(env.get("PRSENTINEL_WAIT_SECONDS", SERVER_WAIT_SECONDS))
    kwargs = {"wait_seconds": wait, "retry_pause": int(env.get("PRSENTINEL_RETRY_PAUSE", 5))}

    event = {}
    event_path = env.get("GITHUB_EVENT_PATH")
    if event_path and os.path.exists(event_path):
        with open(event_path, encoding="utf-8") as handle:
            event = json.load(handle)

    if not api_url:
        annotate("error", "PRSentinel: the 'api-url' input is empty.")
        return 1 if fail_on_error else 0

    if is_fork_pr(event_name, event):
        message = ("PRSentinel skipped this pull request because it comes from a fork. "
                   "GitHub gives fork pull requests no identity token, so no check can run.")
        annotate("notice", message)
        write_summary(env, "### PRSentinel\n" + message)
        return 0

    run_token = None
    run_id = None
    try:
        base = pick_base(event_name, event)
        count, functions, skipped = diff.find_changes(base, "HEAD")
        for path, reason in skipped:
            annotate("warning", "PRSentinel could not read %s (%s); it was skipped." % (path, reason))
        log("PRSentinel: %d file(s) changed, %d Python function(s) changed." % (count, len(functions)))

        identity = client.fetch_oidc_token(env, audience, **kwargs)
        started = client.request_json(
            "POST", api_url + "/v1/runs", token=identity,
            body={"contract_version": CONTRACT_VERSION, "changed_files": count,
                  "changed_functions": functions[:500]},
            **kwargs)
        run_id, run_token = started["run_id"], started["run_token"]
        log("PRSentinel: run %s started for %s." % (run_id, started.get("repo")))

        # The code map is a bonus: if it fails we warn and carry on, the run still completes.
        try:
            outcome = context_sync.sync(api_url, run_id, run_token, os.getcwd(), **kwargs)
            log("PRSentinel: code map %s." % outcome)
        except client.ApiError as err:
            annotate("warning", "PRSentinel could not update the code map: %s" % err.detail)
        except Exception as err:
            annotate("warning", "PRSentinel could not update the code map: %s" % err)

        finished = client.request_json(
            "POST", "%s/v1/runs/%s/finish" % (api_url, run_id), token=run_token,
            body={"status": "completed", "reason": None}, **kwargs)
        markdown = finished["comment_markdown"]
        write_summary(env, markdown)

        pr = event.get("pull_request")
        if event_name == "pull_request" and pr and gh_token:
            try:
                what = comment.post_or_update(env, gh_token, pr["number"],
                                              finished["comment_marker"], markdown)
                log("PRSentinel: comment %s." % what)
            except client.ApiError as err:
                annotate("warning", "PRSentinel could not post the comment: %s. Check that the "
                                    "workflow has 'pull-requests: write'." % err.detail)
        return 0
    except client.ApiError as err:
        problem = err.detail
    except Exception as err:  # never crash the user's CI with a stack trace
        problem = "unexpected problem: %s" % err

    annotate("error" if fail_on_error else "warning", "PRSentinel could not run: " + problem)
    write_summary(env, "### PRSentinel\nCould not run: %s\n\nNothing was checked." % problem)
    if run_token and run_id:
        try:
            client.request_json("POST", "%s/v1/runs/%s/finish" % (api_url, run_id),
                                token=run_token,
                                body={"status": "could_not_run", "reason": problem[:300]},
                                wait_seconds=10)
        except Exception:
            pass
    return 1 if fail_on_error else 0


if __name__ == "__main__":
    sys.exit(run(dict(os.environ)))
