"""E1: scanner results are stored per run, shown in the PR comment and on the dashboard, and a
scanner that did not run or compare is never turned into a pass."""
import hashlib
import uuid

from app.db.models.accounts import Repo
from app.db.models.findings import Finding
from tests.conftest import bearer
from tests.test_results import run  # noqa: F401  (run is a fixture)
from tests.test_run_view import owner_headers


def fp(text):
    return hashlib.sha256(text.encode()).hexdigest()


def finding(tool="bandit", rule="B602", severity="high", file="app.py", line=5,
            message="subprocess call with shell=True", origin="new", key=None, **extra):
    return {"tool": tool, "rule_id": rule, "severity": severity, "confidence": "high",
            "file": file, "line": line, "message": message, "origin": origin,
            "fingerprint": fp(key or f"{tool}{rule}{file}{line}{message}"), **extra}


def scanner(tool="bandit", status="ran", reason=None, compared=True, version="1.9.4"):
    return {"tool": tool, "status": status, "reason": reason, "version": version,
            "compared_with_base": compared}


def send(client, run, findings, scanners=None, **extra):
    body = {"scanners": scanners if scanners is not None else [scanner()],
            "findings": findings, **extra}
    return client.post(f"/v1/runs/{run.id}/findings", json=body, headers=bearer(run.token))


def finish(client, run, body=None):
    response = client.post(
        f"/v1/runs/{run.id}/finish", json=body or {"status": "completed"}, headers=bearer(run.token)
    )
    assert response.status_code == 200
    return response.json()["comment_markdown"]


def stored(db):
    db.expire_all()
    return db.query(Finding).all()


# ------------------------------------------------------------------ storing

def test_findings_are_stored_and_counted(client, run, db):
    response = send(client, run, [
        finding(origin="new"), finding(rule="B105", line=9, origin="existing"),
    ])
    assert response.status_code == 200, response.text
    assert response.json() == {"saved": 2, "new": 1, "existing": 1, "unknown": 0}
    assert {f.rule_id for f in stored(db)} == {"B602", "B105"}


def test_sending_again_replaces_the_earlier_list(client, run, db):
    send(client, run, [finding(), finding(rule="B105", line=9)])
    again = send(client, run, [finding()])
    assert again.json()["saved"] == 1
    assert len(stored(db)) == 1


def test_the_same_fingerprint_is_stored_once(client, run, db):
    response = send(client, run, [finding(), finding()])
    assert response.json()["saved"] == 1


def test_a_scanner_that_did_not_compare_cannot_call_anything_new(client, run, db):
    from app.db.models.runs import Run

    response = send(client, run, [finding(origin="new")], scanners=[scanner(compared=False)])
    assert response.json() == {"saved": 1, "new": 0, "existing": 0, "unknown": 1}
    db.expire_all()
    assert db.get(Run, uuid.UUID(run.id)).summary["scanners"][0]["compared_with_base"] is False


def test_a_finding_from_an_unlisted_scanner_is_unknown(client, run):
    response = send(client, run, [finding(tool="detect-secrets", rule="Secret Keyword")],
                    scanners=[scanner("bandit")])
    assert response.json()["unknown"] == 1


def test_scanner_status_and_counts_are_kept_on_the_run(client, run, db):
    from app.db.models.runs import Run

    send(client, run, [finding(origin="new"), finding(rule="B105", line=9, origin="existing")],
         scanners=[scanner("bandit"), scanner("pip-audit", "could_not_run", "no pinned requirements", False, None)],
         omitted=3)
    db.expire_all()
    summary = db.get(Run, uuid.UUID(run.id)).summary
    bandit, audit = summary["scanners"]
    assert (bandit["new"], bandit["existing"], bandit["unknown"]) == (1, 1, 0)
    assert audit["status"] == "could_not_run" and audit["reason"] == "no pinned requirements"
    assert summary["findings_omitted"] == 3


def test_text_is_cleaned_before_it_is_stored(client, run, db):
    send(client, run, [finding(file=".\\pkg\\app.py", message="line one\nline\x00 two", rule="B1\n02")])
    row = stored(db)[0]
    assert row.file == "pkg/app.py"
    assert row.message == "line one line two" and row.rule_id == "B1 02"


def test_bad_bodies_are_refused(client, run):
    bad_fp = {**finding(), "fingerprint": "nothex"}
    assert send(client, run, [bad_fp]).status_code == 422
    assert send(client, run, [{**finding(), "tool": "evil-scanner"}]).status_code == 422
    assert send(client, run, [{**finding(), "severity": "catastrophic"}]).status_code == 422
    assert send(client, run, [finding(key=str(i)) for i in range(501)]).status_code == 422


def test_needs_the_run_token_and_a_running_run(client, run):
    assert client.post(f"/v1/runs/{run.id}/findings", json={}).status_code == 401
    finish(client, run)
    assert send(client, run, [finding()]).status_code == 409


