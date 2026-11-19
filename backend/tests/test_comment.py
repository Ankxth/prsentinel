"""D4: the PR comment shows what happened to each function's tests, and never calls a missing
result a pass."""
from app.db.models.run_functions import RunFunction
from tests.conftest import bearer
from tests.test_results import post, run, t  # noqa: F401  (run is a fixture)


def finish(client, run, body=None):
    response = client.post(
        f"/v1/runs/{run.id}/finish", json=body or {"status": "completed"}, headers=bearer(run.token)
    )
    assert response.status_code == 200
    return response.json()["comment_markdown"]


def test_a_catching_test_is_shown_with_the_fixed_sentence(client, run):
    post(client, run, [
        t("test_zero", "passed", "failed", 0, 3, "DID NOT RAISE ValueError"),
        t("test_normal", "passed", "passed"),
    ])
    comment = finish(client, run)
    assert "1 of 3 functions tested, 1 with a test that catches a change." in comment
    assert "<summary><code>lib.py::price</code> (<code>modified</code>)" in comment
    assert "<b>1 test catches a change</b>" in comment and "1 test found no difference" in comment
    summary = next(line for line in comment.splitlines() if line.startswith("<summary>"))
    assert "`" not in summary and "**" not in summary  # Markdown is not rendered inside <summary>
    assert "`test_zero`: passes on the old code, fails on the new code" in comment
    assert "failure: `DID NOT RAISE ValueError`" in comment
    assert "`test_normal`: same result on the old and the new code" in comment
    assert "Tests are AI-generated. A catching test is a hint, not proof." in comment
    assert "<details>" in comment and "</details>" in comment


def test_functions_without_a_result_are_listed_as_not_tested(client, run, db):
    post(client, run, [t("test_a", "passed", "passed")])
    row = db.query(RunFunction).filter_by(name="notgen").one()
    row.status, row.reason = "could_not_generate", "AI is switched off for this repository."
    db.commit()
    comment = finish(client, run)
    assert "`lib.py::notgen`" in comment and "**not tested**" in comment
    assert "AI is switched off for this repository." in comment
    assert "`lib.py::fresh`" in comment and "**no test result was reported**" in comment


def test_no_results_at_all_says_nothing_was_tested(client, run, db):
    for row in db.query(RunFunction).all():
        row.status = "pending"
    db.commit()
    comment = finish(client, run)
    assert "nothing was tested" in comment and "**not** a pass" in comment
    assert "Tests are AI-generated" not in comment
    assert "`lib.py::price`" in comment


def test_could_not_test_is_shown_as_not_tested(client, run):
    post(client, run, [], status="could_not_test", reason="pytest is not installed in this job")
    comment = finish(client, run)
    assert "**not tested**" in comment and "pytest is not installed in this job" in comment


def test_flaky_unreliable_and_new_function_failures(client, run):
    post(client, run, [
        t("test_flaky", "passed", "failed", 1, 2),
        t("test_wrong", "failed", "failed", 0, 3),
        t("test_odd", "failed", "passed"),
    ])
    post(client, run, [t("test_new", "passed", "failed", 0, 1, "assert 1 == 2")], name="fresh")
    comment = finish(client, run)
    assert "unstable: 1 rerun passed and 2 failed" in comment
    assert "fails on the old code too, so the test itself may be wrong" in comment
    assert "fails on the old code but passes on the new code" in comment
    assert "fails on this new function, so check the test" in comment
    assert "REAL_BUG" not in comment  # the raw labels are not shown to the reader


def test_only_the_newest_repair_round_is_shown(client, run):
    post(client, run, [t("test_old_round", "failed", "failed")], attempt=0)
    post(client, run, [t("test_new_round", "passed", "passed")], attempt=1)
    comment = finish(client, run)
    assert "test_new_round" in comment and "test_old_round" not in comment


def test_hostile_test_names_and_messages_cannot_break_the_comment(client, run):
    nasty = "x`\n[click](http://evil)\n<img src=x>"
    post(client, run, [t(nasty, "passed", "failed", 0, 3, nasty)])
    comment = finish(client, run)
    assert "\n[click]" not in comment and "\n<img" not in comment
    assert comment.count("`") % 2 == 0


def test_a_failed_or_could_not_run_run_does_not_claim_test_results(client, run):
    post(client, run, [t("test_a", "passed", "failed", 0, 3)])
    comment = finish(client, run, {"status": "could_not_run", "reason": "pip install failed"})
    assert "Could not run" in comment and "**not** a pass" in comment
    assert "test_a" not in comment


def test_a_long_comment_is_shortened(client, run, db):
    from app.services.comment import MAX_COMMENT_CHARS, build_comment
    from app.db.models.runs import Run

    post(client, run, [t(f"test_{i}_" + "n" * 90, "passed", "failed", 0, 3, "m" * 1400) for i in range(40)])
    stored = db.get(Run, __import__("uuid").UUID(run.id))
    comment = build_comment(stored, "o/r", db)
    assert len(comment) <= MAX_COMMENT_CHARS + 100


def test_hostile_function_names_cannot_break_out_of_the_summary(client, run, db):
    nasty = "f</summary><img src=x onerror=alert(1)>`\n[click](http://evil)"
    row = db.query(RunFunction).filter_by(name="price").one()
    row.name = nasty
    db.commit()
    post(client, run, [t("test_a", "passed", "failed", 0, 3)], name=nasty)
    comment = finish(client, run)
    summary = next(line for line in comment.splitlines() if line.startswith("<summary>"))
    assert "<img" not in comment and comment.count("</summary>") == 1
    assert "&lt;/summary&gt;" in summary
    assert "\n[click]" not in comment
