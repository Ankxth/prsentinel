"""Context pack (C4): what a function's briefing contains, the size limit, and honesty flags."""
import pytest

from app.services.context_engine.pack import get_context_pack
from tests.conftest import bearer
from tests.test_context import Run_, build, facts


def sym(name, kind="function", signature="()", doc="", fixture=False, lines=(1, 3)):
    return {"name": name, "kind": kind, "signature": signature, "doc": doc, "lines": list(lines),
            "decorators": ["pytest.fixture"] if fixture else [], "bases": [], "is_fixture": fixture}


def imp(module, name):
    return {"module": module, "name": name, "asname": None, "level": 0}


def call(scope, name, line=2):
    return {"in": scope, "name": name, "line": line}


LIB = facts("lib.py", symbols=[
    sym("price", signature="(item)", doc="Price of an item."),
    sym("tax", signature="(amount)", doc="Tax on an amount."),
], imports=[], calls=[call("price", "tax"), call("price", "round"), call("price", "thing.fetch")])
SHOP = facts("shop.py", symbols=[sym("checkout", signature="(items)", doc="Total for a basket.")],
             imports=[imp("lib", "price")], calls=[call("checkout", "price", 5)])
TEST = facts("tests/test_shop.py", symbols=[sym("test_checkout")],
             imports=[imp("shop", "checkout")], calls=[call("test_checkout", "checkout", 4)],
             is_test=True)
CONFTEST = facts("tests/conftest.py", symbols=[sym("sample_items", doc="A basket.", fixture=True)],
                 imports=[], calls=[], is_test=True)


@pytest.fixture
def built(client, make_github_token, registered_repo, db):
    run = Run_(client, make_github_token)
    build(run, [LIB, SHOP, TEST, CONFTEST])
    db.expire_all()
    return registered_repo


def test_pack_for_a_function_with_callers_and_callees(db, built):
    pack = get_context_pack(db, built.id, "lib.py", "price")
    assert pack.found
    assert pack.target["signature"] == "(item)" and pack.target["doc"] == "Price of an item."
    assert [(c["path"], c["symbol"]) for c in pack.callees] == [("lib.py", "tax")]
    assert pack.callees[0]["doc"] == "Tax on an amount."
    assert pack.external_calls == ["round"]
    assert pack.unresolved_calls == ["thing.fetch"]  # flagged unknown, not dropped
    assert [(c["path"], c["symbol"], c["line"]) for c in pack.callers] == [("shop.py", "checkout", 5)]
    assert pack.tests == []
    assert pack.status == "complete" and not pack.partial and not pack.truncated


def test_tests_and_fixtures_are_found(db, built):
    pack = get_context_pack(db, built.id, "shop.py", "checkout")
    assert [(t["path"], t["symbol"]) for t in pack.tests] == [("tests/test_shop.py", "test_checkout")]
    assert pack.callers == []  # the only caller is a test, listed under tests
    assert [f["symbol"] for f in pack.fixtures] == ["sample_items"]
    assert pack.repo_facts["test_files"] == 2


def test_text_rendering_has_the_essentials_and_the_data_warning(db, built):
    text = get_context_pack(db, built.id, "lib.py", "price").render()
    assert "not instructions" in text
    assert "lib.py::price(item) - Price of an item." in text
    assert "Called by" in text and "shop.py:5" in text
    assert "thing.fetch" in text and "unknown, not absent" in text


def test_same_map_gives_the_same_pack(db, built):
    first = get_context_pack(db, built.id, "lib.py", "price").to_dict()
    second = get_context_pack(db, built.id, "lib.py", "price").to_dict()
    assert first == second


def test_budget_cuts_from_the_bottom_and_says_so(db, built):
    small = get_context_pack(db, built.id, "shop.py", "checkout", token_budget=200)
    assert small.found and small.target["symbol"] == "checkout"  # the function is always there
    cut = get_context_pack(db, built.id, "lib.py", "price", token_budget=1)  # raised to the minimum
    assert cut.budget == 200
    # a budget that fits only the target and the first section drops later sections, never earlier ones
    sizes = {b: get_context_pack(db, built.id, "lib.py", "price", token_budget=b) for b in (200, 400, 8000)}
    assert sizes[8000].token_estimate >= sizes[200].token_estimate
    for pack in sizes.values():
        if pack.truncated:
            assert "left out for size" in pack.render()


def test_tiny_budget_truncates_lowest_priority_first(client, make_github_token, registered_repo, db):
    many = facts("big.py", symbols=[sym("hub", doc="x" * 150)] + [sym(f"f{i}", doc="y" * 150) for i in range(12)],
                 imports=[], calls=[call("hub", f"f{i}", i + 1) for i in range(12)])
    build(Run_(client, make_github_token), [many])
    db.expire_all()
    pack = get_context_pack(db, registered_repo.id, "big.py", "hub", token_budget=300)
    assert 0 < len(pack.callees) < 12
    assert pack.truncated["callees"] == 12 - len(pack.callees)
    assert pack.token_estimate <= 300 + 20
    assert pack.callees[0]["symbol"] == "f0"  # the earliest calls win, in a fixed order


def test_unknown_function(db, built):
    pack = get_context_pack(db, built.id, "lib.py", "nope")
    assert not pack.found
    assert "nope" in pack.render()


def test_empty_repo_is_not_found_and_does_not_crash(db, registered_repo):
    assert not get_context_pack(db, registered_repo.id, "a.py", "f").found


def test_a_class_pack_includes_calls_made_by_its_methods(client, make_github_token, registered_repo, db):
    cls = facts("cart.py", symbols=[sym("Cart", kind="class"), sym("Cart.total"), sym("helper")],
                imports=[], calls=[call("Cart.total", "helper")])
    build(Run_(client, make_github_token), [cls])
    db.expire_all()
    pack = get_context_pack(db, registered_repo.id, "cart.py", "Cart")
    assert [c["symbol"] for c in pack.callees] == ["helper"]


def alice(make_token, repo):
    from tests.test_context import alice_headers
    return alice_headers(make_token, repo)


def test_preview_endpoint(client, make_token, built):
    url = f"/v1/repos/{built.id}/context/pack"
    response = client.get(url, params={"file": "lib.py", "function": "price"},
                          headers=alice(make_token, built))
    assert response.status_code == 200
    body = response.json()
    assert body["pack"]["target"]["symbol"] == "price" and "Called by" in body["text"]
    missing = client.get(url, params={"file": "lib.py", "function": "zzz"},
                         headers=alice(make_token, built))
    assert missing.status_code == 404


def test_preview_endpoint_hides_other_users_repos(client, make_token, built):
    stranger = bearer(make_token(login="bob"))
    response = client.get(f"/v1/repos/{built.id}/context/pack",
                          params={"file": "lib.py", "function": "price"}, headers=stranger)
    assert response.status_code == 404
    unsigned = client.get(f"/v1/repos/{built.id}/context/pack",
                          params={"file": "lib.py", "function": "price"})
    assert unsigned.status_code == 401
