"""Links between places in a repo's code (the call graph), built from stored facts (step C3).

Input is the per-file facts the runner uploaded (symbols, imports, calls). Output is a list of
edges. Nothing here runs or even reads source code: it is name matching, done carefully.

Every edge ends in one of three states:
  resolved    points at exactly one place in the repo: (dst_path, dst_symbol)
  external    a Python built-in, the standard library or an installed package
  unresolved  could not be pinned to one place. We never guess.

What is resolved (in the order Python itself would look a name up):
  1. names defined in the same file (functions, classes, nested functions, methods of the class)
  2. `self.method()` / `cls.method()`, including methods inherited from classes in the repo
  3. names brought in with `import` / `from ... import ...`, including relative imports,
     aliases, and names a package re-exports from its `__init__.py`
  4. Python built-ins (external)
Anything else, for example `obj.method()` where `obj` is a variable, is unresolved: from the
names alone we cannot know what type `obj` has.
"""

import builtins
import sys
from collections.abc import Iterable

BUILTINS = frozenset(dir(builtins))
STDLIB = frozenset(getattr(sys, "stdlib_module_names", ()))
MAX_HOPS = 6  # how far we follow re-exports and base classes before giving up

RESOLVED, EXTERNAL, UNRESOLVED = "resolved", "external", "unresolved"

# A target is (status, path, symbol)
_UNRESOLVED = (UNRESOLVED, None, None)
_EXTERNAL = (EXTERNAL, None, None)


def _module_names(path: str) -> tuple[list[str], list[str]]:
    """(exact dotted name, shorter suffixes) a file can be imported as."""
    parts = path[:-3].split("/") if path.endswith(".py") else path.split("/")
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        return [], []
    exact = ".".join(parts)
    suffixes = [".".join(parts[i:]) for i in range(1, len(parts))]
    return [exact], suffixes


