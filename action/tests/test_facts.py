"""Tests for the context fact extractor (C1)."""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "runner"))

import facts  # noqa: E402

CART = '''"""Shopping cart helpers."""


def add_item_to_cart(item, cart=None):
    """Add an item to the cart and return the cart.

    More detail on a second line.
    """
    if cart is None:
        cart = []
    cart.append(item)
    return cart


class Cart:
    """A cart."""

    @staticmethod
    def total(items) -> int:
        return len(items)

    async def sync(self, *args, key=1, **kw):
        def inner():
            return helper()
        return inner()
'''

CHECKOUT = '''from cart import add_item_to_cart as add
from . import util
from .pkg.mod import thing
import os.path
import json as j


def buy(item, fee=compute_fee()):
    value = add(item)
    util.log(value)
    j.dumps(value).strip()
    return make()()


add("module level")
'''


def get(items, name):
    return next(i for i in items if i["name"] == name)


class ExtractTests(unittest.TestCase):
    def test_symbols_signatures_docs_and_lines(self):
        result = facts.extract_file_facts("cart.py", CART.encode())
        self.assertIsNone(result["parse_error"])
        add = get(result["symbols"], "add_item_to_cart")
        self.assertEqual(add["kind"], "function")
        self.assertEqual(add["signature"], "(item, cart=None)")
        self.assertEqual(add["doc"], "Add an item to the cart and return the cart.")
        self.assertEqual(add["lines"], [4, 12])
        names = [s["name"] for s in result["symbols"]]
        self.assertEqual(names, ["add_item_to_cart", "Cart", "Cart.total", "Cart.sync",
                                 "Cart.sync.inner"])
        total = get(result["symbols"], "Cart.total")
        self.assertEqual(total["signature"], "(items) -> int")
        self.assertEqual(total["decorators"], ["staticmethod"])
        self.assertEqual(total["lines"][0], 18)  # starts at the decorator line
        self.assertEqual(get(result["symbols"], "Cart")["kind"], "class")
        self.assertEqual(get(result["symbols"], "Cart.sync")["signature"], "(self, *args, key=1, **kw)")

    def test_imports(self):
        result = facts.extract_file_facts("checkout.py", CHECKOUT.encode())
        imports = {(i["module"], i["name"], i["asname"], i["level"]) for i in result["imports"]}
        self.assertEqual(imports, {
            ("cart", "add_item_to_cart", "add", 0),
            ("", "util", None, 1),
            ("pkg.mod", "thing", None, 1),
            ("os.path", None, None, 0),
            ("json", None, "j", 0),
        })

    def test_calls_scope_dedupe_and_dynamic(self):
        result = facts.extract_file_facts("checkout.py", CHECKOUT.encode())
        calls = {(c["in"], c["name"]) for c in result["calls"]}
        # the default value is evaluated outside the function
        self.assertIn(("<module>", "compute_fee"), calls)
        self.assertIn(("buy", "add"), calls)
        self.assertIn(("buy", "util.log"), calls)
        self.assertIn(("buy", "j.dumps"), calls)
        self.assertIn(("buy", "make"), calls)
        self.assertIn(("<module>", "add"), calls)
        # j.dumps(value).strip() and make()() cannot be named
        self.assertEqual(result["dynamic_calls"], 2)
        # same call twice in one scope is listed once
        again = facts.extract_file_facts("a.py", b"def f():\n    g()\n    g()\n")
        self.assertEqual(len(again["calls"]), 1)

    def test_nested_function_calls_belong_to_nested_scope(self):
        result = facts.extract_file_facts("cart.py", CART.encode())
        self.assertIn(("Cart.sync.inner", "helper"), {(c["in"], c["name"]) for c in result["calls"]})
        self.assertIn(("Cart.sync", "inner"), {(c["in"], c["name"]) for c in result["calls"]})

    def test_class_bases_and_decorator_calls_run_in_outer_scope(self):
        source = b"@register(make_name())\nclass A(Base, mod.Other):\n    pass\n"
        result = facts.extract_file_facts("a.py", source)
        symbol = get(result["symbols"], "A")
        self.assertEqual(symbol["bases"], ["Base", "mod.Other"])
        self.assertEqual(symbol["decorators"], ["register"])
        calls = {(c["in"], c["name"]) for c in result["calls"]}
        self.assertEqual(calls, {("<module>", "register"), ("<module>", "make_name")})

    def test_fixture_and_test_flags(self):
        source = b"import pytest\n\n@pytest.fixture(scope='session')\ndef db():\n    pass\n\n@fixture\ndef other():\n    pass\n\ndef plain():\n    pass\n"
        result = facts.extract_file_facts("tests/conftest.py", source)
        self.assertTrue(result["is_test"])
        self.assertTrue(get(result["symbols"], "db")["is_fixture"])
        self.assertTrue(get(result["symbols"], "other")["is_fixture"])
        self.assertFalse(get(result["symbols"], "plain")["is_fixture"])

    def test_is_test_path(self):
        for path in ("test_a.py", "a_test.py", "tests/a.py", "pkg/tests/helpers.py", "conftest.py"):
            self.assertTrue(facts.is_test_path(path), path)
        for path in ("a.py", "pkg/contest.py", "pkg/testing_utils.py", "latest/a.py"):
            self.assertFalse(facts.is_test_path(path), path)

    def test_syntax_error_is_recorded_not_raised(self):
        result = facts.extract_file_facts("bad.py", b"def f(:\n")
        self.assertEqual(result["parse_error"], "syntax error (line 1)")
        self.assertEqual((result["symbols"], result["imports"], result["calls"]), ([], [], []))
        self.assertEqual(len(result["hash"]), 64)

    def test_null_bytes_do_not_crash(self):
        result = facts.extract_file_facts("a.py", b"x = 1\0\n")
        self.assertIsNotNone(result["parse_error"])

    def test_hash_changes_with_content_only(self):
        a = facts.extract_file_facts("a.py", b"x = 1\n")["hash"]
        self.assertEqual(a, facts.extract_file_facts("other_name.py", b"x = 1\n")["hash"])
        self.assertNotEqual(a, facts.extract_file_facts("a.py", b"x = 2\n")["hash"])

    def test_no_function_bodies_in_output(self):
        result = facts.extract_file_facts("cart.py", CART.encode())
        self.assertNotIn("cart = []", repr(result))
        self.assertNotIn("return len(items)", repr(result))

    def test_long_doc_and_signature_are_cut(self):
        source = ('def f(a=%r):\n    """%s"""\n' % ("x" * 500, "d" * 500)).encode()
        symbol = facts.extract_file_facts("a.py", source)["symbols"][0]
        self.assertLessEqual(len(symbol["doc"]), facts.MAX_DOC)
        self.assertLessEqual(len(symbol["signature"]), facts.MAX_SIGNATURE)


