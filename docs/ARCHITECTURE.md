# Architecture

Course Compass is a standard-library-only Python package in two layers. The study core (below) is a set of
commands that read a local JSON file, do their work and write it back. The school hub (`course_compass/hub/`,
last section) adds a LEARN sync, a SQLite store and a local web app on top of that core.

## Data flow

```text
                 ingest                         ask
 notes/*.md ──► ingest.py ──► chunking.py ──► store.py (db.json)
   (guarded paths,           (heading-aware      │
    size caps)                chunks + ids)      ▼
                                           retrieval.py (TF-IDF index, rebuilt per command)
                                                 │ top-k hits + scores
                                                 ▼
                                   score < threshold? ──yes──► refusal "Not in your notes."
                                                 │ no
                         ┌───────────────────────┴───────────────────────┐
                 provider local                        provider claude / openai
                         ▼                                               ▼
              answering.extractive_answer                   answering.llm_answer
              (quote chunks verbatim,                       prompts.py ─► llm.py: ClaudeCLIClient (`claude -p`)
               labelled LOCAL FALLBACK)                        or LLMClient (HTTP); typed LLMError on failure
                                                            ─► validate ─► repair once ─► accept / reject

 quiz:   chunks ─► quiz.extract_facts ─► local_quiz (template)   or   llm_quiz (prompt + validate + repair)
                                     ─► QuizItem + new review Card saved to db.json
 review: db cards ─► review.due_queue ─► grade ─► review.schedule (SM-2) ─► db.json
 plan:   attempts ─► review.topic_stats ─► weak_topics ─► study_plan
 export: chunks + attempts + quiz items ─► markdown/json study sheet
```

## Modules

| Module | Responsibility | Depends on |
|---|---|---|
| `cli.py` | argparse commands, human-readable output, exit codes (0 ok / 2 error), choosing the provider (`requested_provider`, `pick_client`) | everything below |
| `__main__.py` | `python3 -m course_compass` → `cli.main()` | `cli` |
| `ingest.py` | Path guard (`..` refused, resolved path and symlink targets must be inside an allowed root), file discovery, size caps, duplicate-topic renaming | `chunking` |
| `chunking.py` | Markdown → sections at headings → chunks of ≤ `max_words` with `overlap`; stable ids `topic#n` | – |
| `retrieval.py` | Tokenizer (lowercase, stopwords, plural folding), TF-IDF vectors with heading words counted twice, cosine search | `chunking` |
| `answering.py` | Question limits, refusal threshold, extractive fallback, LLM answer + JSON/citation validation + one repair | `retrieval`, `prompts`, `llm` |
| `quiz.py` | Definition extraction, local template quiz, distractor ranking, item validation, LLM quiz + one repair | `prompts`, `llm`, `answering.parse_json_object` |
| `review.py` | SM-2 scheduling, due queue, weak-topic stats, study-plan allocation. Pure functions, no I/O | – |
| `store.py` | Load/validate/atomically save the JSON database | – |
| `llm.py` | Two providers with one `chat(messages, temperature)` interface and one `LLMError` family: `LLMClient` (env-var BYOK config, base-URL check, chat-completions HTTP with timeout) and `ClaudeCLIClient` (runs `claude -p` in a throwaway directory, system prompt via `--system-prompt`, transcript on stdin, JSON result parsed, errors redacted) | stdlib `urllib`, `subprocess` |
| `prompts.py` | All prompt templates and chunk formatting | – |
| `evaluation.py` | Retrieval benchmark over a question set (used by `eval` and `tests/test_evaluation.py`) | `retrieval` |
| `llm_eval.py` | Real-model eval: recording/corrupting client wrappers, grounding/citation/refusal/quiz scorers, optional LLM judge, runner and summary (driven by `scripts/eval_llm.py`) | `answering`, `quiz`, `retrieval` |