class _Graph:
    def __init__(self, files: Iterable[dict]):
        self.files = {f["path"]: f for f in files}
        self.exact: dict[str, list[str]] = {}
        self.suffix: dict[str, list[str]] = {}
        for path in self.files:
            exact, suffixes = _module_names(path)
            for name in exact:
                self.exact.setdefault(name, []).append(path)
            for name in suffixes:
                self.suffix.setdefault(name, []).append(path)
        self.symbols = {
            path: {s["name"]: s for s in f.get("symbols") or []} for path, f in self.files.items()
        }
        # Per file: dotted module prefixes usable in calls, and from-imported names.
        self.modules: dict[str, dict[str, tuple]] = {}
        self.names: dict[str, dict[str, tuple]] = {}
        self.import_targets: dict[tuple, tuple] = {}
        for path in self.files:
            self._read_imports(path)

    # ------------------------------------------------------------------ modules and imports

    def find_module(self, dotted: str, exact_only: bool = False) -> tuple[str, str | None]:
        """("found", path) | ("ambiguous", None) | ("missing", None)."""
        hits = self.exact.get(dotted, [])
        if not hits and not exact_only:
            hits = self.suffix.get(dotted, [])
        if len(hits) == 1:
            return "found", hits[0]
        return ("ambiguous", None) if hits else ("missing", None)

    def _absolute(self, path: str, module: str, level: int) -> tuple[str, str]:
        """Find the module a (possibly relative) import names."""
        if level == 0:
            return self.find_module(module)
        package = path.split("/")[:-1]
        if level - 1 > len(package):
            return "missing", None
        base = package[: len(package) - (level - 1)]
        dotted = ".".join(base + ([module] if module else []))
        if not dotted:
            return "missing", None
        return self.find_module(dotted, exact_only=True)

    def _module_entry(self, found: tuple[str, str | None]):
        state, mod_path = found
        if state == "found":
            return ("module", mod_path)
        return ("module-ambiguous", None) if state == "ambiguous" else ("module-external", None)

    def _read_imports(self, path: str) -> None:
        modules: dict[str, tuple] = {}
        names: dict[str, tuple] = {}
        conflicts: set[str] = set()

        def bind(table: dict, key: str, entry: tuple) -> None:
            if key in table and table[key] != entry:
                conflicts.add(key)
            table[key] = entry

        for imp in self.files[path].get("imports") or []:
            module = imp.get("module") or ""
            name = imp.get("name")
            asname = imp.get("asname")
            level = imp.get("level") or 0
            label = "." * level + module
            if name is None:  # import a.b.c [as x]
                if asname:
                    bind(modules, asname, self._module_entry(self.find_module(module)))
                else:
                    parts = module.split(".")
                    for i in range(1, len(parts) + 1):
                        prefix = ".".join(parts[:i])
                        bind(modules, prefix, self._module_entry(self.find_module(prefix)))
                self.import_targets[(path, label, None)] = self._module_entry(self.find_module(module))
                continue
            local = asname or name
            state, mod_path = self._absolute(path, module, level)
            if state == "found":
                if name in self.symbols[mod_path] or self._binds(mod_path, name):
                    bind(names, local, ("name", mod_path, name))
                    target = ("name", mod_path, name)
                else:
                    sub = self._absolute(path, (module + "." if module else "") + name, level)
                    if sub[0] == "found":
                        bind(modules, local, ("module", sub[1]))
                        target = ("module", sub[1])
                    else:  # the module is ours but we cannot find the name in it
                        bind(names, local, ("name", mod_path, name))
                        target = ("name", mod_path, name)
            elif state == "ambiguous":
                bind(names, local, ("name-ambiguous", None, name))
                target = ("name-ambiguous", None, name)
            else:
                sub_state = self._absolute(path, (module + "." if module else "") + name, level)
                if sub_state[0] == "found":  # `from pkg import module` where pkg has no __init__
                    bind(modules, local, ("module", sub_state[1]))
                    target = ("module", sub_state[1])
                else:
                    bind(names, local, ("name-external", None, name))
                    target = ("name-external", None, name)
            self.import_targets[(path, label, name)] = target

        for key in conflicts:  # the same local name bound to two different things: do not guess
            modules.pop(key, None)
            names[key] = ("name-ambiguous", None, key)
        self.modules[path] = modules
        self.names[path] = names

    def _binds(self, path: str, name: str) -> bool:
        """Does file `path` itself import `name`? (a package re-exporting it)"""
        for imp in self.files[path].get("imports") or []:
            if imp.get("name") is not None and (imp.get("asname") or imp.get("name")) == name:
                return True
        return False

    # ------------------------------------------------------------------ resolving inside modules

    def _in_module(self, path: str, rest: list[str], hops: int = 0) -> tuple:
        """Resolve a dotted `rest` (for example ["Cart", "total"]) as seen from file `path`."""
        if hops > MAX_HOPS or not rest:
            return _UNRESOLVED
        dotted = ".".join(rest)
        if dotted in self.symbols[path]:
            return RESOLVED, path, dotted
        first = rest[0]
        entry = self.names.get(path, {}).get(first)
        if entry is not None:
            return self._from_entry(entry, rest[1:], hops + 1)
        entry = self.modules.get(path, {}).get(first)
        if entry is not None and len(rest) > 1:
            return self._from_entry(entry, rest[1:], hops + 1)
        if first in self.symbols[path] and self.symbols[path][first]["kind"] == "class":
            return self._in_class(path, first, ".".join(rest[1:]), hops + 1)
        return _UNRESOLVED

    def _from_entry(self, entry: tuple, rest: list[str], hops: int) -> tuple:
        kind = entry[0]
        if kind == "module":
            return self._in_module(entry[1], rest, hops) if rest else _UNRESOLVED
        if kind == "name":
            return self._in_module(entry[1], [entry[2]] + rest, hops)
        if kind in ("module-external", "name-external"):
            return _EXTERNAL
        return _UNRESOLVED

    def _in_class(self, path: str, cls: str, member: str, hops: int = 0) -> tuple:
        """Find `member` on class `cls` of file `path`, looking at base classes in the repo."""
        if hops > MAX_HOPS or not member:
            return _UNRESOLVED
        if f"{cls}.{member}" in self.symbols[path]:
            return RESOLVED, path, f"{cls}.{member}"
        for base in self.symbols[path][cls].get("bases") or []:
            target = self._name_in_file(path, base.split("."), cls, hops + 1)
            if target[0] == RESOLVED and self.symbols[target[1]][target[2]]["kind"] == "class":
                found = self._in_class(target[1], target[2], member, hops + 1)
                if found[0] == RESOLVED:
                    return found
        return _UNRESOLVED

    # ------------------------------------------------------------------ calls

    def _local(self, path: str, scope: str, first: str) -> str | None:
        """The symbol a bare name means when written inside `scope` of file `path`, if defined here."""
        symbols = self.symbols[path]
        parts = [] if scope == "<module>" else scope.split(".")
        for i in range(len(parts), -1, -1):
            if i < len(parts):
                enclosing = symbols.get(".".join(parts[:i])) if i else None
                if enclosing is not None and enclosing["kind"] == "class":
                    continue  # names defined in a class body are not visible inside its methods
            candidate = ".".join(parts[:i] + [first])
            if candidate in symbols:
                return candidate
        return None

    def _name_in_file(self, path: str, parts: list[str], scope: str, hops: int = 0) -> tuple:
        """Resolve a dotted name used in `scope` of file `path` (not self/cls)."""
        if hops > MAX_HOPS:
            return _UNRESOLVED
        first, rest = parts[0], parts[1:]
        local = self._local(path, scope, first)
        if local is not None:
            if not rest:
                return RESOLVED, path, local
            if self.symbols[path][local]["kind"] == "class":
                return self._in_class(path, local, ".".join(rest), hops + 1)
            return _UNRESOLVED
        # `import a.b` lets calls start with a.b: use the longest imported prefix
        modules = self.modules.get(path, {})
        for k in range(len(parts) - 1, 0, -1):
            entry = modules.get(".".join(parts[:k]))
            if entry is not None:
                return self._from_entry(entry, parts[k:], hops + 1)
        entry = self.names.get(path, {}).get(first)
        if entry is not None:
            return self._from_entry(entry, rest, hops + 1)
        entry = modules.get(first)
        if entry is not None:
            return self._from_entry(entry, rest, hops + 1) if rest else _UNRESOLVED
        if first in BUILTINS:
            return _EXTERNAL
        return _UNRESOLVED

    def resolve_call(self, path: str, scope: str, name: str) -> tuple:
        parts = name.split(".")
        if parts[0] in ("self", "cls"):
            if len(parts) != 2:
                return _UNRESOLVED
            owner = self._enclosing_class(path, scope)
            if owner is None:
                return _UNRESOLVED
            return self._in_class(path, owner, parts[1])
        return self._name_in_file(path, parts, scope)

    def _enclosing_class(self, path: str, scope: str) -> str | None:
        parts = [] if scope == "<module>" else scope.split(".")
        for i in range(len(parts) - 1, 0, -1):
            candidate = self.symbols[path].get(".".join(parts[:i]))
            if candidate is not None and candidate["kind"] == "class":
                return ".".join(parts[:i])
        return None

    def import_target(self, path: str, imp: dict) -> tuple:
        label = "." * (imp.get("level") or 0) + (imp.get("module") or "")
        entry = self.import_targets.get((path, label, imp.get("name")))
        if entry is None:
            return _UNRESOLVED
        kind = entry[0]
        if kind == "module":
            return RESOLVED, entry[1], None
        if kind == "name":
            target = self._in_module(entry[1], [entry[2]])
            if target[0] == RESOLVED:
                return target
            return RESOLVED, entry[1], None  # the file is ours; the name was not found in it
        if kind in ("module-external", "name-external"):
            return _EXTERNAL
        return _UNRESOLVED


