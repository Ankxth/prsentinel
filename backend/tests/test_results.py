"""D3: results from the runner are labelled by fixed rules; nothing here calls a model."""
import uuid

import pytest

from app.db.models.run_functions import FunctionTestResult, RunFunction
from app.db.models.runs import Run
from app.services import classifier as c
from app.services.run_functions import register_functions
from tests.conftest import bearer
from tests.test_context import Run_


@pytest.fixture
def run(client, make_github_token, registered_repo, db):
    started = Run_(client, make_github_token)
    register_functions(
        db, db.get(Run, uuid.UUID(started.id)),
        [{"file": "lib.py", "name": "price", "change_type": "modified"},
         {"file": "lib.py", "name": "fresh", "change_type": "added"},
         {"file": "lib.py", "name": "notgen", "change_type": "modified"}],
    )
    for name in ("price", "fresh"):
        row = db.query(RunFunction).filter_by(name=name).one()
        row.test_code, row.status = "def test_x(): pass\n", "generated"
    db.commit()
    return started


def t(name, before, after, passes=0, fails=0, message=None):
    return {"name": name, "before": before, "after": after, "rerun_passes": passes,
            "rerun_fails": fails, "message": message}


def post(client, run, tests, name="price", status="tested", **extra):
    body = {"file": "lib.py", "name": name, "status": status, "tests": tests, **extra}
    return client.post(f"/v1/runs/{run.id}/results", json=body, headers=bearer(run.token))


def rows(db):
    db.expire_all()
    return {r.test_name: r for r in db.query(FunctionTestResult).all()}


def test_labels_and_diagnoses_come_from_the_rules(client, run, db):
    response = post(client, run, [
        t("test_catch", "passed", "failed", 0, 3, "assert 6 == 4"),
        t("test_ok", "passed", "passed"),
        t("test_bad", "failed", "failed", 0, 3),
        t("test_odd", "failed", "passed"),
        t("test_flaky", "passed", "failed", 1, 2),
    ])
    assert response.status_code == 200
    out = {x["name"]: x for x in response.json()["tests"]}
    assert out["test_catch"]["label"] == c.CATCHES_CHANGE and out["test_catch"]["diagnosis"] == c.REAL_BUG
    assert out["test_ok"]["label"] == c.NO_SIGNAL and out["test_ok"]["diagnosis"] is None
    assert out["test_bad"]["label"] == c.TEST_WRONG_ON_BEFORE and out["test_bad"]["diagnosis"] == c.BAD_TEST
    assert out["test_odd"]["label"] == c.ODD and out["test_odd"]["diagnosis"] is None
    assert out["test_flaky"]["diagnosis"] == c.FLAKY
    stored = rows(db)
    assert stored["test_catch"].message == "assert 6 == 4"
    assert stored["test_ok"].message is None
    db.expire_all()
    assert db.query(RunFunction).filter_by(name="price").one().status == "tested"


def test_added_function_is_never_called_a_real_bug(client, run, db):
    response = post(client, run, [t("test_a", "passed", "failed"), t("test_b", "passed", "passed")],
                    name="fresh")
    out = {x["name"]: x for x in response.json()["tests"]}
    assert out["test_a"]["diagnosis"] == c.BAD_TEST and "new" in out["test_a"]["reason"]
    assert out["test_a"]["label"] == c.NO_SIGNAL
    assert rows(db)["test_a"].before_result == "skipped"


def test_added_function_flaky_is_still_flaky(client, run):
    out = post(client, run, [t("test_a", "passed", "failed", 1, 1)], name="fresh").json()
    assert out["tests"][0]["diagnosis"] == c.FLAKY


def test_unknown_results_count_as_errors(client, run):
    out = post(client, run, [t("test_x", "wat", "wat")]).json()["tests"][0]
    assert out["label"] == c.TEST_WRONG_ON_BEFORE and out["diagnosis"] == c.BAD_TEST


def test_messages_are_cleaned_and_capped(client, run, db):
    message = "boom in /home/runner/work/x/lib.py and C:\\Users\\me\\a.py\x00 " + "z" * 5000
    post(client, run, [t("test_x", "passed", "failed", 0, 1, message)])
    stored = rows(db)["test_x"].message
    assert "/home/runner" not in stored and "C:\\Users" not in stored and "\x00" not in stored
    assert "<path>" in stored and len(stored) <= 1500


def test_sending_the_same_attempt_again_replaces_it(client, run, db):
    post(client, run, [t("test_a", "passed", "passed")])
    post(client, run, [t("test_a", "passed", "failed", 0, 1), t("test_b", "passed", "passed")])
    stored = rows(db)
    assert set(stored) == {"test_a", "test_b"} and stored["test_a"].after_result == "failed"


def test_repair_attempts_are_kept_apart(client, run, db):
    post(client, run, [t("test_a", "failed", "failed")], attempt=0)
    post(client, run, [t("test_a", "passed", "failed")], attempt=1)
    assert db.query(FunctionTestResult).count() == 2


def test_duplicate_names_in_one_request_are_counted_once(client, run, db):
    post(client, run, [t("test_a", "passed", "passed"), t("test_a", "passed", "failed")])
    assert db.query(FunctionTestResult).count() == 1


def test_could_not_test_is_stored_as_no_result(client, run, db):
    out = post(client, run, [], status="could_not_test", reason="pytest is not installed").json()
    assert out["status"] == "could_not_test" and out["reason"] == "pytest is not installed"
    assert db.query(FunctionTestResult).count() == 0


def test_zero_results_is_no_result_never_a_pass(client, run):
    out = post(client, run, []).json()
    assert out["status"] == "could_not_test" and out["tests"] == []


def test_function_without_generated_tests_is_refused(client, run):
    assert post(client, run, [t("test_a", "passed", "passed")], name="notgen").status_code == 409


def test_unknown_function_is_404(client, run):
    assert post(client, run, [t("test_a", "passed", "passed")], name="nope").status_code == 404


def test_other_runs_token_is_refused(client, run, make_github_token):
    other = Run_(client, make_github_token, run_id=8100)
    response = client.post(f"/v1/runs/{run.id}/results",
                           json={"file": "lib.py", "name": "price", "status": "tested", "tests": []},
                           headers=bearer(other.token))
    assert response.status_code == 403


def test_finished_run_is_refused(client, run):
    client.post(f"/v1/runs/{run.id}/finish", json={"status": "completed"}, headers=bearer(run.token))
    assert post(client, run, [t("test_a", "passed", "passed")]).status_code == 409


def test_too_many_tests_are_refused(client, run):
    assert post(client, run, [t(f"t{i}", "passed", "passed") for i in range(201)]).status_code == 422
