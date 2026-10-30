"""Layered lecture simplifier and real-life examples, grounded in the student's notes.

Both features go through the provider interface (`client.chat(messages)`).
With no LLM they fall back to a clearly labelled extractive approximation:
sentences quoted verbatim from the notes, never presented as a simplification
or as generated examples.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from . import prompts
from .answering import _CITATION_RE, parse_json_object, split_sentences, uncited_sentences
from .chunking import OVERLAP_MARK, Chunk
from .llm import LLMError
from .quiz import _DEFINITION_RES, resolve_topic
from .retrieval import STOPWORDS, TfidfIndex, tokenize

MAX_SOURCE_CHUNKS = 8
MAX_SIMPLE_WORDS = 120          # the prompt asks for 90; a little slack before we reject
MAX_EXAMPLES = 8
DEFAULT_EXAMPLES = 4
KEY_SENTENCES = 3
MIN_GROUNDED_OVERLAP = 2        # shared content words between a "grounded" example and its chunk

_EXAMPLE_CUES = re.compile(
    r"\b(such as|for example|for instance|e\.g\.|use[sd]?|using|applie[sd]|apply|predict|switch|"
    r"during|in practice)\b", re.IGNORECASE)


class ExplainError(Exception):
    """Bad input, or LLM output that fails validation twice."""


def provider_name(client) -> str:
    """A human name for whatever ran: 'Claude CLI: sonnet', 'OpenAI-compatible API: gpt-4o-mini'."""
    label = getattr(client, "label", "LLM")
    if isinstance(label, str) and label not in ("", "LLM"):
        return label
    model = getattr(getattr(client, "config", None), "model", None)
    return f"OpenAI-compatible API: {model}" if isinstance(model, str) and model else "LLM"


# ---------------------------------------------------------------- choosing notes

@dataclass
class Selection:
    chunks: list[Chunk]
    subject: str                     # human description, e.g. 'topic "elasticity"'
    refusal: str | None = None       # set when a free-text query matched nothing well enough
    closest: list[Chunk] = field(default_factory=list)


def chunk_number(chunk: Chunk) -> int:
    return int(chunk.id.rsplit("#", 1)[1])


def select_chunks(chunks: list[Chunk], query: str | None = None, topic: str | None = None,
                  chunk_ids: list[str] | None = None, source: str | None = None,
                  threshold: float = 0.15, k: int = 4) -> Selection:
    """Pick the notes to explain: exactly one of a free-text query, topic, chunk ids or note file.

    A query reuses TF-IDF retrieval and its refusal threshold; the others take
    the matching chunks in note order. At most MAX_SOURCE_CHUNKS are used.
    """
    given = [name for name, value in (("a query", query), ("--topic", topic), ("--chunk", chunk_ids),
                                      ("--file", source)) if value]
    if len(given) != 1:
        raise ExplainError("Say what to explain with exactly one of: a query, --topic, --chunk or --file.")
    if not chunks:
        raise ExplainError("No notes ingested yet. Run `ingest` first.")
    if topic:
        slug = resolve_topic(topic, chunks)
        picked = [chunk for chunk in chunks if chunk.topic == slug]
        subject = f'topic "{slug}"'
    elif chunk_ids:
        by_id = {chunk.id: chunk for chunk in chunks}
        unknown = [cid for cid in chunk_ids if cid not in by_id]
        if unknown:
            raise ExplainError(f"Unknown chunk id(s): {', '.join(unknown)}. Ids look like elasticity#3.")
        picked = [by_id[cid] for cid in dict.fromkeys(chunk_ids)]
        subject = "chunk(s) " + ", ".join(chunk.id for chunk in picked)
    elif source:
        wanted = PurePosixPath(source.replace("\\", "/"))
        picked = [chunk for chunk in chunks
                  if PurePosixPath(chunk.source) == wanted or PurePosixPath(chunk.source).name == wanted.name]
        if not picked:
            known = ", ".join(sorted({chunk.source for chunk in chunks}))
            raise ExplainError(f"No ingested note file matches {source!r}. Known files: {known}.")
        subject = f"file {picked[0].source}"
    else:
        query = query.strip()
        hits = TfidfIndex(chunks).search(query, k=k)
        if not hits or hits[0].score < threshold:
            best = f"{hits[0].score:.2f}" if hits else "0.00"
            return Selection([], f'"{query}"', refusal=f"(best match score {best} is below the "
                             f"{threshold:.2f} threshold)", closest=[hit.chunk for hit in hits[:3]])
        picked = [hit.chunk for hit in hits if hit.score >= threshold / 2]
        subject = f'"{query}"'
    if len(picked) > MAX_SOURCE_CHUNKS:
        picked = picked[:MAX_SOURCE_CHUNKS]
        subject += f" (first {MAX_SOURCE_CHUNKS} chunks)"
    picked.sort(key=lambda chunk: (chunk.source, chunk_number(chunk)))
    return Selection(picked, subject)


def fresh_text(chunk: Chunk) -> str:
    """Chunk text without the words carried over from the previous chunk."""
    lines = chunk.text.splitlines()
    if lines and lines[0].startswith(OVERLAP_MARK):
        lines = lines[1:]
    return "\n".join(lines)


def note_sentences(chunk: Chunk, skip_definitions: bool = False) -> list[str]:
    sentences = []
    for line in fresh_text(chunk).splitlines():
        if skip_definitions and any(pattern.match(line) for pattern in _DEFINITION_RES):
            continue
        line = re.sub(r"^\s*[-*+]\s+", "", line).replace("**", "")
        sentences += split_sentences(line)
    return sentences


# ---------------------------------------------------------------- simplify

@dataclass
class Simplified:
    mode: str                 # "llm" or "local"
    header: str
    simple: str               # layer 1
    full: str                 # layer 2
    citations: list[str]
    chunks: list[Chunk]


def _check_ids(text: str, allowed: set[str]) -> list[str]:
    unknown = sorted(set(_CITATION_RE.findall(text)) - allowed)
    return [f"cited ids not in the provided chunks: {', '.join(unknown)}"] if unknown else []


def validate_simplify_payload(text: str, allowed: set[str]) -> tuple[dict, list[str]]:
    """Return (payload, errors); an empty error list means both layers are usable."""
    try:
        data = parse_json_object(text)
    except ValueError as exc:
        return {}, [f"reply is not valid JSON ({exc})"]
    errors = []
    simple, full = data.get("simple"), data.get("full")
    if not isinstance(simple, str) or not simple.strip():
        errors.append('"simple" must be a non-empty string')
        simple = ""
    if not isinstance(full, str) or not full.strip():
        errors.append('"full" must be a non-empty string')
        full = ""
    if simple:
        if "\n\n" in simple.strip():
            errors.append('"simple" must be ONE paragraph (no blank lines)')
        words = len(_CITATION_RE.sub("", simple).split())
        if words > MAX_SIMPLE_WORDS:
            errors.append(f'"simple" has {words} words; keep it to one short paragraph (about 90 words)')
        if not _CITATION_RE.search(simple):
            errors.append('"simple" needs at least one [chunk-id] citation')
    if full:
        if not _CITATION_RE.search(full):
            errors.append('"full" has no citations')
        uncited = uncited_sentences(full)
        if uncited:
            quoted = "; ".join(f'"{sentence[:80]}"' for sentence in uncited[:3])
            errors.append(f'every sentence of "full" needs its own [chunk-id] citation; uncited: {quoted}')
    errors += _check_ids(simple + "\n" + full, allowed)
    cited = set(_CITATION_RE.findall(simple + "\n" + full))
    data["citations"] = sorted(cited, key=lambda c: (c.split("#")[0], int(c.split("#")[1])))
    return data, errors


def simplify(selection: Selection, client=None) -> Simplified:
    if selection.refusal or not selection.chunks:
        raise ExplainError("Nothing selected to simplify.")
    if client is None:
        return extractive_simplify(selection)
    allowed = {chunk.id for chunk in selection.chunks}
    messages = [
        {"role": "system", "content": prompts.SIMPLIFY_SYSTEM},
        {"role": "user", "content": prompts.SIMPLIFY_USER.format(
            subject=selection.subject, chunks=prompts.format_chunks(selection.chunks))},
    ]
    data, errors = _chat_with_repair(client, messages, lambda reply: validate_simplify_payload(reply, allowed))
    if errors:
        raise ExplainError("LLM simplification rejected after one repair attempt: " + "; ".join(errors[:4]))
    header = (f"SIMPLIFY by LLM ({provider_name(client)}), grounded in your notes: {selection.subject}")
    return Simplified("llm", header, data["simple"].strip(), data["full"].strip(), data["citations"],
                      selection.chunks)


def key_sentences(chunks: list[Chunk], count: int = KEY_SENTENCES) -> list[tuple[str, str]]:
    """The `count` sentences that share the most frequent content words of the selection.

    Returns (sentence, chunk id) in note order. Plain extraction: nothing is reworded.
    """
    candidates = [(sentence, chunk.id) for chunk in chunks for sentence in note_sentences(chunk)]
    frequency: dict[str, int] = {}
    for sentence, _ in candidates:
        for token in set(tokenize(sentence)):
            frequency[token] = frequency.get(token, 0) + 1

    def score(sentence: str) -> float:
        tokens = set(tokenize(sentence))
        return sum(frequency[t] for t in tokens) / (len(sentence.split()) ** 0.5 or 1)

    ranked = sorted(range(len(candidates)), key=lambda i: (-score(candidates[i][0]), i))[:count]
    return [candidates[i] for i in sorted(ranked)]


def extractive_simplify(selection: Selection) -> Simplified:
    picked = key_sentences(selection.chunks)
    simple = " ".join(f"{sentence} [{chunk_id}]" for sentence, chunk_id in picked)
    full = "\n\n".join(f"[{chunk.id}] ({chunk.heading})\n{fresh_text(chunk)}" for chunk in selection.chunks)
    header = ("SIMPLIFY: LOCAL EXTRACTIVE APPROXIMATION (no LLM, not a simplification): "
              f"sentences quoted verbatim from your notes: {selection.subject}")
    return Simplified("local", header, simple, full, [chunk.id for chunk in selection.chunks], selection.chunks)


# ---------------------------------------------------------------- examples

GROUNDED = "grounded"
ILLUSTRATIVE = "illustrative"


@dataclass
class Example:
    kind: str                 # GROUNDED (stated in the notes) or ILLUSTRATIVE (not from the notes)
    text: str
    citations: list[str]      # chunks that state it (grounded only)
    illustrates: str = ""     # chunk defining the concept (illustrative only)


@dataclass
class ExampleSet:
    mode: str                 # "llm", "local" or "refusal"
    header: str
    concept: str
    examples: list[Example]
    chunks: list[Chunk]
    note: str = ""


def content_words(text: str) -> set[str]:
    return {token for token in tokenize(text) if len(token) > 2 and token not in STOPWORDS}


def validate_examples_payload(text: str, chunks: list[Chunk], n: int) -> tuple[dict, list[str]]:
    try:
        data = parse_json_object(text)
    except ValueError as exc:
        return {}, [f"reply is not valid JSON ({exc})"]
    by_id = {chunk.id: chunk for chunk in chunks}
    errors = []
    in_notes, examples = data.get("in_notes"), data.get("examples")
    if not isinstance(in_notes, bool):
        errors.append('"in_notes" must be true or false')
    if not isinstance(examples, list):
        return data, errors + ['"examples" must be a list']
    if in_notes is True and not examples:
        errors.append('"examples" is empty but "in_notes" is true')
    if len(examples) > n:
        errors.append(f"expected at most {n} examples, got {len(examples)}")
    for number, raw in enumerate(examples, start=1):
        where = f"example {number}"
        if not isinstance(raw, dict):
            errors.append(f"{where}: not a JSON object")
            continue
        kind, body = raw.get("kind"), raw.get("text")
        citations = raw.get("citations", [])
        if kind not in (GROUNDED, ILLUSTRATIVE):
            errors.append(f'{where}: "kind" must be "grounded" or "illustrative"')
        if not isinstance(body, str) or not body.strip():
            errors.append(f'{where}: missing "text"')
            body = ""
        if not isinstance(citations, list) or not all(isinstance(c, str) for c in citations):
            errors.append(f'{where}: "citations" must be a list of chunk ids')
            citations = []
        citations = list(dict.fromkeys(citations + _CITATION_RE.findall(body)))
        unknown = [c for c in citations if c not in by_id]
        if unknown:
            errors.append(f"{where}: cited ids not in the provided chunks: {', '.join(unknown)}")
        if kind == GROUNDED:
            if not citations:
                errors.append(f"{where}: a grounded example must cite the chunk that states it")
            else:
                stated = set().union(*(content_words(fresh_text(by_id[c])) for c in citations if c in by_id))
                if len(content_words(body) & stated) < MIN_GROUNDED_OVERLAP:
                    errors.append(f"{where}: labelled grounded but shares almost no wording with its cited "
                                  "chunk; label it illustrative unless the chunk states it")
        elif kind == ILLUSTRATIVE:
            if citations:
                errors.append(f'{where}: an illustrative example is not from the notes, so "citations" '
                              'must be []; put the concept\'s chunk id in "illustrates"')
            if raw.get("illustrates") not in by_id:
                errors.append(f'{where}: "illustrates" must be the id of the chunk defining the concept')
        raw["citations"] = citations
    return data, errors


def real_life_examples(concept: str, selection: Selection, client=None, n: int = DEFAULT_EXAMPLES) -> ExampleSet:
    concept = (concept or "").strip()
    if not concept:
        raise ExplainError("Concept is empty.")
    if not 1 <= n <= MAX_EXAMPLES:
        raise ExplainError(f"Number of examples must be between 1 and {MAX_EXAMPLES}.")
    if selection.refusal or not selection.chunks:
        return ExampleSet("refusal", "Refusal", concept, [], selection.closest,
                          note=f"{prompts.REFUSAL_TEXT} {selection.refusal or ''}".strip())
    if client is None:
        return extractive_examples(concept, selection, n)
    messages = [
        {"role": "system", "content": prompts.EXAMPLES_SYSTEM.format(n=n)},
        {"role": "user", "content": prompts.EXAMPLES_USER.format(
            chunks=prompts.format_chunks(selection.chunks), concept=concept, n=n)},
    ]
    data, errors = _chat_with_repair(
        client, messages, lambda reply: validate_examples_payload(reply, selection.chunks, n))
    if errors:
        raise ExplainError("LLM examples rejected after one repair attempt: " + "; ".join(errors[:4]))
    if not data["in_notes"]:
        return ExampleSet("refusal", "Refusal", concept, [], selection.chunks,
                          note=f"{prompts.REFUSAL_TEXT} (the LLM found no chunk describing this concept)")
    examples = [Example(raw["kind"], raw["text"].strip(), raw["citations"],
                        raw.get("illustrates", "") if raw["kind"] == ILLUSTRATIVE else "")
                for raw in data["examples"]]
    examples.sort(key=lambda example: example.kind != GROUNDED)  # grounded first, stable
    header = f"EXAMPLES by LLM ({provider_name(client)}) for \"{concept}\": grounded ones cite your notes"
    return ExampleSet("llm", header, concept, examples, selection.chunks)


def extractive_examples(concept: str, selection: Selection, n: int) -> ExampleSet:
    """Sentences from the notes that mention a use, case or example. Never invents one."""
    found = []
    for chunk in selection.chunks:
        for sentence in note_sentences(chunk, skip_definitions=True):
            if _EXAMPLE_CUES.search(sentence):
                found.append(Example(GROUNDED, sentence, [chunk.id]))
    header = (f"EXAMPLES: LOCAL FALLBACK (extractive, no LLM) for \"{concept}\": sentences quoted verbatim "
              "from your notes that mention uses or cases")
    note = ("No illustrative examples are made up without an LLM. "
            "Use --provider claude or set COURSE_COMPASS_API_KEY for generated ones.")
    if not found:
        note = "Your notes for this concept contain no sentence that mentions a use or example. " + note
    return ExampleSet("local", header, concept, found[:n], selection.chunks, note=note)


# ---------------------------------------------------------------- shared

def _chat_with_repair(client, messages: list[dict], validate):
    try:
        reply = client.chat(messages, temperature=0.2)
        data, errors = validate(reply)
        if errors:
            messages = messages + [
                {"role": "assistant", "content": reply},
                {"role": "user", "content": prompts.REPAIR_USER.format(errors="\n".join(errors))}]
            reply = client.chat(messages, temperature=0.0)
            data, errors = validate(reply)
    except LLMError as exc:
        raise ExplainError(str(exc)) from None
    return data, errors
