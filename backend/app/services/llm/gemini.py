"""Google's Gemini API (the fallback provider). One fixed address; only the model name comes from
settings and it is checked before it is used. The key goes in a header, never in the address, so it
cannot end up in a log line or an error text."""

import logging
import re

import httpx

log = logging.getLogger("prsentinel.llm")

BASE = "https://generativelanguage.googleapis.com/v1beta/models/"
_MODEL_NAME = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


def _error(kind: str, message: str, retryable: bool = False):
    from app.services.llm import LLMError

    return LLMError(kind, message, retryable=retryable)


def _reason(response) -> str:
    """Google's own explanation of a refusal (never contains our key), cut short, for the log."""
    try:
        message = response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError, AttributeError):
        return "no explanation in the answer"
    return " ".join(str(message).split())[:300]


def _generation_config(model: str, max_tokens: int) -> dict:
    config: dict = {"maxOutputTokens": max_tokens, "temperature": 0.2}
    # Gemini 2.5 Flash thinks by default and its thinking uses up the output limit, which can leave
    # an empty answer. A budget of 0 switches it off (2.5 Pro cannot switch it off, so it is left alone).
    if model.startswith("gemini-2.5-flash"):
        config["thinkingConfig"] = {"thinkingBudget": 0}
    return config


def _body(model: str, messages: list[dict], max_tokens: int) -> dict:
    system = "\n\n".join(m["content"] for m in messages if m.get("role") == "system")
    contents = [
        {"role": "model" if m.get("role") == "assistant" else "user", "parts": [{"text": m["content"]}]}
        for m in messages
        if m.get("role") != "system"
    ]
    body: dict = {"contents": contents, "generationConfig": _generation_config(model, max_tokens)}
    if system:
        body["system_instruction"] = {"parts": [{"text": system}]}
    return body


def chat(*, api_key: str, model: str, messages: list[dict], max_tokens: int, timeout: float) -> dict:
    """Returns {"text", "input_tokens", "output_tokens"}. Never puts the key or Google's body in an error."""
    if not _MODEL_NAME.match(model):
        raise _error("unavailable", "The AI service is not set up correctly on this server.")
    try:
        response = httpx.post(
            f"{BASE}{model}:generateContent",
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=_body(model, messages, max_tokens),
            timeout=timeout,
        )
    except httpx.HTTPError:
        raise _error("unavailable", "Could not reach the AI service. Try again.", retryable=True) from None

    if response.status_code == 429:
        raise _error("rate_limited", "The AI service is busy. Try again in a minute.", retryable=True)
    if response.status_code in (401, 403):
        log.warning("gemini did not accept the key: status=%s reason=%s",
                    response.status_code, _reason(response))
        raise _error("unavailable", "The AI service did not accept this server's key.")
    if response.status_code != 200:
        log.warning("gemini refused the request: status=%s model=%s reason=%s",
                    response.status_code, model, _reason(response))
        raise _error("unavailable", "The AI service returned an unexpected answer. Try again.",
                     retryable=response.status_code >= 500)

    try:
        data = response.json()
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(p["text"] for p in parts if isinstance(p.get("text"), str) and not p.get("thought"))
        usage = data.get("usageMetadata") or {}
        if not text.strip():
            raise ValueError("empty answer")
        return {
            "text": text,
            "input_tokens": int(usage.get("promptTokenCount") or 0),
            # Thinking tokens are paid from the same allowance, so they are counted too.
            "output_tokens": int(usage.get("candidatesTokenCount") or 0)
            + int(usage.get("thoughtsTokenCount") or 0),
        }
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        raise _error("bad_response", "The AI service sent an answer we could not read.") from None
