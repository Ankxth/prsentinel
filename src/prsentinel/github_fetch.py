"""Fetch the old and new code of a GitHub pull request or commit.

The demo app accepts a link such as

    https://github.com/owner/repo/pull/12
    https://github.com/owner/repo/commit/1a2b3c4

and this module turns it into before/after file pairs that the pipeline can be
run on, one pair per changed Python file.

What counts as "before" and "after"
-----------------------------------
- A pull request: before is the base commit, after is the head commit. That is
  the same direction the pull request reads in, and the same direction the
  GitHub workflow uses.
- A commit: before is its first parent, after is the commit itself.

Which files are scored
----------------------
The same rules as .github/workflows/prsentinel.yml, so a link pasted into the
app and a pull request scored by the workflow pick the same files:

- only .py files
- nothing under tests/, because PRSentinel writes its own tests and has no
  business judging somebody else's
- nothing deleted, and nothing brand new: both need an old and a new version to
  compare
- at most config.GITHUB_MAX_FILES files, so one link cannot spend the day's AI
  allowance

Each file left out is listed with the reason, so the page can say what was not
looked at rather than leaving it to be guessed.

The one big limit
-----------------
PRSentinel tests each file on its own, in an empty folder. A file that imports
other modules from its own repository, or packages that are not installed here,
will not load, and its tests will come back as errors. That is a limit of the
tool, not of this module, and the page says so next to the link box.

Network and keys
----------------
Two calls go to api.github.com per link (the change itself and its file list).
The file contents come from raw.githubusercontent.com, which does not count
against the API's hourly limit. Without a login GitHub allows 60 API calls an
hour from one address, so about 30 links. A GITHUB_TOKEN in .env raises that to
5,000 and lets private repositories be read. The value is only ever put in a
request header. It is never printed, never written to a file, and never put in
an error message.
"""

import ast
import re
from urllib.parse import quote, urlparse

import httpx

from prsentinel import config

API_ROOT = "https://api.github.com"
RAW_ROOT = "https://raw.githubusercontent.com"

# Seconds to wait for GitHub before giving up on one request.
REQUEST_TIMEOUT_SECONDS = 20

# How the two kinds of link look once the host has been taken off.
_PULL_PATH = re.compile(
    r"^/(?P<owner>[A-Za-z0-9-]{1,39})/(?P<repo>[A-Za-z0-9._-]{1,100})"
    r"/pull/(?P<number>[0-9]{1,9})(?:/.*)?$")
_COMMIT_PATH = re.compile(
    r"^/(?P<owner>[A-Za-z0-9-]{1,39})/(?P<repo>[A-Za-z0-9._-]{1,100})"
    r"/commit/(?P<sha>[0-9a-fA-F]{7,40})(?:/.*)?$")

LINK_HELP = ("Paste a link to a pull request, like "
             "https://github.com/owner/repo/pull/12, or to a commit, like "
             "https://github.com/owner/repo/commit/1a2b3c4.")


class GitHubError(Exception):
    """Something went wrong that the page can say in one plain sentence."""


# ---------------------------------------------------------------------------
# Reading the link
# ---------------------------------------------------------------------------

def parse_link(text: str) -> dict:
    """Work out what a pasted link points at.

    Returns a dictionary with owner, repo and kind ("pull" or "commit"), plus
    number for a pull request or sha for a commit. Anything that is not a
    github.com pull request or commit link is refused with a sentence saying
    what is accepted.
    """
    link = str(text or "").strip()
    if not link:
        raise GitHubError("There is no link. " + LINK_HELP)

    if "://" not in link:
        link = "https://" + link

    parsed = urlparse(link)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or host not in ("github.com",
                                                              "www.github.com"):
        raise GitHubError("That is not a github.com link. " + LINK_HELP)

    path = parsed.path.rstrip("/")

    found = _PULL_PATH.match(path)
    if found:
        return {"owner": found["owner"], "repo": found["repo"],
                "kind": "pull", "number": int(found["number"])}

    found = _COMMIT_PATH.match(path)
    if found:
        return {"owner": found["owner"], "repo": found["repo"],
                "kind": "commit", "sha": found["sha"].lower()}

    raise GitHubError("That link is not a pull request or a commit. "
                      + LINK_HELP)


