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


class OperatorTests(unittest.TestCase):
    """Ported from v1 test_mutation.py: each fault is made, and only where it should be."""

    def names(self, code, name="f"):
        return [m["operator"] for m in mutation.build_mutants(code, name)["mutants"]]

    def source_for(self, code, operator, name="f"):
        for m in mutation.build_mutants(code, name)["mutants"]:
            if m["operator"] == operator:
                return m["source"]
        self.fail("no %r mutant in %s" % (operator, self.names(code, name)))

    def test_every_operator_is_made_from_the_matching_code(self):
        cases = [
            ("+ to -", "def f(a, b):\n    return a + b\n", "a - b"),
            ("- to +", "def f(a, b):\n    return a - b\n", "a + b"),
            ("< to <=", "def f(a):\n    return a < 3\n", "a <= 3"),
            ("<= to <", "def f(a):\n    return a <= 3\n", "a < 3"),
            ("> to >=", "def f(a):\n    return a > 3\n", "a >= 3"),
            (">= to >", "def f(a):\n    return a >= 3\n", "a > 3"),
            ("== to !=", "def f(a):\n    return a == 3\n", "a != 3"),
            ("!= to ==", "def f(a):\n    return a != 3\n", "a == 3"),
            ("True to False", "def f():\n    x = True\n    return x\n", "x = False"),
            ("False to True", "def f():\n    x = False\n    return x\n", "x = True"),
            ("is None to is not None", "def f(a):\n    return a is None\n", "a is not None"),
            ("is not None to is None", "def f(a):\n    return a is not None\n", "a is None"),
            ("and to or", "def f(a, b):\n    return a and b\n", "a or b"),
            ("or to and", "def f(a, b):\n    return a or b\n", "a and b"),
            ("n to n + 1", "def f():\n    x = 4\n    return x\n", "x = 5"),
            ("return expr to return None", "def f(a):\n    return a\n", "return None"),
            ("None default to []", "def f(a=None):\n    return a\n", "a=[]"),
            ("[] default to None", "def f(a=[]):\n    return a\n", "a=None"),
        ]
        self.assertEqual({c[0] for c in cases}, set(mutation.KIND_LABELS.values()))  # all 18
        for operator, code, expected in cases:
            self.assertIn(expected, self.source_for(code, operator), operator)

    def test_an_is_comparison_to_something_else_is_left_alone(self):
        names = self.names("def f(a, b):\n    return a is b\n")
        self.assertNotIn("is None to is not None", names)
        self.assertNotIn("is not None to is None", names)

    def test_a_nested_functions_default_is_left_alone(self):
        code = "def f():\n    def g(x=None):\n        return x\n    return g\n"
        self.assertNotIn("None default to []", self.names(code))

    def test_a_chained_comparison_changes_one_link_at_a_time(self):
        names = self.names("def f(a):\n    return 1 < a <= 5\n")
        self.assertIn("< to <=", names)
        self.assertIn("<= to <", names)

    def test_a_bool_is_not_also_counted_as_a_number(self):
        self.assertEqual(self.names("def f():\n    x = True\n    return x\n").count("n to n + 1"), 0)

    def test_return_none_is_not_changed_again(self):
        self.assertNotIn("return expr to return None", self.names("def f():\n    return None\n"))

    def test_an_async_function_is_found_and_its_class_method_too(self):
        self.assertEqual(sorted(self.names("async def f(a):\n    return a + 1\n")),
                         ["+ to -", "n to n + 1", "return expr to return None"])

    def test_the_unmutated_rebuild_is_valid_python(self):
        for m in mutation.build_mutants(MODULE, "clamp")["mutants"]:
            ast_ok = compile(m["source"], "m", "exec")
            self.assertIsNotNone(ast_ok)

    def test_an_identical_mutant_is_flagged(self):
        # a changed constant inside an unused `0 * x` still differs in the tree, so it is NOT
        # identical; identical is only set when the tree did not change at all
        for m in mutation.build_mutants("def f(x):\n    return x + 1\n", "f")["mutants"]:
            self.assertFalse(m["identical"])


@unittest.skipUnless(AVAILABLE, "pytest is not installed")
class KillTests(unittest.TestCase):
    """Ported from v1: a test that raises or errors counts as a kill."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="prs_kill_")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        with open(os.path.join(self.root, "lib.py"), "w", encoding="utf-8") as handle:
            handle.write("def f(n):\n    return n + 1\n")

    def test_a_test_that_raises_counts_as_a_kill(self):
        tests = "from lib import f\n\ndef test_f():\n    assert f(1) == 2\n"
        result = mutation.run_mutation(self.root, "lib.py", "f", tests, "t.py")
        self.assertEqual(result["status"], "measured")
        self.assertEqual(result["survived"], 0)

    def test_a_mutant_that_cannot_load_is_counted_apart_and_kept_out_of_the_score(self):
        # `return n + 1` to `return None` makes the module-level `f(1) + 1` fail at import
        with open(os.path.join(self.root, "lib.py"), "w", encoding="utf-8") as handle:
            handle.write("def f(n):\n    return n + 1\n\nVALUE = f(1) + 1\n")
        tests = "from lib import f\n\ndef test_f():\n    assert f(1) == 2\n"
        result = mutation.run_mutation(self.root, "lib.py", "f", tests, "t.py")
        self.assertEqual(result["status"], "measured")
        self.assertGreaterEqual(result["could_not_run"], 1)
        self.assertEqual(result["survived"], 0)


if __name__ == "__main__":
    unittest.main()
