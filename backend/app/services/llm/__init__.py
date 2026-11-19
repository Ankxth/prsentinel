"""LLM router (feature F): which provider and model a call uses, retries, the fallback, the breaker.

    result = complete("file_summary", messages, budget_key="summaries", max_tokens=250)

`budget_key` names which allowance the call is paid from. Each one has its own key and model, so a
busy feature cannot starve another:
    "default"    Groq with GROQ_API_KEY, then Gemini with GEMINI_API_KEY if Groq keeps failing
                 (test generation, vulnerability checks; later features)
    "summaries"  Groq with GROQ_API_KEY_SUMMARIES only. No fallback: not to the default key and not
                 to Gemini, because summaries are optional and must fail softly.

What happens on a call:
  1. Each provider that has a key is tried in order. A provider without a key is skipped, not an error.
  2. A busy answer (429), a server error (5xx) or a network problem is retried after the waits in
     `LLM_RETRY_WAITS` (1 s, then 3 s) while the total time stays within `LLM_TOTAL_BUDGET_SECONDS`.
     A refusal that retrying cannot fix (wrong key, a request the provider rejects) is not retried.
  3. If a provider still fails, the next one is tried.
  4. Circuit breaker: after `LLM_BREAKER_FAILURES` failed calls in a row a provider is skipped for
     `LLM_BREAKER_SECONDS`, so nobody waits through retries on a provider that is known to be down.
     It is kept in memory per provider and budget key, so a restart clears it.

Calls are logged (purpose, provider, tokens, time), never the prompt or the key. The result says
which provider and model really answered.

Rules for every caller: repository code is DATA, never instructions, and the server's keys are never
shown to anyone.
"""

import logging
import time
from dataclasses import dataclass

from app.core.config import get_settings
from app.services.llm import gemini, groq

log = logging.getLogger("prsentinel.llm")

BUDGET_KEYS = ("default", "summaries")

# Replaced in tests, so retries never really wait and the clock can be moved.
_sleep = time.sleep
_now = time.monotonic


class LLMError(Exception):
    """kind: not_configured | rate_limited | unavailable | bad_response

    `retryable` is True when trying the same provider again may work (busy, server error, network).
    """

    def __init__(self, kind: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable


@dataclass(frozen=True)
class LLMResult:
    text: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


@dataclass(frozen=True)
class _Provider:
    name: str
    key: str
    model: str


_MODULES = {"groq": groq, "gemini": gemini}

# (provider name, budget key) -> {"failures": int, "open_until": float}
_breakers: dict = {}


def reset_breakers() -> None:
    _breakers.clear()


def _providers(budget_key: str) -> list[_Provider]:
    """The providers to try, in order, that have a key. Raises ValueError for an unknown budget key."""
    settings = get_settings()
    if budget_key == "summaries":
        chain = [("groq", settings.groq_api_key_summaries, settings.groq_model_summaries)]
    elif budget_key == "default":
        chain = [
            ("groq", settings.groq_api_key, settings.groq_model),
            ("gemini", settings.gemini_api_key, settings.gemini_model),
        ]
    else:
        raise ValueError(f"unknown budget_key: {budget_key}")
    found = []
    for name, key, model in chain:
        value = key.get_secret_value().strip() if key is not None else ""
        if value:
            found.append(_Provider(name, value, model))
    return found


def _breaker_is_open(provider: _Provider, budget_key: str) -> bool:
    state = _breakers.get((provider.name, budget_key))
    return bool(state and state["open_until"] > _now())


def _breaker_success(provider: _Provider, budget_key: str) -> None:
    _breakers.pop((provider.name, budget_key), None)


def _breaker_failure(provider: _Provider, budget_key: str, error: "LLMError") -> None:
    # An unreadable answer says something about one request, not about the provider being down.
    if error.kind == "bad_response":
        return
    settings = get_settings()
    state = _breakers.setdefault((provider.name, budget_key), {"failures": 0, "open_until": 0.0})
    state["failures"] += 1
    if state["failures"] >= settings.llm_breaker_failures:
        # The count is kept, so after the pause one failed call closes the door again at once.
        state["open_until"] = _now() + settings.llm_breaker_seconds
        log.warning("llm breaker open provider=%s budget=%s for %ss",
                    provider.name, budget_key, settings.llm_breaker_seconds)


def _call_with_retries(provider: _Provider, messages, max_tokens, started: float) -> dict:
    settings = get_settings()
    waits = settings.llm_retry_waits
    attempt = 0
    while True:
        try:
            return _MODULES[provider.name].chat(
                api_key=provider.key,
                model=provider.model,
                messages=messages,
                max_tokens=max_tokens,
                timeout=settings.llm_timeout_seconds,
            )
        except LLMError as error:
            if not error.retryable or attempt >= len(waits):
                raise
            wait = waits[attempt]
            # Do not start another try that could run past the total budget.
            if (_now() - started) + wait + settings.llm_timeout_seconds > settings.llm_total_budget_seconds:
                raise
            log.warning("llm retry provider=%s kind=%s attempt=%s wait=%ss",
                        provider.name, error.kind, attempt + 1, wait)
            _sleep(wait)
            attempt += 1


def complete(
    purpose: str,
    messages: list[dict],
    budget_key: str = "default",
    max_tokens: int = 300,
) -> LLMResult:
    """Ask the model. Raises LLMError; the message is safe to show to the user."""
    providers = _providers(budget_key)
    if not providers:
        raise LLMError("not_configured", "This feature is not switched on for this server.")

    started = _now()
    last_error: LLMError | None = None
    for provider in providers:
        if _breaker_is_open(provider, budget_key):
            log.warning("llm skipped provider=%s budget=%s (breaker open)", provider.name, budget_key)
            last_error = last_error or LLMError(
                "unavailable", "The AI service is temporarily unavailable. Try again in a minute."
            )
            continue
        try:
            answer = _call_with_retries(provider, messages, max_tokens, started)
        except LLMError as error:
            _breaker_failure(provider, budget_key, error)
            log.warning("llm provider failed purpose=%s provider=%s kind=%s",
                        purpose, provider.name, error.kind)
            last_error = error
            continue
        _breaker_success(provider, budget_key)
        latency_ms = int((_now() - started) * 1000)
        log.info(
            "llm call purpose=%s provider=%s model=%s in=%s out=%s ms=%s",
            purpose, provider.name, provider.model,
            answer["input_tokens"], answer["output_tokens"], latency_ms,
        )
        return LLMResult(
            text=answer["text"],
            provider=provider.name,
            model=provider.model,
            input_tokens=answer["input_tokens"],
            output_tokens=answer["output_tokens"],
            latency_ms=latency_ms,
        )
    raise last_error


def get_llm():
    """FastAPI dependency. Tests replace it so no real model is ever called."""
    return complete
