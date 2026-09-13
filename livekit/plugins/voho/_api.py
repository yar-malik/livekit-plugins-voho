"""The little that both halves of the plugin share: where the API is, how it
is authenticated, and how its errors become LiveKit's.

Kept apart from the STT and TTS modules so a change to the error contract is
one edit. Voho errors are ``{"error": {"code", "message"}}`` with an HTTP
status, and the code is the part worth surfacing: a developer whose agent
stopped talking wants to see ``insufficient_credit`` in the log, not a 402.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import aiohttp

from livekit.agents import APIConnectionError, APIStatusError, APITimeoutError

DEFAULT_BASE_URL = "https://app.voho.ai"


def resolve_api_key(api_key: str | None) -> str:
    key = api_key or os.environ.get("VOHO_API_KEY")
    if not key:
        raise ValueError(
            "Voho API key is required: pass api_key=... or set VOHO_API_KEY. "
            "Create one at https://app.voho.ai/tokens"
        )
    return key


def resolve_base_url(base_url: str | None) -> str:
    return (base_url or os.environ.get("VOHO_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def ws_url(base_url: str, path: str) -> str:
    if base_url.startswith("https://"):
        return "wss://" + base_url[len("https://") :] + path
    if base_url.startswith("http://"):
        return "ws://" + base_url[len("http://") :] + path
    return base_url + path


def headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "User-Agent": "livekit-plugins-voho"}


def error_from_body(status: int, body: bytes | str) -> APIStatusError:
    """A Voho error body, turned into the exception LiveKit retries or reports on."""
    code, message = "http_error", f"HTTP {status}"
    try:
        parsed = json.loads(body if isinstance(body, str) else body.decode("utf-8", "replace"))
        err = parsed.get("error") if isinstance(parsed, dict) else None
        if isinstance(err, dict):
            code = str(err.get("code", code))
            message = str(err.get("message", message))
    except (ValueError, AttributeError):
        pass
    # 4xx other than rate limiting is the caller's to fix; retrying a bad key
    # or an empty balance just spends the retry budget on the same answer.
    retryable = status >= 500 or status == 429
    return APIStatusError(f"voho: {code}: {message}", status_code=status, body=None, retryable=retryable)


def error_from_frame(frame: dict[str, Any]) -> APIStatusError:
    """The ``{"type": "error"}`` frame a Voho socket sends, as an exception."""
    err = frame.get("error") or {}
    code = str(err.get("code", "socket_error"))
    message = str(err.get("message", "The socket reported an error."))
    terminal = code in {"unauthorized", "insufficient_credit", "unknown_voice", "unknown_model", "unknown_format", "invalid_request", "text_too_long"}
    return APIStatusError(f"voho: {code}: {message}", status_code=-1, body=None, retryable=not terminal)


def translate(exc: BaseException) -> BaseException:
    """Anything aiohttp or asyncio raises, as the exception LiveKit expects."""
    if isinstance(exc, (APIStatusError, APIConnectionError, APITimeoutError)):
        return exc
    if isinstance(exc, asyncio.TimeoutError):
        return APITimeoutError()
    if isinstance(exc, aiohttp.ClientResponseError):
        return APIStatusError(f"voho: HTTP {exc.status}: {exc.message}", status_code=exc.status, body=None)
    if isinstance(exc, aiohttp.ClientError):
        return APIConnectionError(f"voho: {exc}")
    return APIConnectionError(f"voho: {exc!r}")
