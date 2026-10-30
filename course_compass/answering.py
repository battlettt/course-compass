"""Grounded question answering with citations and an honest refusal."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from . import prompts
from .llm import LLMError
from .retrieval import Hit, TfidfIndex

MAX_QUESTION_CHARS = 500
DEFAULT_THRESHOLD = 0.15
DEFAULT_TOP_K = 4
LOCAL_LABEL = "LOCAL FALLBACK (extractive, no LLM): quoted verbatim from your notes"
LLM_LABEL = "LLM answer, grounded in your notes"

_CITATION_RE = re.compile(r"\[([a-z0-9][a-z0-9\-]*#\d+)\]")
# A sentence ends at . ! ? or a closing citation bracket, followed by a new capitalised sentence,
# so "... the market. [elasticity#6]" keeps its trailing citation.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?\]])\s+(?=[A-Z0-9(\"'])")


def provider_tag(client) -> str:
    """'' for the generic OpenAI-compatible client, ' (Claude CLI: sonnet)' for others."""
    label = getattr(client, "label", "LLM")
    return f" ({label})" if isinstance(label, str) and label not in ("", "LLM") else ""


class AnswerError(Exception):
    """Raised for bad input or LLM output that fails validation twice."""


@dataclass
class Answer:
    text: str
    mode: str                      # "llm", "local" or "refusal"
    label: str
    citations: list[str] = field(default_factory=list)
    hits: list[Hit] = field(default_factory=list)

    @property
    def refused(self) -> bool:
        return self.mode == "refusal"


def check_question(question: str) -> str:
    question = (question or "").strip()
    if not question:
        raise AnswerError("Question is empty.")
    if len(question) > MAX_QUESTION_CHARS:
        raise AnswerError(f"Question is too long ({len(question)} chars; limit {MAX_QUESTION_CHARS}).")
    return question


def refusal(hits: list[Hit], reason: str) -> Answer:
    text = f"{prompts.REFUSAL_TEXT} {reason}"
    return Answer(text=text, mode="refusal", label="Refusal", hits=hits)


def retrieve(question: str, index: TfidfIndex, threshold: float = DEFAULT_THRESHOLD,
             k: int = DEFAULT_TOP_K) -> tuple[list[Hit], str | None]:
    """Top-k hits for a question, plus a refusal reason if the best one is below threshold.

    Hits under half the threshold are dropped so weak chunks never reach the LLM.
    """
    if not index.chunks:
        raise AnswerError("No notes ingested yet. Run `ingest` first.")
    hits = index.search(question, k=k)
    if not hits or hits[0].score < threshold:
        best = f"{hits[0].score:.2f}" if hits else "0.00"
        return hits, f"(best match score {best} is below the {threshold:.2f} threshold)"
    return [hit for hit in hits if hit.score >= threshold / 2], None


def answer_question(question: str, index: TfidfIndex, client=None,
                    threshold: float = DEFAULT_THRESHOLD, k: int = DEFAULT_TOP_K) -> Answer:
    """Answer from notes. With `client` use the LLM, otherwise extractive fallback."""
    question = check_question(question)
    hits, too_weak = retrieve(question, index, threshold, k)
    if too_weak:
        return refusal(hits, too_weak)
    if client is None:
        return extractive_answer(hits)
    return llm_answer(question, hits, client)


def extractive_answer(hits: list[Hit], max_chunks: int = 2) -> Answer:
    used = hits[:max_chunks]
    parts = [f"[{hit.chunk.id}] ({hit.chunk.title} > {hit.chunk.heading}, score {hit.score:.2f})\n"
             f"{hit.chunk.text}" for hit in used]
    return Answer(text="\n\n".join(parts), mode="local", label=LOCAL_LABEL,
                  citations=[hit.chunk.id for hit in used], hits=hits)


def split_sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE_SPLIT_RE.split(text.strip()) if part.strip()]


def uncited_sentences(answer: str) -> list[str]:
    return [sentence for sentence in split_sentences(answer)
            if not _CITATION_RE.search(sentence) and re.search(r"[A-Za-z]{3}", sentence)]


def parse_json_object(text: str) -> dict:
    """Parse a JSON object, tolerating a surrounding ```json fence."""
    cleaned = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1)
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("top-level JSON value is not an object")
    return data


def validate_answer_payload(text: str, allowed_ids: set[str]) -> tuple[dict, list[str]]:
    """Return (payload, errors). An empty error list means the payload is usable."""
    try:
        data = parse_json_object(text)
    except ValueError as exc:
        return {}, [f"reply is not valid JSON ({exc})"]
    errors = []
    in_notes = data.get("in_notes")
    answer = data.get("answer", "")
    citations = data.get("citations", [])
    if not isinstance(in_notes, bool):
        errors.append('"in_notes" must be true or false')
    if not isinstance(answer, str):
        errors.append('"answer" must be a string')
        answer = ""
    if not isinstance(citations, list) or not all(isinstance(c, str) for c in citations):
        errors.append('"citations" must be a list of chunk-id strings')
        citations = []
    if in_notes is True:
        inline = _CITATION_RE.findall(answer)
        cited = set(citations) | set(inline)
        if not answer.strip():
            errors.append('"answer" is empty but "in_notes" is true')
        if not cited:
            errors.append("answer has no citations")
        else:
            uncited = uncited_sentences(answer)
            if uncited:
                quoted = "; ".join(f'"{sentence[:80]}"' for sentence in uncited[:3])
                errors.append(f"every sentence needs its own [chunk-id] citation; uncited: {quoted}")
        unknown = sorted(cited - allowed_ids)
        if unknown:
            errors.append(f"cited ids not in the provided chunks: {', '.join(unknown)}")
        data["citations"] = sorted(cited, key=lambda c: (c.split("#")[0], int(c.split("#")[1])))
    return data, errors


def llm_answer(question: str, hits: list[Hit], client) -> Answer:
    allowed = {hit.chunk.id for hit in hits}
    messages = [
        {"role": "system", "content": prompts.ANSWER_SYSTEM},
        {"role": "user", "content": prompts.ANSWER_USER.format(
            chunks=prompts.format_chunks([hit.chunk for hit in hits]), question=question)},
    ]
    try:
        reply = client.chat(messages)
        data, errors = validate_answer_payload(reply, allowed)
        if errors:
            messages += [{"role": "assistant", "content": reply},
                         {"role": "user", "content": prompts.REPAIR_USER.format(errors="\n".join(errors))}]
            reply = client.chat(messages)
            data, errors = validate_answer_payload(reply, allowed)
    except LLMError as exc:
        raise AnswerError(str(exc)) from None
    if errors:
        raise AnswerError("LLM answer rejected after one repair attempt: " + "; ".join(errors))
    if not data["in_notes"]:
        return refusal(hits, "(the LLM found no supporting chunk)")
    label = LLM_LABEL.replace("LLM answer", "LLM answer" + provider_tag(client), 1)
    return Answer(text=data["answer"].strip(), mode="llm", label=label,
                  citations=data["citations"], hits=hits)
