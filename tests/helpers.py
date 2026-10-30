"""Shared test fixtures: paths, sample chunks, and fake LLM transports."""

from __future__ import annotations

import io
import json
from pathlib import Path

from course_compass.ingest import ingest_paths

ROOT = Path(__file__).resolve().parent.parent
NOTES = ROOT / "examples" / "notes"
QUESTIONS = ROOT / "tests" / "examples" / "questions.json"


def sample_chunks(max_words: int = 80, overlap: int = 20):
    return ingest_paths([str(NOTES)], [ROOT], max_words=max_words, overlap=overlap)


class FakeClient:
    """Stands in for LLMClient: returns canned replies and records every call."""

    label = "LLM"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[list[dict]] = []

    def chat(self, messages, temperature=0.0):
        self.calls.append([dict(m) for m in messages])
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200):
        self.status = status
        self._body = io.BytesIO(body)

    def read(self, size=-1):
        return self._body.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def chat_body(content: str) -> bytes:
    return json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]}).encode()


def fake_opener(response=None, error=None, seen=None):
    """Build an injectable urlopen replacement; `seen` collects (request, timeout)."""
    def opener(request, timeout=None):
        if seen is not None:
            seen.append((request, timeout))
        if error is not None:
            raise error
        return response
    return opener
