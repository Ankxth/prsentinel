"""All of the PRSentinel settings live in this one file.

Two rules for this file:

1. The model names are written once, at the top. If we want to try a different
   model later we only change them here.
2. API keys are NEVER written in the code. They are read from environment
   variables instead, so they stay out of version control.
<<<<<<< HEAD

Keys can also be kept in a file called .env in the project root (the folder
that holds pyproject.toml). That file is listed in .gitignore, so it never goes
into version control either. It is read once, when this module is first
imported, and each line is copied into the environment. A variable that is
already set in the environment always wins over the file.
"""

import os
from pathlib import Path


# ---------------------------------------------------------------------------
# Reading the .env file.
# ---------------------------------------------------------------------------

# The project root is two folders above this file: src/prsentinel/config.py.
# The path is worked out from this file rather than from the folder the command
# was started in, so the .env file is found wherever PRSentinel is run from.
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def _load_env_file(path: Path = ENV_FILE) -> None:
    """Copy KEY=VALUE lines from a .env file into the environment.

    - Blank lines and lines starting with # are skipped.
    - An "export " in front of the name is allowed, so the same file can be
      used with "source .env" in bash.
    - Quotes around the value are removed: KEY="abc" and KEY='abc' give abc.
    - A name that is already in the environment is left alone, even when its
      value is empty. The demo app empties GEMINI_API_KEY on purpose before it
      starts a child process, and the file must not quietly put it back.
    - A missing file is not an error. The values are never printed.
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if name and name not in os.environ:
            os.environ[name] = value


_load_env_file()
=======
"""

import os
>>>>>>> 80b49b0dd20cd7b46f14979d88d42bdfb4990ac6


def _int_from_env(name: str, default: int) -> int:
    """Read a whole number from the environment, or use the default."""
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw)
    except ValueError:
        return default


def _float_from_env(name: str, default: float) -> float:
    """Read a decimal number from the environment, or use the default."""
    raw = os.environ.get(name, "").strip()
    try:
        return float(raw)
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Models. These are the only two places a model name is written down.
# ---------------------------------------------------------------------------

# The main model we use. It is tried first.
GROQ_MODEL = "openai/gpt-oss-120b"

# The backup model. It is only used if Groq fails or is rate limited.
GEMINI_MODEL = "gemini-3.5-flash"


# ---------------------------------------------------------------------------
# Retrying when a provider says "too many requests".
# ---------------------------------------------------------------------------

# How many extra tries we give a provider after a rate limit. So with this
# set to 3 we make 1 first try plus 3 retries, 4 calls at the very most.
MAX_RETRIES = _int_from_env("PRSENTINEL_MAX_RETRIES", 3)

# How long we wait before the first retry. Every wait after that is twice as
# long as the one before: 15, then 30, then 60 seconds.
WAIT_SECONDS = _int_from_env("PRSENTINEL_WAIT_SECONDS", 15)

# If the provider tells us in its message how long to wait, we use that number
# instead. This stops an odd or huge number from hanging the program.
MAX_WAIT_SECONDS = 120

# How many times we add the filter before giving up. If this function is
# called more than once we must not pile up copies of the same filter.
GEMINI_FILTERS_ADDED = 0


# ---------------------------------------------------------------------------
# Rerunning a test file to see if it is flaky.
# ---------------------------------------------------------------------------

# How many times we run a test file again to check whether it always gives the
# same answer. A test that passes once and fails once is flaky, so one run on
# its own is never enough to trust.
RERUN_TIMES = _int_from_env("PRSENTINEL_RERUN_TIMES", 5)


# ---------------------------------------------------------------------------
# Repairing a test the pipeline judged to be the wrong test.
# ---------------------------------------------------------------------------

# How many times we ask for the same test to be corrected before giving up. A
# test that still fails on the old code after this many tries is reported as
# unrepaired and left exactly as it was.
MAX_REPAIR_ATTEMPTS = _int_from_env("PRSENTINEL_MAX_REPAIR_ATTEMPTS", 2)

# The most repair calls one run may make altogether, across every test. One
# file can hold several wrong tests and every attempt is a call to the AI, so
# this stops a run with a lot of them from spending the whole day's allowance.
MAX_REPAIR_CALLS_PER_RUN = _int_from_env("PRSENTINEL_MAX_REPAIR_CALLS_PER_RUN",
                                         10)


# ---------------------------------------------------------------------------
# Measuring how many faults the tests catch (mutation testing).
# ---------------------------------------------------------------------------

