"""Tests for the security scanners step (E1): reading the scanners' output, fingerprints that
survive moved code, new-versus-old, honest "could not run", and the whole step against a fake server."""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "runner"))

import diff  # noqa: E402
import main  # noqa: E402
import scanners as sc  # noqa: E402
from test_action import FakeServer, RunnerBase, make_repo, pr_event  # noqa: E402


def have(module):
    return importlib.util.find_spec(module) is not None


def write(root, name, text):
    path = os.path.join(root, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def done(code=0, out="", err=""):
    return SimpleNamespace(returncode=code, stdout=out, stderr=err)


BANDIT_OUT = json.dumps({"errors": [], "results": [{
    "code": "3 x\n", "filename": "./app.py", "issue_confidence": "HIGH",
    "issue_cwe": {"id": 78, "link": "https://cwe.mitre.org/data/definitions/78.html"},
    "issue_severity": "HIGH", "issue_text": "subprocess call with shell=True identified.",
    "line_number": 3, "test_id": "B602", "test_name": "subprocess_popen_with_shell_equals_true"}]})
SECRETS_OUT = json.dumps({"results": {"app.py": [
    {"type": "AWS Access Key", "filename": "app.py", "hashed_secret": "25910f98", "is_verified": False,
     "line_number": 2}]}})
AUDIT_OUT = json.dumps({"dependencies": [
    {"name": "requests", "version": "2.19.0", "vulns": [
        {"id": "PYSEC-2018-28", "fix_versions": ["2.20.0"], "aliases": ["CVE-2018-18074"],
         "description": "long text that must not be sent"},
        {"id": "PYSEC-2018-28", "fix_versions": ["2.20.0"], "aliases": ["CVE-2018-18074"],
         "description": "same id again"}]},
    {"name": "idna", "version": "3.0", "vulns": []}]})


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        write(self.root, "app.py", "import os\nx = 1\n    os.system(cmd, shell=True)\n")
        write(self.root, "requirements.txt", "# deps\nidna==3.0\nrequests==2.19.0\n")

    def test_bandit_output_becomes_a_finding_without_source_code(self):
        (f,) = sc.parse_bandit(BANDIT_OUT, self.root)
        self.assertEqual((f["tool"], f["rule_id"], f["severity"], f["confidence"], f["file"], f["line"]),
                         ("bandit", "B602", "high", "high", "app.py", 3))
        self.assertEqual(f["cwe"], "CWE-78")
        self.assertEqual(f["key_text"], "os.system(cmd, shell=True)")
        self.assertEqual(len(f["fingerprint"]), 64)

    def test_unreadable_output_is_a_scanner_failure(self):
        for parse in (lambda: sc.parse_bandit("not json", self.root),
                      lambda: sc.parse_secrets("", self.root),
                      lambda: sc.parse_pip_audit("{", "requirements.txt", self.root)):
            with self.assertRaises(sc.ScanError):
                parse()

    def test_a_secret_is_identified_by_a_hash_and_its_value_is_never_read(self):
        (f,) = sc.parse_secrets(SECRETS_OUT, self.root)
        self.assertEqual((f["rule_id"], f["file"], f["line"], f["key_text"]), ("AWS Access Key", "app.py", 2, "25910f98"))
        self.assertIn("not stored", f["message"])
        self.assertNotIn("AKIA", json.dumps(f))

    def test_pip_audit_finds_the_pin_line_and_drops_duplicates_and_long_text(self):
        found = sc.parse_pip_audit(AUDIT_OUT, "requirements.txt", self.root)
        self.assertEqual(len(found), 1)
        f = found[0]
        self.assertEqual((f["rule_id"], f["file"], f["line"], f["severity"]), ("PYSEC-2018-28", "requirements.txt", 3, "unknown"))
        self.assertEqual(f["message"], "requests==2.19.0 has a known vulnerability (PYSEC-2018-28, CVE-2018-18074); fixed in 2.20.0")
        self.assertNotIn("long text", json.dumps(f))

    def test_requirements_are_split_into_pinned_and_other_lines(self):
        pinned, other = sc.split_requirements(
            "# c\na==1.0  # note\nB[extra]==2 ; python_version>'3'\nc>=2\nd\n-r more.txt\n-e .\nhttps://x/y.whl\n\n")
        self.assertEqual(pinned, ["a==1.0", "B[extra]==2"])
        self.assertEqual(other, 2)

    def test_requirement_files_are_found_shallow_first_and_virtualenvs_skipped(self):
        for name in ("requirements.txt", "requirements-dev.txt", "api/requirements.txt", "a/b/c/d/requirements.txt",
                     ".venv/lib/requirements.txt", "node_modules/x/requirements.txt", "docs/notes.txt"):
            write(self.root, name, "a==1\n")
        self.assertEqual(sc.find_requirement_files(self.root),
                         ["requirements-dev.txt", "requirements.txt", "api/requirements.txt"])


class FingerprintTests(unittest.TestCase):
    def make(self, rule="B602", path="app.py", line=3, text="os.system(cmd)"):
        return {"tool": "bandit", "rule_id": rule, "file": path, "line": line, "key_text": text}

    def test_moving_code_does_not_change_the_fingerprint(self):
        a = sc.add_fingerprints([self.make(line=3)])[0]["fingerprint"]
        b = sc.add_fingerprints([self.make(line=40)])[0]["fingerprint"]
        self.assertEqual(a, b)

    def test_an_edited_line_or_another_file_or_rule_is_different(self):
        base = sc.add_fingerprints([self.make()])[0]["fingerprint"]
        for other in (self.make(text="os.system(cmd2)"), self.make(path="b.py"), self.make(rule="B605")):
            self.assertNotEqual(sc.add_fingerprints([other])[0]["fingerprint"], base)

    def test_repeats_of_the_same_line_get_different_fingerprints_in_file_order(self):
        found = sc.add_fingerprints([self.make(line=9), self.make(line=3)])
        self.assertEqual(len({f["fingerprint"] for f in found}), 2)
        # adding an unrelated finding earlier in the file does not change these two
        again = sc.add_fingerprints([self.make(line=9), self.make(line=3), self.make(rule="B1", line=1, text="y")])
        self.assertEqual({f["fingerprint"] for f in found}, {f["fingerprint"] for f in again if f["rule_id"] == "B602"})

    def test_new_existing_and_unknown(self):
        old = sc.add_fingerprints([self.make(text="a"), self.make(text="b")])
        new = sc.add_fingerprints([self.make(text="b"), self.make(text="c")])
        sc.mark_origins(new, old, compared=True)
        self.assertEqual({f["key_text"]: f["origin"] for f in new}, {"b": "existing", "c": "new"})
        sc.mark_origins(new, [], compared=False)
        self.assertEqual({f["origin"] for f in new}, {"unknown"})


class InstallTests(unittest.TestCase):
    def test_a_failed_environment_or_install_is_a_scanner_failure_with_a_reason(self):
        work = tempfile.mkdtemp()
        with self.assertRaises(sc.ScanError) as ctx:
            sc.install_tools(work, lambda cmd, cwd, timeout: done(1, err="no venv module"))
        self.assertIn("environment", str(ctx.exception))
        calls = []

        def fake(cmd, cwd, timeout):
            calls.append(cmd)
            return done(0) if "venv" in cmd else done(1, err="No matching distribution found")

        with self.assertRaises(sc.ScanError) as ctx:
            sc.install_tools(work, fake)
        self.assertIn("could not install", str(ctx.exception))
        self.assertTrue(all(pin in calls[-1] for _, pin in sc.PINS))

    def test_the_scanners_are_installed_at_pinned_versions(self):
        for _, pin in sc.PINS:
            self.assertIn("==", pin)

    def test_the_scanner_environment_has_no_secrets(self):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen.update(kwargs["env"])
            return done(0)

        with mock.patch.dict(os.environ, {"PRSENTINEL_GITHUB_TOKEN": "t", "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "x",
                                          "GROQ_API_KEY": "k", "PATH": os.environ.get("PATH", "")}):
            with mock.patch.object(subprocess, "run", fake_run):
                sc.default_run_cmd(["echo"], ".", 5)
        self.assertIn("PATH", seen)
        for name in ("PRSENTINEL_GITHUB_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "GROQ_API_KEY"):
            self.assertNotIn(name, seen)

    def test_a_timeout_becomes_a_scanner_failure(self):
        def slow(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 1)

        with mock.patch.object(subprocess, "run", slow):
            with self.assertRaises(sc.ScanError):
                sc.default_run_cmd(["x"], ".", 1)


def fake_scanner(marker):
    """A scanner that 'finds' an issue in any tree whose app.py contains `marker`."""
    def scan(py, root, run_cmd):
        try:
            text = open(os.path.join(root, "app.py"), encoding="utf-8").read()
        except OSError:
            raise sc.ScanError("app.py is missing")
        found = []
        for number, line in enumerate(text.splitlines(), 1):
            if marker in line:
                found.append({"tool": "bandit", "rule_id": "B602", "severity": "high", "confidence": "high",
                              "file": "app.py", "line": number, "message": "shell=True", "cwe": None,
                              "key_text": line.strip()})
        return sc.add_fingerprints(found), None
    return scan


def broken(reason):
    def scan(py, root, run_cmd):
        raise sc.ScanError(reason)
    return scan


class WholeStepTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeServer()
        self.addCleanup(self.server.close)
        self.kwargs = {"wait_seconds": 0, "retry_pause": 0}
        self.logs, self.notes = [], []
        # The old commit has one risky call; the new commit keeps it (moved) and adds another.
        self.repo, self.first, self.second = make_repo(
            {"app.py": "import os\nos.system(a, shell=True)\n"},
            {"app.py": "import os\nx = 1\nos.system(a, shell=True)\nos.system(b, shell=True)\n"})

    def go(self, base, fakes, **extra):
        patched = {"bandit": fakes.get("bandit", fake_scanner("shell=True")),
                   "pip-audit": fakes.get("pip-audit", broken("no requirements*.txt file was found")),
                   "detect-secrets": fakes.get("detect-secrets", fake_scanner("SECRET"))}
        with mock.patch.dict(sc.SCANNERS, patched):
            return sc.run(self.server.url, "rid", "run-token", base, self.repo, self.kwargs,
                          self.logs.append, lambda level, text: self.notes.append((level, text)),
                          tools_python=extra.pop("tools_python", sys.executable), **extra)

    def posted(self):
        (body,) = [b for m, p, b, a in self.server.requests if p == "/v1/runs/rid/findings"]
        return body

    def test_new_and_existing_findings_are_told_apart_and_a_move_does_not_count_as_new(self):
        old = os.getcwd()
        os.chdir(self.repo)
        try:
            self.go(self.first, {})
        finally:
            os.chdir(old)
        body = self.posted()
        by_origin = sorted((f["origin"], f["line"]) for f in body["findings"])
        self.assertEqual(by_origin, [("existing", 3), ("new", 4)])
        rows = {r["tool"]: r for r in body["scanners"]}
        self.assertEqual((rows["bandit"]["status"], rows["bandit"]["compared_with_base"]), ("ran", True))
        self.assertEqual(rows["pip-audit"]["status"], "could_not_run")
        self.assertIn("no requirements", rows["pip-audit"]["reason"])
        self.assertEqual(rows["detect-secrets"]["status"], "ran")
        self.assertEqual(body["omitted"], 0)
        self.assertTrue(all(len(f["fingerprint"]) == 64 for f in body["findings"]))
        self.assertTrue(all("key_text" not in f for f in body["findings"]))

    def test_the_first_commit_has_nothing_before_it_so_everything_is_new(self):
        self.go(diff.EMPTY_TREE, {})
        self.assertEqual({f["origin"] for f in self.posted()["findings"]}, {"new"})

    def test_when_the_old_code_cannot_be_scanned_nothing_is_called_new(self):
        calls = []

        def only_new(py, root, run_cmd):
            calls.append(root)
            if len(calls) > 1:
                raise sc.ScanError("old tree failed")
            return fake_scanner("shell=True")(py, root, run_cmd)

        self.go(self.first, {"bandit": only_new})
        body = self.posted()
        self.assertEqual({f["origin"] for f in body["findings"]}, {"unknown"})
        self.assertFalse({r["tool"]: r for r in body["scanners"]}["bandit"]["compared_with_base"])

    def test_old_code_without_a_requirements_file_means_every_dependency_is_new(self):
        calls = []

        def audit(py, root, run_cmd):
            calls.append(root)
            if len(calls) > 1:
                raise sc.NoDependencies("no requirements*.txt file was found")
            return fake_scanner("shell=True")(py, root, run_cmd)

        self.go(self.first, {"pip-audit": audit, "bandit": broken("off for this test")})
        body = self.posted()
        row = {r["tool"]: r for r in body["scanners"]}["pip-audit"]
        self.assertTrue(row["compared_with_base"])
        self.assertEqual({f["origin"] for f in body["findings"]}, {"new"})
        self.assertEqual(len(body["findings"]), 2)

    def test_an_unknown_base_commit_gives_unknown_not_new(self):
        self.go("f" * 40, {})
        self.assertEqual({f["origin"] for f in self.posted()["findings"]}, {"unknown"})

    def test_a_scanner_that_fails_is_reported_and_the_others_still_run(self):
        self.go(self.first, {"bandit": broken("it failed (boom)")})
        rows = {r["tool"]: r for r in self.posted()["scanners"]}
        self.assertEqual((rows["bandit"]["status"], rows["bandit"]["reason"]), ("could_not_run", "it failed (boom)"))
        self.assertEqual(rows["detect-secrets"]["status"], "ran")

    def test_if_the_scanners_cannot_be_installed_every_scanner_says_could_not_run(self):
        with mock.patch.object(sc, "install_tools", side_effect=sc.ScanError("could not install the scanners (offline)")):
            sc.run(self.server.url, "rid", "run-token", self.first, self.repo, self.kwargs,
                   self.logs.append, lambda level, text: self.notes.append((level, text)))
        body = self.posted()
        self.assertEqual(body["findings"], [])
        self.assertEqual({r["status"] for r in body["scanners"]}, {"could_not_run"})
        self.assertTrue(all("offline" in r["reason"] for r in body["scanners"]))
        self.assertEqual(self.notes[0][0], "warning")

    def test_the_time_budget_stops_further_scanners_honestly(self):
        self.go(self.first, {}, budget=-1)
        self.assertEqual({r["status"] for r in self.posted()["scanners"]}, {"could_not_run"})
        self.assertIn("time budget", self.posted()["scanners"][0]["reason"])

    def test_too_many_findings_are_cut_with_the_new_ones_kept_first(self):
        rows = [{"tool": "bandit", "rule_id": "B1", "severity": "low", "confidence": "low", "file": "a.py",
                 "line": i, "message": "m", "cwe": None, "fingerprint": "%064x" % i,
                 "origin": "existing"} for i in range(1, sc.MAX_SEND + 20)]
        rows.append({**rows[0], "fingerprint": "%064x" % 99999, "origin": "new", "line": 5000})
        body = sc.build_body([], rows)
        self.assertEqual(len(body["findings"]), sc.MAX_SEND)
        self.assertEqual(body["omitted"], len(rows) - sc.MAX_SEND)
        self.assertEqual(body["findings"][0]["origin"], "new")

    def test_the_old_checkout_and_scratch_folders_are_removed(self):
        before = set(os.listdir(tempfile.gettempdir()))
        old = os.getcwd()
        os.chdir(self.repo)
        try:
            self.go(self.first, {})
        finally:
            os.chdir(old)
        left = [n for n in set(os.listdir(tempfile.gettempdir())) - before
                if n.startswith(("prsentinel_scan_", "prsentinel_old_"))]
        self.assertEqual(left, [])
        listing = subprocess.run(["git", "worktree", "list"], cwd=self.repo, capture_output=True,
                                 text=True).stdout
        self.assertNotIn("prsentinel_old_", listing)


class MainWiringTests(RunnerBase):
    def run_main(self, **patches):
        server = FakeServer()
        self.addCleanup(server.close)
        env = self.env(server, pr_event(self.first), PRSENTINEL_RUN_SCANNERS="true")
        with mock.patch.object(sc, "install_tools", return_value=sys.executable), \
                mock.patch.dict(sc.SCANNERS, patches):
            code = self.go(env)
        return code, server

    def test_findings_are_sent_before_the_run_finishes(self):
        fakes = {"bandit": lambda py, root, run_cmd: ([], None),
                 "pip-audit": broken("no requirements*.txt file was found"),
                 "detect-secrets": lambda py, root, run_cmd: ([], None)}
        code, server = self.run_main(**fakes)
        self.assertEqual(code, 0)
        paths = [p for m, p, b, a in server.requests]
        self.assertLess(paths.index("/v1/runs/rid/findings"), paths.index("/v1/runs/rid/finish"))
        auth = [a for m, p, b, a in server.requests if p == "/v1/runs/rid/findings"]
        self.assertEqual(auth, ["Bearer run-token"])

    def test_a_crashing_scanner_never_fails_the_run(self):
        def crash(py, root, run_cmd):
            raise RuntimeError("surprise")

        code, server = self.run_main(bandit=crash)
        self.assertEqual(code, 0)
        self.assertIn("/v1/runs/rid/finish", [p for m, p, b, a in server.requests])

    def test_scanners_can_be_switched_off(self):
        server = FakeServer()
        self.addCleanup(server.close)
        with mock.patch.object(sc, "install_tools", side_effect=AssertionError("must not install")):
            self.assertEqual(self.go(self.env(server, pr_event(self.first))), 0)
        self.assertNotIn("/v1/runs/rid/findings", [p for m, p, b, a in server.requests])


@unittest.skipUnless(have("bandit") and have("detect_secrets"),
                     "bandit and detect-secrets are not installed in this interpreter")
class RealScannerTests(unittest.TestCase):
    """Runs the real tools on a tiny repository (skipped when they are not installed)."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        write(self.root, "app.py",
              'import subprocess\nAPI_KEY = "AKIAIOSFODNN7EXAMPLE"\n\ndef run(cmd):\n    return subprocess.run(cmd, shell=True)\n')
        write(self.root, "pkg/tests/test_x.py", "import pickle\nassert True\n")
        write(self.root, "test_top.py", "import pickle\nassert True\n")
        subprocess.run(["git", "init", "-q"], cwd=self.root)
        subprocess.run(["git", "add", "-A"], cwd=self.root)

    def test_bandit_finds_shell_true_and_leaves_test_files_alone(self):
        found, _ = sc.scan_bandit(sys.executable, self.root, sc.default_run_cmd)
        self.assertIn(("B602", "app.py", 5), [(f["rule_id"], f["file"], f["line"]) for f in found])
        self.assertEqual({f["file"] for f in found}, {"app.py"})  # nothing from the test files

    def test_detect_secrets_finds_the_key_and_does_not_send_its_value(self):
        found, _ = sc.scan_secrets(sys.executable, self.root, sc.default_run_cmd)
        self.assertTrue(found)
        self.assertTrue(all(f["file"] == "app.py" and f["line"] == 2 for f in found))
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", json.dumps(found))


if __name__ == "__main__":
    unittest.main()
