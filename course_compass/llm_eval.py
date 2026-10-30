"""Evaluate the ask and quiz paths against a real LLM provider.

The scoring helpers here are pure functions (unit-tested with fakes). The
runner `run_eval` drives the real code paths (`answer_question`, `llm_quiz`)
through a `RecordingClient`, so every raw model reply, including replies that
needed the one repair attempt, is kept for scoring. `scripts/eval_llm.py` is
the command-line entry point; see docs/EVAL_LLM.md for the method.
"""

from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .answering import (AnswerError, answer_question, parse_json_object, retrieve, split_sentences,
                        validate_answer_payload)
from .chunking import OVERLAP_MARK, Chunk
from .llm import LLMError
from .quiz import QuizError, QuizItem, _validate_llm_reply, extract_facts, llm_quiz, normalize
from .retrieval import TfidfIndex, tokenize

SUPPORT_THRESHOLD = 0.6     # share of a sentence's content words that must appear in its cited chunks
KEY_SUPPORT_THRESHOLD = 0.6  # same, for a quiz answer key against its source chunk
COPIED_RUN_WORDS = 8         # a question sharing this many consecutive words with its chunk copies it

_CITATION_RE = re.compile(r"\[[a-z0-9][a-z0-9\-]*#\d+\]")
_CITED_ID_RE = re.compile(r"\[([a-z0-9][a-z0-9\-]*#\d+)\]")


# ---------------------------------------------------------------- recording

class RecordingClient:
    """Wraps a provider client and records every call (messages, reply, seconds, error)."""

    def __init__(self, inner):
        self.inner = inner
        self.calls: list[dict] = []

    @property
    def label(self):
        return getattr(self.inner, "label", "LLM")

    def chat(self, messages, temperature=0.0):
        start = time.monotonic()
        record = {"messages": [dict(m) for m in messages], "temperature": temperature}
        try:
            reply = self.inner.chat(messages, temperature)
        except LLMError as exc:
            record.update(error=f"{type(exc).__name__}: {exc}", seconds=time.monotonic() - start)
            self.calls.append(record)
            raise
        record.update(reply=reply, seconds=time.monotonic() - start)
        self.calls.append(record)
        return reply


class CorruptFirstReplyClient:
    """Repair probe: corrupts the model's first reply so the real repair turn must fix it.

    Answers get an invented citation id; quizzes get an invented distractor. The second
    call (the repair request, a multi-turn conversation) goes to the real model unchanged.
    """

    def __init__(self, inner):
        self.inner = inner
        self.corrupted = False

    @property
    def label(self):
        return getattr(self.inner, "label", "LLM")

    def chat(self, messages, temperature=0.0):
        reply = self.inner.chat(messages, temperature)
        if self.corrupted:
            return reply
        self.corrupted = True
        return corrupt_reply(reply)


def corrupt_reply(reply: str) -> str:
    try:
        data = parse_json_object(reply)
    except ValueError:
        return reply
    if isinstance(data.get("items"), list) and data["items"]:
        first = data["items"][0]
        if isinstance(first, dict) and isinstance(first.get("distractors"), list) and first["distractors"]:
            first["distractors"][0] = "Banana republic"
    elif data.get("in_notes") is True:
        data["answer"] = _CITED_ID_RE.sub("[made-up-topic#99]", str(data.get("answer", "")))
        data["citations"] = ["made-up-topic#99"]
    return json.dumps(data)


# ---------------------------------------------------------------- text helpers

def strip_citations(text: str) -> str:
    return _CITATION_RE.sub(" ", text)


def content_words(text: str) -> set[str]:
    return set(tokenize(strip_citations(text).replace(OVERLAP_MARK, " ")))


def support(claim: str, evidence: str) -> float | None:
    """Share of the claim's content words found in the evidence (None if no content words)."""
    words = content_words(claim)
    if not words:
        return None
    return len(words & content_words(evidence)) / len(words)