def build_edges(files: Iterable[dict]) -> list[dict]:
    """Every import and call of every file as an edge. `files` items hold path, symbols, imports, calls."""
    files = list(files)
    graph = _Graph(files)
    edges: list[dict] = []
    seen: set[tuple] = set()

    def add(path, symbol, kind, target_name, line, target):
        status, dst_path, dst_symbol = target
        key = (path, symbol, kind, target_name)
        if key in seen:
            return
        seen.add(key)
        edges.append({
            "src_path": path, "src_symbol": symbol, "kind": kind,
            "target_name": target_name[:300], "dst_path": dst_path, "dst_symbol": dst_symbol,
            "resolved": status == RESOLVED, "status": status, "line": line,
        })

    for file in sorted(files, key=lambda f: f["path"]):
        path = file["path"]
        for imp in file.get("imports") or []:
            module = "." * (imp.get("level") or 0) + (imp.get("module") or "")
            target_name = module if imp.get("name") is None else (
                module + ("" if module.endswith(".") or not module else ".") + imp["name"])
            add(path, "<module>", "import", target_name, None, graph.import_target(path, imp))
        for call in file.get("calls") or []:
            target = graph.resolve_call(path, call["in"], call["name"])
            add(path, call["in"], "call", call["name"], call.get("line"), target)
    return edges
