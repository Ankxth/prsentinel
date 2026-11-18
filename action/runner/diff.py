"""Find which Python functions a commit changed. Standard library only.

The function-comparing logic comes from v1's diff_extractor (ast based): two versions of a
function count as the same when their code means the same thing, so comments and spacing alone
are not a change.
"""
import ast
import subprocess

# Git's well-known "empty folder" id. Diffing against it makes every file look new.
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
MAX_FILE_BYTES = 1_000_000


def git(*args, cwd=None, check=True):
    """Run a git command and return its text output."""
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if check and result.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), result.stderr.strip()))
    return result.stdout


def collect_functions(source):
    """Return {full_name: ast_node}. Methods are named like "Cart.add_item"."""
    tree = ast.parse(source)
    found = {}

    def walk(node, prefix=""):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                full = prefix + child.name
                found[full] = child
                walk(child, full + ".")
            elif isinstance(child, ast.ClassDef):
                walk(child, prefix + child.name + ".")

    walk(tree)
    return found


def compare_sources(before, after):
    """Compare two versions of one file. Returns [(name, 'added'|'modified'|'deleted')].

    `before` or `after` may be None (file did not exist). Raises SyntaxError for bad code.
    """
    old = collect_functions(before) if before is not None else {}
    new = collect_functions(after) if after is not None else {}
    changes = []
    for name in sorted(set(old) | set(new)):
        if name not in old:
            changes.append((name, "added"))
        elif name not in new:
            changes.append((name, "deleted"))
        elif ast.dump(old[name]) != ast.dump(new[name]):
            changes.append((name, "modified"))
    return changes


def _show(rev, path, cwd):
    """File content at a revision, or None if it does not exist there or is too big."""
    result = subprocess.run(
        ["git", "show", "%s:%s" % (rev, path)], cwd=cwd, capture_output=True,
    )
    if result.returncode != 0 or len(result.stdout) > MAX_FILE_BYTES:
        return None
    return result.stdout.decode("utf-8", errors="replace")


def changed_files(base, head="HEAD", cwd=None):
    """Return [(status, path)] for files that differ. status is A, M or D (renames split)."""
    out = git("diff", "--name-status", "--no-renames", base, head, cwd=cwd)
    files = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            files.append((parts[0][:1], parts[-1]))
    return files


def find_changes(base, head="HEAD", cwd=None):
    """Return (files_changed_count, functions, skipped).

    functions: [{"file", "name", "change_type"}]. skipped: [(path, reason)] for Python files
    that could not be read (for example a syntax error).
    """
    files = changed_files(base, head, cwd)
    functions, skipped = [], []
    for status, path in files:
        if not path.endswith(".py"):
            continue
        before = None if status == "A" else _show(base, path, cwd)
        after = None if status == "D" else _show(head, path, cwd)
        if status != "A" and before is None:
            skipped.append((path, "old version not readable"))
            continue
        if status != "D" and after is None:
            skipped.append((path, "new version not readable"))
            continue
        try:
            for name, change_type in compare_sources(before, after):
                functions.append({"file": path, "name": name, "change_type": change_type})
        except SyntaxError:
            skipped.append((path, "syntax error"))
    return len(files), functions, skipped
