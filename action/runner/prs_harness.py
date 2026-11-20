"""Helper for proof tests (feature E3). It is copied next to a generated test and imported by it.

It gives the test a harmless marker string (CANARY, different on every run) and a context manager,
command_sink(), inside which shell commands are NOT run: they are only recorded. When the block
ends, a result file says whether the marker reached a dangerous shell call, and the test fails
with CanaryReached if it did. The failure is raised on exit, so code under test that catches
`Exception` cannot hide it. Standard library only.
"""
import contextlib
import io
import json
import os
import subprocess

CANARY = os.environ.get("PRS_CANARY", "PRS_CANARY_NOT_SET")
_RESULT_FILE = os.environ.get("PRS_RESULT_FILE", "")


class CanaryReached(AssertionError):
    """The marker string reached a shell command that would have run it."""


def unquoted(text, needle):
    """True when `needle` appears in `text` outside single and double quotes (so a shell would
    treat it as part of the command, not as plain data)."""
    single = double = escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        if escaped:
            escaped = False
        elif char == "\\" and not single:
            escaped = True
        elif char == "'" and not double:
            single = not single
        elif char == '"' and not single:
            double = not double
        elif not single and not double and text.startswith(needle, index):
            return True
        index += 1
    return False


def _command_text(args, shell):
    if isinstance(args, (list, tuple)):
        # With shell=True on POSIX only the first item is the command; the rest are arguments.
        return str(args[0]) if (shell and args) else ""
    return str(args)


class _Sink:
    def __init__(self):
        self.calls = 0
        self.dangerous = False

    def record(self, command, shell):
        self.calls += 1
        if shell and unquoted(command, CANARY):
            self.dangerous = True


class _FakePopen:
    def __init__(self, sink, args, **kwargs):
        self.args = args
        self.pid = 0
        self.returncode = 0
        text = bool(kwargs.get("text") or kwargs.get("universal_newlines") or kwargs.get("encoding"))
        self._empty = "" if text else b""
        self.stdin = self.stdout = self.stderr = None
        if kwargs.get("stdout") == subprocess.PIPE:
            self.stdout = io.StringIO("") if text else io.BytesIO(b"")
        if kwargs.get("stderr") == subprocess.PIPE:
            self.stderr = io.StringIO("") if text else io.BytesIO(b"")
        shell = bool(kwargs.get("shell"))
        sink.record(_command_text(args, shell), shell)

    def communicate(self, input=None, timeout=None):
        return self._empty, self._empty

    def wait(self, timeout=None):
        return 0

    def poll(self):
        return 0

    def kill(self):
        pass

    terminate = kill

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@contextlib.contextmanager
def command_sink():
    sink = _Sink()
    saved = (subprocess.Popen, os.system, os.popen)

    def popen(args, *more, **kwargs):
        return _FakePopen(sink, args, **kwargs)

    def system(command):
        sink.record(str(command), True)
        return 0

    def os_popen(command, *more, **kwargs):
        sink.record(str(command), True)
        return io.StringIO("")

    subprocess.Popen, os.system, os.popen = popen, system, os_popen
    try:
        yield sink
    finally:
        subprocess.Popen, os.system, os.popen = saved
        if _RESULT_FILE:
            with open(_RESULT_FILE, "w", encoding="utf-8") as handle:
                json.dump({"calls": sink.calls, "dangerous": sink.dangerous}, handle)
    if sink.dangerous:
        raise CanaryReached("the marker string reached a shell command")
