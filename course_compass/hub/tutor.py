"""Tutor modes: hint-first walkthroughs, check-my-work, and explain-it-back.

Same contract as the rest of Course Compass: the relevant note chunks are
retrieved first, the model gets only those, and its JSON reply is validated
in code (citation ids must be chunks it was given; one repair attempt, then
an error). Problem solving needs reasoning the notes do not spell out, so a
walkthrough may use general steps, but every *course fact* it relies on must
cite a chunk, and anything taken from outside the notes is listed under
"outside_knowledge" so it is never passed off as your course's material.

Math is written in LaTeX ($...$ inline, $$...$$ display) and rendered by the web UI.

With no LLM each mode has an honest local version: walkthrough and
check-my-work show the most relevant note passages and a generic
problem-solving checklist; explain-it-back scores which of the notes' key
terms your explanation covers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..answering import _CITATION_RE, parse_json_object
from ..chunking import Chunk
from ..llm import LLMError
from ..prompts import REPAIR_USER, format_chunks
from ..quiz import extract_facts
from ..retrieval import TfidfIndex, tokenize

MAX_INPUT_CHARS = 6000
TOP_K = 6
MIN_SCORE = 0.08

WALK_SYSTEM = """You are a patient tutor for a university student. You help them solve a problem step by step, using their own course notes.
Rules:
1. Course facts (definitions, formulas, rules, theorems, procedures) must come from the NOTE CHUNKS, cited with their id in square brackets, e.g. [cs-241-lecture-03#2]. Only cite ids that appear in the NOTE CHUNKS.
2. You may use general reasoning and arithmetic. If a step needs a fact that is NOT in the chunks, still give it, but describe it in "outside_knowledge" so the student knows to check it against the course.
3. "hints" are 2 to 4 progressive hints: the first only points at the right idea, each next one gives more away, none gives the final answer.
4. "steps" is the full worked solution, 1 to 12 steps, each with its own "citations" list (may be empty for pure reasoning or arithmetic).
5. Write math in LaTeX: $...$ inline, $$...$$ for display. Keep each step short.
6. Reply with ONLY a JSON object, no markdown fences:
{"hints": ["..."], "steps": [{"text": "...", "citations": ["chunk-id"]}], "final_answer": "...", "outside_knowledge": "empty string, or what came from outside the notes"}"""

CHECK_SYSTEM = """You check a university student's own attempt at a problem, like a TA marking it, using their course notes.
Rules:
1. Find the FIRST step that is wrong, if any, and explain why in one or two sentences. Do not rewrite their whole solution.
2. "verdict" is "correct", "partly" (right approach, some errors or gaps) or "incorrect".
3. Course facts you rely on must cite NOTE CHUNK ids in square brackets; only cite ids that appear in the NOTE CHUNKS. Facts from outside the notes go in "outside_knowledge".
4. "next_step" is one nudge toward fixing it, without giving the full answer.
5. Write math in LaTeX: $...$ inline.
6. Reply with ONLY a JSON object, no markdown fences:
{"verdict": "correct|partly|incorrect", "first_error": "empty if none", "feedback": ["short point", "..."], "next_step": "...", "citations": ["chunk-id"], "outside_knowledge": ""}"""

EXPLAIN_SYSTEM = """A student explains a concept in their own words so you can find gaps (the "explain it back" or Feynman method). Compare their explanation ONLY against the NOTE CHUNKS.
Rules:
1. "covered": key points from the chunks the student got right. "missing": key points from the chunks they left out. "misconceptions": things they said that contradict the chunks, with the correction.
2. Every point cites the chunk it comes from, as a single id in "citation". Only use ids that appear in the NOTE CHUNKS.
3. "score" is 0 to 100: how completely and correctly they explained what the chunks say.
4. "follow_up" is one question that makes them think about their biggest gap.
5. Reply with ONLY a JSON object, no markdown fences:
{"score": 0, "covered": [{"point": "...", "citation": "chunk-id"}], "missing": [{"point": "...", "citation": "chunk-id"}], "misconceptions": [{"claim": "...", "correction": "...", "citation": "chunk-id"}], "follow_up": "..."}"""

USER_TEMPLATE = """NOTE CHUNKS:
{chunks}

{label}:
{text}
{extra}
Reply with the JSON object only."""

CHECKLIST = [
    "Write down what is given and what is asked, with units or types.",
    "Find the definition, rule or formula in the passages below that connects them.",
    "Do the smallest step that uses it, then check it against a simple example.",
    "Check the answer: does it have the right form, sign, units and size?",
]


class TutorError(Exception):
    pass


@dataclass
class TutorResult:
    mode: str                     # llm or local
    label: str
    data: dict
    sources: list[dict] = field(default_factory=list)


def _check_text(text: str, what: str) -> str:
    text = (text or "").strip()
    if not text:
        raise TutorError(f"{what} is empty.")
    if len(text) > MAX_INPUT_CHARS:
        raise TutorError(f"{what} is too long ({len(text)} characters; limit {MAX_INPUT_CHARS}).")
    return text


def relevant(chunks: list[Chunk], query: str, k: int = TOP_K) -> list:
    if not chunks:
        raise TutorError("No notes for this course yet. Sync LEARN or add notes, then re-index.")
    hits = TfidfIndex(chunks).search(query, k=k)
    return [hit for hit in hits if hit.score >= MIN_SCORE]


def _sources(hits) -> list[dict]:
    return [{"id": h.chunk.id, "source": h.chunk.source, "heading": h.chunk.heading, "score": round(h.score, 3),
             "text": h.chunk.text} for h in hits]


def _ids_in(value) -> set[str]:
    found: set[str] = set()
    if isinstance(value, str):
        found |= set(_CITATION_RE.findall(value))
    elif isinstance(value, dict):
        for inner in value.values():
            found |= _ids_in(inner)
    elif isinstance(value, list):
        for inner in value:
            if isinstance(inner, str) and re.fullmatch(r"[a-z0-9][a-z0-9\-]*#\d+", inner):
                found.add(inner)
            else:
                found |= _ids_in(inner)
    return found


def _validate(text: str, allowed: set[str], required: dict[str, type]) -> tuple[dict, list[str]]:
    try:
        data = parse_json_object(text)
    except ValueError as exc:
        return {}, [f"reply is not valid JSON ({exc})"]
    errors = [f'"{key}" must be a {kind.__name__}' for key, kind in required.items()
              if not isinstance(data.get(key), kind)]
    unknown = sorted(_ids_in(data) - allowed)
    if unknown:
        errors.append(f"cited ids not in the provided chunks: {', '.join(unknown)}")
    return data, errors


def _run(client, system: str, user: str, validate) -> dict:
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    try:
        reply = client.chat(messages, temperature=0.2)
        data, errors = validate(reply)
        if errors:
            messages += [{"role": "assistant", "content": reply},
                         {"role": "user", "content": REPAIR_USER.format(errors="\n".join(errors))}]
            reply = client.chat(messages, temperature=0.0)
            data, errors = validate(reply)
    except LLMError as exc:
        raise TutorError(str(exc)) from None
    if errors:
        raise TutorError("Tutor reply rejected after one repair attempt: " + "; ".join(errors))
    return data


def _label(client) -> str:
    return f"Tutor ({getattr(client, 'label', 'LLM')}), grounded in your notes"


LOCAL_LABEL = "LOCAL tutor (no LLM): the most relevant passages from your notes and a checklist"


def walkthrough(problem: str, chunks: list[Chunk], client=None) -> TutorResult:
    problem = _check_text(problem, "The problem")
    hits = relevant(chunks, problem)
    if client is None:
        hints = [f"Start from [{h.chunk.id}] ({h.chunk.heading})." for h in hits[:3]] or \
                ["Your notes have nothing that matches this problem closely. Check the problem's topic."]
        return TutorResult("local", LOCAL_LABEL, {"hints": hints, "steps": [{"text": s, "citations": []}
                                                                           for s in CHECKLIST],
                                                  "final_answer": "", "outside_knowledge": ""}, _sources(hits))
    allowed = {h.chunk.id for h in hits}

    def validate(reply):
        data, errors = _validate(reply, allowed, {"hints": list, "steps": list, "final_answer": str})
        if data:
            hints = data.get("hints") or []
            if not 2 <= len(hints) <= 4 or not all(isinstance(h, str) and h.strip() for h in hints):
                errors.append('"hints" must be 2 to 4 non-empty strings')
            steps = data.get("steps") or []
            if not 1 <= len(steps) <= 12 or not all(isinstance(s, dict) and isinstance(s.get("text"), str)
                                                    for s in steps):
                errors.append('"steps" must be 1 to 12 objects with a "text" string')
            data.setdefault("outside_knowledge", "")
        return data, errors

    user = USER_TEMPLATE.format(chunks=format_chunks([h.chunk for h in hits]) or "(no matching notes)",
                                label="PROBLEM", text=problem, extra="")
    return TutorResult("llm", _label(client), _run(client, WALK_SYSTEM, user, validate), _sources(hits))


def check_work(problem: str, attempt: str, chunks: list[Chunk], client=None) -> TutorResult:
    problem = _check_text(problem, "The problem")
    attempt = _check_text(attempt, "Your attempt")
    hits = relevant(chunks, problem + " " + attempt)
    if client is None:
        return TutorResult("local", LOCAL_LABEL, {
            "verdict": "", "first_error": "", "next_step": "",
            "feedback": ["Checking a worked solution needs an LLM provider (Settings > AI provider).",
                         "Meanwhile, compare each step with the passages below:"] + CHECKLIST,
            "citations": [], "outside_knowledge": ""}, _sources(hits))
    allowed = {h.chunk.id for h in hits}

    def validate(reply):
        data, errors = _validate(reply, allowed, {"verdict": str, "feedback": list, "next_step": str})
        if data and data.get("verdict") not in ("correct", "partly", "incorrect"):
            errors.append('"verdict" must be correct, partly or incorrect')
        data.setdefault("first_error", "")
        data.setdefault("outside_knowledge", "")
        return data, errors

    user = USER_TEMPLATE.format(chunks=format_chunks([h.chunk for h in hits]) or "(no matching notes)",
                                label="PROBLEM", text=problem, extra=f"\nSTUDENT ATTEMPT:\n{attempt}\n")
    return TutorResult("llm", _label(client), _run(client, CHECK_SYSTEM, user, validate), _sources(hits))


def key_terms(hits) -> list[tuple[str, str]]:
    """(term, chunk id): defined terms first, then the most distinctive words of the passages."""
    chunks = [h.chunk for h in hits]
    terms: list[tuple[str, str]] = [(fact.term, fact.chunk_id) for fact in extract_facts(chunks)]
    seen = {t.lower() for t, _ in terms}
    for hit in hits[:3]:
        counts: dict[str, int] = {}
        for token in tokenize(hit.chunk.text):
            if len(token) > 4 and not token.isdigit():
                counts[token] = counts.get(token, 0) + 1
        for token, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:4]:
            if token not in seen:
                seen.add(token)
                terms.append((token, hit.chunk.id))
    return terms[:12]


def explain_back(concept: str, explanation: str, chunks: list[Chunk], client=None) -> TutorResult:
    concept = _check_text(concept, "The concept")
    explanation = _check_text(explanation, "Your explanation")
    hits = relevant(chunks, concept, k=4)
    if not hits:
        raise TutorError(f"Your notes don't cover {concept!r}, so there is nothing to compare against.")
    if client is None:
        said = set(tokenize(explanation))
        covered, missing = [], []
        for term, chunk_id in key_terms(hits):
            words = set(tokenize(term))
            (covered if words and words <= said else missing).append({"point": term, "citation": chunk_id})
        total = len(covered) + len(missing)
        score = round(100 * len(covered) / total) if total else 0
        follow = f"How does {missing[0]['point']} fit into {concept}?" if missing else \
            f"Can you give an example of {concept} from your own life?"
        return TutorResult("local", "LOCAL check (no LLM): which key terms from your notes your explanation uses",
                           {"score": score, "covered": covered, "missing": missing, "misconceptions": [],
                            "follow_up": follow}, _sources(hits))
    allowed = {h.chunk.id for h in hits}

    def validate(reply):
        data, errors = _validate(reply, allowed, {"covered": list, "missing": list, "follow_up": str})
        score = data.get("score") if data else None
        if data and (not isinstance(score, (int, float)) or not 0 <= score <= 100):
            errors.append('"score" must be a number from 0 to 100')
        data.setdefault("misconceptions", [])
        return data, errors

    user = USER_TEMPLATE.format(chunks=format_chunks([h.chunk for h in hits]), label="CONCEPT", text=concept,
                                extra=f"\nSTUDENT EXPLANATION:\n{explanation}\n")
    return TutorResult("llm", _label(client), _run(client, EXPLAIN_SYSTEM, user, validate), _sources(hits))
