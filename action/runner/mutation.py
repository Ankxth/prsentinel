"""Mutation check (feature D6): do the generated tests catch small deliberate faults?

Ported from v1 `mutation.py`. A "mutant" is a copy of the changed function with ONE small fault
(+ to -, < to <=, True to False, ...). If a test that passes on the real old code fails on the
mutant, the mutant is "killed". If every test still passes, it "survived".

    score = killed / (killed + survived)

The score is a lower bound: some faults change nothing ("equivalent" mutants), so no test could
ever catch them. The comment says so next to the number.

Safety and fairness, in plain words:
* Only the changed function is mutated, in the OLD version of the file, inside the throwaway
  checkout of the old commit (never in the user's working tree). The file is put back afterwards.
* Only tests that PASS on the plain, un-mutated old code count. A test that already fails there
  cannot show anything.
* A mutant that cannot run or times out is counted on its own and kept out of the score.
* The tests run through the same allow-listed child environment as every other test run.
Standard library only (needs Python 3.9+ for `ast.unparse`).
"""
import ast
import copy
import os
import time

import pytest_runner as pr

MAX_MUTANTS = 20
MUTANT_TIMEOUT = 30
TIME_BUDGET_SECONDS = 90

KIND_LABELS = {
    "add_to_sub": "+ to -",
    "sub_to_add": "- to +",
    "lt_to_le": "< to <=",
    "le_to_lt": "<= to <",
    "gt_to_ge": "> to >=",
    "ge_to_gt": ">= to >",
    "true_to_false": "True to False",
    "false_to_true": "False to True",
    "eq_to_noteq": "== to !=",
    "noteq_to_eq": "!= to ==",
    "is_none_to_is_not_none": "is None to is not None",
    "is_not_none_to_is_none": "is not None to is None",
    "and_to_or": "and to or",
    "or_to_and": "or to and",
    "int_to_int_plus_one": "n to n + 1",
    "default_none_to_empty_list": "None default to []",
    "default_empty_list_to_none": "[] default to None",
    "return_to_none": "return expr to return None",
}

COMPARE_SWAPS = {
    "lt_to_le": ast.LtE, "le_to_lt": ast.Lt, "gt_to_ge": ast.GtE, "ge_to_gt": ast.Gt,
    "eq_to_noteq": ast.NotEq, "noteq_to_eq": ast.Eq,
    "is_none_to_is_not_none": ast.IsNot, "is_not_none_to_is_none": ast.Is,
}


def find_function(tree, dotted_name):
    """One function in a parsed module ("Cart.add" is the method add in class Cart), or None."""
    node = tree
    for part in dotted_name.split("."):
        found = None
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                    and child.name == part:
                found = child
                break
        if found is None:
            return None
        node = found
    return node if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else None


def _is_none(node):
    return isinstance(node, ast.Constant) and node.value is None


def _is_empty_list(node):
    return isinstance(node, ast.List) and not node.elts


def _comparison_kind(op, right):
    table = ((ast.Lt, "lt_to_le"), (ast.LtE, "le_to_lt"), (ast.Gt, "gt_to_ge"),
             (ast.GtE, "ge_to_gt"), (ast.Eq, "eq_to_noteq"), (ast.NotEq, "noteq_to_eq"))
    for cls, kind in table:
        if isinstance(op, cls):
            return kind
    if isinstance(op, ast.Is) and _is_none(right):
        return "is_none_to_is_not_none"
    if isinstance(op, ast.IsNot) and _is_none(right):
        return "is_not_none_to_is_none"
    return None


def find_mutation_points(function_node):
    """The faults we can make in one function, in a fixed order: [(kind, target), ...].

    Only the function's OWN parameter defaults are changed (not a nested function's).
    """
    points = []
    for field in ("defaults", "kw_defaults"):
        for index, default in enumerate(getattr(function_node.args, field)):
            if default is None:
                continue
            if _is_none(default):
                points.append(("default_none_to_empty_list", (function_node.args, field, index)))
            elif _is_empty_list(default):
                points.append(("default_empty_list_to_none", (function_node.args, field, index)))
    for node in ast.walk(function_node):
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.Add):
                points.append(("add_to_sub", node))
            elif isinstance(node.op, ast.Sub):
                points.append(("sub_to_add", node))
        elif isinstance(node, ast.BoolOp):
            points.append(("and_to_or" if isinstance(node.op, ast.And) else "or_to_and", node))
        elif isinstance(node, ast.Compare):
            for index, op in enumerate(node.ops):
                kind = _comparison_kind(op, node.comparators[index])
                if kind:
                    points.append((kind, (node, index)))
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                points.append(("true_to_false" if node.value else "false_to_true", node))
            elif type(node.value) is int:
                points.append(("int_to_int_plus_one", node))
        elif isinstance(node, ast.Return):
            if node.value is not None and not _is_none(node.value):
                points.append(("return_to_none", node))
    return points


