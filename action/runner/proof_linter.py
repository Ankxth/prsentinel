"""Checks an AI-written proof test BEFORE it is run. Standard library only.

The test comes from a model, so it is untrusted. It may only do one small thing: import the helper
and the function under test, and call that function inside `with command_sink():` with the marker
string in an argument. Anything else is rejected with a short reason that goes back to the AI.
The text it may write is limited to a few harmless words and shell separators, so even a command
that slips past the helper cannot do damage.
"""
import ast
import re

MAX_CHARS = 4000
MAX_LINES = 60
ALLOWED_WORDS = {"echo", "true", "ok", "test", "hello", "world", "foo", "bar", "abc"}
ALLOWED_CHARS = set(" \t\n;&|()$`'\"\\")
BANNED_NAMES = {
    "open", "exec", "eval", "compile", "__import__", "os", "sys", "socket", "subprocess",
    "importlib", "ctypes", "getattr", "setattr", "delattr", "globals", "locals", "vars", "input",
    "breakpoint", "shutil", "pathlib", "builtins", "pickle", "marshal", "signal", "threading",
    "multiprocessing", "urllib", "requests", "http",
}
BANNED_NODES = (ast.ClassDef, ast.Lambda, ast.While, ast.AsyncFunctionDef, ast.Global,
                ast.Nonlocal, ast.Await, ast.Yield, ast.YieldFrom, ast.AsyncFor, ast.AsyncWith)
_WORD = re.compile(r"[A-Za-z]+")


def _text_errors(value):
    errors = []
    for word in _WORD.findall(value):
        if word.lower() not in ALLOWED_WORDS:
            errors.append("the word %r is not allowed in text; use only: %s"
                          % (word[:20], ", ".join(sorted(ALLOWED_WORDS))))
    leftovers = {c for c in _WORD.sub("", value) if not c.isdigit() and c not in ALLOWED_CHARS}
    if leftovers:
        errors.append("these characters are not allowed in text: %s"
                      % " ".join(sorted(repr(c) for c in leftovers))[:60])
    return errors


def _is_import_of(node, wanted):
    return ast.dump(node) == ast.dump(wanted)


def lint(code, import_line):
    """A list of short reasons the test is rejected. Empty means it may be run."""
    if not isinstance(code, str) or not code.strip():
        return ["the test is empty"]
    if len(code) > MAX_CHARS or code.count("\n") + 1 > MAX_LINES:
        return ["the test is too long (keep it under %d lines)" % MAX_LINES]
    try:
        tree = ast.parse(code)
        wanted = ast.parse(import_line or "").body
    except (SyntaxError, ValueError) as err:
        return ["the test is not valid Python: %s" % str(err)[:80]]
    if len(wanted) != 1 or not isinstance(wanted[0], (ast.Import, ast.ImportFrom)):
        return ["the import line given is not usable"]

    errors = []
    for node in ast.walk(tree):
        if isinstance(node, BANNED_NODES):
            errors.append("%s is not allowed" % type(node).__name__)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            plain = isinstance(node, ast.Import) and [a.name for a in node.names] == ["pytest"] \
                and not node.names[0].asname
            helper = (isinstance(node, ast.ImportFrom) and node.module == "prs_harness"
                      and node.level == 0 and {a.name for a in node.names} <= {"CANARY", "command_sink"}
                      and not any(a.asname for a in node.names))
            if not (plain or helper or _is_import_of(node, wanted[0])):
                errors.append("only these imports are allowed: pytest, CANARY and command_sink "
                              "from prs_harness, and the import line you were given")
        elif isinstance(node, ast.Name) and (node.id in BANNED_NAMES or node.id.startswith("__")):
            errors.append("the name %r is not allowed" % node.id)
        elif isinstance(node, ast.Attribute) and (node.attr.startswith("__") or node.attr in BANNED_NAMES):
            errors.append("the attribute %r is not allowed" % node.attr)
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, bytes):
                errors.append("bytes are not allowed")
            elif isinstance(node.value, str):
                errors += _text_errors(node.value)

    tests = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]
    if not tests:
        errors.append("there must be a function whose name starts with test_")
    hit = False
    for test in tests:
        for node in ast.walk(test):
            if not isinstance(node, ast.With):
                continue
            sinks = [i for i in node.items if isinstance(i.context_expr, ast.Call)
                     and isinstance(i.context_expr.func, ast.Name)
                     and i.context_expr.func.id == "command_sink" and not i.context_expr.args]
            if not sinks:
                continue
            for inner in (n for stmt in node.body for n in ast.walk(stmt)):
                if isinstance(inner, ast.Call) and any(
                        isinstance(n, ast.Name) and n.id == "CANARY"
                        for a in list(inner.args) + [k.value for k in inner.keywords]
                        for n in ast.walk(a)):
                    hit = True
    if tests and not hit:
        errors.append("call the function inside `with command_sink():` with CANARY in an argument")
    seen, unique = set(), []
    for message in errors:
        if message not in seen:
            seen.add(message)
            unique.append(message)
    return unique[:10]
