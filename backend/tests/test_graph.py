"""Call graph (C3): resolving names to places in the repo, and measuring how accurate it is.

The accuracy test uses a small hand-labelled repo. EVERY call in it is labelled with what it
truly points at (written by a person reading the code, not by running the resolver). The numbers
it prints are what the project report quotes.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "action", "runner"))

import facts  # noqa: E402

from app.services.context_engine.graph import build_edges  # noqa: E402

REPO = {
    "pkg/__init__.py": "from .core import Engine, make_engine\n",
    "pkg/core.py": '''import os
from . import util
from .util import clean as tidy


class Base:
    def helper(self):
        return 1


class Engine(Base):
    def run(self, data):
        value = self.helper()
        value += self.compute(data)
        return tidy(str(value))

    def compute(self, data):
        return util.total(data) + len(data)

    def broken(self):
        return self.cache.get("x")


def make_engine():
    engine = Engine()
    engine.run([1])
    return engine


def path_of(name):
    return os.path.join("a", name)
''',
    "pkg/util.py": '''def clean(text):
    return text.strip()


def total(items):
    return sum(items) + helper_missing(items)


def outer():
    def inner():
        return 1
    return inner()
''',
    "other/util.py": '''def clean(text):
    return text
''',
    "app.py": '''import pkg
from pkg import make_engine as build
from pkg.core import Engine
from util import clean
import json as js
import requests


def main():
    engine = build()
    engine.run([1, 2])
    other = Engine()
    pkg.core.path_of("x")
    js.dumps({})
    requests.get("http://x")
    clean("a")
    return other
''',
    "tests/test_engine.py": '''from pkg.core import Engine


def test_run():
    assert Engine().run([1]) == "1"
''',
}

R = "resolved"
X = ("external",)
U = ("unresolved",)

# (file, inside, name) -> what the call TRULY points at, decided by reading the code.
# ("resolved", file, symbol) | ("external",) | ("unresolved",) where unresolved means the target
# cannot be known from the code alone (a variable's method, an ambiguous module, a missing name).
LABELS = {
    ("pkg/core.py", "Engine.run", "self.helper"): (R, "pkg/core.py", "Base.helper"),
    ("pkg/core.py", "Engine.run", "self.compute"): (R, "pkg/core.py", "Engine.compute"),
    ("pkg/core.py", "Engine.run", "tidy"): (R, "pkg/util.py", "clean"),
    ("pkg/core.py", "Engine.run", "str"): X,
    ("pkg/core.py", "Engine.compute", "util.total"): (R, "pkg/util.py", "total"),
    ("pkg/core.py", "Engine.compute", "len"): X,
    ("pkg/core.py", "Engine.broken", "self.cache.get"): U,
    ("pkg/core.py", "make_engine", "Engine"): (R, "pkg/core.py", "Engine"),
    ("pkg/core.py", "make_engine", "engine.run"): U,
    ("pkg/core.py", "path_of", "os.path.join"): X,
    ("pkg/util.py", "clean", "text.strip"): U,
    ("pkg/util.py", "total", "sum"): X,
    ("pkg/util.py", "total", "helper_missing"): U,
    ("pkg/util.py", "outer", "inner"): (R, "pkg/util.py", "outer.inner"),
    ("app.py", "main", "build"): (R, "pkg/core.py", "make_engine"),
    ("app.py", "main", "engine.run"): U,
    ("app.py", "main", "Engine"): (R, "pkg/core.py", "Engine"),
    ("app.py", "main", "pkg.core.path_of"): (R, "pkg/core.py", "path_of"),
    ("app.py", "main", "js.dumps"): X,
    ("app.py", "main", "requests.get"): X,
    ("app.py", "main", "clean"): U,
    ("tests/test_engine.py", "test_run", "Engine"): (R, "pkg/core.py", "Engine"),
}


def repo_files(sources=None):
    out = []
    for path, text in (sources or REPO).items():
        item = facts.extract_file_facts(path, text.encode())
        out.append({k: item[k] for k in ("path", "symbols", "imports", "calls")})
    return out


def edges_of(sources):
    return build_edges(repo_files(sources))


def call(edges, path, scope, name):
    found = [e for e in edges if e["kind"] == "call" and (e["src_path"], e["src_symbol"], e["target_name"]) == (path, scope, name)]
    assert len(found) == 1, (path, scope, name, found)
    return found[0]


def got(edge):
    if edge["status"] == "resolved":
        return ("resolved", edge["dst_path"], edge["dst_symbol"])
    return (edge["status"],)


def test_every_call_is_labelled_and_none_is_resolved_wrongly():
    edges = build_edges(repo_files())
    calls = [e for e in edges if e["kind"] == "call"]
    keys = {(e["src_path"], e["src_symbol"], e["target_name"]) for e in calls}
    assert keys == set(LABELS), (keys ^ set(LABELS))

    correct = missed = wrong = 0
    problems = []
    for e in calls:
        truth = LABELS[(e["src_path"], e["src_symbol"], e["target_name"])]
        answer = got(e)
        if answer == truth:
            correct += 1
        elif answer == ("unresolved",) and truth[0] == "resolved":
            missed += 1  # honest: we said "don't know" about something that could be found
        else:
            wrong += 1
            problems.append((e["src_path"], e["src_symbol"], e["target_name"], answer, truth))
    resolvable = sum(1 for t in LABELS.values() if t[0] == "resolved")
    found = sum(1 for e in calls if e["status"] == "resolved")
    print(
        f"\ncall graph accuracy: {len(calls)} calls | correct {correct} | "
        f"missed (said unresolved) {missed} | WRONG {wrong} | "
        f"resolved {found} of {resolvable} resolvable"
    )
    assert wrong == 0, problems
    assert missed <= 1  # the one known gap: pkg.core.path_of through a package attribute


def test_known_gap_is_reported_unresolved_not_guessed():
    edge = call(build_edges(repo_files()), "app.py", "main", "pkg.core.path_of")
    assert edge["status"] == "unresolved"
    assert edge["dst_path"] is None


def test_inherited_method_resolves_to_the_base_class():
    edge = call(build_edges(repo_files()), "pkg/core.py", "Engine.run", "self.helper")
    assert (edge["dst_path"], edge["dst_symbol"]) == ("pkg/core.py", "Base.helper")


def test_ambiguous_module_name_is_not_guessed():
    # `from util import clean` could mean pkg/util.py or other/util.py
    edges = build_edges(repo_files())
    imp = [e for e in edges if e["kind"] == "import" and e["src_path"] == "app.py" and e["target_name"] == "util.clean"]
    assert imp[0]["status"] == "unresolved"


def test_imports_are_edges_too():
    edges = build_edges(repo_files())
    by_target = {e["target_name"]: e for e in edges if e["kind"] == "import" and e["src_path"] == "app.py"}
    assert by_target["pkg.core.Engine"]["dst_symbol"] == "Engine"
    assert by_target["json"]["status"] == "external"
    assert by_target["requests"]["status"] == "external"
    assert by_target["pkg.make_engine"]["status"] == "resolved"


def test_same_name_in_two_files_resolves_by_import_not_by_name():
    sources = {
        "a.py": "def go():\n    return 1\n",
        "b.py": "def go():\n    return 2\n",
        "main.py": "from b import go\n\n\ndef run():\n    return go()\n",
    }
    edge = call(edges_of(sources), "main.py", "run", "go")
    assert (edge["dst_path"], edge["dst_symbol"]) == ("b.py", "go")


def test_a_local_definition_beats_an_import_and_a_builtin():
    sources = {
        "m.py": "from x import len\n\n\ndef len():\n    return 0\n\n\ndef f():\n    return len()\n",
    }
    edge = call(edges_of(sources), "m.py", "f", "len")
    assert edge["status"] == "resolved" and edge["dst_symbol"] == "len"


def test_method_cannot_see_class_level_names_as_bare_names():
    sources = {
        "m.py": "class A:\n    def helper(self):\n        return 1\n\n    def go(self):\n        return helper()\n",
    }
    edge = call(edges_of(sources), "m.py", "A.go", "helper")
    assert edge["status"] == "unresolved"


def test_src_layout_and_relative_imports():
    sources = {
        "src/shop/__init__.py": "",
        "src/shop/cart.py": "def add(x):\n    return x\n",
        "src/shop/checkout.py": "from .cart import add\nfrom shop import cart\n\n\ndef go():\n    add(1)\n    return cart.add(2)\n",
    }
    edges = edges_of(sources)
    assert call(edges, "src/shop/checkout.py", "go", "add")["dst_path"] == "src/shop/cart.py"
    assert call(edges, "src/shop/checkout.py", "go", "cart.add")["dst_path"] == "src/shop/cart.py"


def test_same_local_name_bound_twice_is_not_guessed():
    sources = {
        "a.py": "def f():\n    return 1\n",
        "b.py": "def f():\n    return 2\n",
        "m.py": "try:\n    from a import f\nexcept ImportError:\n    from b import f\n\n\ndef g():\n    return f()\n",
    }
    assert call(edges_of(sources), "m.py", "g", "f")["status"] == "unresolved"


def test_import_cycles_do_not_hang():
    sources = {
        "a.py": "from b import x\n\n\ndef f():\n    return x()\n",
        "b.py": "from a import x\n",
    }
    assert call(edges_of(sources), "a.py", "f", "x")["status"] == "unresolved"


def test_broken_files_have_no_edges():
    sources = {"bad.py": "def f(:\n", "ok.py": "def g():\n    return len([])\n"}
    edges = edges_of(sources)
    assert [e["src_path"] for e in edges] == ["ok.py"]
