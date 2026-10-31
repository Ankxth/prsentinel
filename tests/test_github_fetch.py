"""Tests for github_fetch.py, the part that turns a GitHub link into file pairs.

None of these reach GitHub. Every answer comes from httpx.MockTransport, a fake
that hands back whatever the test says, so the suite keeps its rule of never
touching the internet.
"""

import json

import httpx
import pytest

from prsentinel import github_fetch as gh

OLD = 'def add(a, b):\n    return a + b\n'
NEW = 'def add(a, b):\n    return a - b\n'


# ---------------------------------------------------------------------------
# Reading the link
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("link", [
    "https://github.com/octo/demo/pull/12",
    "https://github.com/octo/demo/pull/12/",
    "https://github.com/octo/demo/pull/12/files",
    "https://github.com/octo/demo/pull/12/files?diff=split#top",
    "http://www.github.com/octo/demo/pull/12",
    "github.com/octo/demo/pull/12",
    "  https://github.com/octo/demo/pull/12  ",
])
def test_pull_request_links_are_understood(link):
    assert gh.parse_link(link) == {"owner": "octo", "repo": "demo",
                                   "kind": "pull", "number": 12}


@pytest.mark.parametrize("link,sha", [
    ("https://github.com/octo/demo/commit/1a2b3c4", "1a2b3c4"),
    ("https://github.com/octo/my.repo-2/commit/" + "A" * 40, "a" * 40),
])
def test_commit_links_are_understood(link, sha):
    found = gh.parse_link(link)
    assert found["kind"] == "commit"
    assert found["sha"] == sha


@pytest.mark.parametrize("link", [
    "",
    "   ",
    "https://gitlab.com/octo/demo/pull/12",
    "https://github.com.evil.example/octo/demo/pull/12",
    "https://github.com/octo/demo",
    "https://github.com/octo/demo/issues/12",
    "https://github.com/octo/demo/pull/abc",
    "https://github.com/octo/demo/commit/xyz",
    "https://github.com/octo/demo/commit/1a2b",
    "ftp://github.com/octo/demo/pull/12",
    "https://github.com/../etc/pull/1",
])
def test_anything_else_is_refused_with_a_sentence(link):
    with pytest.raises(gh.GitHubError) as caught:
        gh.parse_link(link)
    assert "github.com/owner/repo/pull/12" in str(caught.value)


# ---------------------------------------------------------------------------
# Choosing the files: the same rules as the GitHub workflow
# ---------------------------------------------------------------------------

def test_only_changed_python_files_outside_tests_are_chosen():
    entries = [
        {"filename": "app/core.py", "status": "modified"},
        {"filename": "README.md", "status": "modified"},
        {"filename": "tests/test_core.py", "status": "modified"},
        {"filename": "app/gone.py", "status": "removed"},
        {"filename": "app/new.py", "status": "added"},
        {"filename": "app/moved.py", "status": "renamed",
         "previous_filename": "app/old_place.py", "changes": 4},
        {"filename": "app/just_moved.py", "status": "renamed",
         "previous_filename": "app/x.py", "changes": 0},
    ]
    chosen, skipped = gh.choose_files(entries, limit=5)

    assert chosen == [
        {"path": "app/core.py", "old_path": "app/core.py"},
        {"path": "app/moved.py", "old_path": "app/old_place.py"},
    ]
    reasons = {one["path"]: one["reason"] for one in skipped}
    assert set(reasons) == {"tests/test_core.py", "app/gone.py", "app/new.py",
                            "app/just_moved.py"}
    assert "tests/" in reasons["tests/test_core.py"]
    assert "deleted" in reasons["app/gone.py"]
    assert "new file" in reasons["app/new.py"]
    assert "renamed" in reasons["app/just_moved.py"]
    # A file that is not Python is not mentioned at all.
    assert "README.md" not in reasons


def test_the_file_limit_is_kept_and_the_rest_are_listed():
    entries = [{"filename": f"m{i}.py", "status": "modified"} for i in range(7)]
    chosen, skipped = gh.choose_files(entries, limit=5)
    assert [one["path"] for one in chosen] == [f"m{i}.py" for i in range(5)]
    assert [one["path"] for one in skipped] == ["m5.py", "m6.py"]
    assert "only 5 files" in skipped[0]["reason"]


def test_the_default_limit_is_the_workflow_number():
    entries = [{"filename": f"m{i}.py", "status": "modified"} for i in range(9)]
    chosen, _ = gh.choose_files(entries)
    assert len(chosen) == gh.config.GITHUB_MAX_FILES == 5


def test_a_pair_that_cannot_be_scored_says_why():
    assert gh.check_pair("a.py", OLD, NEW) == ""
    assert "same" in gh.check_pair("a.py", OLD, OLD)
    assert "not valid Python" in gh.check_pair("a.py", OLD, "def (:\n")
    huge = "x = 1\n" * 20000
    assert "larger than" in gh.check_pair("a.py", huge, NEW)