`review.py`, `chunking.py`, `retrieval.py` and `prompts.py` do no I/O at all, which keeps them trivially testable.

## Key decisions

**Standard library only.** `python3 -m unittest discover` must run on any machine with Python 3.11+, with no
virtualenv. This rules out numpy/scikit-learn (TF-IDF is ~60 lines by hand) and the `openai` SDK (`urllib`
is enough for one POST).

**Index rebuilt on every command.** For a few hundred chunks building the TF-IDF index takes milliseconds, so
the database stores only chunks, not vectors. One less thing to go stale or corrupt.

**Chunk ids are `topic#n`.** Readable in citations (`[elasticity#2]`), stable as long as the note doesn't
change, and validated with a strict regex. Re-ingesting drops quiz items whose source chunk no longer exists,
so citations in saved quizzes never dangle.

**Injectable transport.** `LLMClient(config, opener=...)` takes a `urlopen`-compatible callable. Tests pass a
fake opener (success, non-200, `HTTPError`, timeout, bad JSON), so no test touches the network. Answering and
quiz code depend only on an object with `chat(messages, temperature)`, so tests use an even simpler
`FakeClient`.

**Validation lives next to generation.** `validate_answer_payload` and `validate_item` return a *list of
error strings* rather than raising. The same list becomes (a) the repair prompt sent back to the model and
(b) the final error shown to the user. Hard rules are code, never just prompt text.

**Provider selection.** `--provider claude|openai|local` (or `COURSE_COMPASS_PROVIDER`; the flag wins) picks
explicitly, and an explicitly requested LLM that can't run (no `claude` binary, not signed in, no key) is an
error (exit 2), never a quiet fallback. `--local` forces the fallback. `--llm` means the env-var provider if
set, else the OpenAI-compatible one, which requires a key (exit 2 if missing), exactly as before. With nothing
requested, the OpenAI-compatible LLM is used if `COURSE_COMPASS_API_KEY` is set, and the fallback runs with a
one-line notice if not. The output header always says which ran, including the provider and model for Claude
(`LLM answer (Claude CLI: sonnet)`).

**Claude CLI as a provider.** `ClaudeCLIClient` makes one `claude -p` run per chat turn, with `--tools ""` (no
agent tools), `--no-session-persistence`, `--strict-mcp-config`, and Course Compass's system prompt replacing
Claude Code's own. Notes travel on stdin, not argv, so they don't show up in process listings. The run happens in
a fresh temporary directory so no project `CLAUDE.md` is picked up, and `COURSE_COMPASS_API_KEY` is removed from
the child's environment. The subprocess runner is injectable, so tests fake it the same way they fake the HTTP
opener. Timeouts map to `LLMTimeoutError`, "not logged in" style failures to `ProviderUnavailableError` (with a
`/login` hint), other failures to `LLMProviderError`, and unparseable output to `LLMResponseError`. Error text
from the CLI is trimmed to 300 characters and anything key-shaped is masked.

**Deterministic local quizzes.** `local_quiz(..., seed)` and `QuizItem.options()` (shuffled with the item id as
seed) are deterministic. You can print a quiz, think, and grade it with `--answers` in a second command, and
the letters still line up.

**Testable dates.** `--today YYYY-MM-DD` on `quiz`, `review`, `plan`, `export` lets tests (and students
planning ahead) move the clock without patching.

## Database schema (`db.json`, version 1)

```json
{
  "version": 1,
  "settings": {"max_words": 80, "overlap": 20},
  "chunks": [{"id": "elasticity#2", "source": "examples/notes/elasticity.md", "topic": "elasticity",
              "title": "Supply and Demand Elasticity", "heading": "Key terms", "text": "..."}],
  "quiz_items": {"<item id>": {"id": "...", "topic": "...", "question": "...", "correct": "...",
                               "distractors": ["...", "...", "..."], "source_chunk_id": "elasticity#2",
                               "origin": "local"}},
  "cards": {"<item id>": {"ease": 2.5, "interval": 1, "repetitions": 1, "due": "2026-10-31"}},
  "attempts": [{"item_id": "...", "topic": "elasticity", "correct": true, "chosen": "C", "date": "2026-10-30"}]
}
```

