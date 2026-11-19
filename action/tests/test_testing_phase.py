"""Tests for the test phase (D3): running generated tests on the old and the new code."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "runner"))

import pytest_runner as pr  # noqa: E402
import testing_phase as tp  # noqa: E402
from test_action import FakeServer, RunnerBase, pr_event  # noqa: E402

HAS_PYTEST = pr.pytest_available()[0]
needs_pytest = unittest.skipUnless(HAS_PYTEST, "pytest is not installed in this interpreter")

SOURCE = '''import functools

class Cart:
    @property
    def size(self):
        return 3

    def total(self, tax):
        # a comment
        return 1 + tax


def price(item):
    return item * 2
'''


class HelperTests(unittest.TestCase):
    def test_function_source_function_method_and_decorator(self):
        self.assertEqual(tp.function_source(SOURCE, "price"), "def price(item):\n    return item * 2\n")
        self.assertEqual(tp.function_source(SOURCE, "Cart.total"),
                         "def total(self, tax):\n    # a comment\n    return 1 + tax\n")
        self.assertTrue(tp.function_source(SOURCE, "Cart.size").startswith("@property\ndef size"))
        self.assertIsNone(tp.function_source(SOURCE, "missing"))
        self.assertIsNone(tp.function_source("def broken(:\n", "broken"))

    def test_test_files_are_not_tested(self):
        for path in ("tests/test_a.py", "pkg/tests/helpers.py", "test_x.py", "a_test.py", "conftest.py",
                     "pkg/conftest.py"):
            self.assertTrue(tp.is_test_path(path), path)
        for path in ("app/pricing.py", "attest.py", "src/contest.py"):
            self.assertFalse(tp.is_test_path(path), path)

    def test_child_env_keeps_only_allowed_names(self):
        source = {"PATH": "/bin", "HOME": "/h", "USERPROFILE": "/h", "GITHUB_TOKEN": "t", "PRSENTINEL_GITHUB_TOKEN": "t",
                  "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "t", "ACTIONS_ID_TOKEN_REQUEST_URL": "u",
                  "GROQ_API_KEY": "k", "AWS_SECRET_ACCESS_KEY": "s", "VIRTUAL_ENV": "/v"}
        env = pr.child_env(source, root="/work", run_index=2)
        self.assertEqual(set(env) - {"PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "CI", "PRSENTINEL_RUN_INDEX"},
                         {"PATH", "VIRTUAL_ENV", "USERPROFILE" if os.name == "nt" else "HOME"})
        self.assertTrue(env["PYTHONPATH"].startswith("/work"))
        self.assertEqual(env["PRSENTINEL_RUN_INDEX"], "2")

    def test_build_tests_pairs_old_and_new_results(self):
        after = {"tests": {"t1": "failed", "t2": "passed"}, "messages": {"t1": "boom"},
                 "reruns": {"t1": {"passes": 0, "fails": 3}}}
        got = {t["name"]: t for t in tp.build_tests({"tests": {"t1": "passed"}}, after)}
        self.assertEqual((got["t1"]["before"], got["t1"]["after"], got["t1"]["rerun_fails"],
                          got["t1"]["message"]), ("passed", "failed", 3, "boom"))
        self.assertEqual(got["t2"]["before"], "error")  # missing on the old side: never a pass
        file_broken = {t["before"] for t in tp.build_tests({"tests": {}}, after)}
        self.assertEqual(file_broken, {"error"})        # old file could not load
        new_function = {t["before"] for t in tp.build_tests(None, after)}
        self.assertEqual(new_function, {"skipped"})


@needs_pytest
class RunnerProcessTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        with open(os.path.join(self.root, "lib.py"), "w") as handle:
            handle.write("def f():\n    return 1\n")

    def run_code(self, code, **kwargs):
        return pr.run_in_tree(self.root, code, "test_prs_0.py", **kwargs)

    def test_passing_and_failing_tests(self):
        out = self.run_code("from lib import f\n\ndef test_ok():\n    assert f() == 1\n\n"
                            "def test_bad():\n    assert f() == 2, 'wrong value'\n")
        self.assertEqual(out["status"], pr.OK)
        self.assertEqual(out["tests"], {"test_ok": "passed", "test_bad": "failed"})
        self.assertIn("wrong value", out["messages"]["test_bad"])

    def test_class_tests_use_class_prefix(self):
        out = self.run_code("from lib import f\n\nclass TestF:\n    def test_a(self):\n        assert f() == 1\n")
        self.assertEqual(out["tests"], {"TestF::test_a": "passed"})

    def test_import_failure_is_an_error_with_no_tests(self):
        out = self.run_code("from lib import missing\n\ndef test_a():\n    pass\n")
        self.assertEqual(out["status"], pr.BROKEN)
        self.assertEqual(out["tests"], {})
        self.assertIn("missing", out["error"])

    def test_timeout_is_reported_not_passed(self):
        out = self.run_code("import time\n\ndef test_a():\n    time.sleep(30)\n", timeout=2)
        self.assertEqual(out["status"], pr.TIMEOUT)
        self.assertEqual(out["tests"], {})

    def test_the_test_folder_is_removed_afterwards(self):
        self.run_code("def test_a():\n    pass\n")
        self.assertFalse(os.path.exists(os.path.join(self.root, pr.TEST_DIR)))
        self.assertEqual(sorted(os.listdir(self.root)), ["lib.py"])

    def test_secrets_never_reach_the_tests(self):
        code = ("import os\n\ndef test_a():\n    assert 'SECRET_PRS_X' not in os.environ\n"
                "    assert 'ACTIONS_ID_TOKEN_REQUEST_TOKEN' not in os.environ\n")
        with mock.patch.dict(os.environ, {"SECRET_PRS_X": "1", "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "t"}):
            out = self.run_code(code)
        self.assertEqual(out["tests"], {"test_a": "passed"})

    def test_reruns_expose_a_flaky_test_and_a_steady_failure(self):
        code = ("import os\n\ndef test_flaky():\n    assert int(os.environ.get('PRSENTINEL_RUN_INDEX', '1')) % 2 == 0\n\n"
                "def test_steady():\n    assert False\n\ndef test_fine():\n    pass\n")
        out = self.run_code(code, rerun_times=4)
        self.assertEqual(out["tests"]["test_flaky"], "failed")  # the first run counts as run 1
        self.assertEqual(out["reruns"]["test_flaky"], {"passes": 2, "fails": 2})
        self.assertEqual(out["reruns"]["test_steady"], {"passes": 0, "fails": 4})
        self.assertNotIn("test_fine", out["reruns"])

    def test_the_repos_own_addopts_are_ignored(self):
        with open(os.path.join(self.root, "pytest.ini"), "w") as handle:
            handle.write("[pytest]\naddopts = --definitely-not-an-option\n")
        out = self.run_code("def test_a():\n    pass\n")
        self.assertEqual(out["tests"], {"test_a": "passed"})


@needs_pytest
class RepairHelperTests(unittest.TestCase):
    def test_label_matches_the_servers_rule(self):
        self.assertEqual(tp._label("passed", "failed"), "CATCHES_CHANGE")
        self.assertEqual(tp._label("passed", "error"), "CATCHES_CHANGE")
        self.assertEqual(tp._label("failed", "failed"), "TEST_WRONG_ON_BEFORE")
        self.assertEqual(tp._label("error", "failed"), "TEST_WRONG_ON_BEFORE")
        self.assertEqual(tp._label("failed", "passed"), "ODD")
        self.assertEqual(tp._label("passed", "passed"), "NO_SIGNAL")
        self.assertEqual(tp._label("skipped", "failed"), "CATCHES_CHANGE")  # an added function: no old result

    def test_a_repair_may_not_lose_a_catching_test(self):
        def t(name, before, after):
            return {"name": name, "before": before, "after": after}
        previous = [t("catch", "passed", "failed"), t("wrong", "failed", "failed"), t("same", "passed", "passed")]
        self.assertEqual(tp.lost_catches(previous, [t("catch", "passed", "failed"), t("wrong", "passed", "failed")]), [])
        self.assertEqual(tp.lost_catches(previous, [t("wrong", "passed", "passed")]), ["catch"])          # removed
        self.assertEqual(tp.lost_catches(previous, [t("catch", "passed", "passed")]), ["catch"])          # weakened
        self.assertEqual(tp.lost_catches(previous, [t("catch", "failed", "failed")]), ["catch"])          # now wrong


class EndToEndTests(RunnerBase):
    GOOD = {"status": "generated", "test_code": "from a import f\n\ndef test_f():\n    assert f() == 1\n",
            "import_line": "from a import f", "cached": False, "function_id": "fid", "reason": None}

    def run_with(self, reply, repairs=None):
        server = FakeServer(generate_reply=reply, repair_replies=repairs)
        self.addCleanup(server.close)
        code = self.go(self.env(server, pr_event(self.first), PRSENTINEL_RUN_TESTS="true"))
        return code, server

    def posted(self, server, suffix):
        return [r for r in server.requests if r[1].endswith(suffix)]

    def test_changed_function_is_generated_run_on_both_versions_and_reported(self):
        code, server = self.run_with(self.GOOD)
        self.assertEqual(code, 0)
        (generate,) = self.posted(server, "/generate")
        self.assertEqual(generate[2]["old_code"], "def f():\n    return 1\n")
        self.assertEqual(generate[2]["new_code"], "def f():\n    return 2\n")
        self.assertEqual(generate[3], "Bearer run-token")
        (results,) = self.posted(server, "/results")
        body = results[2]
        self.assertEqual((body["file"], body["name"], body["status"]), ("a.py", "f", "tested"))
        (test,) = body["tests"]
        self.assertEqual((test["name"], test["before"], test["after"]), ("test_f", "passed", "failed"))
        self.assertEqual((test["rerun_passes"], test["rerun_fails"]), (0, 3))
        self.assertIn("assert", test["message"])
        self.assertEqual(sorted(os.listdir(self.repo)), sorted(
            [n for n in os.listdir(self.repo) if n != pr.TEST_DIR]))
        self.assertFalse(os.path.exists(os.path.join(self.repo, pr.TEST_DIR)))

    WRONG = ("from a import f\n\ndef test_catch():\n    assert f() == 1\n\n"
             "def test_wrong():\n    assert f() == 3\n")

    def generated(self, code):
        return dict(self.GOOD, test_code=code)

    def repaired(self, wrong_value, keep_catch=True):
        code = "from a import f\n\n"
        if keep_catch:
            code += "def test_catch():\n    assert f() == 1\n\n"
        code += "def test_wrong():\n    assert f() == %d\n" % wrong_value
        return {"status": "generated", "test_code": code, "import_line": "from a import f",
                "function_id": "fid", "reason": None, "attempt": 1}

    def test_a_test_wrong_on_both_versions_is_repaired_and_reported_again(self):
        code, server = self.run_with(self.generated(self.WRONG), [self.repaired(1)])
        self.assertEqual(code, 0)
        (repair,) = self.posted(server, "/repair")
        body = repair[2]
        self.assertEqual((body["attempt"], body["old_code"], body["new_code"]),
                         (1, "def f():\n    return 1\n", "def f():\n    return 2\n"))
        self.assertEqual([t["name"] for t in body["failing"]], ["test_wrong"])
        self.assertIn("f() == 3", body["test_code"])
        self.assertEqual(repair[3], "Bearer run-token")
        first, second = [r[2] for r in self.posted(server, "/results")]
        self.assertEqual((first["attempt"], second["attempt"]), (0, 1))
        got = {t["name"]: (t["before"], t["after"]) for t in second["tests"]}
        self.assertEqual(got, {"test_catch": ("passed", "failed"), "test_wrong": ("passed", "failed")})

    def test_the_mutation_check_runs_on_the_old_code_and_is_reported(self):
        code, server = self.run_with(self.GOOD)
        self.assertEqual(code, 0)
        (mut,) = self.posted(server, "/mutation")
        body = mut[2]
        self.assertEqual((body["file"], body["name"], body["status"]), ("a.py", "f", "measured"))
        self.assertGreaterEqual(body["killed"], 1)  # `return 1` to `return 2` / `return None`
        self.assertEqual(body["survived"], 0)
        self.assertEqual(mut[3], "Bearer run-token")
        # the old code is only ever touched in the throwaway checkout, never in the user's tree
        with open(os.path.join(self.repo, "a.py"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "def f():\n    return 2\n")

    def test_the_mutation_check_uses_the_repaired_tests(self):
        code, server = self.run_with(self.generated(self.WRONG), [self.repaired(1)])
        self.assertEqual(code, 0)
        (mut,) = self.posted(server, "/mutation")
        self.assertEqual(mut[2]["status"], "measured")
        self.assertGreaterEqual(mut[2]["killed"], 1)

    def test_there_are_at_most_two_repair_rounds(self):
        _, server = self.run_with(self.generated(self.WRONG), [self.repaired(4), self.repaired(5)])
        self.assertEqual(len(self.posted(server, "/repair")), 2)
        attempts = [r[2]["attempt"] for r in self.posted(server, "/results")]
        self.assertEqual(attempts, [0, 1, 2])
        self.assertEqual(len(self.posted(server, "/finish")), 1)

    def test_the_second_round_only_runs_while_something_is_still_wrong(self):
        _, server = self.run_with(self.generated(self.WRONG), [self.repaired(1), self.repaired(9)])
        self.assertEqual(len(self.posted(server, "/repair")), 1)

    def test_a_repair_that_loses_a_catching_test_is_not_used(self):
        _, server = self.run_with(self.generated(self.WRONG), [self.repaired(1, keep_catch=False)])
        self.assertEqual(len(self.posted(server, "/repair")), 1)
        (only,) = self.posted(server, "/results")
        self.assertEqual(only[2]["attempt"], 0)

    def test_a_repair_that_cannot_run_is_not_used(self):
        broken = dict(self.repaired(1), test_code="from a import missing\n\ndef test_x():\n    pass\n")
        _, server = self.run_with(self.generated(self.WRONG), [broken])
        (only,) = self.posted(server, "/results")
        self.assertEqual((only[2]["attempt"], only[2]["status"]), (0, "tested"))

    def test_no_repair_is_asked_for_when_a_test_is_not_wrong_on_both(self):
        _, server = self.run_with(self.GOOD)
        self.assertEqual(self.posted(server, "/repair"), [])

    def test_when_the_server_cannot_repair_the_first_results_stay(self):
        _, server = self.run_with(self.generated(self.WRONG), [
            {"status": "could_not_generate", "reason": "The AI service is busy."}])
        (only,) = self.posted(server, "/results")
        self.assertEqual((only[2]["attempt"], only[2]["status"]), (0, "tested"))
        self.assertEqual(len(self.posted(server, "/finish")), 1)

    def test_no_tests_from_the_server_means_no_results_call(self):
        code, server = self.run_with(None)
        self.assertEqual(code, 0)
        self.assertEqual(self.posted(server, "/results"), [])
        self.assertEqual(len(self.posted(server, "/finish")), 1)

    def test_tests_that_cannot_load_are_reported_as_could_not_test(self):
        bad = dict(self.GOOD, test_code="from a import missing\n\ndef test_f():\n    pass\n")
        _, server = self.run_with(bad)
        (results,) = self.posted(server, "/results")
        self.assertEqual(results[2]["status"], "could_not_test")
        self.assertIn("could not run on the new code", results[2]["reason"])
        self.assertEqual(results[2]["tests"], [])

    def test_switched_off_makes_no_calls(self):
        server = FakeServer(generate_reply=self.GOOD)
        self.addCleanup(server.close)
        self.go(self.env(server, pr_event(self.first), PRSENTINEL_RUN_TESTS="false"))
        self.assertEqual(self.posted(server, "/generate"), [])

    def test_missing_pytest_skips_generation_with_a_note(self):
        server = FakeServer(generate_reply=self.GOOD)
        self.addCleanup(server.close)
        with mock.patch.object(pr, "pytest_available", return_value=(False, "No module named pytest")):
            code = self.go(self.env(server, pr_event(self.first), PRSENTINEL_RUN_TESTS="true"))
        self.assertEqual(code, 0)
        self.assertEqual(self.posted(server, "/generate"), [])
        self.assertEqual(len(self.posted(server, "/finish")), 1)


if __name__ == "__main__":
    unittest.main()