# ------------------------------------------------------------------ dashboard

def test_the_owner_sees_the_findings_new_first(client, run, db, make_token):
    send(client, run, [
        finding(rule="B105", severity="low", line=9, origin="existing"),
        finding(rule="B602", severity="high", origin="new"),
        finding(rule="B404", severity="low", line=1, origin="new"),
    ])
    response = client.get(f"/v1/runs/{run.id}/findings", headers=owner_headers(db, make_token))
    assert response.status_code == 200
    data = response.json()
    assert data["counts"] == {"new": 2, "existing": 1, "unknown": 0}
    assert [f["rule_id"] for f in data["findings"]] == ["B602", "B404", "B105"]
    assert "not proof" in data["note"]
    assert data["scanners"][0]["tool"] == "bandit"


def test_other_users_get_404_and_a_run_token_is_refused(client, run, make_token):
    stranger = bearer(make_token(user_id=uuid.uuid4(), login="mallory"))
    assert client.get(f"/v1/runs/{run.id}/findings", headers=stranger).status_code == 404
    assert client.get(f"/v1/runs/{run.id}/findings", headers=bearer(run.token)).status_code == 401
    assert client.get(f"/v1/runs/{run.id}/findings").status_code == 401


def test_deleting_the_repo_deletes_its_findings(client, run, db):
    send(client, run, [finding()])
    db.expire_all()
    assert len(stored(db)) == 1
    repo = db.query(Repo).one()
    db.delete(repo)
    db.commit()
    assert db.query(Finding).count() == 0


# ------------------------------------------------------------------ the PR comment

def test_no_security_section_when_the_runner_sent_nothing(client, run):
    assert "Security checks" not in finish(client, run)


def test_new_findings_are_listed_and_old_ones_only_counted(client, run):
    send(client, run, [
        finding(rule="B602", message="shell=True used", origin="new"),
        finding(rule="B105", line=9, message="hardcoded password", origin="existing"),
    ])
    comment = finish(client, run)
    assert "#### Security checks" in comment
    assert "**1 new** finding(s) introduced by this commit; 1 already existed before it." in comment
    assert "`B602`" in comment and "shell=True used" in comment
    assert "hardcoded password" not in comment
    assert "not proof the code is safe" in comment


def test_no_new_findings_is_not_called_safe(client, run):
    send(client, run, [finding(origin="existing")])
    comment = finish(client, run)
    assert "No new findings in this commit. 1 finding(s) already existed before it." in comment
    assert "safe" not in comment.replace("not proof the code is safe", "")


def test_a_scanner_that_could_not_run_is_said_so(client, run):
    send(client, run, [], scanners=[scanner("bandit"), scanner("pip-audit", "could_not_run", "no pinned requirements", False, None)])
    comment = finish(client, run)
    assert "`pip-audit` **could not run**" in comment and "`no pinned requirements`" in comment
    assert "`bandit` ran" in comment


def test_no_scanner_ran_is_not_a_pass(client, run):
    send(client, run, [], scanners=[scanner("bandit", "could_not_run", "install failed", False, None)])
    comment = finish(client, run)
    assert "No scanner ran, so nothing was checked. This is **not** a pass." in comment


def test_findings_that_could_not_be_compared_are_not_called_new(client, run):
    send(client, run, [finding(origin="new")], scanners=[scanner(compared=False)])
    comment = finish(client, run)
    assert "the old code could not be scanned" in comment
    assert "new** finding" not in comment


def test_only_ten_findings_are_listed(client, run):
    send(client, run, [finding(rule=f"B{i}", line=i, key=str(i)) for i in range(14)])
    comment = finish(client, run)
    assert comment.count("- **High**") == 10
    assert "… and 4 more (see the dashboard)" in comment


def test_hostile_scanner_text_cannot_break_the_comment(client, run):
    import re

    nasty = "`](http://evil) <img src=x onerror=alert(1)>\n# heading"
    send(client, run, [finding(message=nasty, rule=nasty, file=nasty)])
    comment = finish(client, run)
    line = next(x for x in comment.splitlines() if x.startswith("- **High**"))
    outside_code = re.sub(r"`[^`]*`", "", line)
    assert "<img" not in outside_code and "](" not in outside_code
    assert not any(x.startswith("# heading") for x in comment.splitlines())


def test_a_could_not_run_run_does_not_show_scanner_results(client, run):
    send(client, run, [finding()])
    comment = finish(client, run, {"status": "could_not_run", "reason": "pip install failed"})
    assert "Security checks" not in comment


def test_a_scanner_that_ran_partly_says_what_it_skipped(client, run):
    note = "1 dependency line(s) without == were not checked"
    send(client, run, [], scanners=[{**scanner("pip-audit", compared=True), "reason": note}])
    comment = finish(client, run)
    assert f"`pip-audit` ran (`{note}`)" in comment
