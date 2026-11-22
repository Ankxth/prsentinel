"""D8: the dashboard run page gets the same facts as the PR comment, owner-only, and never the
generated test code."""
import uuid

from sqlalchemy import select

from app.db.models.accounts import User
from app.db.models.run_functions import RunFunction
from tests.conftest import bearer
from tests.test_mutation import mutation
from tests.test_results import post, run, t  # noqa: F401  (run is a fixture)


def owner_headers(db, make_token):
    owner = db.query(User).one()
    return bearer(
        make_token(user_id=owner.id, login=owner.github_login, github_id=str(owner.github_id))
    )


def view(client, run, db, make_token):
    response = client.get(f"/v1/runs/{run.id}/functions", headers=owner_headers(db, make_token))
    assert response.status_code == 200
    return response.json()


def fn(data, name):
    return next(f for f in data["functions"] if f["name"] == name)


def test_tests_labels_and_counts_match_the_comment(client, run, db, make_token):
    post(client, run, [
        t("test_zero", "passed", "failed", 0, 3, "DID NOT RAISE ValueError"),
        t("test_normal", "passed", "passed"),
    ])
    data = view(client, run, db, make_token)
    assert data["status"] == "running"
    assert data["ai_note"] == "Tests are AI-generated. A catching test is a hint, not proof."
    price = fn(data, "price")
    assert price["status"] == "tested"
    assert price["counts"]["catches"] == 1 and price["counts"]["same"] == 1
    tests = {x["name"]: x for x in price["tests"]}
    assert tests["test_zero"]["kind"] == "catches"
    assert tests["test_zero"]["diagnosis"] == "REAL_BUG"
    assert tests["test_zero"]["message"] == "DID NOT RAISE ValueError"
    assert tests["test_normal"]["label"] == "NO_SIGNAL"


def test_functions_without_results_are_never_shown_as_tested(client, run, db, make_token):
    post(client, run, [t("test_a", "passed", "passed")])
    row = db.query(RunFunction).filter_by(name="notgen").one()
    row.status, row.reason = "could_not_generate", "AI is switched off for this repository."
    db.commit()
    data = view(client, run, db, make_token)
    notgen = fn(data, "notgen")
    assert notgen["status"] == "could_not_generate" and notgen["tests"] == []
    assert notgen["reason"] == "AI is switched off for this repository."
    assert fn(data, "fresh")["tests"] == [] and fn(data, "fresh")["status"] == "generated"


def test_tested_without_any_result_row_is_not_a_pass(client, run, db, make_token):
    row = db.query(RunFunction).filter_by(name="price").one()
    row.status = "tested"
    db.commit()
    price = fn(view(client, run, db, make_token), "price")
    assert price["status"] == "could_not_test" and price["tests"] == []


def test_only_the_newest_repair_round_is_listed(client, run, db, make_token):
    post(client, run, [t("test_a", "failed", "failed", 0, 3)])
    post(client, run, [t("test_a", "passed", "passed")], attempt=1)
    price = fn(view(client, run, db, make_token), "price")
    assert price["repair_rounds"] == 1
    assert len(price["tests"]) == 1 and price["tests"][0]["kind"] == "same"


def test_mutation_is_shown_with_the_lower_bound_note(client, run, db, make_token):
    post(client, run, [t("test_a", "passed", "passed")])
    mutation(client, run)
    m = fn(view(client, run, db, make_token), "price")["mutation"]
    assert m["status"] == "measured" and m["killed"] == 3 and m["survived"] == 1
    assert m["percent"] == 75 and m["equivalent"] == 1
    assert m["survivors"] == ["< to <="]
    assert "lower bound" in m["note"]


def test_not_measured_has_a_reason_and_no_score(client, run, db, make_token):
    post(client, run, [t("test_a", "failed", "failed")])
    mutation(client, run, status="not_measured", reason="no generated test passes on the old code",
             killed=0, survived=0)
    m = fn(view(client, run, db, make_token), "price")["mutation"]
    assert m == {"status": "not_measured", "reason": "no generated test passes on the old code"}


def test_a_function_never_reported_has_no_mutation(client, run, db, make_token):
    post(client, run, [t("test_a", "passed", "passed")])
    assert fn(view(client, run, db, make_token), "price")["mutation"] is None


def test_the_generated_test_code_is_sent_to_the_owner_and_capped(client, run, db, make_token):
    post(client, run, [t("test_a", "passed", "passed")])
    row = db.scalar(select(RunFunction).where(RunFunction.run_id == uuid.UUID(str(run.id))))
    row.test_code = "def test_x():\n    pass\n" + "#" * 30000
    db.commit()
    data = fn(view(client, run, db, make_token), "price")
    assert data["test_code"].startswith("def test_x")
    assert len(data["test_code"]) == 20000


def test_long_failure_text_is_cut(client, run, db, make_token):
    post(client, run, [t("test_a", "passed", "failed", 0, 3, "x" * 1400)])
    test = fn(view(client, run, db, make_token), "price")["tests"][0]
    assert len(test["message"]) == 500


def test_other_users_get_404(client, run, db, make_token):
    stranger = bearer(make_token(user_id=uuid.uuid4(), login="mallory"))
    assert client.get(f"/v1/runs/{run.id}/functions", headers=stranger).status_code == 404


def test_run_token_and_no_sign_in_are_refused(client, run):
    assert client.get(f"/v1/runs/{run.id}/functions", headers=bearer(run.token)).status_code == 401
    assert client.get(f"/v1/runs/{run.id}/functions").status_code == 401