# ---------------------------------------------------------------------------
# Choosing the files
# ---------------------------------------------------------------------------

def choose_files(entries: list, limit: int = None) -> tuple:
    """Split GitHub's list of changed files into the ones to score and the rest.

    entries is the list GitHub returns, one dictionary per file, with at least
    "filename" and "status" (added, removed, modified, renamed, copied, changed
    or unchanged). Renamed files also carry "previous_filename".

    Returns (chosen, skipped). chosen is a list of {"path", "old_path"}.
    skipped is a list of {"path", "reason"}. Files that are not Python at all
    are left out of both, because nobody expects them to be scored.
    """
    if limit is None:
        limit = config.GITHUB_MAX_FILES

    chosen = []
    skipped = []

    for entry in entries:
        path = str(entry.get("filename") or "")
        status = str(entry.get("status") or "")
        if not path.endswith(".py"):
            continue

        if path.startswith("tests/"):
            skipped.append({"path": path,
                            "reason": "it is under tests/, and PRSentinel does "
                                      "not judge existing tests"})
            continue
        if status == "removed":
            skipped.append({"path": path,
                            "reason": "it was deleted, so there is no new "
                                      "version to test"})
            continue
        if status == "added":
            skipped.append({"path": path,
                            "reason": "it is a new file, so there is no old "
                                      "version to compare against"})
            continue
        if status == "renamed" and not entry.get("changes"):
            skipped.append({"path": path,
                            "reason": "it was only renamed, and no code in it "
                                      "changed"})
            continue

        if len(chosen) >= limit:
            skipped.append({"path": path,
                            "reason": f"only {limit} files are scored per "
                                      f"link, so the AI allowance stays "
                                      f"bounded"})
            continue

        old_path = str(entry.get("previous_filename") or path)
        chosen.append({"path": path, "old_path": old_path})

    return chosen, skipped


def check_pair(path: str, before: str, after: str) -> str:
    """A reason this pair cannot be scored, or an empty string if it can."""
    limit = config.APP_MAX_PASTED_BYTES
    for label, text in (("old", before), ("new", after)):
        if len(text.encode("utf-8")) > limit:
            return (f"the {label} version is larger than {limit // 1000} KB, "
                    f"which is more than the app will hold")
        try:
            ast.parse(text)
        except SyntaxError as error:
            return (f"the {label} version is not valid Python "
                    f"(line {error.lineno}: {error.msg})")
    if before == after:
        return "the old and new versions are the same"
    return ""


# ---------------------------------------------------------------------------
# Talking to GitHub
# ---------------------------------------------------------------------------

def _headers(token: str, raw: bool = False) -> dict:
    headers = {"User-Agent": "prsentinel-demo",
               "X-GitHub-Api-Version": "2022-11-28",
               "Accept": ("application/vnd.github.raw" if raw
                          else "application/vnd.github+json")}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _explain(answer: httpx.Response, what: str, has_token: bool) -> str:
    """One sentence for a GitHub answer that was not a success."""
    status = answer.status_code
    remaining = answer.headers.get("x-ratelimit-remaining")

    if status in (403, 429) and remaining == "0":
        if has_token:
            return ("GitHub's hourly request limit for your GITHUB_TOKEN is "
                    "used up. Wait a while and try again.")
        return ("GitHub's limit for requests without a login is used up (60 "
                "an hour). Wait a while, or put a GITHUB_TOKEN in .env to get "
                "5,000 an hour.")
    if status == 401:
        return ("GitHub refused the GITHUB_TOKEN in .env. Check that it is "
                "still valid.")
    if status == 404:
        hint = ("" if has_token else
                " If the repository is private, put a GITHUB_TOKEN that can "
                "read it in .env.")
        return f"GitHub could not find {what}. Check the link.{hint}"
    return f"GitHub answered {status} when asked for {what}."


