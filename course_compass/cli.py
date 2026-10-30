"""Command-line interface: ingest -> ask -> quiz -> review -> plan -> export.

Exit codes: 0 on success (an honest "not in your notes" refusal is a
success), 2 on any user-facing error (bad input, missing database, missing
key, LLM output rejected after repair).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from . import __version__
from .answering import DEFAULT_THRESHOLD, DEFAULT_TOP_K, AnswerError, answer_question
from .chunking import DEFAULT_MAX_WORDS, DEFAULT_OVERLAP, Chunk
from .evaluation import evaluate, load_questions
from .ingest import IngestError, ingest_paths
from .llm import LLMClient, LLMConfig, LLMError, MissingKeyError, key_available
from .quiz import QuizError, QuizItem, extract_facts, llm_quiz, local_quiz, resolve_topic
from .retrieval import TfidfIndex
from .review import (Card, due_queue, new_card, quality_from_correct, schedule, study_plan,
                     topic_stats, weak_topics)
from .store import StoreError, load_db, save_db

DEFAULT_DB = ".course-compass/db.json"
EXIT_OK = 0
EXIT_ERROR = 2
LOCAL_NOTICE = "No COURSE_COMPASS_API_KEY set: using the LOCAL fallback (no LLM)."


class CLIError(Exception):
    pass


# ---------------------------------------------------------------- helpers

def parse_today(value: str | None) -> date:
    if not value:
        return date.today()
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise CLIError(f"--today must be YYYY-MM-DD, got {value!r}.") from None


def pick_client(args, out):
    """Return an LLM client, or None for the local fallback."""
    if getattr(args, "local", False):
        return None
    if getattr(args, "llm", False) or key_available():
        return LLMClient(LLMConfig.from_env())  # raises MissingKeyError with a clear message
    print(LOCAL_NOTICE, file=out)
    return None


def load_chunks(db: dict) -> list[Chunk]:
    chunks = [Chunk.from_dict(raw) for raw in db["chunks"]]
    if not chunks:
        raise CLIError("The database has no notes. Run `ingest` first.")
    return chunks


def parse_answers(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    letters = [part.strip().upper() for part in raw.replace(" ", ",").split(",") if part.strip()]
    bad = [letter for letter in letters if letter not in ("A", "B", "C", "D")]
    if bad or not letters:
        raise CLIError("--answers must be letters A-D separated by commas, e.g. B,A,D.")
    return letters


def print_item(number: int, item: QuizItem, out) -> None:
    print(f"\nQ{number}. [{item.topic}] {item.question}", file=out)
    for letter, option in zip("ABCD", item.options()):
        print(f"   {letter}) {option}", file=out)


def ask_letter(prompt: str, out) -> str | None:
    """Read one answer letter from stdin; None on EOF or blank (stop)."""
    while True:
        print(prompt, end="", file=out, flush=True)
        line = sys.stdin.readline()
        if not line or not line.strip():
            return None
        letter = line.strip().upper()
        if letter in ("A", "B", "C", "D"):
            return letter
        print("Please type A, B, C or D (blank line to stop).", file=out)


def record_attempt(db: dict, item: QuizItem, letter: str, today: date) -> bool:
    correct = item.correct_letter() == letter
    card = Card.from_dict(db["cards"].get(item.id) or new_card(today).to_dict())
    db["cards"][item.id] = schedule(card, quality_from_correct(correct), today).to_dict()
    db["attempts"].append({"item_id": item.id, "topic": item.topic, "correct": correct,
                           "chosen": letter, "date": today.isoformat()})
    return correct


def grade(db: dict, items: list[QuizItem], letters: list[str] | None, interactive: bool,
          today: date, out) -> tuple[int, int]:
    """Grade items from --answers or interactive input. Returns (right, answered)."""
    right = answered = 0
    for number, item in enumerate(items, start=1):
        if letters is not None:
            if number > len(letters):
                break
            letter = letters[number - 1]
        else:
            print_item(number, item, out)
            letter = ask_letter("Your answer: ", out) if interactive else None
            if letter is None:
                break
        ok = record_attempt(db, item, letter, today)
        answered += 1
        right += ok
        verdict = "correct" if ok else f"wrong - answer {item.correct_letter()}) {item.correct}"
        print(f"Q{number}: you chose {letter}: {verdict}  (source {item.source_chunk_id})", file=out)
    return right, answered


def all_topics(db: dict) -> list[str]:
    return sorted({raw["topic"] for raw in db["chunks"]})


# ---------------------------------------------------------------- commands

def cmd_ingest(args, out) -> int:
    roots = [Path(p) for p in (args.allow_dir or ["."])]
    chunks = ingest_paths(args.paths, roots, max_words=args.max_words, overlap=args.overlap)
    db = load_db(args.db, must_exist=False)
    db["chunks"] = [chunk.to_dict() for chunk in chunks]
    db["settings"].update({"max_words": args.max_words, "overlap": args.overlap})
    known = {chunk.id for chunk in chunks}
    stale = [item_id for item_id, raw in db["quiz_items"].items() if raw["source_chunk_id"] not in known]
    for item_id in stale:
        db["quiz_items"].pop(item_id)
        db["cards"].pop(item_id, None)
    save_db(args.db, db)
    sources = sorted({chunk.source for chunk in chunks})
    print(f"Ingested {len(sources)} note file(s) into {len(chunks)} chunks "
          f"(max {args.max_words} words, overlap {args.overlap}) -> {args.db}", file=out)
    for source in sources:
        count = sum(1 for chunk in chunks if chunk.source == source)
        print(f"  {source}: {count} chunks", file=out)
    if stale:
        print(f"Dropped {len(stale)} quiz item(s) whose source chunk no longer exists.", file=out)
    return EXIT_OK


def cmd_ask(args, out) -> int:
    db = load_db(args.db)
    index = TfidfIndex(load_chunks(db))
    client = pick_client(args, out)
    answer = answer_question(args.question, index, client=client, threshold=args.threshold, k=args.k)
    print(f"== {answer.label} ==", file=out)
    print(answer.text, file=out)
    if answer.refused:
        if answer.hits:
            print("\nClosest chunks (too weak to answer from):", file=out)
            for hit in answer.hits[:3]:
                print(f"  [{hit.chunk.id}] {hit.chunk.heading} (score {hit.score:.2f})", file=out)
        return EXIT_OK
    by_id = {hit.chunk.id: hit for hit in answer.hits}
    print("\nSources:", file=out)
    for chunk_id in answer.citations:
        hit = by_id[chunk_id]
        print(f"  [{chunk_id}] {hit.chunk.source} > {hit.chunk.heading} (score {hit.score:.2f})", file=out)
    return EXIT_OK


def cmd_quiz(args, out) -> int:
    db = load_db(args.db)
    chunks = load_chunks(db)
    today = parse_today(args.today)
    letters = parse_answers(args.answers)
    topic = resolve_topic(args.topic, chunks)
    client = pick_client(args, out)
    if client is None:
        items = local_quiz(chunks, topic, args.n, seed=args.seed)
        print("== LOCAL FALLBACK quiz (template questions built from your definitions, no LLM) ==",
              file=out)
    else:
        items = llm_quiz(chunks, topic, args.n, client)
        print("== LLM quiz (validated: grounded source chunk, distractors from your notes) ==", file=out)
    for item in items:
        db["quiz_items"][item.id] = item.to_dict()
        db["cards"].setdefault(item.id, new_card(today).to_dict())
    if letters is None and not args.interactive:
        for number, item in enumerate(items, start=1):
            print_item(number, item, out)
        how = ("`quiz ... --answers B,A,...` (same --seed), `quiz -i`, or `review`" if client is None
               else "`review --answers B,A,...` or `review -i` (an LLM quiz is different every run)")
        print(f"\nSaved {len(items)} item(s) to your review queue. Answer them with {how}.", file=out)
        save_db(args.db, db)
        return EXIT_OK
    right, answered = grade(db, items, letters, args.interactive, today, out)
    save_db(args.db, db)
    print(f"\nScore: {right}/{answered}. Results saved; run `plan` to see weak topics.", file=out)
    return EXIT_OK


def cmd_review(args, out) -> int:
    db = load_db(args.db)
    today = parse_today(args.today)
    letters = parse_answers(args.answers)
    cards = {item_id: Card.from_dict(raw) for item_id, raw in db["cards"].items()
             if item_id in db["quiz_items"]}
    queue = due_queue(cards, today)[:args.limit]
    if not queue:
        upcoming = sorted(card.due for card in cards.values())
        hint = f" Next card is due {upcoming[0]}." if upcoming else " Run `quiz` to create cards."
        print(f"Nothing due for review on {today.isoformat()}.{hint}", file=out)
        return EXIT_OK
    items = [QuizItem.from_dict(db["quiz_items"][item_id]) for item_id in queue]
    print(f"{len(queue)} card(s) due on {today.isoformat()} (most overdue and hardest first):", file=out)
    if letters is None and not args.interactive:
        for number, item_id in enumerate(queue, start=1):
            card, item = cards[item_id], items[number - 1]
            print(f"  {number}. [{item.topic}] due {card.due}, ease {card.ease:.2f}, "
                  f"interval {card.interval}d\n     {item.question}", file=out)
        print("\nAnswer them with `review --answers B,A,...` or `review -i`.", file=out)
        return EXIT_OK
    right, answered = grade(db, items, letters, args.interactive, today, out)
    save_db(args.db, db)
    print(f"\nReviewed {answered} card(s): {right} correct. Next due dates updated.", file=out)
    return EXIT_OK


def due_counts(db: dict, today: date) -> dict[str, int]:
    cards = {item_id: Card.from_dict(raw) for item_id, raw in db["cards"].items()
             if item_id in db["quiz_items"]}
    counts: dict[str, int] = {}
    for item_id in due_queue(cards, today):
        topic = db["quiz_items"][item_id]["topic"]
        counts[topic] = counts.get(topic, 0) + 1
    return counts


def cmd_plan(args, out) -> int:
    db = load_db(args.db)
    load_chunks(db)
    today = parse_today(args.today)
    stats = topic_stats(db["attempts"], all_topics(db))
    blocks = study_plan(stats, due_counts(db, today), minutes=args.minutes)
    by_topic = {stat.topic: stat for stat in stats}
    print(f"Study plan for {today.isoformat()} ({args.minutes} minutes, weakest topics first):", file=out)
    for number, block in enumerate(blocks, start=1):
        stat = by_topic[block.topic]
        record = f"{stat.correct}/{stat.attempts} correct" if stat.attempts else "not quizzed yet"
        print(f"  {number}. {block.topic:<22} {block.minutes:>3} min  weakness {block.weakness:.2f}  "
              f"({record}, {block.due_cards} card(s) due)", file=out)
    if not db["attempts"]:
        print("\nNo quiz results yet, so every topic counts as 0.50 weak. Take a quiz to sharpen the plan.",
              file=out)
    return EXIT_OK


def study_sheet(db: dict, today: date) -> str:
    chunks = load_chunks(db)
    stats = weak_topics(topic_stats(db["attempts"], all_topics(db)))
    titles = {chunk.topic: chunk.title for chunk in chunks}
    facts = extract_facts(chunks)
    lines = [f"# Study sheet ({today.isoformat()})", "",
             "Generated by Course Compass from your own notes. Every fact is quoted from the chunk in brackets.",
             "", "## Weak topics", "", "| Topic | Correct | Attempts | Weakness |", "|---|---|---|---|"]
    for stat in stats:
        lines.append(f"| {titles.get(stat.topic, stat.topic)} | {stat.correct} | {stat.attempts} "
                     f"| {stat.weakness:.2f} |")
    lines += ["", "## Key facts (weakest topic first)", ""]
    for stat in stats:
        topic_facts = [fact for fact in facts if fact.topic == stat.topic]
        if not topic_facts:
            continue
        lines += [f"### {titles.get(stat.topic, stat.topic)}", ""]
        lines += [f"- **{fact.term}**: {fact.definition} [{fact.chunk_id}]" for fact in topic_facts]
        lines.append("")
    misses = [attempt for attempt in db["attempts"] if not attempt["correct"]]
    lines += ["## Quiz misses", ""]
    if not misses:
        lines.append("No missed questions yet.")
    seen: set[str] = set()
    for attempt in misses:
        raw = db["quiz_items"].get(attempt["item_id"])
        if raw is None or attempt["item_id"] in seen:
            continue
        seen.add(attempt["item_id"])
        wrong = sum(1 for a in misses if a["item_id"] == attempt["item_id"])
        lines.append(f"- {raw['question']}  \n  Answer: **{raw['correct']}** "
                     f"[{raw['source_chunk_id']}] (missed {wrong}x)")
    return "\n".join(lines).rstrip() + "\n"


def cmd_export(args, out) -> int:
    db = load_db(args.db)
    today = parse_today(args.today)
    if args.format == "markdown":
        text = study_sheet(db, today)
    else:
        stats = weak_topics(topic_stats(db["attempts"], all_topics(db)))
        text = json.dumps({
            "date": today.isoformat(),
            "weak_topics": [{"topic": s.topic, "attempts": s.attempts, "correct": s.correct,
                             "weakness": round(s.weakness, 3)} for s in stats],
            "facts": [vars(fact) for fact in extract_facts(load_chunks(db))],
            "misses": [a for a in db["attempts"] if not a["correct"]],
        }, indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"Wrote {args.format} study sheet to {args.out}", file=out)
    else:
        out.write(text)
    return EXIT_OK


def cmd_eval(args, out) -> int:
    questions = load_questions(args.questions)
    roots = [Path(p) for p in (args.allow_dir or ["."])]
    sizes = [int(s) for s in args.sizes.split(",")]
    overlaps = [int(o) for o in args.overlaps.split(",")]
    print(f"{'max_words':>9} {'overlap':>7} {'chunks':>6} {'top1':>9} {'top3':>9} "
          f"{'false_ref':>9} {'right_ref':>9}", file=out)
    for size in sizes:
        for overlap in overlaps:
            if overlap >= size:
                continue
            chunks = ingest_paths(args.paths, roots, max_words=size, overlap=overlap)
            r = evaluate(chunks, questions, size, overlap, threshold=args.threshold)
            print(f"{size:>9} {overlap:>7} {r.chunks:>6} {r.top1:>3}/{r.answerable:<2} {r.top1_rate:>4.0%} "
                  f"{r.top3:>3}/{r.answerable:<2} {r.top3_rate:>4.0%} {r.false_refusals:>9} "
                  f"{r.correct_refusals:>6}/{r.unanswerable}", file=out)
    return EXIT_OK


# ---------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=DEFAULT_DB, help=f"database file (default {DEFAULT_DB})")

    dated = argparse.ArgumentParser(add_help=False)
    dated.add_argument("--today", help="pretend today is YYYY-MM-DD (for planning ahead or testing)")

    mode = argparse.ArgumentParser(add_help=False)
    group = mode.add_mutually_exclusive_group()
    group.add_argument("--llm", action="store_true", help="require the LLM (needs COURSE_COMPASS_API_KEY)")
    group.add_argument("--local", action="store_true", help="force the local fallback, no network")

    answering = argparse.ArgumentParser(add_help=False)
    answering.add_argument("--answers", help="grade non-interactively, e.g. B,A,D,C,A")
    answering.add_argument("-i", "--interactive", action="store_true", help="answer questions at the prompt")

    parser = argparse.ArgumentParser(
        prog="course-compass",
        description="A study companion grounded in your own notes: ask, quiz, review, plan.")
    parser.add_argument("--version", action="version", version=f"course-compass {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser("ingest", parents=[common], help="chunk and index note files")
    p.add_argument("paths", nargs="+", help="note files or folders (.md, .markdown, .txt)")
    p.add_argument("--allow-dir", action="append",
                   help="directory notes must live under (repeatable; default: current directory)")
    p.add_argument("--max-words", type=int, default=DEFAULT_MAX_WORDS)
    p.add_argument("--overlap", type=int, default=DEFAULT_OVERLAP)
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("ask", parents=[common, mode], help="ask a question, get a cited answer")
    p.add_argument("question")
    p.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                   help=f"refuse below this retrieval score (default {DEFAULT_THRESHOLD})")
    p.add_argument("-k", type=int, default=DEFAULT_TOP_K, help="chunks to retrieve")
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("quiz", parents=[common, dated, mode, answering], help="generate a quiz from notes")
    p.add_argument("--topic", help="topic slug or title word, e.g. elasticity")
    p.add_argument("--n", type=int, default=5, help="number of questions (1-20)")
    p.add_argument("--seed", type=int, default=0, help="local quiz shuffle seed")
    p.set_defaults(func=cmd_quiz)

    p = sub.add_parser("review", parents=[common, dated, answering], help="spaced-review queue")
    p.add_argument("--limit", type=int, default=10, help="max cards per session")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("plan", parents=[common, dated], help="study plan, weakest topics first")
    p.add_argument("--minutes", type=int, default=60)
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("export", parents=[common, dated], help="printable study sheet")
    p.add_argument("--format", choices=["markdown", "json"], default="markdown")
    p.add_argument("--out", help="write to this file instead of stdout")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("eval", help="measure retrieval on a question set across chunk sizes")
    p.add_argument("paths", nargs="+")
    p.add_argument("--questions", required=True, help="JSON question set (see tests/examples)")
    p.add_argument("--allow-dir", action="append")
    p.add_argument("--sizes", default="40,80,120,200")
    p.add_argument("--overlaps", default="0,20")
    p.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    p.set_defaults(func=cmd_eval)
    return parser


def main(argv: list[str] | None = None, out=None) -> int:
    out = out or sys.stdout
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse exits 2 on bad usage, 0 on --help
        return int(exc.code or 0)
    if not args.command:
        parser.print_help(out)
        return EXIT_ERROR
    try:
        return args.func(args, out)
    except (CLIError, IngestError, StoreError, AnswerError, QuizError, MissingKeyError, LLMError,
            ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
