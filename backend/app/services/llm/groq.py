"""Groq's chat API (OpenAI-compatible). One fixed address, nothing the user types is ever used in it."""

import logging

import httpx

log = logging.getLogger("prsentinel.llm")

URL = "https://api.groq.com/openai/v1/chat/completions"


def _model_options(model: str) -> dict:
    """Settings that keep "thinking" models short and return only the final answer.

    Groq's reasoning models think before they answer. Their thinking can use up a small token
    limit, so it is kept low and left out of the reply (see console.groq.com/docs/reasoning).
    """
    if model.startswith("openai/gpt-oss"):
        return {"reasoning_effort": "low", "include_reasoning": False}
    if model.startswith("qwen/qwen3"):
        return {"reasoning_effort": "none", "reasoning_format": "hidden"}
    return {}


def _error(kind: str, message: str):
    # Imported here to avoid a circular import; the error type lives in the package root.
    from app.services.llm import LLMError

    return LLMError(kind, message)


def _reason(response) -> str:
    """Groq's own explanation of a refusal (never contains our key), cut short, for the server log."""
    try:
        message = response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError, AttributeError):
        return "no explanation in the answer"
    return " ".join(str(message).split())[:300]


def chat(*, api_key: str, model: str, messages: list[dict], max_tokens: int, timeout: float) -> dict:
    """Returns {"text", "input_tokens", "output_tokens"}. Never puts the key or Groq's body in an error."""
    try:
        response = httpx.post(
            URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": messages,
                "max_completion_tokens": max_tokens,
                "temperature": 0.2,
                **_model_options(model),
            },
            timeout=timeout,
        )
    except httpx.HTTPError:
        raise _error("unavailable", "Could not reach the AI service. Try again.") from None

    if response.status_code == 429:
        raise _error("rate_limited", "The AI service is busy. Try again in a minute.")
    if response.status_code in (401, 403):
        raise _error("unavailable", "The AI service did not accept this server's key.")
    if response.status_code != 200:
        log.warning("groq refused the request: status=%s model=%s reason=%s",
                    response.status_code, model, _reason(response))
        raise _error("unavailable", "The AI service returned an unexpected answer. Try again.")

    try:
        data = response.json()
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage") or {}
        if not isinstance(text, str):
            raise TypeError("content is not text")
        return {
            "text": text,
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
        }
    except (ValueError, KeyError, IndexError, TypeError):
        raise _error("bad_response", "The AI service sent an answer we could not read.") from None
