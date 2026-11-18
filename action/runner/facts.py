"""Context facts (feature C, step C1). Standard library only.

Reads the Python files of a checked-out repository and produces compact "facts" about them:
fingerprints (hashes), symbols, imports and call names. No function bodies are included, so
very little source ever leaves the user's runner.

Shape of the facts for one file (all keys always present):

    {
      "path": "cart.py",
      "hash": "<sha256 hex of the file bytes>",
      "is_test": false,
      "parse_error": null,                     # or "syntax error (line 3)"; then the lists are empty
      "symbols": [{"name": "Cart.add", "kind": "function"|"class", "signature": "(self, item)",
                   "doc": "first docstring line", "lines": [start, end],
                   "decorators": ["staticmethod"], "bases": ["Base"], "is_fixture": false}],
      "imports": [{"module": "cart", "name": "add_item", "asname": null, "level": 0}],
      "calls":   [{"in": "buy", "name": "cart.add_item", "line": 4}],
      "dynamic_calls": 2                       # calls whose target cannot be named (obj().x(), f()())
    }

Symbol names are qualified: methods are "Class.method", nested functions "outer.inner".
"""
import ast
import hashlib
import os
import subprocess

MAX_FILES = 500
MAX_FILE_BYTES = 1_000_000
SKIP_DIRS = {".git", "venv", ".venv", "node_modules", "site-packages", "__pycache__", ".tox"}
MAX_DOC = 200
MAX_SIGNATURE = 300


def hash_bytes(data):
    return hashlib.sha256(data).hexdigest()


def is_test_path(path):
    parts = path.split("/")
    name = parts[-1]
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
        or any(part in ("tests", "test") for part in parts[:-1])
    )


