"""Measure retrieval quality on a small hand-built question set."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .answering import DEFAULT_THRESHOLD
from .chunking import Chunk
from .retrieval import TfidfIndex


@dataclass
class EvalResult:
    max_words: int
    overlap: int
    chunks: int
    answerable: int
    top1: int
    top3: int
    false_refusals: int
    unanswerable: int
    correct_refusals: int

    @property
    def top1_rate(self) -> float:
        return self.top1 / self.answerable if self.answerable else 0.0

    @property
    def top3_rate(self) -> float:
        return self.top3 / self.answerable if self.answerable else 0.0


def load_questions(path: str | Path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("question file must hold a JSON list")
    return data


def _matches(chunk: Chunk, case: dict) -> bool:
    return chunk.topic == case["topic"] and case["phrase"].lower() in chunk.text.lower()


def evaluate(chunks: list[Chunk], questions: list[dict], max_words: int, overlap: int,
             threshold: float = DEFAULT_THRESHOLD) -> EvalResult:
    """Answerable cases have `topic` + `phrase` (text the right chunk contains);
    unanswerable cases have `"answerable": false` and should be refused."""
    index = TfidfIndex(chunks)
    result = EvalResult(max_words, overlap, len(chunks), 0, 0, 0, 0, 0, 0)
    for case in questions:
        hits = index.search(case["question"], k=3)
        refused = not hits or hits[0].score < threshold
        if case.get("answerable", True):
            result.answerable += 1
            result.top1 += bool(hits) and _matches(hits[0].chunk, case)
            result.top3 += any(_matches(hit.chunk, case) for hit in hits)
            result.false_refusals += refused
        else:
            result.unanswerable += 1
            result.correct_refusals += refused
    return result
