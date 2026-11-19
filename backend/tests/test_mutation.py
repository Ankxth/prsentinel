"""D6: the mutation counts from the runner are stored with a fixed score rule and shown in the
comment with the honest caveat. Nothing here calls a model."""
from app.db.models.run_functions import RunFunction
from tests.conftest import bearer
from tests.test_comment import finish
from tests.test_results import post, run, t  # noqa: F401  (run is a fixture)


def mutation(client, run, name="price", **over):
    body = {"file": "lib.py", "name": name, "status": "measured", "found": 5, "killed": 3,
            "survived": 1, "equivalent": 1, "timed_out": 0, "could_not_run": 0,
            "survivors": ["< to <="], **over}
    return client.post(f"/v1/runs/{run.id}/mutation", json=body, headers=bearer(run.token))


def stored(db, name="price"):
    db.expire_all()
    return db.query(RunFunction).filter_by(name=name).one().mutation


def test_score_is_worked_out_by_the_server(client, run, db):
    post(client, run, [t("test_a", "passed", "passed")])
    out = mutation(client, run)
    assert out.status_code == 200
    assert out.json()["status"] == "measured" and out.json()["score"] == 0.75
    m = stored(db)
    assert m["killed"] == 3 and m["survived"] == 1 and m["equivalent"] == 1
    assert m["survivors"] == ["< to <="]


def test_comment_shows_counts_and_the_lower_bound_note(client, run):
    post(client, run, [t("test_a", "passed", "passed")])
    mutation(client, run)
    comment = finish(client, run)
    assert "Mutation check: the tests caught 3 of 4 small deliberate faults (75%)" in comment
    assert "1 changed nothing and were left out" in comment
    assert "Faults the tests missed: `< to <=`." in comment
    assert "lower bound" in comment


def test_nothing_scored_is_not_measured_never_a_score(client, run, db):
    post(client, run, [t("test_a", "passed", "passed")])
    out = mutation(client, run, killed=0, survived=0, equivalent=2)
    assert out.json() == {"function_id": out.json()["function_id"], "status": "not_measured",
                          "score": None}
    assert stored(db)["score"] is None
    assert "Mutation check: not measured" in finish(client, run)


def test_not_measured_shows_the_reason(client, run):
    post(client, run, [t("test_a", "failed", "failed")])
    mutation(client, run, status="not_measured", reason="no generated test passes on the old code",
             killed=0, survived=0)
    comment = finish(client, run)
    assert "not measured (`no generated test passes on the old code`)" in comment
    assert "caught" not in comment


def test_a_function_that_was_never_reported_has_no_mutation_line(client, run):
    post(client, run, [t("test_a", "passed", "passed")])
    assert "Mutation check" not in finish(client, run)


def test_hostile_survivor_names_cannot_break_the_comment(client, run):
    post(client, run, [t("test_a", "passed", "passed")])
    mutation(client, run, survivors=["`</details>**x**"])
    comment = finish(client, run)
    # backticks are replaced and the text sits inside a code span, which GitHub shows literally
    assert "Faults the tests missed: `'</details>**x**`." in comment


def test_function_without_results_is_refused(client, run):
    assert mutation(client, run, name="notgen").status_code == 409


def test_unknown_function_is_404(client, run):
    assert mutation(client, run, name="nope").status_code == 404


def test_other_token_and_finished_run(client, run, make_github_token):
    post(client, run, [t("test_a", "passed", "passed")])
    bad = client.post(f"/v1/runs/{run.id}/mutation", json={"file": "lib.py", "name": "price",
                      "status": "measured"}, headers=bearer("nope"))
    assert bad.status_code == 401
    finish(client, run)
    assert mutation(client, run).status_code == 409


def test_absurd_counts_are_refused(client, run):
    post(client, run, [t("test_a", "passed", "passed")])
    assert mutation(client, run, killed=5000).status_code == 422
