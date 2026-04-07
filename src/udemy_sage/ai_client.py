"""Multi-provider AI client with retry and exponential backoff."""

from __future__ import annotations

import errno
import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any

from udemy_sage.config import COST_PER_M_TOKENS

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are an expert educator. Given a lesson transcript from an online course, \
generate detailed study notes in Markdown. Use the following structure exactly:

## Summary
A concise 2-3 sentence overview of the lesson content.

## Key concepts
- Bullet points of the main concepts taught.

## Code examples
If the lesson includes code, provide clean, annotated examples. \
If no code is relevant, write "N/A".

## Insights
Deeper observations, connections to broader topics, or practical tips.

## Questions to reflect on
3-5 thought-provoking questions to reinforce understanding.\
"""

MAX_RETRIES = 4
BACKOFF_BASE = 2.0
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}

# Optional SDK imports are deferred so a minimal install avoids pulling extras.
# pylint: disable=import-outside-toplevel

_INSTALL_OPENAI = "Install the openai extra: pip install 'udemy-sage[openai]'"
_INSTALL_ANTHROPIC = (
    "Install the anthropic extra: pip install 'udemy-sage[anthropic]'"
)
_INSTALL_GEMINI = "Install the gemini extra: pip install 'udemy-sage[gemini]'"


def _http_status_from_exception(exc: BaseException) -> int | None:
    """Best-effort HTTP status from SDK, httpx, or urllib exceptions."""
    for attr in ("status_code", "code"):
        v = getattr(exc, attr, None)
        if isinstance(v, int) and 100 <= v <= 599:
            return v
    v = getattr(exc, "status", None)
    if isinstance(v, int) and 100 <= v <= 599:
        return v
    response = getattr(exc, "response", None)
    if response is not None:
        sc = getattr(response, "status_code", None)
        if isinstance(sc, int) and 100 <= sc <= 599:
            return sc
    return None


def _is_retryable_error(  # pylint: disable=too-many-return-statements
    exc: BaseException,
) -> bool:
    """Return True if retrying may succeed (limits, 5xx, timeouts, I/O)."""
    status = _http_status_from_exception(exc)
    if status is not None and status in RETRYABLE_STATUSES:
        return True

    if isinstance(exc, OSError):
        if exc.errno in {
            errno.EPIPE,
            errno.ETIMEDOUT,
            errno.ECONNRESET,
            errno.ECONNREFUSED,
            errno.EHOSTUNREACH,
            errno.ENETUNREACH,
        }:
            return True

    try:
        import httpx

        if isinstance(
            exc,
            (
                httpx.TimeoutException,
                httpx.ConnectTimeout,
                httpx.ReadTimeout,
                httpx.WriteTimeout,
                httpx.ConnectError,
                httpx.ReadError,
                httpx.RemoteProtocolError,
            ),
        ):
            return True
    except ImportError:
        pass

    try:
        import openai as openai_sdk

        if isinstance(
            exc,
            (
                openai_sdk.APIConnectionError,
                openai_sdk.APITimeoutError,
                openai_sdk.RateLimitError,
            ),
        ):
            return True
    except ImportError:
        pass

    try:
        import anthropic as anthropic_sdk

        if isinstance(
            exc,
            (
                anthropic_sdk.APIConnectionError,
                anthropic_sdk.APITimeoutError,
                anthropic_sdk.RateLimitError,
                anthropic_sdk.InternalServerError,
            ),
        ):
            return True
    except ImportError:
        pass

    try:
        from google.genai import errors as genai_errors

        if isinstance(exc, genai_errors.ServerError):
            st = _http_status_from_exception(exc)
            if st is not None:
                return st in RETRYABLE_STATUSES
    except ImportError:
        pass

    error_str = str(exc)
    return any(str(s) in error_str for s in RETRYABLE_STATUSES)


class AIError(Exception):
    """Raised when AI generation fails after all retries."""


def build_shared_client(
    provider: str, api_key: str, base_url: str | None = None
) -> Any:
    """Return one shared SDK client per provider; Ollama returns None."""
    if provider == "ollama":
        return None
    if provider == "openai":
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise AIError(_INSTALL_OPENAI) from exc
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        return OpenAI(**kwargs)
    if provider == "openrouter":
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise AIError(_INSTALL_OPENAI) from exc
        return OpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
        )
    if provider == "anthropic":
        try:
            from anthropic import Anthropic
        except ImportError as exc:
            raise AIError(_INSTALL_ANTHROPIC) from exc
        return Anthropic(api_key=api_key)
    if provider == "gemini":
        try:
            from google import genai
        except ImportError as exc:
            raise AIError(_INSTALL_GEMINI) from exc
        return genai.Client(api_key=api_key)
    raise AIError(f"Unknown provider: {provider}")


# pylint: disable-next=too-many-arguments,too-many-positional-arguments
def generate_note(
    provider: str,
    model: str,
    api_key: str,
    transcript: str,
    lesson_title: str,
    base_url: str | None = None,
    client: Any | None = None,
) -> tuple[str, int]:
    """Generate a note from a transcript; returns (markdown, token_count)."""
    user_prompt = f"Lesson: {lesson_title}\n\nTranscript:\n{transcript}"

    adapters: dict[str, Any] = {
        "openai": _call_openai,
        "anthropic": _call_anthropic,
        "gemini": _call_gemini,
        "openrouter": _call_openrouter,
        "ollama": _call_ollama,
    }

    adapter = adapters.get(provider)
    if not adapter:
        raise AIError(f"Unknown provider: {provider}")

    return _with_retry(
        lambda: adapter(model, api_key, user_prompt, base_url, client),
    )


def estimate_cost(provider: str, total_tokens: int) -> float:
    """Estimate cost in USD for the given token count."""
    rate = COST_PER_M_TOKENS.get(provider, 0.0)
    return (total_tokens / 1_000_000) * rate


def _with_retry(fn: Any) -> tuple[str, int]:
    """Execute fn with exponential backoff on retryable errors."""
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            return fn()
        except AIError:
            raise
        except Exception as e:  # pylint: disable=broad-exception-caught
            last_error = e
            is_retryable = _is_retryable_error(e)
            if not is_retryable or attempt == MAX_RETRIES:
                raise AIError(
                    f"Failed after {attempt + 1} attempts: {e}",
                ) from e
            wait = BACKOFF_BASE ** attempt
            logger.warning(
                "Attempt %d failed (%s), retrying in %.1fs",
                attempt + 1,
                e,
                wait,
            )
            time.sleep(wait)

    raise AIError(f"Failed after all retries: {last_error}")


# ---------------------------------------------------------------------------
# Provider adapters
# ---------------------------------------------------------------------------


def _call_openai(
    model: str,
    api_key: str,
    user_prompt: str,
    base_url: str | None,
    client: Any | None,
) -> tuple[str, int]:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise AIError(_INSTALL_OPENAI) from exc

    if client is None:
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        client = OpenAI(**kwargs)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )
    content = resp.choices[0].message.content or ""
    tokens = resp.usage.total_tokens if resp.usage else 0
    return content, tokens


def _call_anthropic(
    model: str,
    api_key: str,
    user_prompt: str,
    _base_url: str | None,
    client: Any | None,
) -> tuple[str, int]:
    try:
        from anthropic import Anthropic
        from anthropic.types import TextBlock
    except ImportError as exc:
        raise AIError(_INSTALL_ANTHROPIC) from exc

    if client is None:
        client = Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=model,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    if not resp.content:
        content = ""
    else:
        block0 = resp.content[0]
        content = block0.text if isinstance(block0, TextBlock) else ""
    tokens = resp.usage.input_tokens + resp.usage.output_tokens
    return content, tokens


def _call_gemini(
    model: str,
    api_key: str,
    user_prompt: str,
    _base_url: str | None,
    client: Any | None,
) -> tuple[str, int]:
    try:
        from google import genai
    except ImportError as exc:
        raise AIError(_INSTALL_GEMINI) from exc

    if client is None:
        client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(
        model=model,
        contents=f"{SYSTEM_PROMPT}\n\n{user_prompt}",
    )
    content = resp.text or ""
    raw = (
        resp.usage_metadata.total_token_count
        if resp.usage_metadata
        else None
    )
    tokens = int(raw or 0)
    return content, tokens


def _call_openrouter(
    model: str,
    api_key: str,
    user_prompt: str,
    _base_url: str | None,
    client: Any | None,
) -> tuple[str, int]:
    """OpenRouter uses the OpenAI-compatible API."""
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise AIError(_INSTALL_OPENAI) from exc

    if client is None:
        client = OpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
        )
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )
    content = resp.choices[0].message.content or ""
    tokens = resp.usage.total_tokens if resp.usage else 0
    return content, tokens


def _call_ollama(
    model: str,
    _api_key: str,
    user_prompt: str,
    base_url: str | None,
    _client: Any | None,
) -> tuple[str, int]:
    """Ollama via plain HTTP — no extra dependencies required."""
    url = (base_url or "http://localhost:11434") + "/api/chat"
    payload = json.dumps({
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    }).encode()

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read())
    except urllib.error.URLError as e:
        raise AIError(f"Ollama request failed: {e}") from e

    content = data.get("message", {}).get("content", "")
    tokens = data.get("eval_count", 0) + data.get("prompt_eval_count", 0)
    return content, tokens
