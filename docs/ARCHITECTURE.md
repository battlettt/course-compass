# Architecture

Course Compass is a small, standard-library-only Python package plus one static HTML page. There is no
server: every command reads a local JSON file, does its work, and writes the file back.

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
                    no key / --local                                key set / --llm
                         ▼                                               ▼
              answering.extractive_answer                   answering.llm_answer
              (quote chunks verbatim,                       prompts.py ─► llm.py (HTTP, timeout)
               labelled LOCAL FALLBACK)                     ─► validate ─► repair once ─► accept / reject

 quiz:   chunks ─► quiz.extract_facts ─► local_quiz (template)   or   llm_quiz (prompt + validate + repair)
                                     ─► QuizItem + new review Card saved to db.json
 review: db cards ─► review.due_queue ─► grade ─► review.schedule (SM-2) ─► db.json
 plan:   attempts ─► review.topic_stats ─► weak_topics ─► study_plan
 export: chunks + attempts + quiz items ─► markdown/json study sheet
```

## Modules

| Module | Responsibility | Depends on |
|---|---|---|
| `cli.py` | argparse commands, human-readable output, exit codes (0 ok / 2 error), choosing LLM vs local | everything below |
| `__main__.py` | `python3 -m course_compass` → `cli.main()` | `cli` |
| `ingest.py` | Path guard (`..` refused, resolved path and symlink targets must be inside an allowed root), file discovery, size caps, duplicate-topic renaming | `chunking` |
| `chunking.py` | Markdown → sections at headings → chunks of ≤ `max_words` with `overlap`; stable ids `topic#n` | – |
| `retrieval.py` | Tokenizer (lowercase, stopwords, plural folding), TF-IDF vectors with heading words counted twice, cosine search | `chunking` |
| `answering.py` | Question limits, refusal threshold, extractive fallback, LLM answer + JSON/citation validation + one repair | `retrieval`, `prompts`, `llm` |
| `quiz.py` | Definition extraction, local template quiz, distractor ranking, item validation, LLM quiz + one repair | `prompts`, `llm`, `answering.parse_json_object` |
| `review.py` | SM-2 scheduling, due queue, weak-topic stats, study-plan allocation. Pure functions, no I/O | – |
| `store.py` | Load/validate/atomically save the JSON database | – |
| `llm.py` | Env-var config (BYOK), base-URL check, chat-completions HTTP with timeout and typed errors | stdlib `urllib` |
| `prompts.py` | All prompt templates and chunk formatting | – |
| `evaluation.py` | Retrieval benchmark over a question set (used by `eval` and `tests/test_evaluation.py`) | `retrieval` |

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

**LLM selection.** `--local` forces the fallback. `--llm` requires a key (exit 2 if missing). Otherwise the
LLM is used if `COURSE_COMPASS_API_KEY` is set, and the fallback runs with a one-line notice if not. The output
header always says which ran.

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