def apply_mutation(target, kind):
    """Change one node in place to make one small fault."""
    if kind == "add_to_sub":
        target.op = ast.Sub()
    elif kind == "sub_to_add":
        target.op = ast.Add()
    elif kind == "and_to_or":
        target.op = ast.Or()
    elif kind == "or_to_and":
        target.op = ast.And()
    elif kind in COMPARE_SWAPS:
        node, index = target
        node.ops[index] = COMPARE_SWAPS[kind]()
    elif kind == "true_to_false":
        target.value = False
    elif kind == "false_to_true":
        target.value = True
    elif kind == "int_to_int_plus_one":
        target.value = target.value + 1
    elif kind == "default_none_to_empty_list":
        arguments, field, index = target
        getattr(arguments, field)[index] = ast.List(elts=[], ctx=ast.Load())
    elif kind == "default_empty_list_to_none":
        arguments, field, index = target
        getattr(arguments, field)[index] = ast.Constant(value=None)
    elif kind == "return_to_none":
        target.value = ast.Constant(value=None)


def build_mutants(code, function_name, limit=MAX_MUTANTS):
    """Mutants of one function inside `code`: {"found", "capped", "mutants": [{operator, source,
    identical}]}. The same function always gives the same list."""
    tree = ast.parse(code)
    function_node = find_function(tree, function_name)
    if function_node is None:
        return {"found": 0, "capped": False, "mutants": []}
    points = find_mutation_points(function_node)
    original = ast.dump(function_node)
    mutants = []
    for index, (kind, _) in enumerate(points[:limit]):
        copied = copy.deepcopy(tree)
        function_copy = find_function(copied, function_name)
        apply_mutation(find_mutation_points(function_copy)[index][1], kind)
        mutants.append({"operator": KIND_LABELS[kind], "source": ast.unparse(copied),
                        "identical": ast.dump(function_copy) == original})
    return {"found": len(points), "capped": len(points) > limit, "mutants": mutants}


def _empty(reason, **extra):
    base = {"status": "not_measured", "reason": reason[:300], "found": 0, "capped": False,
            "killed": 0, "survived": 0, "equivalent": 0, "timed_out": 0, "could_not_run": 0,
            "survivors": [], "stopped_early": False}
    base.update(extra)
    return base


def run_mutation(old_root, path, name, code, file_name, timeout=MUTANT_TIMEOUT,
                 budget=TIME_BUDGET_SECONDS, limit=MAX_MUTANTS, clock=time.monotonic):
    """Run the test file `code` against every mutant of function `name` in `old_root/path`.

    Returns a dict with status "measured" or "not_measured" (with a plain reason) and the counts.
    The original file is always put back.
    """
    if not hasattr(ast, "unparse"):
        return _empty("the mutation check needs Python 3.9 or newer")
    full = os.path.join(old_root, path)
    try:
        with open(full, "rb") as handle:
            original = handle.read()
        text = original.decode("utf-8")
        built = build_mutants(text, name, limit)
    except (OSError, UnicodeDecodeError, SyntaxError, ValueError) as err:
        return _empty("the old file could not be read for mutation: %s" % err)
    if not built["found"]:
        return _empty("no small faults could be made in this function")

    started = clock()
    result = _empty("", found=built["found"], capped=built["capped"])
    result["status"], result["reason"] = "measured", ""
    try:
        # The plain rebuild, unchanged, gives the starting point: which tests pass on it.
        with open(full, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(ast.unparse(ast.parse(text)))
        base = pr.run_in_tree(old_root, code, file_name, timeout=timeout)
        if base["status"] != pr.OK:
            return _empty("the tests could not run on the old code: %s" % base["error"],
                          found=built["found"])
        green = {n for n, r in base["tests"].items() if r == pr.PASSED}
        if not green:
            return _empty("no generated test passes on the old code, so there is nothing to measure",
                          found=built["found"])
        for mutant in built["mutants"]:
            if mutant["identical"]:
                result["equivalent"] += 1
                continue
            if clock() - started > budget:
                result["stopped_early"] = True
                break
            with open(full, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(mutant["source"])
            run = pr.run_in_tree(old_root, code, file_name, timeout=timeout)
            if run["status"] == pr.TIMEOUT:
                result["timed_out"] += 1
            elif run["status"] != pr.OK:
                result["could_not_run"] += 1
            elif any(run["tests"].get(n, pr.ERROR) in (pr.FAILED, pr.ERROR) for n in green):
                result["killed"] += 1
            else:
                result["survived"] += 1
                if len(result["survivors"]) < 10:
                    result["survivors"].append(mutant["operator"])
        if result["killed"] + result["survived"] == 0:
            result["status"] = "not_measured"
            result["reason"] = "no mutant could be scored for this function"
        return result
    finally:
        try:
            with open(full, "wb") as handle:
                handle.write(original)
        except OSError:
            pass
