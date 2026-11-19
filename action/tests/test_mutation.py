"""D6: the mutation check on the runner side (the faults, the score inputs, the safety rules)."""
import os
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "runner"))

import mutation  # noqa: E402
import pytest_runner as pr  # noqa: E402

AVAILABLE = pr.pytest_available()[0]

MODULE = textwrap.dedent('''\
    def clamp(x, low=None):
        # keeps x at or above low
        if low is not None and x < low:
            return low
        return x + 1
    ''')

TESTS_STRONG = textwrap.dedent('''\
    from lib import clamp

    def test_below():
        assert clamp(1, 5) == 5

    def test_equal():
        assert clamp(5, 5) == 6

    def test_none():
        assert clamp(2) == 3
    ''')

TESTS_WEAK = textwrap.dedent('''\
    from lib import clamp

    def test_runs():
        assert clamp(10) is not None
    ''')


class BuildTests(unittest.TestCase):
    def test_finds_the_expected_faults_in_a_fixed_order(self):
        built = mutation.build_mutants(MODULE, "clamp")
        names = [m["operator"] for m in built["mutants"]]
        self.assertEqual(names[0], "None default to []")
        for expected in ("is not None to is None", "and to or", "< to <=", "+ to -", "n to n + 1"):
            self.assertIn(expected, names)
        self.assertFalse(built["capped"])
        self.assertEqual(built["found"], len(built["mutants"]))
        self.assertEqual(names, [m["operator"] for m in mutation.build_mutants(MODULE, "clamp")["mutants"]])

    def test_method_names_are_dotted_and_unknown_names_give_nothing(self):
        code = "class A:\n    def f(self, a):\n        return a + 1\n"
        self.assertEqual(mutation.build_mutants(code, "A.f")["found"], 3)
        self.assertEqual(mutation.build_mutants(code, "A.g")["found"], 0)

    def test_the_cap_cuts_the_list_and_says_so(self):
        built = mutation.build_mutants(MODULE, "clamp", limit=2)
        self.assertTrue(built["capped"])
        self.assertEqual(len(built["mutants"]), 2)

    def test_a_mutant_really_differs_and_is_valid_python(self):
        for m in mutation.build_mutants(MODULE, "clamp")["mutants"]:
            compile(m["source"], "m", "exec")
        self.assertIn("x <= low", [m["source"] for m in mutation.build_mutants(MODULE, "clamp")["mutants"]
                                   if m["operator"] == "< to <="][0])


@unittest.skipUnless(AVAILABLE, "pytest is not installed")
class RunTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="prs_mut_")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        with open(os.path.join(self.root, "lib.py"), "w", encoding="utf-8") as handle:
            handle.write(MODULE)

    def read(self):
        with open(os.path.join(self.root, "lib.py"), encoding="utf-8") as handle:
            return handle.read()

    def test_strong_tests_kill_most_faults_and_the_file_is_put_back(self):
        result = mutation.run_mutation(self.root, "lib.py", "clamp", TESTS_STRONG, "test_m.py")
        self.assertEqual(result["status"], "measured")
        self.assertGreaterEqual(result["killed"], 4)
        self.assertEqual(self.read(), MODULE)  # byte for byte, comment included
        self.assertFalse(os.path.exists(os.path.join(self.root, pr.TEST_DIR)))

    def test_weak_tests_let_faults_survive_and_name_them(self):
        result = mutation.run_mutation(self.root, "lib.py", "clamp", TESTS_WEAK, "test_m.py")
        self.assertEqual(result["status"], "measured")
        self.assertGreater(result["survived"], 0)
        self.assertTrue(result["survivors"])
        self.assertEqual(self.read(), MODULE)

    def test_tests_that_fail_on_the_plain_old_code_are_not_measured(self):
        wrong = "from lib import clamp\n\ndef test_wrong():\n    assert clamp(1) == 99\n"
        result = mutation.run_mutation(self.root, "lib.py", "clamp", wrong, "test_m.py")
        self.assertEqual(result["status"], "not_measured")
        self.assertIn("no generated test passes", result["reason"])
        self.assertEqual(result["killed"], 0)

    def test_a_test_that_already_fails_cannot_kill_anything(self):
        mixed = TESTS_WEAK + "\ndef test_wrong():\n    assert clamp(1) == 99\n"
        result = mutation.run_mutation(self.root, "lib.py", "clamp", mixed, "test_m.py")
        self.assertEqual(result["status"], "measured")
        self.assertGreater(result["survived"], 0)  # the always-failing test did not kill mutants

    def test_missing_function_or_file_is_not_measured_and_nothing_breaks(self):
        self.assertEqual(mutation.run_mutation(self.root, "lib.py", "nope", TESTS_WEAK, "t.py")["status"],
                         "not_measured")
        self.assertEqual(mutation.run_mutation(self.root, "gone.py", "clamp", TESTS_WEAK, "t.py")["status"],
                         "not_measured")

    def test_the_time_budget_stops_early_and_says_so(self):
        ticks = iter([0, 0, 1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000])
        result = mutation.run_mutation(self.root, "lib.py", "clamp", TESTS_STRONG, "test_m.py",
                                       budget=5, clock=lambda: next(ticks))
        self.assertTrue(result["stopped_early"])
        self.assertEqual(self.read(), MODULE)


if __name__ == "__main__":
    unittest.main()