# The most mutants we make for one function in a single run. A function with a
# lot of operators can offer far more mutants than we want to run, and every
# mutant is a whole pytest run, so we take the first N and say plainly when the
# cap cut the list.
MAX_MUTANTS_PER_FUNCTION = _int_from_env("PRSENTINEL_MAX_MUTANTS_PER_FUNCTION",
                                         30)

# How long one mutant may run before we give up on it. A mutant that hangs is
# stopped and counted on its own, never left running forever.
MUTATION_TIMEOUT_SECONDS = _int_from_env("PRSENTINEL_MUTATION_TIMEOUT_SECONDS",
                                         30)


# ---------------------------------------------------------------------------
# The demo web app (src/prsentinel/app.py)
# ---------------------------------------------------------------------------

# How long one run in the demo app may take before it is stopped. The app runs
# each job in a child process precisely so that this limit can actually stop
# something: a thread cannot be killed, so a run that hangs would sit there and
# hold the one-run-at-a-time lock for ever.
APP_RUN_TIMEOUT_SECONDS = _int_from_env("PRSENTINEL_APP_RUN_TIMEOUT_SECONDS", 300)

# How much pasted source the demo app will accept, in bytes. Pasted code is
# written to disk and then read by pytest, so there has to be a ceiling on how
# much of someone's file we hold. Comfortably bigger than any example here.
APP_MAX_PASTED_BYTES = _int_from_env("PRSENTINEL_APP_MAX_PASTED_BYTES", 60000)


# ---------------------------------------------------------------------------
<<<<<<< HEAD
# Checking a GitHub pull request or commit from the demo app
# ---------------------------------------------------------------------------

# The most changed .py files scored for one pasted link. The same number the
# GitHub workflow uses, so both pick the same files and the cost of one link
# stays bounded: every file is at least one AI call per changed function.
GITHUB_MAX_FILES = _int_from_env("PRSENTINEL_GITHUB_MAX_FILES", 5)

# How long one GitHub link run may take in the demo app before it is stopped.
# Longer than a single demo case, because a link can hold several files and
# every one of them is a fresh generation plus reruns plus mutation testing.
APP_GITHUB_RUN_TIMEOUT_SECONDS = _int_from_env(
    "PRSENTINEL_APP_GITHUB_RUN_TIMEOUT_SECONDS", 900)


# ---------------------------------------------------------------------------
=======
>>>>>>> 80b49b0dd20cd7b46f14979d88d42bdfb4990ac6
# How creative the AI is allowed to be.
# ---------------------------------------------------------------------------

# This is used ONLY when the AI is writing new tests, and nowhere else. A
# temperature of 0 asks for the most predictable answer we can get, which is
# what we want when the job is to write tests that agree with the code.
#
# It does NOT make the AI perfectly repeatable. Even at 0, a hosted model can
# give a slightly different answer each time, so running the generator twice
# can still produce two different test files.
#
# The failure classifier does not use this setting. It keeps whatever
# temperature the provider chooses, so classifying is not affected by it.
GENERATION_TEMPERATURE = _float_from_env("PRSENTINEL_TEMPERATURE", 0.0)


# ---------------------------------------------------------------------------
# API keys. These come from the environment, never from the code.
# ---------------------------------------------------------------------------


def get_groq_api_key() -> str:
    """Return the Groq API key, or an empty string if it is not set."""
    return os.environ.get("GROQ_API_KEY", "").strip()


def get_gemini_api_key() -> str:
    """Return the Gemini API key, or an empty string if it is not set."""
    return os.environ.get("GEMINI_API_KEY", "").strip()


<<<<<<< HEAD
def get_github_token() -> str:
    """Return the GitHub token, or an empty string if it is not set.

    Optional. Without it public repositories still work, at 60 GitHub API calls
    an hour. With it the limit is 5,000 and private repositories can be read.
    """
    return os.environ.get("GITHUB_TOKEN", "").strip()


=======
>>>>>>> 80b49b0dd20cd7b46f14979d88d42bdfb4990ac6
def has_groq_key() -> bool:
    """Return True if a Groq API key is available."""
    return bool(get_groq_api_key())


def has_gemini_key() -> bool:
    """Return True if a Gemini API key is available."""
<<<<<<< HEAD
    return bool(get_gemini_api_key())
=======
    return bool(get_gemini_api_key())
>>>>>>> 80b49b0dd20cd7b46f14979d88d42bdfb4990ac6
