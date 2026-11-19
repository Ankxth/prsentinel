"""Tests for the Action runner. Run: python -m unittest discover -s action/tests -v"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "runner"))

import client  # noqa: E402
import diff  # noqa: E402
import main  # noqa: E402

MARKER = "<!-- prsentinel-comment -->"


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def make_repo(files_before, files_after):
    """Create a repo with two commits. Returns (path, first_sha, second_sha)."""
    path = tempfile.mkdtemp()
    git(path, "init", "-q")
    git(path, "config", "user.email", "t@t")
    git(path, "config", "user.name", "t")
    for step, files in enumerate((files_before, files_after)):
        for name, content in files.items():
            full = os.path.join(path, name)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            if content is None:
                os.remove(full)
            else:
                with open(full, "w", encoding="utf-8") as handle:
                    handle.write(content)
        git(path, "add", "-A")
        git(path, "commit", "-q", "-m", "c%d" % step)
    shas = subprocess.run(["git", "rev-list", "--reverse", "HEAD"], cwd=path,
                          capture_output=True, text=True).stdout.split()
    return path, shas[0], shas[1]


class DiffTests(unittest.TestCase):
    def test_added_modified_deleted_and_comment_only(self):
        before = {
            "a.py": "def keep():\n    return 1\n\ndef change():\n    return 1\n\ndef drop():\n    return 0\n",
            "gone.py": "def old():\n    return 1\n",
            "notes.txt": "hello\n",
        }
        after = {
            "a.py": "def keep():\n    # only a comment changed\n    return 1\n\ndef change():\n    return 2\n\n"
                    "class Cart:\n    def total(self):\n        return 3\n",
            "gone.py": None,
            "new.py": "def fresh():\n    pass\n",
            "notes.txt": "changed\n",
        }
        path, first, second = make_repo(before, after)
        count, functions, skipped = diff.find_changes(first, second, cwd=path)
        got = {(f["file"], f["name"], f["change_type"]) for f in functions}
        self.assertEqual(count, 4)
        self.assertEqual(skipped, [])
        self.assertEqual(got, {
            ("a.py", "change", "modified"), ("a.py", "drop", "deleted"),
            ("a.py", "Cart.total", "added"), ("gone.py", "old", "deleted"),
            ("new.py", "fresh", "added"),
        })

    def test_syntax_error_is_skipped_not_fatal(self):
        path, first, second = make_repo({"a.py": "def f():\n    pass\n"},
                                        {"a.py": "def f(:\n"})
        _, functions, skipped = diff.find_changes(first, second, cwd=path)
        self.assertEqual(functions, [])
        self.assertEqual(skipped, [("a.py", "syntax error")])


class FakeServer:
    """Pretends to be the PRSentinel server, the GitHub token endpoint and the GitHub API."""

    def __init__(self, fail_first_runs=0, existing_comments=None, runs_status=None,
                 context_status=None, known=(), batch_limit=100, generate_reply=None,
                 repair_replies=None):
        self.generate_reply = generate_reply
        self.repair_replies = list(repair_replies or [])
        self.context_status = context_status
        self.known = set(known)
        self.batch_limit = batch_limit
        self.requests = []
        self.fail_first_runs = fail_first_runs
        self.comments = existing_comments if existing_comments is not None else []
        self.runs_status = runs_status
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code, body):
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _body(self):
                length = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(length)) if length else None

            def _handle(self):
                body = self._body()
                outer.requests.append((self.command, self.path, body,
                                       self.headers.get("Authorization")))
                path = self.path.split("?")[0]
                if path == "/oidc":
                    return self._send(200, {"value": "identity-token"})
                if path == "/v1/runs":
                    if outer.fail_first_runs > 0:
                        outer.fail_first_runs -= 1
                        return self._send(503, {"detail": "waking"})
                    if outer.runs_status:
                        return self._send(outer.runs_status, {"detail": "Add this repo first."})
                    return self._send(201, {"contract_version": 1, "run_id": "rid",
                                            "run_token": "run-token", "expires_in": 3600,
                                            "repo": "o/r", "limits": {"max_functions": 50}})
                if path.startswith("/v1/runs/rid/context/"):
                    if outer.context_status:
                        return self._send(outer.context_status, {"detail": "Another build is running."})
                    step = path.rsplit("/", 1)[1]
                    if step == "manifest":
                        need = [p for p in body["files"] if p not in outer.known]
                        return self._send(200, {"need": need, "unchanged": len(body["files"]) - len(need),
                                                "removed": 0, "max_batch_files": outer.batch_limit})
                    if step == "files":
                        return self._send(200, {"saved": len(body["files"])})
                    return self._send(200, {"status": "complete", "built_from_sha": "x",
                                            "partial": False, "python_files": 1})
                if path == "/v1/runs/rid/generate":
                    reply = outer.generate_reply or {"status": "could_not_generate",
                                                     "reason": "no model in this test"}
                    return self._send(200, reply)
                if path == "/v1/runs/rid/repair":
                    reply = (outer.repair_replies.pop(0) if outer.repair_replies else
                             {"status": "could_not_generate", "reason": "no repair in this test"})
                    return self._send(200, reply)
                if path == "/v1/runs/rid/results":
                    failed = ("failed", "error")

                    def label(t):
                        if t["before"] in failed and t["after"] in failed:
                            return "TEST_WRONG_ON_BEFORE"
                        if t["before"] in failed:
                            return "ODD"
                        return "CATCHES_CHANGE" if t["after"] in failed else "NO_SIGNAL"

                    return self._send(200, {"function_id": "fid", "status": body["status"], "tests": [
                        {"name": t["name"], "label": label(t), "diagnosis": None, "reason": None}
                        for t in body["tests"]]})
                if path == "/v1/runs/rid/finish":
                    return self._send(200, {"status": body["status"], "comment_marker": MARKER,
                                            "comment_markdown": MARKER + "\n### PRSentinel\nhi"})
                if path == "/repos/o/r/issues/7/comments":
                    if self.command == "GET":
                        return self._send(200, outer.comments)
                    return self._send(201, {"id": 99})
                if path.startswith("/repos/o/r/issues/comments/"):
                    return self._send(200, {"id": 1})
                self._send(404, {"detail": "nope"})

            do_GET = do_POST = do_PATCH = _handle

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.httpd.server_port
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


def pr_event(base_sha, fork=False):
    head_repo = "someone/r" if fork else "o/r"
    return {"pull_request": {"number": 7, "base": {"sha": base_sha, "repo": {"full_name": "o/r"}},
                             "head": {"repo": {"full_name": head_repo}}}}


class RunnerBase(unittest.TestCase):
    def setUp(self):
        self.repo, self.first, self.second = make_repo(
            {"a.py": "def f():\n    return 1\n"}, {"a.py": "def f():\n    return 2\n"})
        self.summary = os.path.join(self.repo, "summary.md")

    def env(self, server, event, event_name="pull_request", **extra):
        event_file = os.path.join(self.repo, "event.json")
        with open(event_file, "w") as handle:
            json.dump(event, handle)
        env = {
            "PRSENTINEL_API_URL": server.url, "PRSENTINEL_GITHUB_TOKEN": "gh-token",
            "GITHUB_EVENT_NAME": event_name, "GITHUB_EVENT_PATH": event_file,
            "GITHUB_REPOSITORY": "o/r", "GITHUB_API_URL": server.url,
            "GITHUB_STEP_SUMMARY": self.summary,
            "ACTIONS_ID_TOKEN_REQUEST_URL": server.url + "/oidc?x=1",
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "req-token",
            "PRSENTINEL_RETRY_PAUSE": "0", "PRSENTINEL_WAIT_SECONDS": "5",
            "PRSENTINEL_RUN_TESTS": "false",
        }
        env.update(extra)
        return env

    def go(self, env):
        old = os.getcwd()
        os.chdir(self.repo)
        try:
            return main.run(env)
        finally:
            os.chdir(old)


class RunnerTests(RunnerBase):
    def test_pull_request_creates_comment(self):
        server = FakeServer()
        self.addCleanup(server.close)
        code = self.go(self.env(server, pr_event(self.first)))
        self.assertEqual(code, 0)
        start = [r for r in server.requests if r[1] == "/v1/runs"][0]
        self.assertEqual(start[3], "Bearer identity-token")
        self.assertEqual(start[2]["changed_functions"],
                         [{"file": "a.py", "name": "f", "change_type": "modified"}])
        self.assertEqual(start[2]["contract_version"], 1)
        oidc = [r for r in server.requests if r[1].startswith("/oidc")][0]
        self.assertIn("audience=prsentinel", oidc[1])
        finish = [r for r in server.requests if r[1].endswith("/finish")][0]
        self.assertEqual(finish[3], "Bearer run-token")
        self.assertEqual(finish[2]["status"], "completed")
        self.assertTrue(any(r[0] == "POST" and r[1].endswith("/issues/7/comments")
                            for r in server.requests))
        with open(self.summary) as handle:
            self.assertIn("PRSentinel", handle.read())

    def test_existing_comment_is_updated(self):
        server = FakeServer(existing_comments=[{"id": 1, "body": MARKER + " old"}])
        self.addCleanup(server.close)
        self.go(self.env(server, pr_event(self.first)))
        self.assertTrue(any(r[0] == "PATCH" and r[1].endswith("/issues/comments/1")
                            for r in server.requests))
        self.assertFalse(any(r[0] == "POST" and r[1].endswith("/issues/7/comments")
                             for r in server.requests))

    def test_retries_while_server_wakes_up(self):
        server = FakeServer(fail_first_runs=2)
        self.addCleanup(server.close)
        self.assertEqual(self.go(self.env(server, pr_event(self.first))), 0)
        starts = [r for r in server.requests if r[1] == "/v1/runs"]
        self.assertEqual(len(starts), 3)

    def test_fork_pr_is_skipped(self):
        server = FakeServer()
        self.addCleanup(server.close)
        self.assertEqual(self.go(self.env(server, pr_event(self.first, fork=True))), 0)
        self.assertEqual(server.requests, [])

    def test_push_uses_before_sha_and_no_comment(self):
        server = FakeServer()
        self.addCleanup(server.close)
        self.go(self.env(server, {"before": self.first}, event_name="push"))
        start = [r for r in server.requests if r[1] == "/v1/runs"][0]
        self.assertEqual(len(start[2]["changed_functions"]), 1)
        self.assertFalse(any("/comments" in r[1] for r in server.requests))

    def test_first_push_all_zero_before_falls_back(self):
        server = FakeServer()
        self.addCleanup(server.close)
        self.assertEqual(self.go(self.env(server, {"before": "0" * 40}, event_name="push")), 0)

    def test_missing_id_token_permission_is_clear_and_not_fatal(self):
        server = FakeServer()
        self.addCleanup(server.close)
        env = self.env(server, pr_event(self.first))
        del env["ACTIONS_ID_TOKEN_REQUEST_URL"]
        self.assertEqual(self.go(env), 0)
        with open(self.summary) as handle:
            text = handle.read()
        self.assertIn("id-token: write", text)
        self.assertIn("Nothing was checked", text)
        env["PRSENTINEL_FAIL_ON_ERROR"] = "true"
        self.assertEqual(self.go(env), 1)

    def test_server_refusal_reports_detail_and_never_a_pass(self):
        server = FakeServer(runs_status=403)
        self.addCleanup(server.close)
        self.assertEqual(self.go(self.env(server, pr_event(self.first))), 0)
        with open(self.summary) as handle:
            self.assertIn("Add this repo first.", handle.read())
        self.assertFalse(any(r[1].endswith("/finish") for r in server.requests))


class ContextSyncTests(RunnerBase):
    """The runner keeps the server's code map up to date, and never fails the run because of it."""

    def context_calls(self, server):
        return [r for r in server.requests if "/context/" in r[1]]

    def test_uploads_facts_for_files_the_server_needs_then_commits(self):
        server = FakeServer()
        self.addCleanup(server.close)
        self.assertEqual(self.go(self.env(server, pr_event(self.first))), 0)
        steps = [r[1].rsplit("/", 1)[1] for r in self.context_calls(server)]
        self.assertEqual(steps, ["manifest", "files", "commit"])
        calls = self.context_calls(server)
        self.assertEqual(calls[0][3], "Bearer run-token")
        self.assertEqual(list(calls[0][2]["files"]), ["a.py"])
        sent = calls[1][2]["files"][0]
        self.assertEqual(sent["path"], "a.py")
        self.assertEqual(sent["symbols"][0]["name"], "f")
        self.assertNotIn("return 2", json.dumps(calls[1][2]))  # no function bodies leave the runner
        self.assertEqual(calls[2][2], {"pytest_config": False})
        finishes = [r for r in server.requests if r[1].endswith("/finish")]
        self.assertEqual(finishes[0][2]["status"], "completed")

    def test_unchanged_files_are_not_uploaded(self):
        server = FakeServer(known=["a.py"])
        self.addCleanup(server.close)
        self.go(self.env(server, pr_event(self.first)))
        steps = [r[1].rsplit("/", 1)[1] for r in self.context_calls(server)]
        self.assertEqual(steps, ["manifest", "commit"])

    def test_uploads_are_split_into_batches(self):
        server = FakeServer(batch_limit=1)
        self.addCleanup(server.close)
        with open(os.path.join(self.repo, "b.py"), "w") as handle:
            handle.write("def g():\n    return 1\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "c2")
        self.go(self.env(server, pr_event(self.first)))
        uploads = [r for r in self.context_calls(server) if r[1].endswith("/files")]
        self.assertEqual(len(uploads), 2)
        self.assertTrue(all(len(r[2]["files"]) == 1 for r in uploads))

    def test_map_failure_does_not_fail_the_run(self):
        server = FakeServer(context_status=409)
        self.addCleanup(server.close)
        self.assertEqual(self.go(self.env(server, pr_event(self.first))), 0)
        finishes = [r for r in server.requests if r[1].endswith("/finish")]
        self.assertEqual(finishes[0][2]["status"], "completed")
        self.assertTrue(any(r[0] == "POST" and r[1].endswith("/issues/7/comments")
                            for r in server.requests))

    def test_pytest_config_hint_is_sent(self):
        server = FakeServer()
        self.addCleanup(server.close)
        with open(os.path.join(self.repo, "pytest.ini"), "w") as handle:
            handle.write("[pytest]\n")
        self.go(self.env(server, pr_event(self.first)))
        commit = [r for r in self.context_calls(server) if r[1].endswith("/commit")][0]
        self.assertEqual(commit[2], {"pytest_config": True})


class ClientTests(unittest.TestCase):
    def test_gives_up_after_wait_and_reports_unreachable(self):
        with self.assertRaises(client.ApiError) as ctx:
            client.request_json("GET", "http://127.0.0.1:9/x", wait_seconds=0)
        self.assertEqual(ctx.exception.status, 0)


if __name__ == "__main__":
    unittest.main()
