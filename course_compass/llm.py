"""LLM providers: an OpenAI-compatible HTTP client and a Claude CLI adapter.

Both expose `chat(messages, temperature) -> str` and raise only `LLMError`
subclasses. Configuration comes only from environment variables.

OpenAI-compatible provider (bring your own key):
  COURSE_COMPASS_API_KEY   required for LLM mode; never stored or logged
  COURSE_COMPASS_MODEL     default: gpt-4o-mini
  COURSE_COMPASS_BASE_URL  default: https://api.openai.com/v1

Claude CLI provider (uses your signed-in Claude Code account, no key):
  COURSE_COMPASS_CLAUDE_MODEL    default: sonnet
  COURSE_COMPASS_CLAUDE_BIN      default: `claude` found on PATH
  COURSE_COMPASS_CLAUDE_TIMEOUT  seconds per call, default: 120
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
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


class ProviderUnavailableError(LLMError):
    """The requested provider cannot run here (not installed, not signed in)."""


class LLMProviderError(LLMError):
    """The provider ran but reported a failure."""


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


# ---------------------------------------------------------------- Claude CLI

DEFAULT_CLAUDE_MODEL = "sonnet"
DEFAULT_CLAUDE_TIMEOUT = 120.0
MAX_ERROR_DETAIL = 300

_SECRET_RES = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)(bearer|token|key|secret|password)([\s:=]+)\S+"),
    re.compile(r"[A-Za-z0-9_\-]{32,}"),
]
_AUTH_HINTS = ("not logged in", "/login", "invalid api key", "authentication", "oauth", "unauthorized",
               "credit balance", "usage limit")


def redact(text: str) -> str:
    """Trim provider output for an error message and mask anything key-shaped."""
    text = " ".join((text or "").split())[:MAX_ERROR_DETAIL]
    for pattern in _SECRET_RES:
        text = pattern.sub(lambda m: (m.group(1) + m.group(2) + "***") if m.re.groups else "***", text)
    return text


@dataclass
class ClaudeCLIConfig:
    binary: str
    model: str = DEFAULT_CLAUDE_MODEL
    timeout: float = DEFAULT_CLAUDE_TIMEOUT

    @classmethod
    def from_env(cls, env: dict | None = None, which: Callable[[str], str | None] = shutil.which
                 ) -> "ClaudeCLIConfig":
        env = os.environ if env is None else env
        wanted = env.get("COURSE_COMPASS_CLAUDE_BIN", "").strip() or "claude"
        binary = which(wanted)
        if not binary:
            raise ProviderUnavailableError(
                f"Claude CLI provider unavailable: {wanted!r} was not found or is not executable. "
                "Install Claude Code and sign in (run `claude` once), or set COURSE_COMPASS_CLAUDE_BIN "
                "to the full path of the claude binary.")
        model = env.get("COURSE_COMPASS_CLAUDE_MODEL", "").strip() or DEFAULT_CLAUDE_MODEL
        raw_timeout = env.get("COURSE_COMPASS_CLAUDE_TIMEOUT", "").strip()
        try:
            timeout = float(raw_timeout) if raw_timeout else DEFAULT_CLAUDE_TIMEOUT
        except ValueError:
            raise LLMError("COURSE_COMPASS_CLAUDE_TIMEOUT must be a number of seconds.") from None
        if timeout <= 0:
            raise LLMError("COURSE_COMPASS_CLAUDE_TIMEOUT must be positive.")
        return cls(binary=binary, model=model, timeout=timeout)


def claude_available(env: dict | None = None) -> bool:
    try:
        ClaudeCLIConfig.from_env(env)
    except LLMError:
        return False
    return True


def serialize_messages(messages: list[dict]) -> tuple[str, str]:
    """Split chat messages into (system prompt, stdin prompt) for `claude -p`.

    System messages become the CLI system prompt. A single user message is
    sent as-is. A multi-turn exchange (e.g. answer + repair request) is sent
    as a role-tagged transcript so the model can see its own earlier reply.
    """
    system = "\n\n".join(m["content"] for m in messages if m.get("role") == "system")
    turns = [m for m in messages if m.get("role") != "system"]
    if not turns or turns[-1].get("role") != "user":
        raise LLMError("The last chat message must come from the user.")
    if len(turns) == 1:
        return system, turns[0]["content"]
    parts = ["The conversation so far is below, one turn per tag. Reply to the final user turn only, "
             "as the assistant."]
    for turn in turns:
        role = "assistant" if turn.get("role") == "assistant" else "user"
        parts.append(f"<turn role=\"{role}\">\n{turn['content']}\n</turn>")
    return system, "\n\n".join(parts)


def _run_subprocess(argv: list[str], stdin: str, timeout: float, env: dict, cwd: str):
    return subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=timeout,
                          env=env, cwd=cwd, check=False)


Runner = Callable[..., object]


class ClaudeCLIClient:
    """Calls the signed-in Claude Code CLI (`claude -p`) once per chat turn.

    `runner(argv, stdin, timeout, env, cwd)` must return an object with
    `returncode`, `stdout` and `stderr` (like `subprocess.CompletedProcess`);
    it is injectable so tests never start a real model.
    """

    def __init__(self, config: ClaudeCLIConfig, runner: Runner | None = None):
        self.config = config
        self._run = runner or _run_subprocess

    @property
    def label(self) -> str:
        return f"Claude CLI: {self.config.model}"

    def argv(self, system: str) -> list[str]:
        argv = [self.config.binary, "-p", "--model", self.config.model, "--output-format", "json",
                "--tools", "", "--no-session-persistence", "--strict-mcp-config",
                "--disable-slash-commands"]
        if system:
            argv += ["--system-prompt", system]
        return argv

    def chat(self, messages: list[dict], temperature: float = 0.0) -> str:
        # `claude -p` has no temperature flag; the argument is accepted for interface parity.
        system, prompt = serialize_messages(messages)
        # The child needs HOME etc. to find the signed-in session, but never our API key.
        env = {k: v for k, v in os.environ.items() if k != "COURSE_COMPASS_API_KEY"}
        try:
            with tempfile.TemporaryDirectory(prefix="course-compass-") as cwd:  # no project CLAUDE.md
                result = self._run(self.argv(system), prompt, self.config.timeout, env, cwd)
        except subprocess.TimeoutExpired:
            raise LLMTimeoutError(f"Claude CLI timed out after {self.config.timeout:.0f}s.") from None
        except FileNotFoundError:
            raise ProviderUnavailableError(
                f"Claude CLI provider unavailable: could not start {self.config.binary!r}.") from None
        except OSError as exc:
            raise LLMProviderError(f"Claude CLI could not be started ({type(exc).__name__}).") from None
        return parse_claude_output(result.returncode, result.stdout or "", result.stderr or "")


def parse_claude_output(returncode: int, stdout: str, stderr: str) -> str:
    if len(stdout) > MAX_RESPONSE_BYTES:
        raise LLMResponseError("Claude CLI response too large.")
    data = None
    try:
        data = json.loads(stdout)
    except ValueError:
        pass
    if not isinstance(data, dict):
        detail = redact(stderr or stdout) or "no output"
        if returncode != 0:
            raise _claude_failure(f"Claude CLI exited with code {returncode}: {detail}")
        raise LLMResponseError("Claude CLI returned output that is not the expected JSON result.")
    result = data.get("result")
    if data.get("is_error") or returncode != 0 or data.get("subtype") not in (None, "success"):
        detail = redact(result if isinstance(result, str) else "") or redact(stderr) or "no detail"
        status = data.get("api_error_status")
        prefix = f"Claude CLI call failed (exit {returncode}" + (f", API status {status}" if status else "") + ")"
        raise _claude_failure(f"{prefix}: {detail}")
    if not isinstance(result, str) or not result.strip():
        raise LLMResponseError("Claude CLI returned an empty message.")
    return result


def _claude_failure(message: str) -> LLMError:
    if any(hint in message.lower() for hint in _AUTH_HINTS):
        return ProviderUnavailableError(
            message + " (Sign in to Claude Code by running `claude` and `/login`, then retry.)")
    return LLMProviderError(message)