def longest_shared_run(text: str, source: str) -> int:
    """Length in words of the longest run of consecutive words `text` copies from `source`."""
    a = re.findall(r"[a-z0-9]+", text.lower())
    b = re.findall(r"[a-z0-9]+", source.lower())
    best, prev = 0, [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        row = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                row[j] = prev[j - 1] + 1
                best = max(best, row[j])
        prev = row
    return best


def has_key_facts(text: str, key_facts: list[list[str]]) -> bool:
    """Every group of alternatives must have at least one match (case-insensitive, word-bounded)."""
    lowered = strip_citations(text).lower()
    return all(any(re.search(rf"(?<![a-z0-9]){re.escape(alt.lower())}", lowered) for alt in group)
               for group in key_facts)


# ---------------------------------------------------------------- answer scoring

@dataclass
class AnswerScore:
    id: str
    question: str
    answerable: bool
    kind: str
    status: str                     # answered | refused_threshold | refused_llm | rejected | error
    calls: int = 0
    first_reply_valid: bool | None = None
    first_reply_errors: list[str] = field(default_factory=list)
    raw_unknown_citations: list[str] = field(default_factory=list)
    answer: str = ""
    citations: list[str] = field(default_factory=list)
    retrieved: list[str] = field(default_factory=list)
    expected_chunks: list[str] = field(default_factory=list)
    cites_expected_chunk: bool | None = None
    sentences: int = 0
    cited_sentences: int = 0
    supported_sentences: int = 0
    min_support: float | None = None
    key_facts_present: bool | None = None
    judge_supported: bool | None = None
    judge_notes: str = ""
    error: str = ""
    seconds: float = 0.0


def score_answer_text(answer: str, chunk_text: dict[str, str]) -> dict:
    """Sentence-level grounding: every sentence should carry a citation whose chunk contains its words."""
    sentences = split_sentences(answer)
    cited = supported = 0
    supports = []
    for sentence in sentences:
        ids = _CITED_ID_RE.findall(sentence)
        if ids:
            cited += 1
        evidence = "\n".join(chunk_text.get(chunk_id, "") for chunk_id in ids)
        value = support(sentence, evidence) if ids else 0.0
        if value is None:
            continue
        supports.append(value)
        supported += value >= SUPPORT_THRESHOLD
    return {"sentences": len(sentences), "cited_sentences": cited, "supported_sentences": supported,
            "min_support": round(min(supports), 2) if supports else None}


def expected_chunk_ids(case: dict, chunks: list[Chunk]) -> list[str]:
    phrase = case.get("phrase", "").lower()
    return [chunk.id for chunk in chunks
            if chunk.topic == case.get("topic") and phrase and phrase in chunk.text.lower()]


def classify_first_reply(calls: list[dict], allowed: set[str]) -> tuple[bool | None, list[str], list[str]]:
    """(valid?, errors, unknown cited ids) for the model's first raw reply."""
    if not calls or "reply" not in calls[0]:
        return None, [], []
    reply = calls[0]["reply"]
    _, errors = validate_answer_payload(reply, allowed)
    try:
        data = parse_json_object(reply)
        cited = set(_CITED_ID_RE.findall(str(data.get("answer", ""))))
        if isinstance(data.get("citations"), list):
            cited |= {c for c in data["citations"] if isinstance(c, str)}
    except ValueError:
        cited = set()
    return not errors, errors, sorted(cited - allowed)


# ---------------------------------------------------------------- quiz scoring

@dataclass
class QuizItemScore:
    question: str
    correct: str
    distractors: list[str]
    source_chunk_id: str
    key_support: float | None
    key_in_source: bool
    correct_is_note_term: bool
    same_topic_distractors: int
    longest_option_is_correct: bool
    off_subject_distractors: int = 0
    copied_run: int = 0
    answer_in_question: bool = False
    judge_letter: str = ""
    judge_agrees: bool | None = None
    judge_also_correct: list[str] = field(default_factory=list)


def score_quiz_item(item: QuizItem, chunk_text: dict[str, str], term_topic: dict[str, str],
                    subject_of: dict[str, str] | None = None) -> QuizItemScore:
    """Mechanical quiz checks: is the key in the source chunk, and do the options look alike?

    `subject_of` maps topic -> subject; a distractor from another subject is an easy giveaway.
    """
    source = chunk_text.get(item.source_chunk_id, "")
    key_support = support(item.correct, source)
    same_topic = sum(term_topic.get(normalize(d)) == item.topic for d in item.distractors)
    lengths = [len(d) for d in item.distractors]
    subject_of = subject_of or {}
    home = subject_of.get(item.topic)
    off_subject = sum(home is not None and subject_of.get(term_topic.get(normalize(d), "")) not in (None, home)
                      for d in item.distractors)
    return QuizItemScore(
        question=item.question, correct=item.correct, distractors=list(item.distractors),
        source_chunk_id=item.source_chunk_id, key_support=None if key_support is None else round(key_support, 2),
        key_in_source=key_support is not None and key_support >= KEY_SUPPORT_THRESHOLD,
        correct_is_note_term=normalize(item.correct) in term_topic,
        same_topic_distractors=same_topic,
        longest_option_is_correct=len(item.correct) > 1.5 * max(lengths),
        off_subject_distractors=off_subject,
        copied_run=longest_shared_run(item.question, source),
        answer_in_question=normalize(item.correct) in normalize(item.question),
    )


@dataclass
class QuizScore:
    id: str
    topic: str
    n: int
    status: str          # accepted | rejected | error
    calls: int = 0
    first_reply_valid: bool | None = None
    first_reply_errors: list[str] = field(default_factory=list)
    items: list[QuizItemScore] = field(default_factory=list)
    error: str = ""
    seconds: float = 0.0


# ---------------------------------------------------------------- judges (optional second opinion)

GROUNDING_JUDGE_SYSTEM = """You are a strict grader checking whether a study answer is fully supported by the NOTE CHUNKS.
A claim is supported only if the chunks state it (paraphrase is fine; outside knowledge is not).
Reply with ONLY a JSON object: {"supported": true|false, "unsupported_claims": ["..."]}"""

QUIZ_JUDGE_SYSTEM = """You are checking a multiple-choice question against its SOURCE TEXT.
Using ONLY the source text, pick the single best option. Then list any OTHER options that the source text
would also make correct (normally none).
Reply with ONLY a JSON object: {"letter": "A", "also_correct": []}"""


def judge_answer(judge, answer: str, chunks: list[Chunk]) -> tuple[bool | None, str]:
    from .prompts import format_chunks
    messages = [{"role": "system", "content": GROUNDING_JUDGE_SYSTEM},
                {"role": "user", "content": f"NOTE CHUNKS:\n{format_chunks(chunks)}\n\nANSWER:\n{answer}"}]
    try:
        data = parse_json_object(judge.chat(messages))
    except (LLMError, ValueError) as exc:
        return None, f"judge failed: {exc}"
    verdict = data.get("supported")
    claims = data.get("unsupported_claims") or []
    return (verdict if isinstance(verdict, bool) else None), "; ".join(map(str, claims))


def judge_quiz_item(judge, item: QuizItem, source_text: str) -> tuple[str, list[str]]:
    options = item.options()
    listing = "\n".join(f"{letter}) {option}" for letter, option in zip("ABCD", options))
    messages = [{"role": "system", "content": QUIZ_JUDGE_SYSTEM},
                {"role": "user", "content": f"SOURCE TEXT:\n{source_text}\n\nQUESTION: {item.question}\n{listing}"}]
    try:
        data = parse_json_object(judge.chat(messages))
    except (LLMError, ValueError):
        return "", []
    letter = str(data.get("letter", "")).strip().upper()[:1]
    also = [str(x).strip().upper()[:1] for x in data.get("also_correct") or [] if str(x).strip()]
    return letter, [x for x in also if x in "ABCD" and x != letter]


# ---------------------------------------------------------------- runner

def load_spec(path: str | Path) -> dict:
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(spec, dict) or not isinstance(spec.get("questions"), list):
        raise ValueError("eval spec must be a JSON object with a 'questions' list")
    spec.setdefault("quizzes", [])
    return spec


def eval_question(case: dict, index: TfidfIndex, chunks: list[Chunk], client, judge=None,
                  threshold: float = 0.15, k: int = 4) -> AnswerScore:
    by_id = {chunk.id: chunk for chunk in chunks}
    answerable = case.get("answerable", True)
    score = AnswerScore(id=case["id"], question=case["question"], answerable=answerable,
                        kind=case.get("kind", "in-notes" if answerable else "off-topic"), status="",
                        expected_chunks=expected_chunk_ids(case, chunks) if answerable else [])
    hits, _ = retrieve(case["question"], index, threshold, k)
    score.retrieved = [hit.chunk.id for hit in hits]
    recorder = RecordingClient(client)
    start = time.monotonic()
    try:
        answer = answer_question(case["question"], index, client=recorder, threshold=threshold, k=k)
    except AnswerError as exc:
        answer = None
        score.error = str(exc)
        score.status = "rejected" if "rejected after one repair" in str(exc) else "error"
    score.seconds = round(time.monotonic() - start, 1)
    score.calls = len(recorder.calls)
    score.first_reply_valid, score.first_reply_errors, score.raw_unknown_citations = classify_first_reply(
        recorder.calls, set(score.retrieved))
    if answer is None:
        return score
    if answer.refused:
        score.status = "refused_llm" if recorder.calls else "refused_threshold"
        return score
    score.status = "answered"
    score.answer = answer.text
    score.citations = list(answer.citations)
    score.cites_expected_chunk = (bool(set(score.citations) & set(score.expected_chunks))
                                  if answerable else None)
    chunk_text = {chunk_id: by_id[chunk_id].text for chunk_id in score.retrieved}
    for name, value in score_answer_text(answer.text, chunk_text).items():
        setattr(score, name, value)
    if answerable and case.get("key_facts"):
        score.key_facts_present = has_key_facts(answer.text, case["key_facts"])
    if judge is not None:
        score.judge_supported, score.judge_notes = judge_answer(
            judge, answer.text, [by_id[c] for c in score.citations])
    return score


def eval_quiz(case: dict, chunks: list[Chunk], client, judge=None,
              subject_of: dict[str, str] | None = None) -> QuizScore:
    score = QuizScore(id=case["id"], topic=case["topic"], n=case["n"], status="")
    recorder = RecordingClient(client)
    start = time.monotonic()
    try:
        items = llm_quiz(chunks, case["topic"], case["n"], recorder)
        score.status = "accepted"
    except QuizError as exc:
        items = []
        score.error = str(exc)
        score.status = "rejected" if "rejected after one repair" in str(exc) else "error"
    score.seconds = round(time.monotonic() - start, 1)
    score.calls = len(recorder.calls)
    if recorder.calls and "reply" in recorder.calls[0]:
        facts = extract_facts(chunks)
        sources = {chunk.id: chunk.text for chunk in chunks if chunk.topic == case["topic"]}
        _, errors = _validate_llm_reply(recorder.calls[0]["reply"], case["n"], set(sources),
                                        {normalize(f.term) for f in facts}, sources)
        score.first_reply_valid, score.first_reply_errors = not errors, errors
    chunk_text = {chunk.id: chunk.text for chunk in chunks}
    term_topic = {normalize(fact.term): fact.topic for fact in extract_facts(chunks)}
    for item in items:
        item_score = score_quiz_item(item, chunk_text, term_topic, subject_of)
        if judge is not None:
            letter, also = judge_quiz_item(judge, item, chunk_text[item.source_chunk_id])
            item_score.judge_letter = letter
            item_score.judge_agrees = (letter == item.correct_letter()) if letter else None
            item_score.judge_also_correct = also
        score.items.append(item_score)
    return score


def subject_map(spec: dict) -> dict[str, str]:
    return {topic: subject for subject, topics in spec.get("subjects", {}).items() for topic in topics}


@dataclass
class RepairProbe:
    id: str
    calls: int
    outcome: str        # repaired | rejected | error | not_probed
    detail: str = ""


def probe_repair(case_id: str, spec: dict, index: TfidfIndex, chunks: list[Chunk], client,
                 threshold: float = 0.15, k: int = 4) -> RepairProbe:
    """Force one invalid first reply and check the real model repairs it in the second turn."""
    recorder = RecordingClient(CorruptFirstReplyClient(client))
    quiz_case = next((q for q in spec["quizzes"] if q["id"] == case_id), None)
    try:
        if quiz_case:
            llm_quiz(chunks, quiz_case["topic"], quiz_case["n"], recorder)
        else:
            case = next(q for q in spec["questions"] if q["id"] == case_id)
            answer = answer_question(case["question"], index, client=recorder, threshold=threshold, k=k)
            if answer.refused:
                return RepairProbe(case_id, len(recorder.calls), "not_probed", "model refused, nothing to corrupt")
    except (AnswerError, QuizError) as exc:
        outcome = "rejected" if "rejected after one repair" in str(exc) else "error"
        return RepairProbe(case_id, len(recorder.calls), outcome, str(exc)[:300])
    outcome = "repaired" if len(recorder.calls) == 2 else "not_probed"
    return RepairProbe(case_id, len(recorder.calls), outcome)


def run_eval(spec: dict, chunks: list[Chunk], client, judge=None, threshold: float = 0.15, k: int = 4,
             workers: int = 1, log=None) -> dict:
    index = TfidfIndex(chunks)
    lock = threading.Lock()
    subjects = subject_map(spec)

    def note(message):
        if log:
            with lock:
                log(message)

    def ask(case):
        result = eval_question(case, index, chunks, client, judge, threshold, k)
        note(f"  ask {result.id}: {result.status} ({result.calls} call(s), {result.seconds}s)")
        return result

    def quiz(case):
        result = eval_quiz(case, chunks, client, judge, subjects)
        note(f"  quiz {result.id}: {result.status} ({len(result.items)} item(s), {result.seconds}s)")
        return result

    def repair(case_id):
        result = probe_repair(case_id, spec, index, chunks, client, threshold, k)
        note(f"  repair probe {result.id}: {result.outcome} ({result.calls} call(s))")
        return result

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        answers = list(pool.map(ask, spec["questions"]))
        quizzes = list(pool.map(quiz, spec["quizzes"]))
        probes = list(pool.map(repair, spec.get("repair_probes", [])))
    return {"answers": answers, "quizzes": quizzes, "repair_probes": probes,
            "summary": summarize(answers, quizzes, probes)}


# ---------------------------------------------------------------- summary

def _rate(part: int, whole: int) -> dict:
    return {"n": part, "of": whole, "pct": round(100 * part / whole, 1) if whole else None}


def summarize(answers: list[AnswerScore], quizzes: list[QuizScore], probes: list[RepairProbe] = ()) -> dict:
    inside = [a for a in answers if a.answerable]
    outside = [a for a in answers if not a.answerable]
    answered = [a for a in inside if a.status == "answered"]
    llm_asked = [a for a in answers if a.calls]
    sentences = sum(a.sentences for a in answered)
    off_reached_llm = [a for a in outside if a.status != "refused_threshold"]
    judged = [a for a in answered if a.judge_supported is not None]
    items = [item for q in quizzes for item in q.items]
    judged_items = [item for item in items if item.judge_agrees is not None]
    llm_quizzes = [q for q in quizzes if q.calls]
    return {
        "answerable": {
            "answered": _rate(len(answered), len(inside)),
            "false_refusals": _rate(sum(a.status.startswith("refused") for a in inside), len(inside)),
            "rejected_or_error": _rate(sum(a.status in ("rejected", "error") for a in inside), len(inside)),
            "key_facts_present": _rate(sum(bool(a.key_facts_present) for a in answered), len(answered)),
        },
        "grounding": {
            "sentences_with_citation": _rate(sum(a.cited_sentences for a in answered), sentences),
            "sentences_lexically_supported": _rate(sum(a.supported_sentences for a in answered), sentences),
            "answers_fully_supported_lexical": _rate(
                sum(a.sentences > 0 and a.supported_sentences == a.sentences for a in answered), len(answered)),
            "answers_judged_supported": _rate(sum(bool(a.judge_supported) for a in judged), len(judged)),
        },
        "citations": {
            "final_citations_in_retrieved": _rate(
                sum(set(a.citations) <= set(a.retrieved) for a in answered), len(answered)),
            "cites_expected_chunk": _rate(sum(bool(a.cites_expected_chunk) for a in answered), len(answered)),
            "raw_first_replies_with_unknown_ids": _rate(
                sum(bool(a.raw_unknown_citations) for a in llm_asked), len(llm_asked)),
        },
        "refusal": {
            "off_topic_refused": _rate(sum(a.status.startswith("refused") for a in outside), len(outside)),
            "off_topic_refused_by_threshold": _rate(
                sum(a.status == "refused_threshold" for a in outside), len(outside)),
            "off_topic_reaching_llm_refused_by_llm": _rate(
                sum(a.status == "refused_llm" for a in off_reached_llm), len(off_reached_llm)),
            "off_topic_answered_hallucinated": _rate(sum(a.status == "answered" for a in outside), len(outside)),
        },
        "structured_output": {
            "ask_first_reply_valid": _rate(sum(bool(a.first_reply_valid) for a in llm_asked), len(llm_asked)),
            "ask_needed_repair": _rate(sum(a.calls >= 2 for a in llm_asked), len(llm_asked)),
            "ask_rejected_after_repair": _rate(sum(a.status == "rejected" for a in llm_asked), len(llm_asked)),
            "ask_provider_errors": _rate(sum(a.status == "error" for a in answers), len(answers)),
            "quiz_first_reply_valid": _rate(sum(bool(q.first_reply_valid) for q in llm_quizzes),
                                            len(llm_quizzes)),
            "quiz_needed_repair": _rate(sum(q.calls >= 2 for q in llm_quizzes), len(llm_quizzes)),
            "quiz_rejected_after_repair": _rate(sum(q.status == "rejected" for q in llm_quizzes),
                                                len(llm_quizzes)),
            "forced_repair_probes_repaired": _rate(sum(p.outcome == "repaired" for p in probes),
                                                   sum(p.outcome != "not_probed" for p in probes)),
        },
        "quiz": {
            "generations_accepted": _rate(sum(q.status == "accepted" for q in quizzes), len(quizzes)),
            "items": len(items),
            "key_in_source_lexical": _rate(sum(item.key_in_source for item in items), len(items)),
            "judge_agrees_with_key": _rate(sum(bool(item.judge_agrees) for item in judged_items),
                                           len(judged_items)),
            "items_with_another_correct_option": _rate(
                sum(bool(item.judge_also_correct) for item in judged_items), len(judged_items)),
            "correct_answer_is_note_term": _rate(sum(item.correct_is_note_term for item in items), len(items)),
            "distractors_same_topic": _rate(sum(item.same_topic_distractors for item in items), 3 * len(items)),
            "distractors_off_subject": _rate(sum(item.off_subject_distractors for item in items), 3 * len(items)),
            "items_with_off_subject_distractor": _rate(
                sum(item.off_subject_distractors > 0 for item in items), len(items)),
            "questions_copying_source": _rate(
                sum(item.copied_run >= COPIED_RUN_WORDS for item in items), len(items)),
            "answer_term_in_question": _rate(sum(item.answer_in_question for item in items), len(items)),
            "longest_option_gives_answer_away": _rate(
                sum(item.longest_option_is_correct for item in items), len(items)),
        },
        "seconds": {
            "ask_mean": round(sum(a.seconds for a in llm_asked) / len(llm_asked), 1) if llm_asked else None,
            "quiz_mean": round(sum(q.seconds for q in llm_quizzes) / len(llm_quizzes), 1) if llm_quizzes else None,
        },
    }


def format_summary(summary: dict) -> str:
    lines = []
    for section, metrics in summary.items():
        lines.append(f"{section}:")
        for name, value in metrics.items():
            if isinstance(value, dict):
                pct = "n/a" if value["pct"] is None else f"{value['pct']}%"
                lines.append(f"  {name:<40} {value['n']:>3}/{value['of']:<3} {pct}")
            else:
                lines.append(f"  {name:<40} {value}")
    return "\n".join(lines)


# ---------------------------------------------------------------- rescoring saved runs

def rescore(saved: dict, spec: dict, chunks: list[Chunk]) -> dict:
    """Recompute scores for a saved results file with the current helpers (no model calls).

    Lets an older run be compared on metrics added later. Judge verdicts are kept as recorded.
    """
    chunk_text = {chunk.id: chunk.text for chunk in chunks}
    term_topic = {normalize(fact.term): fact.topic for fact in extract_facts(chunks)}
    subjects = subject_map(spec)
    answers = []
    for raw in saved["answers"]:
        score = AnswerScore(**raw)
        if score.status == "answered":
            for name, value in score_answer_text(score.answer, {i: chunk_text[i] for i in score.retrieved}).items():
                setattr(score, name, value)
        answers.append(score)
    quizzes = []
    for raw in saved["quizzes"]:
        items = []
        for item_raw in raw["items"]:
            item = QuizItem(id="", topic=raw["topic"], question=item_raw["question"], correct=item_raw["correct"],
                            distractors=item_raw["distractors"], source_chunk_id=item_raw["source_chunk_id"],
                            origin="llm")
            fresh = score_quiz_item(item, chunk_text, term_topic, subjects)
            for name in ("judge_letter", "judge_agrees", "judge_also_correct"):
                setattr(fresh, name, item_raw.get(name, getattr(fresh, name)))
            items.append(fresh)
        quizzes.append(QuizScore(**{**raw, "items": items}))
    probes = [RepairProbe(**raw) for raw in saved.get("repair_probes", [])]
    return {"answers": answers, "quizzes": quizzes, "repair_probes": probes,
            "summary": summarize(answers, quizzes, probes)}