Loading checks the version and the type of every top-level field. A corrupt or unknown file is an error with
a "delete it and re-run ingest" hint, never a crash or silent reset.

## Demo page

`demo/index.html` is a single self-contained file (inline CSS and JS, no external requests) that reimplements
the local path in JavaScript: the same chunking rules, tokenizer, TF-IDF scoring, 0.15 threshold, definition
extraction and same-topic distractor preference. It has no LLM mode and says so on the page. All note text is
inserted as DOM text nodes, so pasted notes cannot inject HTML. `tests/test_demo.py` checks it statically
(no `src`/`href` to remote URLs, no `fetch`/XHR, no `innerHTML`, threshold matches the Python default).

## Tests

`tests/` (unittest, stdlib only), one file per module plus `test_cli.py` for end-to-end runs through
`cli.main()` on the sample notes in local mode, `test_evaluation.py` pinning the retrieval numbers quoted
in `LEARNING.md`, and `test_docs.py` checking `PROMPTS.md` matches the code.

## The school hub

```text
 LEARN (D2L Brightspace API, your session cookies, GET only)
      │  d2l.py: D2LClient (host-pinned, no redirects, size caps, retry on 429/5xx)
      ▼
 sync.py: Syncer ──► hub.sqlite3 (db.py: courses, items, announcements, files, grades, syllabus, changes,
      │                time_sessions, plan_blocks, sync_runs)
      ├─ files/<CODE>/<module>/...        downloaded content
      ├─ syllabus.py   outline text ─► grading scheme, key dates, contacts (each with its source line)
      └─ changes       new work, moved dates, grades, announcements ─► notify.py (Mac, ntfy)
 iCal feed fallback ─► ics.py parse ─► items           timetable .ics ─► class items (busy time)

 jobs.full_refresh:  sync ─► notes.build_index ─► planner.replan ─► notify.send_pending
 notes.py:   LEARN files + notes/<CODE>/ ─► extract.py (pdf, pptx, docx, html, ipynb, OCR) ─► chunks ─► study.json
             (the same db the study core uses, so ask / quiz / review / cram work on your LEARN slides)
 planner.py: open work + estimates (calibrated from logged time) + availability - busy ─► EDF study blocks
 grades.py:  grade rows + syllabus components + what-ifs ─► standing, projected, needed
 tutor.py:   retrieval ─► prompt ─► LLM ─► validate citations ─► repair once   (or a labelled local fallback)

 server.py (127.0.0.1, token + Host check + CSP) ─► /api/* JSON ─► web/app.js (vanilla JS, hash router)
                                                 ─► /feed/<secret>/<name>.ics ─► Apple Calendar subscription
```

Design choices:

- **SQLite for the hub, JSON for the study core.** The hub has relational data that changes in place on every
  sync (items, grades, changes), so it uses SQLite with WAL and one connection guarded by a lock. The study
  core's JSON file is reused unchanged, so every existing command works on notes that came from LEARN.
- **Per-endpoint removal.** An item is only deleted as "removed from LEARN" when the endpoint that lists it
  answered successfully on this sync. A timeout on quizzes can never wipe your quizzes.
- **Quiet first sync.** Importing a course for the first time makes one "course added" change, not hundreds of
  alerts.
- **No build step for the web app.** One ES module and one stylesheet, served as files, so the CSP can forbid
  inline script and outside requests entirely. KaTeX is vendored for math.
- **Feeds instead of exports.** Apple Calendar subscribes to `webcal://` feeds and re-fetches them, so moved due
  dates follow automatically. Deadlines are 15-minute events that end at the due time, with alarms.
