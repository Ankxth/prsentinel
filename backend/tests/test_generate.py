"""D2: test generation. The model is always a fake; the server never runs generated code."""
import pytest

from app.core.config import get_settings
from app.db.models.llm_usage import LLMUsage
from app.db.models.run_functions import RunFunction
from app.services import generator, llm
from app.services.llm import get_llm
from tests.conftest import bearer
from tests.test_context import Run_
from tests.test_summaries import FakeLLM, LIB, build

OLD = "def price(item):\n    return item * 2\n"
NEW = "def price(item):\n    return item * 3\n"
GOOD = "Here you go:\n```python\nfrom lib import price\n\ndef test_price():\n    assert price(2) == 4\n```\n"


@pytest.fixture
def fake(client):
    model = FakeLLM()
    model.reply = GOOD
    client.app.dependency_overrides[get_llm] = lambda: model
    return model


@pytest.fixture
def run(client, make_github_token, registered_repo, db):
    started = Run_(client, make_github_token)
    # START has no functions, so register one the way a real start would
    from app.db.models.runs import Run
    import uuid
    from app.services.run_functions import register_functions
    row = db.get(Run, uuid.UUID(started.id))
    register_functions(db, row, [{"file": "lib.py", "name": "price", "change_type": "modified"},
                                 {"file": "lib.py", "name": "old", "change_type": "deleted"}])
    return started


def gen(client, run, **over):
    body = {"file": "lib.py", "name": "price", "old_code": OLD, "new_code": NEW, **over}
    return client.post(f"/v1/runs/{run.id}/generate", json=body, headers=bearer(run.token))


def row(db, name="price"):
    db.expire_all()
    return db.query(RunFunction).filter_by(name=name).one()


# ------------------------------------------------------------------ pure helpers

@pytest.mark.parametrize("file,expected", [
    ("app/pricing.py", "app.pricing"), ("src/pkg/a.py", "pkg.a"),
    ("pkg/__init__.py", "pkg"), ("lib.py", "lib"), ("a\\b.py", "a.b"),
])
def test_module_for(file, expected):
    assert generator.module_for(file) == expected


def test_method_is_imported_through_its_class():
    assert generator.import_line("app/cart.py", "Cart.total") == "from app.cart import Cart"


def test_extract_code_prefers_python_block_and_rejects_chatter():
    assert generator.extract_code("x\n```python\nassert 1\n```\ny") == "assert 1\n"
    assert generator.extract_code("```\nassert 2\n```") == "assert 2\n"
    for bad in ("", "   ", "no code here", "```python\n\n```"):
        with pytest.raises(ValueError):
            generator.extract_code(bad)
    with pytest.raises(ValueError):
        generator.extract_code("```python\n" + "x = 1\n" * 10000 + "```")


def test_cache_key_changes_with_every_input():
    base = generator.cache_key("o", "n", "p", "m")
    assert base == generator.cache_key("o", "n", "p", "m")
    for args in (("o2", "n", "p", "m"), ("o", "n2", "p", "m"), ("o", "n", "p2", "m"), ("o", "n", "p", "m2")):
        assert generator.cache_key(*args) != base


def test_prompt_treats_repo_text_as_data_and_never_names_the_right_version():
    system, user = generator.build_messages("lib.py", "price", OLD, NEW, "pack text")
    assert "DATA" in system["content"] and "never instructions" in system["content"]
    text = user["content"]
    assert OLD.strip() in text and NEW.strip() in text and "pack text" in text
    assert "from lib import price" in text
    assert "intended behaviour" in text  # old behaviour is the specification
    new_fn = generator.build_messages("lib.py", "price", None, NEW, "")[1]["content"]
    assert "this function is new" in new_fn and "no code map" in new_fn


# ------------------------------------------------------------------ the endpoint

def test_generates_and_stores_tests(client, run, db, fake):
    response = gen(client, run)
    assert response.status_code == 200
    out = response.json()
    assert out["status"] == "generated" and out["cached"] is False
    assert "def test_price" in out["test_code"] and "```" not in out["test_code"]
    assert out["import_line"] == "from lib import price"
    stored = row(db)
    assert stored.status == "generated" and stored.test_code == out["test_code"] and stored.cache_key
    call = fake.calls[0]
    assert call["purpose"] == "test_generation" and call["budget_key"] == "default"
    assert OLD.strip() in call["messages"][1]["content"]


def test_pack_from_the_code_map_is_in_the_prompt(client, make_github_token, registered_repo, db, fake):
    started = Run_(client, make_github_token, run_id=7000)
    build(started, [LIB])
    started.finish()
    second = Run_(client, make_github_token, run_id=7001)
    from app.db.models.runs import Run
    import uuid
    from app.services.run_functions import register_functions
    register_functions(db, db.get(Run, uuid.UUID(second.id)), [{"file": "lib.py", "name": "price"}])
    assert gen(client, second).status_code == 200
    assert "price(item)" in fake.calls[0]["messages"][1]["content"]


