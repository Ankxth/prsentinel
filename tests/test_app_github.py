"""Tests for the GitHub link mode of the demo web app.

The fetch is faked and so is the child process, so nothing here reaches GitHub
or an AI. The one test that runs the child's code does it in this process,
with run_pipeline replaced, because a real fresh run would need a Groq call.
"""

import json
import subprocess
import threading
import time

import pytest
from fastapi.testclient import TestClient

from prsentinel import app as A
from prsentinel import github_fetch as gh

OLD = 'def add(a, b):\n    return a + b\n'
NEW = 'def add(a, b):\n    return a - b\n'


# ---------------------------------------------------------------------------
# Fixtures: a fake child, a sandbox, a fake fetch
# ---------------------------------------------------------------------------

class _SilentPipe:
    def __init__(self, done):
        self._done = done

    def __iter__(self):
        self._done.wait()
        return iter(())


class FakeChild:
    def __init__(self):
        self._done = threading.Event()

    @property
    def stdout(self):
        return _SilentPipe(self._done)

    def wait(self, timeout=None):
        if not self._done.wait(min(0.4, timeout or 0.4)):
            raise subprocess.TimeoutExpired("fake", timeout)
        return 0

    def kill(self):
        self._done.set()

    def release(self):
        self._done.set()


def wait_until_free(seconds=5.0):
    limit = time.time() + seconds
    while time.time() < limit:
        if not A.RUNNER.busy():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def client():
    with TestClient(A.app) as made:
        yield made


@pytest.fixture
def children(monkeypatch):
    made = []

    def spawn(*args, **kwargs):
        child = FakeChild()
        child.env = kwargs.get("env") or {}
        made.append(child)
        return child

    monkeypatch.setattr(A.subprocess, "Popen", spawn)
    yield made
    for child in made:
        child.release()
    assert wait_until_free()


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "GENERATED", tmp_path / "generated")
    monkeypatch.setattr(A, "SCRATCH", tmp_path / "scratch")
    monkeypatch.setattr(A, "REPORTS", tmp_path / "reports")
    monkeypatch.setenv("GROQ_API_KEY", "a-groq-key-for-the-test")
    return tmp_path


def a_change(files=None, skipped=None):
    return {
        "owner": "octo", "repo": "demo", "kind": "pull", "number": 7,
        "url": "https://github.com/octo/demo/pull/7", "title": "Tidy add()",
        "base_sha": "b" * 40, "head_sha": "h" * 40,
        "files": files if files is not None else [
            {"path": "calc.py", "before": OLD, "after": NEW},
            {"path": "pkg/more.py", "before": OLD, "after": NEW},
        ],
        "skipped": skipped or [],
    }


@pytest.fixture
def fetched(monkeypatch):
    """Replace the fetch with one that hands back a fixed change."""
    asked = []
    change = {"value": a_change()}

    def fake_fetch(link, token=None, client=None):
        asked.append(link)
        return change["value"]

    monkeypatch.setattr(A.gh, "fetch_change", fake_fetch)
    return {"asked": asked, "change": change}


LINK = "https://github.com/octo/demo/pull/7"


# ---------------------------------------------------------------------------
# Starting a run
# ---------------------------------------------------------------------------

def test_a_link_starts_one_run_with_a_folder_per_file(client, sandbox, children,
                                                      fetched):
    answer = client.post("/api/run", json={"github": LINK, "mode": "fresh",
                                           "mutation": False})
    assert answer.status_code == 200, answer.text
    said = answer.json()
    assert said["files"] == ["calc.py", "pkg/more.py"]
    assert said["mode"] == "fresh"
    assert fetched["asked"] == [LINK]

    # One claimed folder per file, each named after the run.
    folders = sorted(p.name for p in (sandbox / "generated").iterdir())
    assert len(folders) == 2
    assert all(name.startswith("demo_gh_") for name in folders)
    for name in folders:
        assert (sandbox / "generated" / name / A.MARKER).is_file()

    # The job file holds every pair, and the pairs hold the fetched code.
    job_file = next((sandbox / "scratch").glob("demo_run_*/job.json"))
    job = json.loads(job_file.read_text(encoding="utf-8"))
    assert [one["path"] for one in job["files"]] == ["calc.py", "pkg/more.py"]
    for one in job["files"]:
        assert open(one["before"], encoding="utf-8").read() == OLD
        assert open(one["after"], encoding="utf-8").read() == NEW
    assert job["github"]["number"] == 7
    assert job["mode"] == "fresh"


