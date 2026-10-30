"""Quiz generation (LLM or local template) and strict validation.

Every item has one correct answer and exactly three distinct distractors.
Distractors are always terms that already exist in the student's notes:
the local generator picks them directly, and LLM output is rejected if a
distractor is not in the pool of note terms we supplied.
"""

from __future__ import annotations

import hashlib
import random
import re
from dataclasses import asdict, dataclass

from . import prompts
from .answering import parse_json_object
from .chunking import OVERLAP_MARK, Chunk
from .llm import LLMError

MAX_QUIZ_ITEMS = 20
MAX_SOURCE_CHUNKS = 8

_DEFINITION_RES = [
    re.compile(r"^\s*[-*+]\s+\*\*(?P<term>[^*]{1,60})\*\*\s*:\s*(?P<definition>.{10,})$"),
    re.compile(r"^\s*[-*+]\s+(?P<term>[A-Z][\w\s\-]{1,40}?)\s*:\s*(?P<definition>.{10,})$"),
]


class QuizError(Exception):
    """Raised when a quiz cannot be built or LLM output fails validation twice."""


@dataclass
class Fact:
    term: str
    definition: str
    chunk_id: str
    topic: str


@dataclass
class QuizItem:
    id: str
    topic: str
    question: str
    correct: str
    distractors: list[str]
    source_chunk_id: str
    origin: str  # "llm" or "local"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "QuizItem":
        return cls(**{name: data[name] for name in cls.__dataclass_fields__})

    def options(self) -> list[str]:
        """Correct answer plus distractors in a stable shuffled order."""
        opts = [self.correct] + list(self.distractors)
        random.Random(self.id).shuffle(opts)
        return opts

    def correct_letter(self) -> str:
        return "ABCD"[self.options().index(self.correct)]


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower().rstrip("."))


def item_id(question: str, correct: str) -> str:
    return hashlib.sha1(f"{question}\x00{correct}".encode("utf-8")).hexdigest()[:10]


def extract_facts(chunks: list[Chunk]) -> list[Fact]:
    """Pull `- **Term**: definition` style lines out of chunks (deduplicated by term)."""
    facts: list[Fact] = []
    seen: set[str] = set()
    for chunk in chunks:
        for line in chunk.text.splitlines():
            if line.startswith(OVERLAP_MARK):
                continue
            for pattern in _DEFINITION_RES:
                match = pattern.match(line)
                if match:
                    term = match.group("term").strip()
                    if normalize(term) not in seen:
                        seen.add(normalize(term))
                        definition = match.group("definition").strip().rstrip(".")
                        facts.append(Fact(term, definition, chunk.id, chunk.topic))
                    break
    return facts


def validate_item(raw: dict, chunk_ids: set[str], pool: set[str] | None = None) -> list[str]:
    """Return a list of problems with one raw quiz item (empty list = valid)."""
    if not isinstance(raw, dict):
        return ["item is not a JSON object"]
    errors = []
    question, correct = raw.get("question"), raw.get("correct")
    distractors = raw.get("distractors")
    if not isinstance(question, str) or not question.strip():
        errors.append("missing question text")
    if not isinstance(correct, str) or not correct.strip():
        errors.append("missing correct answer")
        correct = ""
    if (not isinstance(distractors, list) or len(distractors) != 3
            or not all(isinstance(d, str) and d.strip() for d in distractors)):
        errors.append("needs exactly 3 non-empty distractors")
    else:
        normalized = [normalize(d) for d in distractors]
        if len(set(normalized)) != 3:
            errors.append("distractors are not distinct")
        if normalize(correct) in normalized:
            errors.append("a distractor repeats the correct answer")
        if pool is not None:
            outside = [d for d in distractors if normalize(d) not in pool]
            if outside:
                errors.append(f"distractors not taken from the notes' term pool: {outside}")
    if raw.get("source_chunk_id") not in chunk_ids:
        errors.append(f"source_chunk_id {raw.get('source_chunk_id')!r} is not a known chunk")
    return errors


def build_item(raw: dict, topic_of: dict[str, str], origin: str) -> QuizItem:
    question = raw["question"].strip()
    correct = raw["correct"].strip()
    return QuizItem(
        id=item_id(question, correct),
        topic=topic_of[raw["source_chunk_id"]],
        question=question,
        correct=correct,
        distractors=[d.strip() for d in raw["distractors"]],
        source_chunk_id=raw["source_chunk_id"],
        origin=origin,
    )


def resolve_topic(query: str | None, chunks: list[Chunk]) -> str | None:
    """Map a user topic like 'elasticity' or 'Hypothesis' to a topic slug."""
    if not query:
        return None
    wanted = query.strip().lower()
    topics = {}
    for chunk in chunks:
        topics.setdefault(chunk.topic, chunk.title.lower())
    for slug in topics:
        if slug == wanted:
            return slug
    for slug, title in topics.items():
        if wanted in slug or wanted in title:
            return slug
    raise QuizError(f"No topic matches {query!r}. Known topics: {', '.join(sorted(topics)) or 'none'}.")


