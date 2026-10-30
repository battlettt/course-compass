"""Pluggable OpenAI-compatible chat-completions client (bring your own key).

Configuration comes only from environment variables:
  COURSE_COMPASS_API_KEY   required for LLM mode; never stored or logged
  COURSE_COMPASS_MODEL     default: gpt-4o-mini
  COURSE_COMPASS_BASE_URL  default: https://api.openai.com/v1
"""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_TIMEOUT = 30.0
MAX_RESPONSE_BYTES = 1_000_000


class LLMError(Exception):
    """Base class for every LLM client failure."""


class MissingKeyError(LLMError):
    pass


class LLMHTTPError(LLMError):
    pass


class LLMTimeoutError(LLMError):
    pass


class LLMResponseError(LLMError):
    pass


@dataclass
class LLMConfig:
    api_key: str
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    timeout: float = DEFAULT_TIMEOUT

    def __repr__(self) -> str:  # never leak the key into logs or tracebacks
        return f"LLMConfig(model={self.model!r}, base_url={self.base_url!r}, api_key='***')"

    @classmethod
    def from_env(cls, env: dict | None = None) -> "LLMConfig":
        env = os.environ if env is None else env
        key = env.get("COURSE_COMPASS_API_KEY", "").strip()
        if not key:
            raise MissingKeyError(
                "No API key found. Set COURSE_COMPASS_API_KEY to use LLM mode, "
                "or use --local for the extractive fallback.")
        base_url = env.get("COURSE_COMPASS_BASE_URL", "").strip() or DEFAULT_BASE_URL
        check_base_url(base_url)
        model = env.get("COURSE_COMPASS_MODEL", "").strip() or DEFAULT_MODEL
        return cls(api_key=key, model=model, base_url=base_url.rstrip("/"))


def key_available(env: dict | None = None) -> bool:
    env = os.environ if env is None else env
    return bool(env.get("COURSE_COMPASS_API_KEY", "").strip())


def check_base_url(url: str) -> None:
    """Require https, except plain http to a local server (e.g. a local model)."""
    parsed = urlparse(url)
    if parsed.scheme == "https" and parsed.netloc:
        return
    if parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1"):
        return
    raise LLMError(f"Refusing base URL {url!r}: use https (or http only for localhost).")


Opener = Callable[..., object]


class LLMClient:
    """Minimal chat-completions client. `opener` is injectable for tests."""

    label = "LLM"

    def __init__(self, config: LLMConfig, opener: Opener | None = None):
        self.config = config
        self._open = opener or urllib.request.urlopen

    def chat(self, messages: list[dict], temperature: float = 0.0) -> str:
        payload = json.dumps({
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature,
        }).encode("utf-8")
        request = urllib.request.Request(
            f"{self.config.base_url}/chat/completions",
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.api_key}",
            },
        )
        try:
            with self._open(request, timeout=self.config.timeout) as response:
                status = getattr(response, "status", 200)
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise LLMHTTPError(f"LLM request failed with HTTP {exc.code}.") from None
        except (socket.timeout, TimeoutError):
            raise LLMTimeoutError(
                f"LLM request timed out after {self.config.timeout:.0f}s.") from None
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                raise LLMTimeoutError(
                    f"LLM request timed out after {self.config.timeout:.0f}s.") from None
            raise LLMHTTPError(f"Could not reach LLM endpoint: {exc.reason}") from None
        if status != 200:
            raise LLMHTTPError(f"LLM request failed with HTTP {status}.")
        if len(body) > MAX_RESPONSE_BYTES:
            raise LLMResponseError("LLM response too large.")
        return parse_chat_response(body)


def parse_chat_response(body: bytes) -> str:
    try:
        data = json.loads(body.decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise LLMResponseError("LLM returned a response that is not valid chat-completions JSON.") from None
    if not isinstance(content, str) or not content.strip():
        raise LLMResponseError("LLM returned an empty message.")
    return content