# ---------------------------------------------------------------------------
# Talking to a fake GitHub
# ---------------------------------------------------------------------------

def fake_github(routes, seen=None):
    """A client whose every answer comes from the routes dictionary.

    routes maps a full URL to (status, body, headers). A body that is a dict or
    a list is sent as JSON, a string as plain text.
    """
    def handle(request):
        if seen is not None:
            seen.append(request)
        url = str(request.url)
        if url not in routes:
            return httpx.Response(404, json={"message": "Not Found"})
        status, body, headers = routes[url]
        if isinstance(body, (dict, list)):
            return httpx.Response(status, content=json.dumps(body).encode(),
                                  headers={"content-type": "application/json",
                                           **headers})
        return httpx.Response(status, content=body.encode("utf-8"),
                              headers=headers)

    return httpx.Client(transport=httpx.MockTransport(handle))


API = "https://api.github.com/repos/octo/demo"
RAW = "https://raw.githubusercontent.com"

PULL = {
    "title": "Tidy add()",
    "base": {"sha": "b" * 40, "repo": {"full_name": "octo/demo"}},
    "head": {"sha": "h" * 40, "repo": {"full_name": "octo/demo"}},
}


def pull_routes(files, extra=None):
    routes = {
        f"{API}/pulls/7": (200, PULL, {}),
        f"{API}/pulls/7/files?per_page=100": (200, files, {}),
    }
    routes.update(extra or {})
    return routes


def test_a_pull_request_becomes_before_and_after_pairs():
    files = [{"filename": "calc.py", "status": "modified"},
             {"filename": "notes.txt", "status": "modified"},
             {"filename": "tests/test_calc.py", "status": "modified"}]
    routes = pull_routes(files, {
        f"{RAW}/octo/demo/{'b' * 40}/calc.py": (200, OLD, {}),
        f"{RAW}/octo/demo/{'h' * 40}/calc.py": (200, NEW, {}),
    })

    change = gh.fetch_change("https://github.com/octo/demo/pull/7", token="",
                             client=fake_github(routes))

    assert change["kind"] == "pull"
    assert change["number"] == 7
    assert change["title"] == "Tidy add()"
    assert change["url"] == "https://github.com/octo/demo/pull/7"
    assert change["base_sha"] == "b" * 40
    assert change["head_sha"] == "h" * 40
    assert change["files"] == [{"path": "calc.py", "before": OLD, "after": NEW}]
    assert [one["path"] for one in change["skipped"]] == ["tests/test_calc.py"]


def test_a_pull_request_from_a_fork_reads_the_new_code_from_the_fork():
    pull = json.loads(json.dumps(PULL))
    pull["head"]["repo"]["full_name"] = "someone/demo"
    routes = {
        f"{API}/pulls/7": (200, pull, {}),
        f"{API}/pulls/7/files?per_page=100": (
            200, [{"filename": "calc.py", "status": "modified"}], {}),
        f"{RAW}/octo/demo/{'b' * 40}/calc.py": (200, OLD, {}),
        f"{RAW}/someone/demo/{'h' * 40}/calc.py": (200, NEW, {}),
    }
    change = gh.fetch_change("https://github.com/octo/demo/pull/7", token="",
                             client=fake_github(routes))
    assert change["files"][0]["after"] == NEW


def test_a_renamed_file_reads_its_old_code_from_its_old_name():
    files = [{"filename": "pkg/new_name.py", "status": "renamed",
              "previous_filename": "pkg/old_name.py", "changes": 2}]
    routes = pull_routes(files, {
        f"{RAW}/octo/demo/{'b' * 40}/pkg/old_name.py": (200, OLD, {}),
        f"{RAW}/octo/demo/{'h' * 40}/pkg/new_name.py": (200, NEW, {}),
    })
    change = gh.fetch_change("github.com/octo/demo/pull/7", token="",
                             client=fake_github(routes))
    assert change["files"][0]["path"] == "pkg/new_name.py"
    assert change["files"][0]["before"] == OLD


def test_a_commit_is_compared_with_its_first_parent():
    sha = "c" * 40
    routes = {
        f"{API}/commits/{'c' * 7}": (200, {
            "sha": sha,
            "parents": [{"sha": "p" * 40}],
            "commit": {"message": "Make add subtract\n\nLonger text."},
            "files": [{"filename": "calc.py", "status": "modified"}],
        }, {}),
        f"{RAW}/octo/demo/{'p' * 40}/calc.py": (200, OLD, {}),
        f"{RAW}/octo/demo/{sha}/calc.py": (200, NEW, {}),
    }
    change = gh.fetch_change("https://github.com/octo/demo/commit/ccccccc",
                             token="", client=fake_github(routes))
    assert change["kind"] == "commit"
    assert change["title"] == "Make add subtract"
    assert change["base_sha"] == "p" * 40
    assert change["head_sha"] == sha
    assert "number" not in change
    assert change["files"][0]["after"] == NEW