def rank_distractors(correct: Fact, candidates: list[Fact], rng: random.Random) -> list[str]:
    """Prefer same-topic terms of similar length: they are plausible, not giveaways."""
    def score(fact: Fact) -> tuple:
        same_topic = fact.topic == correct.topic
        length_gap = abs(len(fact.term) - len(correct.term))
        return (not same_topic, length_gap, rng.random())

    chosen: list[str] = []
    for fact in sorted(candidates, key=score):
        if normalize(fact.term) == normalize(correct.term):
            continue
        if normalize(fact.term) in {normalize(c) for c in chosen}:
            continue
        chosen.append(fact.term)
        if len(chosen) == 3:
            break
    return chosen


def local_quiz(chunks: list[Chunk], topic: str | None, n: int, seed: int = 0) -> list[QuizItem]:
    """Template quiz: 'which term matches this definition?' using note terms as distractors."""
    n = check_n(n)
    all_facts = extract_facts(chunks)
    targets = [fact for fact in all_facts if topic is None or fact.topic == topic]
    if not targets:
        raise QuizError("No definitions found to quiz on. Notes need lines like `- **Term**: definition`.")
    rng = random.Random(seed)
    rng.shuffle(targets)
    chunk_ids = {chunk.id for chunk in chunks}
    topic_of = {chunk.id: chunk.topic for chunk in chunks}
    items = []
    for fact in targets[:n]:
        distractors = rank_distractors(fact, all_facts, rng)
        raw = {
            "question": f'Which term from your notes matches this definition: "{fact.definition}"?',
            "correct": fact.term,
            "distractors": distractors,
            "source_chunk_id": fact.chunk_id,
        }
        errors = validate_item(raw, chunk_ids)
        if errors:
            raise QuizError(f"Not enough distinct terms in your notes to build distractors ({errors[0]}).")
        items.append(build_item(raw, topic_of, origin="local"))
    return items


def check_n(n: int) -> int:
    if not 1 <= n <= MAX_QUIZ_ITEMS:
        raise QuizError(f"Number of questions must be between 1 and {MAX_QUIZ_ITEMS}.")
    return n


def _validate_llm_reply(reply: str, n: int, chunk_ids: set[str], pool: set[str]) -> tuple[list, list[str]]:
    try:
        data = parse_json_object(reply)
    except ValueError as exc:
        return [], [f"reply is not valid JSON ({exc})"]
    items = data.get("items")
    if not isinstance(items, list) or not items:
        return [], ['"items" must be a non-empty list']
    errors = []
    if len(items) > n:
        errors.append(f"expected at most {n} items, got {len(items)}")
    for number, raw in enumerate(items, start=1):
        errors += [f"item {number}: {problem}" for problem in validate_item(raw, chunk_ids, pool)]
    return items, errors


def llm_quiz(chunks: list[Chunk], topic: str | None, n: int, client) -> list[QuizItem]:
    """Ask the LLM for questions; validate; repair once; otherwise reject."""
    n = check_n(n)
    sources = [chunk for chunk in chunks if topic is None or chunk.topic == topic][:MAX_SOURCE_CHUNKS]
    if not sources:
        raise QuizError("No notes to quiz on.")
    pool_terms = [fact.term for fact in extract_facts(chunks)]
    if len(pool_terms) < 4:
        raise QuizError("Notes contain fewer than 4 defined terms, so distractors cannot be grounded.")
    pool = {normalize(term) for term in pool_terms}
    chunk_ids = {chunk.id for chunk in sources}
    messages = [
        {"role": "system", "content": prompts.QUIZ_SYSTEM},
        {"role": "user", "content": prompts.QUIZ_USER.format(
            chunks=prompts.format_chunks(sources), pool=prompts.format_pool(pool_terms), n=n)},
    ]
    try:
        reply = client.chat(messages, temperature=0.3)
        items, errors = _validate_llm_reply(reply, n, chunk_ids, pool)
        if errors:
            messages += [{"role": "assistant", "content": reply},
                         {"role": "user", "content": prompts.REPAIR_USER.format(errors="\n".join(errors))}]
            reply = client.chat(messages, temperature=0.0)
            items, errors = _validate_llm_reply(reply, n, chunk_ids, pool)
    except LLMError as exc:
        raise QuizError(str(exc)) from None
    if errors:
        raise QuizError("LLM quiz rejected after one repair attempt: " + "; ".join(errors[:5]))
    topic_of = {chunk.id: chunk.topic for chunk in sources}
    return [build_item(raw, topic_of, origin="llm") for raw in items]
