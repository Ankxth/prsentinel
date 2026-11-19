"""Groq's chat API (OpenAI-compatible). One fixed address, nothing the user types is ever used in it."""

import httpx

URL = "https://api.groq.com/openai/v1/chat/completions"


def _error(kind: str, message: str):
    # Imported here to avoid a circular import; the error type lives in the package root.
    from app.services.llm import LLMError

    return LLMError(kind, message)


def chat(*, api_key: str, model: str, messages: list[dict], max_tokens: int, timeout: float) -> dict:
    """Returns {"text", "input_tokens", "output_tokens"}. Never puts the key or Groq's body in an error."""
    try:
        response = httpx.post(
            URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.2,
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