def make_repo(files):
    path = tempfile.mkdtemp()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    for name, content in files.items():
        full = os.path.join(path, name)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as handle:
            handle.write(content if isinstance(content, bytes) else content.encode())
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    return path


class ScanTests(unittest.TestCase):
    def test_manifest_lists_tracked_python_files_only(self):
        root = make_repo({"a.py": "x = 1\n", "pkg/b.py": "y = 2\n", "notes.txt": "hi",
                          "venv/lib/c.py": "z = 3\n", "node_modules/d.py": "w = 4\n"})
        with open(os.path.join(root, "untracked.py"), "w") as handle:
            handle.write("u = 1\n")
        scan = facts.scan_repo(root)
        self.assertEqual(sorted(scan.manifest), ["a.py", "pkg/b.py"])
        self.assertFalse(scan.partial)
        self.assertEqual(scan.manifest["a.py"], facts.hash_bytes(b"x = 1\n"))

    def test_limits_mark_scan_partial_with_reasons(self):
        root = make_repo({"a.py": "x = 1\n", "b.py": "y = 2\n", "c.py": "z = 3\n",
                          "a_big.py": "#" * 100})
        scan = facts.scan_repo(root, max_files=2, max_file_bytes=50)
        self.assertTrue(scan.partial)
        self.assertEqual(sorted(scan.manifest), ["a.py", "b.py"])
        self.assertEqual(dict(scan.skipped), {"a_big.py": "file too large", "c.py": "file limit"})
        self.assertEqual(scan.total_files, 4)

    def test_works_without_git(self):
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, "pkg"))
        os.makedirs(os.path.join(root, ".venv"))
        for name in ("a.py", "pkg/b.py", ".venv/c.py"):
            with open(os.path.join(root, name), "w") as handle:
                handle.write("x = 1\n")
        scan = facts.scan_repo(root)
        self.assertEqual(sorted(scan.manifest), ["a.py", "pkg/b.py"])

    def test_collect_facts_only_for_requested_paths(self):
        root = make_repo({"a.py": "def f():\n    pass\n", "b.py": "def g():\n    pass\n"})
        result = facts.collect_facts(root, ["b.py", "missing.py"])
        self.assertEqual([f["path"] for f in result], ["b.py"])
        self.assertEqual(result[0]["symbols"][0]["name"], "g")

    def test_repo_facts(self):
        root = make_repo({"cart.py": "def f():\n    pass\n",
                          "tests/test_cart.py": "import pytest\nfrom cart import f\n",
                          "tests/conftest.py": ""})
        scan = facts.scan_repo(root)
        all_facts = facts.collect_facts(root, list(scan.manifest))
        info = facts.repo_facts(root, all_facts, scan)
        self.assertEqual(info["test_framework"], "pytest")
        self.assertTrue(info["has_conftest"])
        self.assertEqual(info["python_files"], 3)
        self.assertEqual(info["test_files"], 2)
        self.assertFalse(info["partial"])

    def test_repo_facts_unittest_and_unknown(self):
        root = make_repo({"test_a.py": "import unittest\n"})
        scan = facts.scan_repo(root)
        info = facts.repo_facts(root, facts.collect_facts(root, list(scan.manifest)), scan)
        self.assertEqual(info["test_framework"], "unittest")
        root = make_repo({"a.py": "x = 1\n"})
        scan = facts.scan_repo(root)
        info = facts.repo_facts(root, facts.collect_facts(root, list(scan.manifest)), scan)
        self.assertEqual(info["test_framework"], "unknown")


if __name__ == "__main__":
    unittest.main()
