"""Diff reader tests, ported from v1 `test_diff_extractor.py` (the v2 reader compares git revisions
and returns only names and change types; the old and new source are taken from git later)."""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "runner"))

import diff  # noqa: E402

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "..", "bench", "v1-capstone", "examples")


def example(folder, name):
    with open(os.path.join(EXAMPLES, folder, name), encoding="utf-8") as handle:
        return handle.read()


def compare(before, after):
    return dict(diff.compare_sources(before, after))


class CompareTests(unittest.TestCase):
    def test_round1_off_by_one_is_a_modified_function_and_the_rest_is_not_reported(self):
        got = compare(example("round1_off_by_one", "before.py"), example("round1_off_by_one", "after.py"))
        self.assertEqual(got.get("get_recent_scores"), "modified")
        self.assertNotIn("average_score", got)

    def test_round2_mutable_default_is_modified_and_the_rest_is_not_reported(self):
        got = compare(example("round2_mutable_default", "before.py"),
                      example("round2_mutable_default", "after.py"))
        self.assertEqual(got.get("add_item_to_cart"), "modified")
        self.assertEqual(len(got), 1)

    def test_comment_and_whitespace_only_edits_are_not_changes(self):
        self.assertEqual(diff.compare_sources("def f(a):\n    return a\n",
                                              "# new comment\ndef f(a):\n    return a  # same\n"), [])
        self.assertEqual(diff.compare_sources("def f(a):\n    return a\n",
                                              "def f( a ):\n\n    return   a\n"), [])

    def test_a_changed_default_docstring_or_parameter_name_is_a_change(self):
        base = "def f(a, b=1):\n    'doc'\n    return a\n"
        self.assertEqual(compare(base, base.replace("b=1", "b=2")), {"f": "modified"})
        self.assertEqual(compare(base, base.replace("'doc'", "'other'")), {"f": "modified"})
        self.assertEqual(compare(base, base.replace("a, b", "x, b").replace("return a", "return x")),
                         {"f": "modified"})

    def test_added_and_removed_functions(self):
        self.assertEqual(compare("def a():\n    pass\n", "def a():\n    pass\n\ndef b():\n    pass\n"),
                         {"b": "added"})
        self.assertEqual(compare("def a():\n    pass\n\ndef b():\n    pass\n", "def a():\n    pass\n"),
                         {"b": "deleted"})

    def test_method_and_nested_names_include_their_parents(self):
        before = "class Cart:\n    def total(self):\n        return 1\n"
        after = ("class Cart:\n    def total(self):\n        def inner():\n            return 2\n"
                 "        return inner()\n    def add(self):\n        pass\n")
        got = compare(before, after)
        self.assertEqual(got["Cart.total"], "modified")
        self.assertEqual(got["Cart.total.inner"], "added")
        self.assertEqual(got["Cart.add"], "added")

    def test_a_removed_method_is_reported_as_deleted(self):
        before = "class A:\n    def f(self):\n        pass\n    def g(self):\n        pass\n"
        after = "class A:\n    def f(self):\n        pass\n"
        self.assertEqual(compare(before, after), {"A.g": "deleted"})

    def test_identical_files_report_nothing(self):
        code = example("round1_off_by_one", "before.py")
        self.assertEqual(diff.compare_sources(code, code), [])

    def test_a_new_file_adds_everything_and_a_removed_file_deletes_everything(self):
        code = "def a():\n    pass\n"
        self.assertEqual(diff.compare_sources(None, code), [("a", "added")])
        self.assertEqual(diff.compare_sources(code, None), [("a", "deleted")])

    def test_bad_code_raises_a_syntax_error(self):
        with self.assertRaises(SyntaxError):
            diff.compare_sources("def f(:\n", "def f():\n    pass\n")

    def test_async_functions_are_found(self):
        self.assertEqual(compare("async def f():\n    return 1\n", "async def f():\n    return 2\n"),
                         {"f": "modified"})


class GitTests(unittest.TestCase):
    """The v2 reader works on git revisions."""

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="prs_diff_")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.repo, ignore_errors=True))
        self.git("init", "-q")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True)

    def write(self, name, text):
        os.makedirs(os.path.dirname(os.path.join(self.repo, name)) or self.repo, exist_ok=True)
        with open(os.path.join(self.repo, name), "w", encoding="utf-8") as handle:
            handle.write(text)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True,
                              text=True).stdout.strip()

    def test_finds_modified_added_and_deleted_functions_across_files(self):
        self.write("a.py", "def f():\n    return 1\n")
        self.write("gone.py", "def old():\n    pass\n")
        self.write("notes.txt", "x")
        first = self.commit("one")
        self.write("a.py", "def f():\n    return 2\n\ndef g():\n    pass\n")
        os.remove(os.path.join(self.repo, "gone.py"))
        self.write("b.py", "def new():\n    pass\n")
        self.write("notes.txt", "y")
        self.commit("two")
        count, functions, skipped = diff.find_changes(first, "HEAD", self.repo)
        self.assertEqual(count, 4)  # a.py, gone.py, b.py, notes.txt
        got = {(f["file"], f["name"]): f["change_type"] for f in functions}
        self.assertEqual(got, {("a.py", "f"): "modified", ("a.py", "g"): "added",
                               ("gone.py", "old"): "deleted", ("b.py", "new"): "added"})
        self.assertEqual(skipped, [])

    def test_a_file_with_a_syntax_error_is_skipped_with_a_reason(self):
        self.write("a.py", "def f():\n    return 1\n")
        first = self.commit("one")
        self.write("a.py", "def f(:\n")
        self.commit("two")
        _, functions, skipped = diff.find_changes(first, "HEAD", self.repo)
        self.assertEqual(functions, [])
        self.assertEqual(skipped, [("a.py", "syntax error")])

    def test_unchanged_commit_reports_nothing(self):
        self.write("a.py", "def f():\n    return 1\n")
        first = self.commit("one")
        count, functions, skipped = diff.find_changes(first, "HEAD", self.repo)
        self.assertEqual((count, functions, skipped), (0, [], []))


if __name__ == "__main__":
    unittest.main()
