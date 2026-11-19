"""The LLM router (F): retries, the Gemini fallback, the circuit breaker, and the Gemini request."""
import httpx
import pytest

from app.core.config import get_settings
from app.services import llm

MESSAGES = [
    {"role": "system", "content": "be brief"},
    {"role": "user", "content": "question"},
]
GROQ_OK = {
    "choices": [{"message": {"content": "groq answer"}}],
    "usage": {"prompt_tokens": 12, "completion_tokens": 5},
}
GEMINI_OK = {
    "candidates": [{"content": {"parts": [{"text": "gemini answer"}]}}],
    "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 4, "thoughtsTokenCount": 2},
}


class FakeResponse:
    def __init__(self, status_code=200, body=None, broken=False):
        self.status_code = status_code
        self._body = body
        self._broken = broken

    def json(self):
        if self._broken:
            raise ValueError("not json")
        return self._body


class Net:
    """Stands in for the internet. `script[name]` is the list of answers that provider gives, in
    order (the last one repeats). An Exception in the list is raised instead of returned."""

    def __init__(self):
        self.calls = []
        self.script = {"groq": [FakeResponse(200, GROQ_OK)], "gemini": [FakeResponse(200, GEMINI_OK)]}

    def post(self, url, headers=None, json=None, timeout=None):
        name = "gemini" if "generativelanguage" in url else "groq"
        self.calls.append({"name": name, "url": url, "headers": headers, "json": json,
                           "timeout": timeout})
        queue = self.script[name]
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item

    def count(self, name):
        return sum(1 for call in self.calls if call["name"] == name)


@pytest.fixture
def net(monkeypatch):
    fake = Net()
    monkeypatch.setattr(httpx, "post", fake.post)
    return fake


@pytest.fixture
def sleeps(monkeypatch):
    waited = []
    monkeypatch.setattr(llm, "_sleep", waited.append)
    return waited


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(llm, "_now", lambda: now[0])
    return now


def keys(monkeypatch, groq="", gemini="", summaries=""):
    monkeypatch.setenv("GROQ_API_KEY", groq)
    monkeypatch.setenv("GEMINI_API_KEY", gemini)
    monkeypatch.setenv("GROQ_API_KEY_SUMMARIES", summaries)
    get_settings.cache_clear()


def busy():
    return FakeResponse(429)


def server_error():
    return FakeResponse(500)


# ---- retries -------------------------------------------------------------------------------


def test_a_busy_answer_is_retried_after_a_short_wait(monkeypatch, net, sleeps):
    keys(monkeypatch, groq="g")
    net.script["groq"] = [busy(), FakeResponse(200, GROQ_OK)]
    result = llm.complete("p", MESSAGES)
    assert result.text == "groq answer" and result.provider == "groq"
    assert net.count("groq") == 2 and sleeps == [1.0]


def test_the_waits_grow_and_retries_stop(monkeypatch, net, sleeps):
    keys(monkeypatch, groq="g")
    net.script["groq"] = [server_error()]
    with pytest.raises(llm.LLMError) as error:
        llm.complete("p", MESSAGES)
    assert error.value.kind == "unavailable"
    assert net.count("groq") == 3 and sleeps == [1.0, 3.0]


def test_a_network_problem_is_retried(monkeypatch, net, sleeps):
    keys(monkeypatch, groq="g")
    net.script["groq"] = [httpx.ConnectTimeout("slow"), FakeResponse(200, GROQ_OK)]
    assert llm.complete("p", MESSAGES).text == "groq answer"
    assert net.count("groq") == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_a_refusal_that_retrying_cannot_fix_is_not_retried(monkeypatch, net, sleeps, status):
    keys(monkeypatch, groq="g")
    net.script["groq"] = [FakeResponse(status, {"error": {"message": "no"}})]
    with pytest.raises(llm.LLMError):
        llm.complete("p", MESSAGES)
    assert net.count("groq") == 1 and sleeps == []


def test_an_unreadable_answer_is_not_retried(monkeypatch, net, sleeps):
    keys(monkeypatch, groq="g")
    net.script["groq"] = [FakeResponse(200, broken=True)]
    with pytest.raises(llm.LLMError) as error:
        llm.complete("p", MESSAGES)
    assert error.value.kind == "bad_response" and net.count("groq") == 1


def test_no_retry_that_could_run_past_the_total_budget(monkeypatch, net, sleeps):
    keys(monkeypatch, groq="g")
    monkeypatch.setenv("LLM_TOTAL_BUDGET_SECONDS", "10")  # shorter than one 20 s attempt
    get_settings.cache_clear()
    net.script["groq"] = [busy()]
    with pytest.raises(llm.LLMError) as error:
        llm.complete("p", MESSAGES)
    assert error.value.kind == "rate_limited" and net.count("groq") == 1 and sleeps == []