def test_a_first_commit_has_nothing_to_compare_with():
    routes = {f"{API}/commits/{'c' * 7}": (200, {
        "sha": "c" * 40, "parents": [], "files": []}, {})}
    with pytest.raises(gh.GitHubError) as caught:
        gh.fetch_change("https://github.com/octo/demo/commit/ccccccc",
                        token="", client=fake_github(routes))
    assert "first one" in str(caught.value)


def test_a_pair_that_is_not_python_is_listed_not_scored():
    files = [{"filename": "calc.py", "status": "modified"}]
    routes = pull_routes(files, {
        f"{RAW}/octo/demo/{'b' * 40}/calc.py": (200, OLD, {}),
        f"{RAW}/octo/demo/{'h' * 40}/calc.py": (200, "def (:\n", {}),
    })
    change = gh.fetch_change("https://github.com/octo/demo/pull/7", token="",
                             client=fake_github(routes))
    assert change["files"] == []
    assert "not valid Python" in change["skipped"][0]["reason"]


# ---------------------------------------------------------------------------
# When GitHub says no
# ---------------------------------------------------------------------------

def test_a_missing_pull_request_says_so_and_mentions_private_repos():
    with pytest.raises(gh.GitHubError) as caught:
        gh.fetch_change("https://github.com/octo/demo/pull/7", token="",
                        client=fake_github({}))
    said = str(caught.value)
    assert "could not find pull request #7" in said
    assert "private" in said


def test_the_hourly_limit_is_explained():
    routes = {f"{API}/pulls/7": (403, {"message": "rate limit"},
                                 {"x-ratelimit-remaining": "0"})}
    with pytest.raises(gh.GitHubError) as caught:
        gh.fetch_change("https://github.com/octo/demo/pull/7", token="",
                        client=fake_github(routes))
    assert "60 an hour" in str(caught.value)
    assert "GITHUB_TOKEN" in str(caught.value)


def test_a_refused_token_is_explained_without_showing_it():
    secret = "ghp_SECRETVALUE0001doNotPrint"
    routes = {f"{API}/pulls/7": (401, {"message": "Bad credentials"}, {})}
    with pytest.raises(gh.GitHubError) as caught:
        gh.fetch_change("https://github.com/octo/demo/pull/7", token=secret,
                        client=fake_github(routes))
    assert "refused" in str(caught.value)
    assert secret not in str(caught.value)


def test_github_being_unreachable_is_one_sentence():
    def broken(request):
        raise httpx.ConnectError("no route", request=request)

    client = httpx.Client(transport=httpx.MockTransport(broken))
    with pytest.raises(gh.GitHubError) as caught:
        gh.fetch_change("https://github.com/octo/demo/pull/7", token="",
                        client=client)
    assert "Could not reach GitHub" in str(caught.value)


# ---------------------------------------------------------------------------
# The token
# ---------------------------------------------------------------------------

def full_routes():
    return pull_routes([{"filename": "calc.py", "status": "modified"}], {
        f"{RAW}/octo/demo/{'b' * 40}/calc.py": (200, OLD, {}),
        f"{RAW}/octo/demo/{'h' * 40}/calc.py": (200, NEW, {}),
    })


def test_without_a_token_no_authorization_header_is_sent():
    seen = []
    gh.fetch_change("https://github.com/octo/demo/pull/7", token="",
                    client=fake_github(full_routes(), seen))
    assert seen
    assert all("authorization" not in request.headers for request in seen)


def test_with_a_token_every_request_carries_it_and_nothing_else_does():
    seen = []
    secret = "ghp_SECRETVALUE0002doNotPrint"
    change = gh.fetch_change("https://github.com/octo/demo/pull/7",
                             token=secret,
                             client=fake_github(full_routes(), seen))
    assert all(request.headers["authorization"] == f"Bearer {secret}"
               for request in seen)
    assert secret not in json.dumps(change)


def test_the_token_is_read_from_the_environment_when_not_given(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_FROMENV0003")
    seen = []
    gh.fetch_change("https://github.com/octo/demo/pull/7",
                    client=fake_github(full_routes(), seen))
    assert seen[0].headers["authorization"] == "Bearer ghp_FROMENV0003"


def test_only_two_calls_go_to_the_rate_limited_api():
    """The file contents come from raw.githubusercontent.com, which does not
    count against the 60 an hour, so a link costs two API calls however many
    files it has."""
    seen = []
    gh.fetch_change("https://github.com/octo/demo/pull/7", token="",
                    client=fake_github(full_routes(), seen))
    api_calls = [r for r in seen if r.url.host == "api.github.com"]
    assert len(api_calls) == 2
