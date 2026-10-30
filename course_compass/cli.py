"""Command-line interface: ingest -> ask -> quiz -> review -> plan -> export,
plus simplify, examples, due, progress and cram.

Exit codes: 0 on success (an honest "not in your notes" refusal is a
success), 2 on any user-facing error (bad input, missing database, missing
key, LLM output rejected after repair).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from . import __version__
from .answering import DEFAULT_THRESHOLD, DEFAULT_TOP_K, AnswerError, answer_question, provider_tag
from .chunking import DEFAULT_MAX_WORDS, DEFAULT_OVERLAP, Chunk
from .evaluation import evaluate, load_questions
from .explain import (DEFAULT_EXAMPLES, GROUNDED, ExplainError, real_life_examples, select_chunks,
                      simplify)
from .ingest import IngestError, ingest_paths
from .llm import (ClaudeCLIClient, ClaudeCLIConfig, LLMClient, LLMConfig, LLMError, MissingKeyError,
                  key_available)
from .quiz import QuizError, QuizItem, extract_facts, llm_quiz, local_quiz, resolve_topic
from .progress import cram_topics, split_questions, topic_progress
from .reminders import (DEFAULT_HORIZON_DAYS, due_digest, overdue_words, review_events, to_ics,
                        upcoming_count)
from .retrieval import TfidfIndex
from .review import (Card, due_queue, new_card, quality_from_correct, schedule, study_plan,
                     topic_stats, weak_topics)
from .store import StoreError, load_db, save_db

DEFAULT_DB = ".course-compass/db.json"
EXIT_OK = 0
EXIT_ERROR = 2
LOCAL_NOTICE = "No COURSE_COMPASS_API_KEY set: using the LOCAL fallback (no LLM)."
PROVIDERS = ("claude", "openai", "local")
LOCAL_QUIZ_LABEL = "LOCAL FALLBACK quiz (template questions built from your definitions, no LLM)"
LLM_QUIZ_LABEL = "LLM quiz{tag} (validated: grounded source chunk, distractors from your notes)"
LOCAL_DATA = "local data, no LLM"
CRAM_MAX = 10


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


def requested_provider(args, env=None) -> str | None:
    """The provider the user explicitly asked for, or None to auto-select.

    Precedence: --local / --provider, then COURSE_COMPASS_PROVIDER, then --llm
    (which means "the OpenAI-compatible provider" unless the env var says otherwise).
    """
    env = os.environ if env is None else env
    flag = getattr(args, "provider", None)
    local = getattr(args, "local", False)
    llm = getattr(args, "llm", False)
    if flag and local and flag != "local":
        raise CLIError(f"--local conflicts with --provider {flag}.")
    if flag == "local" and llm:
        raise CLIError("--llm conflicts with --provider local.")
    if local:
        return "local"
    if flag:
        return flag
    from_env = env.get("COURSE_COMPASS_PROVIDER", "").strip().lower()
    if from_env and from_env not in PROVIDERS:
        raise CLIError(f"COURSE_COMPASS_PROVIDER must be one of {', '.join(PROVIDERS)}, got {from_env!r}.")
    if from_env == "local" and llm:
        raise CLIError("--llm conflicts with COURSE_COMPASS_PROVIDER=local.")
    if from_env:
        return from_env
    return "openai" if llm else None


def pick_client(args, out, env=None):
    """Return an LLM client, or None for the local fallback.

    An explicitly requested LLM provider that cannot run is an error, never a
    silent fall back to local mode.
    """
    env = os.environ if env is None else env
    provider = requested_provider(args, env)
    if provider == "local":
        if not getattr(args, "local", False) and getattr(args, "provider", None) != "local":
            print("COURSE_COMPASS_PROVIDER=local: using the LOCAL fallback (no LLM).", file=out)
        return None
    if provider == "claude":
        return ClaudeCLIClient(ClaudeCLIConfig.from_env(env))  # raises ProviderUnavailableError
    if provider == "openai" or key_available(env):
        return LLMClient(LLMConfig.from_env(env))  # raises MissingKeyError with a clear message
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


def record_attempt(db: dict, item: QuizItem, letter: str, today: date, session: str | None = None) -> bool:
    correct = item.correct_letter() == letter
    card = Card.from_dict(db["cards"].get(item.id) or new_card(today).to_dict())
    db["cards"][item.id] = schedule(card, quality_from_correct(correct), today).to_dict()
    attempt = {"item_id": item.id, "topic": item.topic, "correct": correct,
               "chosen": letter, "date": today.isoformat()}
    if session:
        attempt["session"] = session
    db["attempts"].append(attempt)
    return correct


def grade(db: dict, items: list[QuizItem], letters: list[str] | None, interactive: bool,
          today: date, out, session: str | None = None) -> tuple[int, int]:
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
        ok = record_attempt(db, item, letter, today, session)
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
        print(f"== {LOCAL_QUIZ_LABEL} ==", file=out)
    else:
        items = llm_quiz(chunks, topic, args.n, client)
        print(f"== {LLM_QUIZ_LABEL.format(tag=provider_tag(client))} ==", file=out)
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
    trends = {p.topic: p for p in topic_progress(db, all_topics(db), today)}
    print(f"Study plan for {today.isoformat()} ({args.minutes} minutes, weakest topics first):", file=out)
    for number, block in enumerate(blocks, start=1):
        stat = by_topic[block.topic]
        record = f"{stat.correct}/{stat.attempts} correct" if stat.attempts else "not quizzed yet"
        trend = f", trend: {trends[block.topic].trend_text}" if stat.attempts else ""
        print(f"  {number}. {block.topic:<22} {block.minutes:>3} min  weakness {block.weakness:.2f}  "
              f"({record}{trend}, {block.due_cards} card(s) due)", file=out)
    if not db["attempts"]:
        print("\nNo quiz results yet, so every topic counts as 0.50 weak and there are no trends to show. "
              "Take a quiz to sharpen the plan.", file=out)
    else:
        print("\nRun `progress` for per-topic history and how due cards relate to it.", file=out)
    return EXIT_OK


# ---------------------------------------------------------------- new school features

def print_sources(chunks: list[Chunk], out) -> None:
    print("\nSources:", file=out)
    for chunk in chunks:
        print(f"  [{chunk.id}] {chunk.source} > {chunk.heading}", file=out)


def explain_selection(args, chunks: list[Chunk], query: str | None):
    return select_chunks(chunks, query=query, topic=args.topic, chunk_ids=args.chunk, source=args.file,
                         threshold=args.threshold, k=args.k)


def print_refusal(text: str, closest: list[Chunk], out) -> None:
    print("== Refusal ==", file=out)
    print(text, file=out)
    if closest:
        print("\nClosest chunks (too weak to use):", file=out)
        for chunk in closest:
            print(f"  [{chunk.id}] {chunk.heading}", file=out)


def cmd_simplify(args, out) -> int:
    db = load_db(args.db)
    chunks = load_chunks(db)
    selection = explain_selection(args, chunks, args.query)
    if selection.refusal:
        print_refusal(f"Not in your notes. {selection.refusal}", selection.closest, out)
        return EXIT_OK
    client = pick_client(args, out)
    result = simplify(selection, client)
    print(f"== {result.header} ==", file=out)
    if result.mode == "llm":
        print("\n-- Layer 1: the simple version --", file=out)
        print(result.simple, file=out)
        print("\n-- Layer 2: the full-depth version --", file=out)
        print(result.full, file=out)
        by_id = {chunk.id: chunk for chunk in result.chunks}
        print_sources([by_id[cid] for cid in result.citations], out)
    else:
        print("\n-- Layer 1: key sentences (extracted, not simplified) --", file=out)
        print(result.simple, file=out)
        print("\n-- Layer 2: the full notes, verbatim --", file=out)
        print(result.full, file=out)
        print_sources(result.chunks, out)
        print("\nA real plain-language simplification needs an LLM: use --provider claude, or set "
              "COURSE_COMPASS_API_KEY.", file=out)
    return EXIT_OK


def cmd_examples(args, out) -> int:
    db = load_db(args.db)
    chunks = load_chunks(db)
    selection = explain_selection(args, chunks, None if (args.topic or args.chunk or args.file) else args.concept)
    if selection.refusal:
        print_refusal(f"Not in your notes. {selection.refusal}", selection.closest, out)
        return EXIT_OK
    client = pick_client(args, out)
    result = real_life_examples(args.concept, selection, client, n=args.n)
    if result.mode == "refusal":
        print_refusal(result.note, [], out)
        return EXIT_OK
    print(f"== {result.header} ==", file=out)
    if not result.examples:
        print("(none found)", file=out)
    for number, example in enumerate(result.examples, start=1):
        if example.kind == GROUNDED:
            tag = f"[FROM YOUR NOTES: {', '.join(example.citations)}]"
        else:
            tag = f"[ILLUSTRATIVE, not from your notes; illustrates {example.illustrates}]"
        print(f"  {number}. {tag}\n     {example.text}", file=out)
    if result.note:
        print(f"\n{result.note}", file=out)
    print_sources(result.chunks, out)
    return EXIT_OK


def cmd_due(args, out) -> int:
    db = load_db(args.db)
    today = parse_today(args.today)
    topics = due_digest(db, today)
    print(f"== Due for review on {today.isoformat()} ({LOCAL_DATA}: your SM-2 review queue) ==", file=out)
    if not topics:
        soon = upcoming_count(db, today, 7)
        print("Nothing is due today." + (f" {soon} card(s) come due in the next 7 days." if soon
                                          else " Run `quiz` to create review cards."), file=out)
        return EXIT_OK
    total = sum(len(topic.cards) for topic in topics)
    print(f"{total} card(s) across {len(topics)} topic(s), most overdue and hardest first.", file=out)
    for topic in topics:
        hard = sum(1 for card in topic.cards if card.difficulty == "hard")
        extra = f", {hard} you keep finding hard" if hard else ""
        print(f"\n{topic.topic}: {len(topic.cards)} card(s), oldest {overdue_words(topic.most_overdue)}{extra}",
              file=out)
        for card in topic.cards[:args.limit]:
            print(f"  - {overdue_words(card.days_overdue)}, {card.difficulty} (ease {card.card.ease:.2f}): "
                  f"{card.question}", file=out)
        if len(topic.cards) > args.limit:
            print(f"  ... and {len(topic.cards) - args.limit} more", file=out)
    soon = upcoming_count(db, today, 7)
    if soon:
        print(f"\nComing up: {soon} more card(s) in the next 7 days.", file=out)
    print("\nStart with `review` (or `review -i`). Export reminders with `export --format ics --out reviews.ics`.",
          file=out)
    return EXIT_OK


def cmd_progress(args, out) -> int:
    db = load_db(args.db)
    load_chunks(db)
    today = parse_today(args.today)
    print(f"== Progress on {today.isoformat()} ({LOCAL_DATA}: your quiz and review history) ==", file=out)
    rows = topic_progress(db, all_topics(db), today)
    if not db["attempts"]:
        print("No quiz attempts yet, so there is no history to show trends from.", file=out)
        print("Take a quiz (e.g. `quiz --topic elasticity --answers B,A,...`) and come back.", file=out)
        for row in rows:
            print(f"  {row.topic:<22} not quizzed yet, {row.due} card(s) due", file=out)
        return EXIT_OK
    first = min(a.get("date", "") for a in db["attempts"])
    print(f"{len(db['attempts'])} attempt(s) since {first}. Weakest topics first. A trend compares the "
          f"first half of a topic's attempts with the second half.", file=out)
    for number, row in enumerate(rows, start=1):
        stat = row.stat
        record = (f"{stat.correct}/{stat.attempts} correct, weakness {stat.weakness:.2f}" if stat.attempts
                  else "not quizzed yet")
        print(f"\n{number}. {row.topic} ({record})", file=out)
        if stat.attempts:
            print(f"   trend: {row.trend_text}", file=out)
            sessions = ", ".join(f"{day} {right}/{n}" for day, right, n in row.sessions[-6:])
            print(f"   by day: {sessions}" + (f"  ({row.cram_attempts} from cram)" if row.cram_attempts else ""),
                  file=out)
        print(f"   due now: {row.due} card(s)" + (f", {row.due_missed} missed last time" if row.due_missed else ""),
              file=out)
        print(f"   {row.advice}", file=out)
    return EXIT_OK


def cmd_cram(args, out) -> int:
    db = load_db(args.db)
    chunks = load_chunks(db)
    today = parse_today(args.today)
    letters = parse_answers(args.answers)
    if not 1 <= args.n <= CRAM_MAX:
        raise CLIError(f"Cram sessions are short: --n must be between 1 and {CRAM_MAX}.")
    quizzable = {fact.topic for fact in extract_facts(chunks)}
    stats = topic_stats(db["attempts"], all_topics(db))
    topics = cram_topics(stats, quizzable, args.topics)
    if not topics:
        raise QuizError("No topic has definitions to quiz on. Notes need lines like `- **Term**: definition`.")
    seed = len(db["attempts"]) if args.seed is None else args.seed
    client = pick_client(args, out)
    by_topic = {stat.topic: stat for stat in stats}
    items: list[QuizItem] = []
    for topic, count in split_questions(topics, args.n).items():
        if client is None:
            items += local_quiz(chunks, topic, count, seed=seed)
        else:
            items += llm_quiz(chunks, topic, count, client)
    picked = ", ".join(f"{t} (weakness {by_topic[t].weakness:.2f})" for t in topics)
    print(f"== CRAM: {len(items)} rapid-fire question(s), weakest topics first: {picked} ==", file=out)
    label = LOCAL_QUIZ_LABEL if client is None else LLM_QUIZ_LABEL.format(tag=provider_tag(client))
    print(f"== {label} ==", file=out)
    for item in items:
        db["quiz_items"][item.id] = item.to_dict()
        db["cards"].setdefault(item.id, new_card(today).to_dict())
    if letters is None and not args.interactive:
        for number, item in enumerate(items, start=1):
            print_item(number, item, out)
        how = ("`cram --answers B,A,...` (same options; the questions stay the same until you answer)"
               if client is None else "`review --answers B,A,...` or `cram -i` (an LLM quiz is different every run)")
        print(f"\nSaved {len(items)} item(s) to your review queue. Answer them with {how}.", file=out)
        save_db(args.db, db)
        return EXIT_OK
    right, answered = grade(db, items, letters, args.interactive, today, out, session="cram")
    save_db(args.db, db)
    print(f"\nCram score: {right}/{answered}. Saved to the same review cards and history; "
          "run `progress` to see the movement.", file=out)
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
    if args.format == "ics":
        events = review_events(db, today, args.days)
        text = to_ics(events)
        cards = sum(len(event.questions) for event in events)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8", newline="")
            print(f"Wrote {len(events)} review event(s) ({cards} card(s), one all-day event per topic per "
                  f"due date, {today.isoformat()} to +{args.days} days; {LOCAL_DATA}) to {args.out}", file=out)
        else:
            out.write(text)
        return EXIT_OK
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
    mode.add_argument("--provider", choices=PROVIDERS,
                      help="claude (signed-in Claude Code CLI), openai (any OpenAI-compatible API, "
                           "needs COURSE_COMPASS_API_KEY) or local; default: COURSE_COMPASS_PROVIDER, "
                           "else openai if a key is set, else local")

    answering = argparse.ArgumentParser(add_help=False)
    answering.add_argument("--answers", help="grade non-interactively, e.g. B,A,D,C,A")
    answering.add_argument("-i", "--interactive", action="store_true", help="answer questions at the prompt")

    parser = argparse.ArgumentParser(
        prog="course-compass",
        description="A study companion grounded in your own notes: ask, quiz, review, plan, plus "
                    "simplify, examples, due, progress and cram.",
        epilog="LLM features (ask, quiz, simplify, examples, cram) name the provider in their header; "
               "review, due, progress, plan and export are local data and need no LLM.")
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

    p = sub.add_parser("export", parents=[common, dated],
                       help="printable study sheet, or an .ics calendar of upcoming reviews")
    p.add_argument("--format", choices=["markdown", "json", "ics"], default="markdown",
                   help="ics: one all-day event per topic per SM-2 due date (overdue cards on --today)")
    p.add_argument("--days", type=int, default=DEFAULT_HORIZON_DAYS,
                   help=f"ics only: include reviews due up to this many days ahead (default {DEFAULT_HORIZON_DAYS})")
    p.add_argument("--out", help="write to this file instead of stdout")
    p.set_defaults(func=cmd_export)

    selector = argparse.ArgumentParser(add_help=False)
    selector.add_argument("--topic", help="use every chunk of this topic (slug or title word)")
    selector.add_argument("--chunk", action="append", help="use this chunk id, e.g. elasticity#4 (repeatable)")
    selector.add_argument("--file", help="use every chunk of this ingested note file")
    selector.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                          help=f"free-text lookup: refuse below this retrieval score (default {DEFAULT_THRESHOLD})")
    selector.add_argument("-k", type=int, default=DEFAULT_TOP_K, help="free-text lookup: chunks to retrieve")

    p = sub.add_parser("simplify", parents=[common, mode, selector],
                       help="explain notes in layers: a simple paragraph, then full depth (LLM)")
    p.add_argument("query", nargs="?", help="what to explain, looked up in your notes (or use --topic/--chunk/--file)")
    p.set_defaults(func=cmd_simplify)

    p = sub.add_parser("examples", parents=[common, mode, selector],
                       help="real-life examples of a concept, grounded vs illustrative labelled (LLM)")
    p.add_argument("concept", help="the concept, e.g. \"inelastic demand\"")
    p.add_argument("--n", type=int, default=DEFAULT_EXAMPLES, help=f"number of examples (default {DEFAULT_EXAMPLES}, max 8)")
    p.set_defaults(func=cmd_examples)

    p = sub.add_parser("due", parents=[common, dated], help="what is due for review today, by topic (local)")
    p.add_argument("--limit", type=int, default=5, help="max cards listed per topic (default 5)")
    p.set_defaults(func=cmd_due)

    p = sub.add_parser("progress", parents=[common, dated],
                       help="per-topic accuracy trend over time and due cards (local)")
    p.set_defaults(func=cmd_progress)

    p = sub.add_parser("cram", parents=[common, dated, mode, answering],
                       help="short rapid-fire quiz on your weakest topics before an exam")
    p.add_argument("--n", type=int, default=5, help=f"questions per session (default 5, max {CRAM_MAX})")
    p.add_argument("--topics", type=int, default=2, help="how many of the weakest topics to drill (default 2)")
    p.add_argument("--seed", type=int, default=None,
                   help="local quiz shuffle seed (default: number of attempts so far, so each session differs)")
    p.set_defaults(func=cmd_cram)

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
    except (CLIError, IngestError, StoreError, AnswerError, QuizError, ExplainError, MissingKeyError,
            LLMError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