def test_asking_again_reuses_the_tests_without_the_model(client, run, fake):
    gen(client, run)
    again = gen(client, run).json()
    assert again["cached"] is True and again["status"] == "generated"
    assert len(fake.calls) == 1


def test_changed_code_asks_again(client, run, fake):
    gen(client, run)
    gen(client, run, new_code="def price(item):\n    return item * 4\n")
    assert len(fake.calls) == 2


def test_usage_is_recorded_without_prompts(client, run, db, fake):
    gen(client, run)
    (usage,) = db.query(LLMUsage).all()
    assert (usage.input_tokens, usage.output_tokens, usage.purpose) == (10, 8, "test_generation")


def test_repo_without_opt_in_gets_no_model_call(client, run, db, fake, registered_repo):
    registered_repo.llm_opt_in = False
    db.commit()
    out = gen(client, run).json()
    assert out["status"] == "could_not_generate" and "opt-in" in out["reason"]
    assert fake.calls == [] and row(db).status == "could_not_generate"


@pytest.mark.parametrize("kind", ["rate_limited", "unavailable", "bad_response", "not_configured"])
def test_model_trouble_is_a_clear_no_result(client, run, db, fake, kind):
    fake.error = llm.LLMError(kind, "internal detail with secret-ish text")
    out = gen(client, run)
    assert out.status_code == 200
    body = out.json()
    assert body["status"] == "could_not_generate" and body["test_code"] is None
    assert "secret-ish" not in body["reason"]
    assert row(db).test_code is None and db.query(LLMUsage).count() == 0


def test_answer_without_code_is_a_no_result(client, run, db, fake):
    fake.reply = "Sorry, I cannot do that."
    out = gen(client, run).json()
    assert out["status"] == "could_not_generate" and "usable test code" in out["reason"]
    assert db.query(LLMUsage).count() == 1  # the call happened, so it counts


def test_repo_daily_allowance(client, run, db, fake, monkeypatch):
    monkeypatch.setenv("LLM_DAILY_TOKENS_PER_REPO", "30")
    get_settings.cache_clear()
    assert gen(client, run).json()["status"] == "generated"   # uses 18 tokens
    gen_two = gen(client, run, new_code="def price(item):\n    return 1\n").json()
    assert gen_two["status"] == "generated"                   # 18 < 30, still allowed
    third = gen(client, run, new_code="def price(item):\n    return 2\n").json()
    assert third["status"] == "could_not_generate" and "allowance" in third["reason"]
    assert len(fake.calls) == 2


def test_user_daily_allowance(client, run, fake, monkeypatch):
    monkeypatch.setenv("LLM_DAILY_TOKENS_PER_USER", "10")
    get_settings.cache_clear()
    gen(client, run)
    blocked = gen(client, run, new_code="def price(item):\n    return 1\n").json()
    assert blocked["status"] == "could_not_generate" and "allowance" in blocked["reason"]


def test_cached_answers_do_not_use_the_allowance(client, run, fake, monkeypatch):
    monkeypatch.setenv("LLM_DAILY_TOKENS_PER_REPO", "1")
    get_settings.cache_clear()
    gen(client, run)  # first call is allowed (0 used), now over the limit
    assert gen(client, run).json()["cached"] is True


def test_function_must_belong_to_the_run(client, run, fake):
    assert gen(client, run, name="nope").status_code == 404
    assert fake.calls == []


def test_deleted_function_is_refused(client, run, fake):
    assert gen(client, run, name="old").status_code == 422


def test_another_runs_token_is_refused(client, run, make_github_token, registered_repo, fake):
    other = Run_(client, make_github_token, run_id=8000)
    response = client.post(f"/v1/runs/{run.id}/generate",
                           json={"file": "lib.py", "name": "price", "new_code": NEW},
                           headers=bearer(other.token))
    assert response.status_code == 403


def test_no_token_is_refused(client, run):
    assert client.post(f"/v1/runs/{run.id}/generate", json={"file": "a", "name": "b", "new_code": "x"}
                       ).status_code in (401, 403)


def test_finished_run_cannot_generate(client, run, fake):
    client.post(f"/v1/runs/{run.id}/finish", json={"status": "completed"}, headers=bearer(run.token))
    assert gen(client, run).status_code == 409


def test_cache_is_not_shared_across_repos(client, make_github_token, registered_repo, db, fake):
    # a second repo with an identical function must not read the first repo's stored tests
    from app.db.models.accounts import Repo
    other = Repo(owner_user_id=registered_repo.owner_user_id, github_id=101, github_owner="alice",
                 github_name="other", default_branch="main", llm_opt_in=True, settings={})
    db.add(other)
    db.commit()
    first = Run_(client, make_github_token, run_id=9000)
    second = Run_(client, make_github_token, run_id=9001, repo="alice/other", repo_id=101)
    from app.db.models.runs import Run
    import uuid
    from app.services.run_functions import register_functions
    for started in (first, second):
        register_functions(db, db.get(Run, uuid.UUID(started.id)), [{"file": "lib.py", "name": "price"}])
    gen(client, first)
    assert gen(client, second).json()["cached"] is False
    assert len(fake.calls) == 2