def test_every_file_a_link_run_made_is_gone_when_it_ends(client, sandbox,
                                                         children, fetched):
    answer = client.post("/api/run", json={"github": LINK, "mode": "fresh"})
    assert answer.status_code == 200, answer.text

    reports = sandbox / "reports"
    reports.mkdir()
    for folder in (sandbox / "generated").iterdir():
        (reports / f"{folder.name}.json").write_text("{}", encoding="utf-8")
        (reports / f"{folder.name}.md").write_text("", encoding="utf-8")

    children[0].release()
    assert wait_until_free()

    assert list((sandbox / "generated").iterdir()) == []
    assert not (sandbox / "scratch").exists()
    assert list(reports.iterdir()) == []


def test_a_link_run_has_the_longer_time_limit(client, sandbox, children,
                                              fetched):
    client.post("/api/run", json={"github": LINK, "mode": "fresh"})
    assert A.RUNNER.current.timeout == A.config.APP_GITHUB_RUN_TIMEOUT_SECONDS


def test_the_child_of_a_link_run_gets_neither_backup_nor_github_key(
        client, sandbox, children, fetched, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_NOTFORTHECHILD")
    monkeypatch.setenv("GEMINI_API_KEY", "not-for-the-child-either")
    client.post("/api/run", json={"github": LINK, "mode": "fresh"})
    env = children[0].env
    assert env["GITHUB_TOKEN"] == ""
    assert env["GEMINI_API_KEY"] == ""
    assert env["GROQ_API_KEY"] == "a-groq-key-for-the-test"


def test_a_link_with_saved_tests_is_refused(client, sandbox, children, fetched):
    answer = client.post("/api/run", json={"github": LINK, "mode": "saved"})
    assert answer.status_code == 400
    assert "no saved tests" in answer.json()["detail"]
    assert fetched["asked"] == [], "nothing should be fetched for a refusal"
    assert not children


def test_a_link_without_a_groq_key_is_refused(client, sandbox, children,
                                              fetched, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    answer = client.post("/api/run", json={"github": LINK, "mode": "fresh"})
    assert answer.status_code == 400
    assert "No Groq key is set" in answer.json()["detail"]
    assert fetched["asked"] == []


def test_a_fetch_problem_is_one_sentence_and_leaves_nothing(client, sandbox,
                                                            children,
                                                            monkeypatch):
    def broken(link, token=None, client=None):
        raise gh.GitHubError("GitHub could not find pull request #7.")

    monkeypatch.setattr(A.gh, "fetch_change", broken)
    answer = client.post("/api/run", json={"github": LINK, "mode": "fresh"})
    assert answer.status_code == 400
    assert answer.json()["detail"] == "GitHub could not find pull request #7."
    assert not (sandbox / "scratch").exists()
    assert not (sandbox / "generated").exists()
    assert not children


def test_a_change_with_nothing_to_score_says_what_was_left_out(
        client, sandbox, children, fetched):
    fetched["change"]["value"] = a_change(
        files=[], skipped=[{"path": "app/new.py",
                            "reason": "it is a new file"}])
    answer = client.post("/api/run", json={"github": LINK, "mode": "fresh"})
    assert answer.status_code == 400
    said = answer.json()["detail"]
    assert "Nothing in that change can be scored" in said
    assert "app/new.py: it is a new file" in said
    assert not children


def test_a_folder_somebody_else_made_stops_the_run_and_is_left_alone(
        client, sandbox, children, fetched, monkeypatch):
    monkeypatch.setattr(A.uuid, "uuid4",
                        lambda: type("U", (), {"hex": "abcdef123456" * 3})())
    theirs = sandbox / "generated" / "demo_gh_abcdef_2"
    theirs.mkdir(parents=True)
    (theirs / "keep_me.py").write_text("x = 1\n", encoding="utf-8")

    answer = client.post("/api/run", json={"github": LINK, "mode": "fresh"})
    assert answer.status_code == 409
    assert (theirs / "keep_me.py").is_file()
    # The first file's folder, which the app did make, is taken away again.
    assert not (sandbox / "generated" / "demo_gh_abcdef_1").exists()
    assert not (sandbox / "scratch").exists()
    assert not children


def test_the_demos_answer_carries_the_link_limits(client):
    said = client.get("/api/demos").json()
    assert said["github_max_files"] == A.config.GITHUB_MAX_FILES
    assert said["github_timeout_seconds"] == \
        A.config.APP_GITHUB_RUN_TIMEOUT_SECONDS


def test_the_timeout_sentence_names_the_right_number_of_minutes():
    assert A.timeout_message(None) == A.TIMEOUT_MESSAGE
    assert A.timeout_message(A.config.APP_RUN_TIMEOUT_SECONDS) == \
        A.TIMEOUT_MESSAGE
    assert "longer than 15 minutes" in A.timeout_message(900)


# ---------------------------------------------------------------------------
# The child's side, in this process
# ---------------------------------------------------------------------------

def marked_lines(text):
    return [line for line in text.splitlines() if line[:2] in
            ("P ", "R ", "F ", "E ")]


def test_the_child_runs_the_pipeline_once_per_file_and_sends_one_result(
        monkeypatch, capfd, tmp_path):
    calls = []

    def fake_pipeline(before, after, **kwargs):
        calls.append((before, after, kwargs))
        return {"before": before, "after": after, "functions": [],
                "summary": f"done {kwargs['name']}"}

    monkeypatch.setattr(A.pl, "run_pipeline", fake_pipeline)

    job = {
        "github": {"owner": "octo", "repo": "demo", "kind": "pull",
                   "number": 7, "skipped": []},
        "files": [
            {"path": "calc.py", "before": str(tmp_path / "1/before.py"),
             "after": str(tmp_path / "1/after.py"), "name": "demo_gh_x_1"},
            {"path": "pkg/more.py", "before": str(tmp_path / "2/before.py"),
             "after": str(tmp_path / "2/after.py"), "name": "demo_gh_x_2"},
        ],
        "mode": "fresh",
        "mutation": True,
    }
    assert A.run_once(job) == 0

    assert [kwargs["name"] for _, _, kwargs in calls] == ["demo_gh_x_1",
                                                          "demo_gh_x_2"]
    for _, _, kwargs in calls:
        assert kwargs["reuse_tests"] is False
        assert kwargs["fallback_allowed"] is False
        assert kwargs["repair_tests"] is False
        assert kwargs["mutation"] is True

    lines = marked_lines(capfd.readouterr().out)
    results = [line for line in lines if line.startswith("R ")]
    assert len(results) == 1
    report = json.loads(results[0][2:])
    assert report["app_mode"] == "generated"
    assert report["github"]["number"] == 7
    assert [one["path"] for one in report["files"]] == ["calc.py",
                                                        "pkg/more.py"]
    # Paths become bare file names, as for every other run.
    assert report["files"][0]["before"] == "before.py"
    assert any("file 1 of 2: calc.py" in line for line in lines)


def test_one_broken_file_does_not_stop_the_others(monkeypatch, capfd):
    def fake_pipeline(before, after, **kwargs):
        if kwargs["name"].endswith("_1"):
            raise ValueError("could not read it")
        return {"functions": []}

    monkeypatch.setattr(A.pl, "run_pipeline", fake_pipeline)
    job = {"github": {}, "mode": "fresh", "files": [
        {"path": "a.py", "before": "b", "after": "a", "name": "demo_gh_y_1"},
        {"path": "b.py", "before": "b", "after": "a", "name": "demo_gh_y_2"},
    ]}
    A.run_once(job)

    result = [line for line in marked_lines(capfd.readouterr().out)
              if line.startswith("R ")][0]
    files = json.loads(result[2:])["files"]
    assert files[0] == {"path": "a.py",
                        "error": "ValueError: could not read it"}
    assert files[1]["path"] == "b.py"


def test_the_allowance_running_out_stops_the_whole_link_run(monkeypatch,
                                                            capfd):
    def fake_pipeline(before, after, **kwargs):
        raise A.pl.DailyLimitStop("used up")

    monkeypatch.setattr(A.pl, "run_pipeline", fake_pipeline)
    job = {"github": {}, "mode": "fresh", "files": [
        {"path": "a.py", "before": "b", "after": "a", "name": "demo_gh_z_1"},
        {"path": "b.py", "before": "b", "after": "a", "name": "demo_gh_z_2"},
    ]}
    A.run_once(job)

    lines = marked_lines(capfd.readouterr().out)
    assert any(line == "F " + A.DAILY_LIMIT_MESSAGE for line in lines)
    assert not any(line.startswith("R ") for line in lines)


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

def test_the_page_offers_the_link_box_and_draws_one_block_per_file():
    page = A.page_text()
    for needed in ('id="githubLink"', 'id="githubBox"', "renderGithub",
                   "report.files", "A GitHub pull request or commit"):
        assert needed in page, needed


def test_the_page_builds_the_github_part_from_text_not_html():
    """A pull request title is whatever somebody typed into GitHub."""
    page = A.page_text()
    assert "innerHTML" not in page
    assert 'startsWith("https://github.com/")' in page
