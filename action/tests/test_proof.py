"""Tests for the proof agent in the runner (E3): the test checker, the canary helper, running a
test for real, and the back-and-forth loop against a fake server."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "runner"))

import proof_linter as pl  # noqa: E402
import proof_phase as pp  # noqa: E402

IMPORT = "from app import run_command"
GOOD = (
    "from prs_harness import CANARY, command_sink\n"
    "from app import run_command\n\n"
    "def test_injection():\n"
    "    with command_sink():\n"
    "        run_command('ok; echo ' + CANARY)\n"
)
VULNERABLE = "import subprocess\n\ndef run_command(cmd):\n    subprocess.run('echo ' + cmd, shell=True)\n"
QUOTED = "import subprocess, shlex\n\ndef run_command(cmd):\n    subprocess.run('echo ' + shlex.quote(cmd), shell=True)\n"
LISTED = "import subprocess\n\ndef run_command(cmd):\n    subprocess.run(['echo', cmd])\n"
SYSTEM = "import os\n\ndef run_command(cmd):\n    os.system('echo ' + cmd)\n"
SWALLOWS = ("import subprocess\n\ndef run_command(cmd):\n    try:\n"
            "        subprocess.run('echo ' + cmd, shell=True)\n    except Exception:\n        pass\n")


def write(root, name, text):
    path = os.path.join(root, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def with_body(*lines):
    return ("from prs_harness import CANARY, command_sink\n" + IMPORT + "\n\ndef test_x():\n"
            + "\n".join("    " + line for line in lines) + "\n")


class LintTests(unittest.TestCase):
    def test_a_good_test_passes(self):
        self.assertEqual(pl.lint(GOOD, IMPORT), [])
        self.assertEqual(pl.lint(with_body("with command_sink():", "    run_command(f'ok; echo {CANARY}')"),
                                 IMPORT), [])

    def test_bad_tests_are_rejected_with_a_reason(self):
        bad = {
            "empty": "",
            "syntax": "def test_(:\n",
            "other import": GOOD.replace("from app import run_command", "import os\nfrom app import run_command"),
            "wrong target import": GOOD.replace("from app import run_command", "from app import other"),
            "no sink": with_body("run_command('ok; echo ' + CANARY)"),
            "no canary": with_body("with command_sink():", "    run_command('ok')"),
            "no test function": "from prs_harness import CANARY, command_sink\n" + IMPORT + "\n",
            "open": with_body("with command_sink():", "    open('x')", "    run_command(CANARY)"),
            "exec": with_body("with command_sink():", "    exec('1')", "    run_command(CANARY)"),
            "dunder": with_body("with command_sink():", "    run_command(CANARY.__class__)"),
            "dunder name": with_body("with command_sink():", "    run_command(__builtins__)"),
            "bad word": with_body("with command_sink():", "    run_command('rm ' + CANARY)"),
            "redirect": with_body("with command_sink():", "    run_command('echo > ok' + CANARY)"),
            "bytes": with_body("with command_sink():", "    run_command(b'ok' + CANARY)"),
            "loop": with_body("while True:", "    pass"),
            "lambda": with_body("f = lambda: 1", "with command_sink():", "    run_command(CANARY)"),
            "too long": "x = 1\n" * 100 + GOOD,
        }
        for name, code in bad.items():
            with self.subTest(name):
                errors = pl.lint(code, IMPORT)
                self.assertTrue(errors, name)
                self.assertTrue(all(isinstance(e, str) and len(e) < 200 for e in errors))

    def test_a_pytest_import_is_fine(self):
        code = "import pytest\n" + GOOD
        self.assertEqual(pl.lint(code, IMPORT), [])


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)

    def prove(self, source):
        write(self.root, "app.py", source)
        function = {"file": "app.py", "name": "run_command", "source": source,
                    "import_line": IMPORT, "class_name": None}
        return pp.execute(self.root, GOOD, function, 1)

    def test_an_injectable_shell_call_is_reproduced_three_times(self):
        out = self.prove(VULNERABLE)
        self.assertEqual((out["lint_ok"], out["outcomes"]), (True, ["canary"] * 3), out)

    def test_os_system_counts_too(self):
        self.assertEqual(self.prove(SYSTEM)["outcomes"], ["canary"] * 3)

    def test_catching_exception_in_the_code_does_not_hide_it(self):
        self.assertEqual(self.prove(SWALLOWS)["outcomes"], ["canary"] * 3)

    def test_quoting_the_input_is_not_called_injectable(self):
        self.assertEqual(self.prove(QUOTED)["outcomes"], ["clean"] * 3)

    def test_a_list_without_a_shell_is_not_called_injectable(self):
        self.assertEqual(self.prove(LISTED)["outcomes"], ["clean"] * 3)

    def test_nothing_is_really_run_and_the_folder_is_removed(self):
        marker = os.path.join(self.root, "created.txt")
        self.prove("import os\n\ndef run_command(cmd):\n    os.system('echo hi ' + cmd)\n")
        self.assertFalse(os.path.exists(marker))
        self.assertFalse(os.path.exists(os.path.join(self.root, ".prsentinel_tests")))

    def test_a_rejected_test_is_not_run(self):
        write(self.root, "app.py", VULNERABLE)
        function = {"file": "app.py", "name": "run_command", "source": VULNERABLE,
                    "import_line": IMPORT, "class_name": None}
        out = pp.execute(self.root, "import os\n" + GOOD, function, 2)
        self.assertEqual((out["lint_ok"], out["outcomes"], out["attempt"]), (False, [], 2))
        self.assertTrue(out["lint_errors"])

    def test_a_missing_target_is_an_error_not_a_hit(self):
        function = {"file": "app.py", "name": "run_command", "source": "x",
                    "import_line": IMPORT, "class_name": None}
        out = pp.execute(self.root, GOOD, function, 1)
        self.assertEqual(out["outcomes"], ["error"] * 3)


class ExtractTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_a_top_level_function_is_found(self):
        write(self.root, "src/pkg/tools.py", "X = 1\n\ndef run(cmd):\n    return cmd\n")
        fn = pp.extract_function(self.root, "src/pkg/tools.py", 4)
        self.assertEqual((fn["name"], fn["import_line"]), ("run", "from pkg.tools import run"))
        self.assertIn("return cmd", fn["source"])

    def test_unsafe_or_unsupported_places_are_skipped(self):
        write(self.root, "a.py", "class K:\n    def m(self, c):\n        return c\n")
        write(self.root, "b.py", "import functools\n\n@functools.cache\ndef f(c):\n    return c\n")
        write(self.root, "c.py", "def f(c):\n    password = 'hunter2hunter2'\n    return c\n")
        write(self.root, "d.py", "def f(c):\n" + "    # x\n" * 5000 + "    return c\n")
        write(self.root, "my-file.py", "def f(c):\n    return c\n")
        write(self.root, "e.py", "def f(c:\n")
        for rel, line in (("a.py", 3), ("b.py", 5), ("c.py", 3), ("d.py", 3), ("my-file.py", 2),
                          ("e.py", 1), ("../x.py", 1), ("missing.py", 1), ("a.py", 99)):
            with self.subTest(rel):
                self.assertIsNone(pp.extract_function(self.root, rel, line))

    def test_candidates_pick_new_command_injection_only_and_at_most_three(self):
        write(self.root, "app.py", "".join("def f%d(c):\n    return c\n" % i for i in range(5)))
        write(self.root, "s.py", "def g(c):\n    return c\n")

        def find(line, rule="B602", origin="new", tool="bandit", file="app.py"):
            return {"tool": tool, "rule_id": rule, "origin": origin, "file": file, "line": line,
                    "fingerprint": "%064x" % line}

        findings = [find(2), find(4), find(6), find(8), find(10), find(2, rule="B101"),
                    find(2, origin="existing"), find(2, tool="pip-audit")]
        picked = pp.candidates(findings, self.root)
        self.assertEqual([f["name"] for _, f in picked], ["f0", "f1", "f2"])
        secret = [find(2, file="s.py"), find(1, tool="detect-secrets", file="s.py")]
        self.assertEqual(pp.candidates(secret, self.root), [])


class FakeAgent:
    """A server that hands out the same test each round and records what it is told."""

    def __init__(self, tests):
        self.tests, self.calls = list(tests), []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length))
                outer.calls.append(body)
                result = body.get("result")
                if result and result["outcomes"] == ["canary"] * 3:
                    reply = {"action": "done", "verdict": "verified", "reason": "reproduced"}
                elif not outer.tests:
                    reply = {"action": "done", "verdict": "not_reproduced", "reason": "gave up"}
                else:
                    reply = {"action": "run_test", "attempt": len(outer.calls),
                             "test_code": outer.tests.pop(0)}
                raw = json.dumps(reply).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.httpd.server_port
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


class LoopTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.kwargs = {"wait_seconds": 0, "retry_pause": 0}
        self.logs = []

    def finding(self):
        return {"tool": "bandit", "rule_id": "B602", "origin": "new", "file": "app.py", "line": 4,
                "fingerprint": "a" * 64}

    def go(self, agent):
        self.addCleanup(agent.close)
        return pp.run(agent.url, "rid", "tok", [self.finding()], self.root, self.kwargs, self.logs.append)

    def test_a_bad_first_test_is_fixed_in_round_two_and_verified(self):
        write(self.root, "app.py", VULNERABLE)
        agent = FakeAgent(["import os\n" + GOOD, GOOD])
        verdicts = self.go(agent)
        self.assertEqual(verdicts, {"a" * 64: "verified"})
        self.assertEqual(len(agent.calls), 3)
        self.assertIsNone(agent.calls[0]["result"])
        self.assertFalse(agent.calls[1]["result"]["lint_ok"])
        self.assertEqual(agent.calls[2]["result"]["outcomes"], ["canary"] * 3)
        self.assertEqual(agent.calls[0]["function"]["name"], "run_command")
        self.assertTrue(any("run_command" in line for line in self.logs))

    def test_a_safe_function_ends_as_could_not_confirm(self):
        write(self.root, "app.py", QUOTED)
        verdicts = self.go(FakeAgent([GOOD]))
        self.assertEqual(verdicts, {"a" * 64: "not_reproduced"})

    def test_no_function_to_send_means_nothing_is_asked(self):
        write(self.root, "app.py", "class K:\n    def a(self):\n        pass\n")
        agent = FakeAgent([GOOD])
        self.addCleanup(agent.close)
        self.assertEqual(pp.run(agent.url, "rid", "tok", [dict(self.finding(), line=2)], self.root,
                                self.kwargs, self.logs.append), {})
        self.assertEqual(agent.calls, [])

    def test_a_server_error_is_logged_and_nothing_breaks(self):
        write(self.root, "app.py", VULNERABLE)
        verdicts = pp.run("http://127.0.0.1:9", "rid", "tok", [self.finding()], self.root,
                          {"wait_seconds": 0, "retry_pause": 0, "timeout": 1}, self.logs.append)
        self.assertEqual(verdicts, {})
        self.assertTrue(any("stopped" in line for line in self.logs))


if __name__ == "__main__":
    unittest.main()
