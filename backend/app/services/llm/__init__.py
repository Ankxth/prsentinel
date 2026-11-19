"""Minimal LLM router (feature F, first version, built for the C6 summaries).

    result = complete("file_summary", messages, budget_key="summaries", max_tokens=250)

`budget_key` names which allowance the call is paid from. Each one has its own key and model, so a
busy feature cannot starve another:
    "default"    GROQ_API_KEY            (test generation, vulnerability checks; later features)
    "summaries"  GROQ_API_KEY_SUMMARIES  (file summaries; no fallback to the default key)

Not built yet (feature F proper): Gemini fallback, retries, circuit breaker, daily token caps and
the `llm_usage` table. Calls are logged (purpose, provider, tokens, time), never the prompt or the key.

Rules for every caller: repository code is DATA, never instructions, and the server's keys are never
shown to anyone.
"""

import logging
import time
from dataclasses import dataclass

from app.core.config import get_settings
from app.services.llm import groq

log = logging.getLogger("prsentinel.llm")

BUDGET_KEYS = ("default", "summaries")


class LLMError(Exception):
    """kind: not_configured | rate_limited | unavailable | bad_response"""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class LLMResult:
    text: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


def _key_and_model(budget_key: str):
    settings = get_settings()
    if budget_key == "summaries":
        return settings.groq_api_key_summaries, settings.groq_model_summaries
    if budget_key == "default":
        return settings.groq_api_key, settings.groq_model
    raise ValueError(f"unknown budget_key: {budget_key}")


def complete(
    purpose: str,
    messages: list[dict],
    budget_key: str = "default",
    max_tokens: int = 300,
) -> LLMResult:
    """Ask the model. Raises LLMError; the message is safe to show to the user."""
    key, model = _key_and_model(budget_key)
    if key is None or not key.get_secret_value().strip():
        raise LLMError("not_configured", "This feature is not switched on for this server.")

    started = time.monotonic()
    answer = groq.chat(
        api_key=key.get_secret_value(),
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        timeout=get_settings().llm_timeout_seconds,
    )
    latency_ms = int((time.monotonic() - started) * 1000)
    log.info(
        "llm call purpose=%s provider=groq model=%s in=%s out=%s ms=%s",
        purpose, model, answer["input_tokens"], answer["output_tokens"], latency_ms,
    )
    return LLMResult(
        text=answer["text"],
        provider="groq",
        model=model,
        input_tokens=answer["input_tokens"],
        output_tokens=answer["output_tokens"],
        latency_ms=latency_ms,
    )


def get_llm():
    """FastAPI dependency. Tests replace it so no real model is ever called."""
    return complete