# ---- the Gemini fallback ---------------------------------------------------------------------


def test_gemini_answers_when_groq_keeps_failing(monkeypatch, net, sleeps):
    keys(monkeypatch, groq="g", gemini="m")
    net.script["groq"] = [server_error()]
    result = llm.complete("p", MESSAGES)
    assert (result.text, result.provider, result.model) == (
        "gemini answer", "gemini", "gemini-2.5-flash")
    # thinking tokens are paid from the same allowance, so they are counted
    assert (result.input_tokens, result.output_tokens) == (11, 6)
    assert net.count("groq") == 3 and net.count("gemini") == 1


def test_a_wrong_groq_key_goes_straight_to_gemini(monkeypatch, net, sleeps):
    keys(monkeypatch, groq="g", gemini="m")
    net.script["groq"] = [FakeResponse(401)]
    assert llm.complete("p", MESSAGES).provider == "gemini"
    assert net.count("groq") == 1 and sleeps == []


def test_gemini_is_not_called_while_groq_works(monkeypatch, net):
    keys(monkeypatch, groq="g", gemini="m")
    assert llm.complete("p", MESSAGES).provider == "groq"
    assert net.count("gemini") == 0


def test_without_groq_key_gemini_is_used_and_groq_is_never_called(monkeypatch, net):
    keys(monkeypatch, gemini="m")
    assert llm.complete("p", MESSAGES).provider == "gemini"
    assert net.count("groq") == 0


def test_when_every_provider_fails_the_last_error_is_raised(monkeypatch, net, sleeps):
    keys(monkeypatch, groq="g", gemini="m")
    net.script["groq"] = [server_error()]
    net.script["gemini"] = [busy()]
    with pytest.raises(llm.LLMError) as error:
        llm.complete("p", MESSAGES)
    assert error.value.kind == "rate_limited"


def test_no_key_at_all_is_not_configured(monkeypatch, net):
    keys(monkeypatch)
    with pytest.raises(llm.LLMError) as error:
        llm.complete("p", MESSAGES)
    assert error.value.kind == "not_configured" and net.calls == []


def test_summaries_never_use_gemini_or_the_main_key(monkeypatch, net, sleeps):
    keys(monkeypatch, groq="main", gemini="m", summaries="s")
    net.script["groq"] = [server_error()]
    with pytest.raises(llm.LLMError):
        llm.complete("file_summary", MESSAGES, budget_key="summaries")
    assert net.count("gemini") == 0
    assert {c["headers"]["Authorization"] for c in net.calls} == {"Bearer s"}


def test_summaries_with_only_a_gemini_key_are_switched_off(monkeypatch, net):
    keys(monkeypatch, gemini="m")
    with pytest.raises(llm.LLMError) as error:
        llm.complete("file_summary", MESSAGES, budget_key="summaries")
    assert error.value.kind == "not_configured" and net.calls == []


def test_errors_never_contain_a_key(monkeypatch, net, sleeps):
    keys(monkeypatch, groq="groq-secret", gemini="gemini-secret")
    net.script["groq"] = [server_error()]
    net.script["gemini"] = [FakeResponse(500)]
    with pytest.raises(llm.LLMError) as error:
        llm.complete("p", MESSAGES)
    assert "secret" not in str(error.value)


# ---- what is sent to Gemini ------------------------------------------------------------------


