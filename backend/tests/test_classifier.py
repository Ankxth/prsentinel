import itertools
import uuid

import pytest

from app.db.models.run_functions import FunctionTestResult, RunFunction
from app.services import classifier as c
from app.services.run_functions import register_functions
from tests.test_runs import CHANGED, start
from app.db.models.runs import Run


# ------------------------------------------------------------------ labels

@pytest.mark.parametrize(
    "before,after,expected",
    [
        ("passed", "failed", c.CATCHES_CHANGE),
        ("passed", "error", c.CATCHES_CHANGE),
        ("failed", "failed", c.TEST_WRONG_ON_BEFORE),
        ("error", "failed", c.TEST_WRONG_ON_BEFORE),
        ("failed", "error", c.TEST_WRONG_ON_BEFORE),
        ("failed", "passed", c.ODD),
        ("error", "passed", c.ODD),
        ("passed", "passed", c.NO_SIGNAL),
        ("skipped", "skipped", c.NO_SIGNAL),
        ("passed", "skipped", c.NO_SIGNAL),
        ("skipped", "failed", c.CATCHES_CHANGE),
    ],
)
def test_label_for(before, after, expected):
    assert c.label_for(before, after) == expected


def test_every_pair_gets_a_known_label():
    for before, after in itertools.product(c.RESULTS, repeat=2):
        assert c.label_for(before, after) in c.LABELS


def test_unknown_result_is_an_error_never_a_pass():
    assert c.normalize_result("whatever") == c.TEST_ERROR
    assert c.normalize_result(None) == c.TEST_ERROR
    assert c.label_for("passed", "weird") == c.CATCHES_CHANGE
    assert c.label_for("weird", "passed") == c.ODD


# ------------------------------------------------------------------ rule classifier

def test_passed_before_failed_after_is_real_bug():
    out = c.rule_classify("passed", "failed")
    assert out["diagnosis"] == c.REAL_BUG
    assert "old code" in out["reason"]


def test_error_after_is_also_real_bug():
    assert c.rule_classify("passed", "error")["diagnosis"] == c.REAL_BUG


def test_failing_on_old_code_is_bad_test():
    assert c.rule_classify("failed", "failed")["diagnosis"] == c.BAD_TEST
    assert c.rule_classify("error", "failed")["diagnosis"] == c.BAD_TEST


def test_mixed_reruns_are_flaky_and_win_over_everything():
    out = c.rule_classify("passed", "failed", rerun_passes=2, rerun_fails=1)
    assert out["diagnosis"] == c.FLAKY
    assert c.rule_classify("failed", "failed", 1, 1)["diagnosis"] == c.FLAKY
    assert "2 passes and 1 failures" in out["reason"]


def test_always_failing_reruns_are_not_flaky():
    assert c.rule_classify("passed", "failed", 0, 3)["diagnosis"] == c.REAL_BUG


def test_inconclusive_results_fall_back_to_bad_test_and_say_so():
    out = c.rule_classify("passed", "passed")
    assert out["diagnosis"] == c.BAD_TEST
    assert "conclusive" in out["reason"]


def test_every_outcome_has_a_diagnosis_and_reason():
    for before, after in itertools.product(c.RESULTS, repeat=2):
        out = c.rule_classify(before, after)
        assert out["diagnosis"] in c.DIAGNOSES
        assert out["reason"]


# ------------------------------------------------------------------ rows

def test_start_run_creates_a_row_per_function(client, make_github_token, registered_repo, db):
    run_id = start(client, make_github_token()).json()["run_id"]
    rows = db.query(RunFunction).filter_by(run_id=uuid.UUID(run_id)).all()
    assert sorted((r.file, r.name, r.status) for r in rows) == [
        ("app/a.py", "add", "pending"),
        ("app/b.py", "Cart.total", "pending"),
    ]


def test_repeat_start_does_not_duplicate_rows(client, make_github_token, registered_repo, db):
    start(client, make_github_token())
    start(client, make_github_token())
    assert db.query(RunFunction).count() == len(CHANGED["changed_functions"])


def test_deleted_function_starts_as_skipped(db, registered_repo):
    run = Run(repo_id=registered_repo.id, github_run_id=9, run_attempt=1, sha="a" * 40,
              ref="refs/heads/main", event="push", status="running", summary={})
    db.add(run)
    db.commit()
    rows = register_functions(
        db, run,
        [{"file": "a.py", "name": "gone", "change_type": "deleted"},
         {"file": "a.py", "name": "kept", "change_type": "modified"}],
    )
    by_name = {r.name: r for r in rows}
    assert by_name["gone"].status == "skipped"
    assert by_name["gone"].reason
    assert by_name["kept"].status == "pending"


def test_results_are_removed_with_their_run(db, registered_repo):
    run = Run(repo_id=registered_repo.id, github_run_id=10, run_attempt=1, sha="a" * 40,
              ref="refs/heads/main", event="push", status="running", summary={})
    db.add(run)
    db.commit()
    (fn,) = register_functions(db, run, [{"file": "a.py", "name": "f"}])
    db.add(FunctionTestResult(run_function_id=fn.id, run_id=run.id, test_name="test_f",
                              before_result="passed", after_result="failed",
                              label=c.CATCHES_CHANGE))
    db.commit()
    db.delete(run)
    db.commit()
    assert db.query(RunFunction).count() == 0
    assert db.query(FunctionTestResult).count() == 0