def _dotted(node):
    """a.b.c for a chain of names and attributes, or None for anything else."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _first_line(text):
    if not text:
        return ""
    for line in text.strip().splitlines():
        if line.strip():
            return line.strip()[:MAX_DOC]
    return ""


def _decorator_name(node):
    target = node.func if isinstance(node, ast.Call) else node
    return _dotted(target)


def _signature(node):
    text = "(" + ast.unparse(node.args) + ")"
    if node.returns is not None:
        text += " -> " + ast.unparse(node.returns)
    return text[:MAX_SIGNATURE]


class _Collector(ast.NodeVisitor):
    def __init__(self):
        self.symbols = []
        self.imports = []
        self.calls = []
        self.dynamic_calls = 0
        self.scope = []
        self._seen_calls = set()

    def _qualified(self, name):
        return ".".join(self.scope + [name])

    def _here(self):
        return ".".join(self.scope) if self.scope else "<module>"

    def _visit_outer(self, node):
        """Decorators, defaults and base classes are evaluated in the OUTER scope."""
        for decorator in node.decorator_list:
            self.visit(decorator)
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                self.visit(base)
            for keyword in node.keywords:
                self.visit(keyword.value)
        else:
            for default in node.args.defaults + [d for d in node.args.kw_defaults if d is not None]:
                self.visit(default)

    def _start_line(self, node):
        if node.decorator_list:
            return min(d.lineno for d in node.decorator_list)
        return node.lineno

    def visit_FunctionDef(self, node):
        self._visit_outer(node)
        decorators = [n for n in (_decorator_name(d) for d in node.decorator_list) if n]
        self.symbols.append({
            "name": self._qualified(node.name),
            "kind": "function",
            "signature": _signature(node),
            "doc": _first_line(ast.get_docstring(node)),
            "lines": [self._start_line(node), node.end_lineno],
            "decorators": decorators,
            "bases": [],
            "is_fixture": any(d.split(".")[-1] == "fixture" for d in decorators),
        })
        self.scope.append(node.name)
        for statement in node.body:
            self.visit(statement)
        self.scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self._visit_outer(node)
        decorators = [n for n in (_decorator_name(d) for d in node.decorator_list) if n]
        self.symbols.append({
            "name": self._qualified(node.name),
            "kind": "class",
            "signature": "",
            "doc": _first_line(ast.get_docstring(node)),
            "lines": [self._start_line(node), node.end_lineno],
            "decorators": decorators,
            "bases": [n for n in (_dotted(b) for b in node.bases) if n],
            "is_fixture": False,
        })
        self.scope.append(node.name)
        for statement in node.body:
            self.visit(statement)
        self.scope.pop()

    def visit_Import(self, node):
        for alias in node.names:
            self.imports.append({"module": alias.name, "name": None,
                                 "asname": alias.asname, "level": 0})

    def visit_ImportFrom(self, node):
        for alias in node.names:
            self.imports.append({"module": node.module or "", "name": alias.name,
                                 "asname": alias.asname, "level": node.level or 0})

    def visit_Call(self, node):
        name = _dotted(node.func)
        if name is None:
            self.dynamic_calls += 1
        else:
            key = (self._here(), name)
            if key not in self._seen_calls:
                self._seen_calls.add(key)
                self.calls.append({"in": key[0], "name": name, "line": node.lineno})
        self.generic_visit(node)


def extract_file_facts(path, data):
    """Facts for one file. `data` is the raw bytes. Never raises for bad Python."""
    facts = {
        "path": path,
        "hash": hash_bytes(data),
        "is_test": is_test_path(path),
        "parse_error": None,
        "symbols": [],
        "imports": [],
        "calls": [],
        "dynamic_calls": 0,
    }
    try:
        tree = ast.parse(data.decode("utf-8", errors="replace"), filename=path)
        collector = _Collector()
        collector.visit(tree)
    except SyntaxError as err:
        facts["parse_error"] = "syntax error (line %s)" % (err.lineno or "?")
        return facts
    except (ValueError, RecursionError) as err:
        facts["parse_error"] = "could not parse (%s)" % type(err).__name__
        return facts
    facts["symbols"] = collector.symbols
    facts["imports"] = collector.imports
    facts["calls"] = collector.calls
    facts["dynamic_calls"] = collector.dynamic_calls
    return facts


# ----------------------------------------------------------------- whole repository

def _list_python_files(root):
    """Tracked .py files (sorted, forward slashes). Falls back to walking the folder."""
    result = subprocess.run(["git", "ls-files", "-z", "--", "*.py"], cwd=root,
                            capture_output=True)
    if result.returncode == 0:
        paths = [p for p in result.stdout.decode("utf-8", errors="replace").split("\0") if p]
    else:
        paths = []
        for folder, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in files:
                if name.endswith(".py"):
                    full = os.path.join(folder, name)
                    paths.append(os.path.relpath(full, root).replace(os.sep, "/"))
    paths = [p for p in paths if not (set(p.split("/")[:-1]) & SKIP_DIRS)]
    return sorted(set(paths))


class Scan:
    """Result of looking at a repository: fingerprints plus an honest account of what was left out."""

    def __init__(self, manifest, skipped, total_files):
        self.manifest = manifest          # {path: hash} for every file we will describe
        self.skipped = skipped            # [(path, reason)] left out on purpose
        self.total_files = total_files    # tracked .py files seen, before limits
        self.partial = bool(skipped)      # True when anything was left out


def scan_repo(root, max_files=MAX_FILES, max_file_bytes=MAX_FILE_BYTES):
    paths = _list_python_files(root)
    manifest, skipped = {}, []
    for path in paths:
        full = os.path.join(root, path)
        if len(manifest) >= max_files:
            skipped.append((path, "file limit"))
            continue
        try:
            if os.path.getsize(full) > max_file_bytes:
                skipped.append((path, "file too large"))
                continue
            with open(full, "rb") as handle:
                manifest[path] = hash_bytes(handle.read())
        except OSError:
            skipped.append((path, "not readable"))
    return Scan(manifest, skipped, len(paths))


def collect_facts(root, paths):
    """Facts for the given paths (the ones the server asked for)."""
    facts = []
    for path in paths:
        try:
            with open(os.path.join(root, path), "rb") as handle:
                facts.append(extract_file_facts(path, handle.read()))
        except OSError:
            continue
    return facts


def _read_small(root, name, limit=200_000):
    try:
        with open(os.path.join(root, name), "rb") as handle:
            return handle.read(limit).decode("utf-8", errors="replace")
    except OSError:
        return ""


def repo_facts(root, facts_list, scan):
    """A few repository-wide facts. Based on the facts we hold plus config files."""
    imports = {i["module"].split(".")[0] for f in facts_list for i in f["imports"]}
    has_conftest = any(p.split("/")[-1] == "conftest.py" for p in scan.manifest)
    pytest_config = (
        os.path.exists(os.path.join(root, "pytest.ini"))
        or "pytest" in _read_small(root, "pyproject.toml")
        or "[tool:pytest]" in _read_small(root, "setup.cfg")
        or "[pytest]" in _read_small(root, "tox.ini")
    )
    if "pytest" in imports or has_conftest or pytest_config:
        framework = "pytest"
    elif "unittest" in imports:
        framework = "unittest"
    else:
        framework = "unknown"
    return {
        "test_framework": framework,
        "has_conftest": has_conftest,
        "python_files": len(scan.manifest),
        "test_files": sum(1 for p in scan.manifest if is_test_path(p)),
        "partial": scan.partial,
        "skipped_files": len(scan.skipped),
    }


def config_hints(root):
    """Hints only the runner can know (the server never sees config files)."""
    return {
        "pytest_config": bool(
            os.path.exists(os.path.join(root, "pytest.ini"))
            or "pytest" in _read_small(root, "pyproject.toml")
            or "[tool:pytest]" in _read_small(root, "setup.cfg")
            or "[pytest]" in _read_small(root, "tox.ini")
        )
    }