def test_the_gemini_request(monkeypatch, net):
    keys(monkeypatch, gemini="gem-key")
    llm.complete("p", MESSAGES + [{"role": "assistant", "content": "earlier"},
                                  {"role": "user", "content": "again"}], max_tokens=777)
    call = net.calls[0]
    assert call["url"] == ("https://generativelanguage.googleapis.com/v1beta/models/"
                           "gemini-2.5-flash:generateContent")
    assert call["headers"]["x-goog-api-key"] == "gem-key"
    assert "gem-key" not in call["url"]  # the key travels in a header only
    body = call["json"]
    assert body["system_instruction"] == {"parts": [{"text": "be brief"}]}
    assert [(c["role"], c["parts"][0]["text"]) for c in body["contents"]] == [
        ("user", "question"), ("model", "earlier"), ("user", "again")]
    assert body["generationConfig"]["maxOutputTokens"] == 777
    assert body["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}
    assert call["timeout"] == 20.0


def test_thinking_is_left_alone_for_models_that_cannot_switch_it_off(monkeypatch, net):
    keys(monkeypatch, gemini="m")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-pro")
    get_settings.cache_clear()
    llm.complete("p", MESSAGES)
    assert "thinkingConfig" not in net.calls[0]["json"]["generationConfig"]


def test_a_strange_model_name_is_never_put_in_the_address(monkeypatch, net):
    keys(monkeypatch, gemini="m")
    monkeypatch.setenv("GEMINI_MODEL", "x/../../evil?y=1")
    get_settings.cache_clear()
    with pytest.raises(llm.LLMError) as error:
        llm.complete("p", MESSAGES)
    assert error.value.kind == "unavailable" and net.calls == []


@pytest.mark.parametrize(
    "body",
    [
        {"candidates": []},
        {"candidates": [{"content": {"parts": [{"text": ""}]}, "finishReason": "MAX_TOKENS"}]},
        {"candidates": [{"finishReason": "SAFETY"}]},
        {"candidates": [{"content": {"parts": [{"text": "hidden", "thought": True}]}}]},
    ],
)
def test_gemini_answers_without_usable_text_are_errors(monkeypatch, net, body):
    keys(monkeypatch, gemini="m")
    net.script["gemini"] = [FakeResponse(200, body)]
    with pytest.raises(llm.LLMError) as error:
        llm.complete("p", MESSAGES)
    assert error.value.kind == "bad_response"


def test_gemini_thought_summaries_are_left_out_of_the_answer(monkeypatch, net):
    keys(monkeypatch, gemini="m")
    body = {"candidates": [{"content": {"parts": [
        {"text": "thinking...", "thought": True}, {"text": "real "}, {"text": "answer"}]}}]}
    net.script["gemini"] = [FakeResponse(200, body)]
    result = llm.complete("p", MESSAGES)
    assert result.text == "real answer" and (result.input_tokens, result.output_tokens) == (0, 0)


# ---- the circuit breaker ---------------------------------------------------------------------


def test_three_failed_calls_in_a_row_open_the_breaker(monkeypatch, net, clock):
    keys(monkeypatch, groq="g")
    net.script["groq"] = [FakeResponse(401)]
    for _ in range(3):
        with pytest.raises(llm.LLMError):
            llm.complete("p", MESSAGES)
    assert net.count("groq") == 3
    with pytest.raises(llm.LLMError) as error:
        llm.complete("p", MESSAGES)
    assert net.count("groq") == 3  # skipped, nothing was sent
    assert error.value.kind == "unavailable" and "temporarily" in str(error.value)


def test_an_open_breaker_sends_the_call_to_the_next_provider_at_once(monkeypatch, net, clock):
    keys(monkeypatch, groq="g", gemini="m")
    net.script["groq"] = [FakeResponse(401)]
    for _ in range(3):
        llm.complete("p", MESSAGES)
    before = net.count("groq")
    assert llm.complete("p", MESSAGES).provider == "gemini"
    assert net.count("groq") == before


def test_after_the_pause_one_try_is_made_and_a_failure_closes_it_again(monkeypatch, net, clock):
    keys(monkeypatch, groq="g")
    net.script["groq"] = [FakeResponse(401)]
    for _ in range(3):
        with pytest.raises(llm.LLMError):
            llm.complete("p", MESSAGES)
    clock[0] += 61
    with pytest.raises(llm.LLMError):
        llm.complete("p", MESSAGES)
    assert net.count("groq") == 4  # one fresh try
    with pytest.raises(llm.LLMError):
        llm.complete("p", MESSAGES)
    assert net.count("groq") == 4  # closed again straight away


def test_a_success_clears_the_count(monkeypatch, net, clock):
    keys(monkeypatch, groq="g")
    net.script["groq"] = [FakeResponse(401), FakeResponse(401), FakeResponse(200, GROQ_OK),
                          FakeResponse(401)]
    for _ in range(2):
        with pytest.raises(llm.LLMError):
            llm.complete("p", MESSAGES)
    assert llm.complete("p", MESSAGES).text == "groq answer"
    for _ in range(2):  # two more failures are not three in a row
        with pytest.raises(llm.LLMError):
            llm.complete("p", MESSAGES)
    assert net.count("groq") == 5


def test_one_budget_key_tripping_does_not_block_another(monkeypatch, net, clock):
    keys(monkeypatch, groq="g", summaries="s")
    net.script["groq"] = [FakeResponse(401)]
    for _ in range(3):
        with pytest.raises(llm.LLMError):
            llm.complete("p", MESSAGES)
    net.script["groq"] = [FakeResponse(200, GROQ_OK)]
    assert llm.complete("file_summary", MESSAGES, budget_key="summaries").text == "groq answer"


def test_an_unreadable_answer_does_not_count_against_the_provider(monkeypatch, net, clock):
    keys(monkeypatch, groq="g")
    net.script["groq"] = [FakeResponse(200, broken=True)]
    for _ in range(6):
        with pytest.raises(llm.LLMError):
            llm.complete("p", MESSAGES)
    assert net.count("groq") == 6  # never skipped
