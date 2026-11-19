"""The minimal LLM router: which key and model a call uses, and how failures are reported."""
import httpx
import pytest

from app.core.config import get_settings
from app.services import llm

MESSAGES = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
GOOD = {
    "choices": [{"message": {"content": "A short answer."}}],
    "usage": {"prompt_tokens": 12, "completion_tokens": 5},
}


class FakeResponse:
    def __init__(self, status_code=200, body=GOOD, broken=False):
        self.status_code = status_code
        self._body = body
        self._broken = broken

    def json(self):
        if self._broken:
            raise ValueError("not json")
        return self._body


@pytest.fixture
def groq(monkeypatch):
    """Replaces the HTTP call; `calls` records every request that would have been sent."""

    class Spy:
        calls: list = []
        response = FakeResponse()
        raises: Exception | None = None

    spy = Spy()
    spy.calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        spy.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        if spy.raises:
            raise spy.raises
        return spy.response

    monkeypatch.setattr("app.services.llm.groq.httpx.post", fake_post)
    return spy


def set_keys(monkeypatch, default="", summaries=""):
    monkeypatch.setenv("GROQ_API_KEY", default)
    monkeypatch.setenv("GROQ_API_KEY_SUMMARIES", summaries)
    get_settings.cache_clear()


def test_summaries_use_their_own_key_and_model(monkeypatch, groq):
    set_keys(monkeypatch, default="main-key", summaries="summary-key")
    result = llm.complete("file_summary", MESSAGES, budget_key="summaries", max_tokens=250)
    call = groq.calls[0]
    assert call["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer summary-key"
    assert call["json"]["model"] == "llama-3.1-8b-instant"
    assert call["json"]["messages"] == MESSAGES and call["json"]["max_tokens"] == 250
    assert call["timeout"] == 20.0
    assert (result.text, result.provider, result.input_tokens, result.output_tokens) == (
        "A short answer.", "groq", 12, 5)


def test_default_budget_uses_the_main_key(monkeypatch, groq):
    set_keys(monkeypatch, default="main-key", summaries="summary-key")
    llm.complete("anything", MESSAGES)
    assert groq.calls[0]["headers"]["Authorization"] == "Bearer main-key"


def test_summaries_never_fall_back_to_the_main_key(monkeypatch, groq):
    set_keys(monkeypatch, default="main-key", summaries="")
    with pytest.raises(llm.LLMError) as error:
        llm.complete("file_summary", MESSAGES, budget_key="summaries")
    assert error.value.kind == "not_configured"
    assert groq.calls == []  # nothing was sent anywhere


def test_a_blank_key_counts_as_not_configured(monkeypatch, groq):
    set_keys(monkeypatch, summaries="   ")
    with pytest.raises(llm.LLMError) as error:
        llm.complete("file_summary", MESSAGES, budget_key="summaries")
    assert error.value.kind == "not_configured" and groq.calls == []


def test_unknown_budget_key_is_a_programming_error(monkeypatch, groq):
    set_keys(monkeypatch, default="main-key")
    with pytest.raises(ValueError):
        llm.complete("x", MESSAGES, budget_key="nope")


@pytest.mark.parametrize(
    "response, kind",
    [
        (FakeResponse(429), "rate_limited"),
        (FakeResponse(401), "unavailable"),
        (FakeResponse(403), "unavailable"),
        (FakeResponse(500), "unavailable"),
        (FakeResponse(200, broken=True), "bad_response"),
        (FakeResponse(200, body={"choices": []}), "bad_response"),
        (FakeResponse(200, body={"choices": [{"message": {"content": None}}]}), "bad_response"),
    ],
)
def test_failures_are_reported_without_leaking(monkeypatch, groq, response, kind):
    set_keys(monkeypatch, summaries="super-secret-key")
    groq.response = response
    with pytest.raises(llm.LLMError) as error:
        llm.complete("file_summary", MESSAGES, budget_key="summaries")
    assert error.value.kind == kind
    assert "super-secret-key" not in str(error.value)


def test_network_trouble_is_reported_as_unavailable(monkeypatch, groq):
    set_keys(monkeypatch, summaries="summary-key")
    groq.raises = httpx.ConnectTimeout("slow")
    with pytest.raises(llm.LLMError) as error:
        llm.complete("file_summary", MESSAGES, budget_key="summaries")
    assert error.value.kind == "unavailable"


def test_missing_usage_numbers_are_zero(monkeypatch, groq):
    set_keys(monkeypatch, summaries="summary-key")
    groq.response = FakeResponse(200, body={"choices": [{"message": {"content": "ok"}}]})
    result = llm.complete("file_summary", MESSAGES, budget_key="summaries")
    assert (result.text, result.input_tokens, result.output_tokens) == ("ok", 0, 0)