def _get(client: httpx.Client, url: str, what: str, token: str,
         raw: bool = False) -> httpx.Response:
    try:
        answer = client.get(url, headers=_headers(token, raw=raw),
                            timeout=REQUEST_TIMEOUT_SECONDS)
    except httpx.HTTPError as error:
        raise GitHubError(f"Could not reach GitHub while asking for {what} "
                          f"({type(error).__name__}). Check the internet "
                          f"connection.") from None
    if answer.status_code != 200:
        raise GitHubError(_explain(answer, what, bool(token)))
    return answer


def _raw_file(client, full_name: str, sha: str, path: str, token: str) -> str:
    url = f"{RAW_ROOT}/{full_name}/{sha}/{quote(path, safe='/')}"
    answer = _get(client, url, f"{path} at {sha[:7]}", token, raw=True)
    return answer.content.decode("utf-8", errors="replace")


def fetch_change(link_text: str, token: str = None, client=None) -> dict:
    """Fetch a pull request or commit and return everything the app needs.

    The result is a dictionary:

        owner, repo, kind, url, title
        number            (pull requests only)
        base_sha, head_sha
        files    [{"path", "before", "after"}]   the pairs to score
        skipped  [{"path", "reason"}]            the files left out, and why

    Raises GitHubError with a plain sentence for anything that goes wrong.

    client is a parameter so the tests can hand in a fake that never touches
    the network.
    """
    link = parse_link(link_text)
    if token is None:
        token = config.get_github_token()

    owner, repo = link["owner"], link["repo"]
    full_name = f"{owner}/{repo}"

    own_client = client is None
    if own_client:
        client = httpx.Client(follow_redirects=True)

    try:
        if link["kind"] == "pull":
            number = link["number"]
            what = f"pull request #{number} in {full_name}"
            pull = _get(client, f"{API_ROOT}/repos/{full_name}/pulls/{number}",
                        what, token).json()

            base_sha = pull["base"]["sha"]
            head_sha = pull["head"]["sha"]
            base_repo = pull["base"]["repo"]["full_name"]
            # A pull request from a fork has its new code in the fork.
            head_repo = ((pull["head"].get("repo") or {}).get("full_name")
                         or base_repo)
            title = str(pull.get("title") or "")
            url = f"https://github.com/{full_name}/pull/{number}"

            entries = _get(client,
                           f"{API_ROOT}/repos/{full_name}/pulls/{number}/files"
                           f"?per_page=100", f"the files of {what}",
                           token).json()
        else:
            sha = link["sha"]
            what = f"commit {sha[:7]} in {full_name}"
            commit = _get(client, f"{API_ROOT}/repos/{full_name}/commits/{sha}",
                          what, token).json()

            parents = commit.get("parents") or []
            if not parents:
                raise GitHubError("That commit is the first one in its "
                                  "repository, so there is no older version "
                                  "to compare it with.")
            base_sha = parents[0]["sha"]
            head_sha = commit["sha"]
            base_repo = head_repo = full_name
            message = str((commit.get("commit") or {}).get("message") or "")
            title = message.splitlines()[0] if message else ""
            url = f"https://github.com/{full_name}/commit/{head_sha}"
            entries = commit.get("files") or []

        chosen, skipped = choose_files(entries)

        files = []
        for one in chosen:
            before = _raw_file(client, base_repo, base_sha, one["old_path"],
                               token)
            after = _raw_file(client, head_repo, head_sha, one["path"], token)
            problem = check_pair(one["path"], before, after)
            if problem:
                skipped.append({"path": one["path"], "reason": problem})
                continue
            files.append({"path": one["path"], "before": before,
                          "after": after})
    finally:
        if own_client:
            client.close()

    result = {
        "owner": owner,
        "repo": repo,
        "kind": link["kind"],
        "url": url,
        "title": title,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "files": files,
        "skipped": skipped,
    }
    if link["kind"] == "pull":
        result["number"] = link["number"]
    return result
