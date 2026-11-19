"""D5: repairing tests that fail on the old code too. The model is always a fake."""
import pytest

from app.core.config import get_settings
from app.db.models.llm_usage import LLMUsage
from app.db.models.run_functions import FunctionTestResult, RunFunction
from app.db.models.runs import Run
from app.services import generator, llm
from tests.conftest import bearer
from tests.test_generate import NEW, OLD, fake, run  # noqa: F401  (fixtures)

TESTS = ("from lib import price\n\ndef test_double():\n    assert price(2) == 5\n\n"
         "def test_zero():\n    assert price(0) == 0\n")
FIXED = "```python\nfrom lib import price\n\ndef test_double():\n    assert price(2) == 4\n\n" \
        "def test_zero():\n    assert price(0) == 0\n```\n"


def tested(client, run, db):
    """The function has been through the first test run (attempt 0): one test wrong on both."""
    row = db.query(RunFunction).filter_by(name="price").one()
    row.test_code, row.status = TESTS, "generated"
    db.commit()
    body = {"file": "lib.py", "name": "price", "status": "tested", "attempt": 0, "tests": [
        {"name": "test_double", "before": "failed", "after": "failed", "rerun_passes": 0,
         "rerun_fails": 3, "message": "assert 4 == 5"},
        {"name": "test_zero", "before": "passed", "after": "passed"}]}
    assert client.post(f"/v1/runs/{run.id}/results", json=body, headers=bearer(run.token)).status_code == 200


def repair(client, run, **over):
    body = {"file": "lib.py", "name": "price", "attempt": 1, "old_code": OLD, "new_code": NEW,
            "test_code": TESTS,
            "failing": [{"name": "test_double", "before": "failed", "after": "failed",
                         "message": "assert 4 == 5"}], **over}
    return client.post(f"/v1/runs/{run.id}/repair", json=body, headers=bearer(run.token))


def function_row(db):
    db.expire_all()
    return db.query(RunFunction).filter_by(name="price").one()


@pytest.fixture
def ready(client, run, fake, db):
    fake.reply = FIXED
    tested(client, run, db)
    return run


def test_returns_the_fixed_tests_and_asks_the_model_once(client, ready, fake, db):
    out = repair(client, ready)
    assert out.status_code == 200
    body = out.json()
    assert body["status"] == "generated" and body["attempt"] == 1
    assert "price(2) == 4" in body["test_code"] and "```" not in body["test_code"]
    assert body["import_line"] == "from lib import price"
    (call,) = fake.calls
    assert call["purpose"] == "test_repair" and call["budget_key"] == "default"
    system, user = call["messages"]
    assert "DATA" in system["content"]
    text = user["content"]
    assert OLD.strip() in text and NEW.strip() in text and TESTS.strip() in text
    assert "test_double" in text and "assert 4 == 5" in text
    assert "intended behaviour" in text and "Do not change any other test" in text
    assert "no `assert True`" in text
    (usage,) = db.query(LLMUsage).all()
    assert usage.purpose == "test_repair"


def test_the_first_results_are_untouched(client, ready, db):
    repair(client, ready)
    row = function_row(db)
    assert row.status == "tested"
    assert db.query(FunctionTestResult).count() == 2


def test_a_failed_repair_keeps_the_first_results(client, ready, fake, db):
    fake.error = llm.LLMError("unavailable", "internal detail")
    body = repair(client, ready).json()
    assert body["status"] == "could_not_generate" and body["test_code"] is None
    assert "internal detail" not in body["reason"]
    assert function_row(db).status == "tested" and db.query(FunctionTestResult).count() == 2
    assert db.query(LLMUsage).count() == 0


@pytest.mark.parametrize("reply", [
    "Sorry, no.",
    "```python\ndef test_double():\n    assert True\n```\n",          # lost the import line
    "```python\nfrom lib import price\n\nx = 1\n```\n",               # no test left
])
def test_unusable_answers_are_a_no_result(client, ready, fake, reply):
    fake.reply = reply
    body = repair(client, ready).json()
    assert body["status"] == "could_not_generate" and "usable" in body["reason"]


def test_failure_text_is_cleaned_and_capped_in_the_prompt(client, ready, fake):
    long = "boom in /home/runner/work/x/lib.py " + "z" * 5000
    repair(client, ready, failing=[{"name": "test_double", "before": "failed", "after": "failed",
                                    "message": long}])
    text = fake.calls[0]["messages"][1]["content"]
    assert "/home/runner" not in text and "<path>" in text
    assert text.count("z") <= generator.MAX_FAILURE_CHARS


def test_repo_without_opt_in_gets_no_model_call(client, ready, fake, registered_repo, db):
    registered_repo.llm_opt_in = False
    db.commit()
    body = repair(client, ready).json()
    assert body["status"] == "could_not_generate" and "opt-in" in body["reason"]
    assert fake.calls == []


def test_daily_allowance_applies(client, ready, fake, monkeypatch):
    monkeypatch.setenv("LLM_DAILY_TOKENS_PER_REPO", "10")
    get_settings.cache_clear()
    assert repair(client, ready).json()["status"] == "generated"  # uses 18 tokens
    blocked = repair(client, ready, attempt=2).json()
    assert blocked["status"] == "could_not_generate" and "allowance" in blocked["reason"]
    assert len(fake.calls) == 1


def test_only_tested_modified_functions_can_be_repaired(client, run, fake, db):
    assert repair(client, run).status_code == 409           # not run yet
    assert fake.calls == []


def test_attempt_and_failing_list_are_checked(client, ready, fake):
    assert repair(client, ready, attempt=0).status_code == 422
    assert repair(client, ready, attempt=generator.MAX_REPAIR_ROUNDS + 1).status_code == 422
    assert repair(client, ready, failing=[]).status_code == 422
    assert fake.calls == []


def test_function_must_belong_to_the_run(client, ready, fake):
    assert repair(client, ready, name="nothing").status_code == 404
    assert fake.calls == []


def test_a_finished_run_cannot_be_repaired(client, ready, fake, db):
    import uuid
    db.get(Run, uuid.UUID(ready.id)).status = "completed"
    db.commit()
    assert repair(client, ready).status_code == 409
    assert fake.calls == []
