# Course Compass — build spec (single full build, then STOP)

Project: course-compass. Repo to be created separately by the orchestrator; build ALL code in /home/hatch/workspace/course-compass-build/app (this is your working directory; do NOT push to GitHub yourself, do NOT create the repo yourself — the orchestrator pushes).

## What it is (plain language)
A study companion for a university student that turns his OWN course notes into three useful things, all grounded in those notes so it cannot invent facts:
1. Ask: ask a question, get an answer with citations to the exact note chunks it came from, and an honest "not in your notes" refusal when retrieval is weak.
2. Quiz: generate flashcards / multiple-choice quizzes FROM the notes, with distractors drawn from other notes (not invented), and track which topics are weak.
3. Review: a spaced-review queue and a study plan that spends time on weak topics first.

It must be APPLIED AI (LLM-based), not classical ML alone: retrieval is local TF-IDF (stdlib), but answering and quiz generation go through a pluggable OpenAI-compatible chat-completions LLM client (user supplies their own API key via env var; NEVER hardcode, request, or store any key). When no key is set, a clearly-labelled LOCAL fallback runs: extractive answers (top chunks quoted verbatim) and template quizzes built from headings/definitions. The fallback must never pretend to be the LLM. Every LLM answer and quiz item is validated: quiz JSON must parse, have 1 correct + 3 distinct distractors, and every cited chunk id must exist; invalid output is repaired once then rejected with a clear error, never silently shown.

## Non-negotiables
- Python 3.11+, STANDARD LIBRARY ONLY (no pip dependencies) so `python3 -m unittest discover` runs anywhere. CLI entry: `python3 -m course_compass` and `course-compass` console behaviour via __main__.py.
- Clean structure, e.g.:
  course_compass/__init__.py, __main__.py, cli.py, chunking.py, retrieval.py, llm.py, answering.py, quiz.py, review.py, store.py, prompts.py
  tests/test_*.py (unittest, stdlib only; LLM HTTP mocked, no network in tests)
  examples/notes/*.md (3-4 short ORIGINAL sample notes you write: e.g. hypothesis testing basics, supply/demand elasticity, water-cycle / runoff basics — generic textbook-level concepts, NOT copied course material)
  demo/index.html (fully client-side static demo: paste or load sample notes, ask a question with keyword retrieval + cited chunks, take a 5-question sample quiz; NO external calls, NO keys; must work opened from GitHub Pages)
  docs/LEARNING.md, docs/ARCHITECTURE.md, docs/PROMPTS.md, README.md, LICENSE (MIT), .gitignore, pyproject.toml
- README: what it is, why it matters, how to run it (ingest -> ask -> quiz -> review, with real commands and real sample output), LLM setup (BYOK env vars COURSE_COMPASS_API_KEY, COURSE_COMPASS_MODEL, COURSE_COMPASS_BASE_URL), what the local fallback can/cannot do, limitations stated plainly (TF-IDF retrieval misses paraphrase; LLM can still err so citations are the check; sample notes are generic, not any real course).
- docs/LEARNING.md must TEACH, not just describe: chunk-size/overlap tradeoffs with the numbers you actually measured on the sample notes, why grounding + citation + refusal threshold beats free generation, how distractor quality is judged, how spaced review scheduling works and why, what you would change with real embeddings, and a short "try it yourself" experiment list. Include measured retrieval results (e.g. top-1 accuracy on a small hand-built question set in tests/examples).
- docs/PROMPTS.md: the full system + user prompt templates verbatim, and why each instruction is there (grounding, citation format, refusal, JSON schema).
- Hardening: no secrets in code; input size caps for notes and questions; path traversal guard (ingest only inside allowed dirs, reject ../ escapes); timeout on LLM HTTP; quiz store is local JSON only; graceful errors for missing key, bad JSON, empty notes, no matches. Add a small SECURITY.md noting BYOK and local-only storage.
- Tests: aim 45+ tests, covering: chunking (heading-aware splits, overlap, empty input), retrieval ranking (known query returns known chunk first, stopword handling, empty corpus), answering (citation ids valid, refusal below threshold, fallback extractive output labelled), LLM client (mocked HTTP: success, non-200, timeout, bad JSON), quiz (schema validation, distinct distractors, correct answer present, fallback quiz generation), review (SM-2-style intervals grow on success / shrink on failure, weak-topic ranking, due queue ordering), store (round-trip, corrupt file error), CLI end-to-end (ingest -> ask -> quiz -> review on sample notes, using local fallback). Run the full suite yourself and report the exact count; fix failures, do not lower assertions to pass.

## CLI (must all work)
- `python3 -m course_compass ingest examples/notes --db .course-compass/db.json`
- `python3 -m course_compass ask "What is a p-value?" --db ... [--llm|--local]`
- `python3 -m course_compass quiz --topic elasticity --n 5 --db ...`
- `python3 -m course_compass review --db ...` and `plan --db ...`
- `python3 -m course_compass export --format markdown --db ...` (a printable study sheet: weak topics + key facts + quiz misses)
Use argparse, human-readable output, exit codes 0/2 sensibly.

## Style / plugins
Use available Claude Code plugins where useful (design, engineering, data) — ask the plugin system to help with the demo page polish and test design if you know how; if not, just build to a high standard yourself. Keep code readable, small functions, no clever abstraction, no placeholder/TODO filler. Every feature listed above must actually run.

## When done
Run: full test suite, CLI end-to-end on sample notes, and a quick check that demo/index.html has no external script/link dependencies (grep for http src/href other than anchor links). Write a BUILD_REPORT.md in the app root with: exact test count and command, CLI commands run + key output, file list, limitations, and anything unfinished (must be nothing unfinished except honestly stated limitations). Then STOP — do not start expansion work, do not push, do not create repos.
