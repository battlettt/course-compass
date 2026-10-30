# Build report: Course Compass 0.1.0

Built 2026-10-30 on Linux, Python 3.12.3. Standard library only (no third-party packages at runtime or in tests).

## Tests

```console
$ python3 -m unittest discover
................................................................................
....................................
----------------------------------------------------------------------
Ran 116 tests in 0.313s

OK
```

**116 tests, 0 failures, 0 errors, 0 skipped.** No test uses the network. LLM HTTP is mocked with an injected
`urlopen` replacement (`tests/helpers.py`), and answering/quiz LLM paths use a `FakeClient`.

| File | Tests | Covers |
|---|---:|---|
| `test_answering.py` | 16 | citation ids valid, refusal below threshold, labelled extractive fallback, LLM repair-once-then-reject, `in_notes: false`, question size caps |
| `test_quiz.py` | 21 | schema validation, 3 distinct distractors, correct answer present, distractors from the notes pool, fallback quiz generation, LLM repair/reject |
| `test_chunking.py` | 12 | heading-aware splits, overlap + marker, long blocks, empty input, id format |
| `test_cli.py` | 12 | end-to-end ingest → ask → quiz → review → plan → export on sample notes (local), refusal, exit codes, `--llm` without key, path traversal, interactive stdin, `python3 -m` entry point |
| `test_llm.py` | 12 | mocked HTTP: success (URL, auth header, timeout, payload), HTTP error, non-200, timeout (2 forms), unreachable host, bad JSON, empty content, key masking, base-URL policy |
| `test_review.py` | 12 | SM-2 intervals grow on success / reset on failure, ease floor, due-queue ordering, weak-topic ranking, study-plan allocation |
| `test_retrieval.py` | 11 | known query → known chunk first, stopwords, plural folding, topic filter, empty corpus, heading boost |
| `test_ingest.py` | 7 | `..` rejection, outside-root rejection, symlink escape, empty notes, size cap, file type, duplicate names |
| `test_store.py` | 5 | round-trip, missing file, corrupt JSON, wrong version/shape, atomic save |
| `test_demo.py` | 4 | demo has no external src/href/link/import, no fetch/XHR/keys, no innerHTML, threshold matches Python |
| `test_evaluation.py` | 2 | pins the retrieval benchmark numbers quoted in `docs/LEARNING.md` |
| `test_docs.py` | 2 | `docs/PROMPTS.md` contains every prompt template verbatim; required files exist |

## CLI commands run (local fallback, no key set)

| Command | Exit | Key output |
|---|---:|---|
| `python3 -m course_compass ingest examples/notes --db .course-compass/db.json` | 0 | `Ingested 4 note file(s) into 21 chunks (max 80 words, overlap 20)` |
| `python3 -m course_compass ask "What is a p-value?" --db ... --local` | 0 | `== LOCAL FALLBACK (extractive, no LLM) ... ==`, sources `[hypothesis-testing#3]` (0.29), `[hypothesis-testing#6]` (0.22) |
| `python3 -m course_compass ask "What is the capital of Australia?" --db ...` | 0 | `Not in your notes. (best match score 0.00 is below the 0.15 threshold)` |
| `python3 -m course_compass ask "What is a p-value?" --db ... --llm` (no key) | 2 | `error: No API key found. Set COURSE_COMPASS_API_KEY ...` |
| `python3 -m course_compass quiz --topic elasticity --n 5 --db ...` | 0 | 5 questions printed, saved as review cards |
| `python3 -m course_compass quiz --topic elasticity --n 5 --answers C,A,A,C,D --db ...` | 0 | `Score: 4/5. Results saved` |
| `python3 -m course_compass review --db ...` | 0 | `Nothing due for review on 2026-10-30. Next card is due 2026-10-31.` |
| `python3 -m course_compass review --today 2026-10-31 --db ...` | 0 | `5 card(s) due on 2026-10-31 (most overdue and hardest first)` |
| `python3 -m course_compass plan --db ...` | 0 | 60 min split: 17/17/17 to unquizzed topics, 9 to elasticity (weakness 0.29, 4/5 correct) |
| `python3 -m course_compass export --format markdown --db ...` | 0 | sections `## Weak topics`, `## Key facts (weakest topic first)`, `## Quiz misses` |
| `python3 -m course_compass quiz --topic chemistry` | 2 | `error: No topic matches 'chemistry'. Known topics: ...` |
| `python3 -m course_compass ingest ../` | 2 | `error: Refusing '../': '..' path components are not allowed.` |
| `python3 -m course_compass ingest /etc/passwd` | 2 | `error: Refusing '/etc/passwd': outside the allowed directories` |
| `python3 -m course_compass eval examples/notes --questions tests/examples/questions.json` | 0 | default 80/20: top-1 16/18, top-3 18/18, 0 false refusals, 4/8 unanswerable refused |

Additional checks:

- **Real HTTP path:** with `COURSE_COMPASS_BASE_URL=http://127.0.0.1:8765/v1` pointing at a throwaway local
  server returning canned chat-completions JSON, `ask` printed `== LLM answer, grounded in your notes ==` with
  a validated `[water-cycle#3]` citation, and `quiz` produced a validated LLM item. A non-localhost `http://`
  base URL was refused (exit 2).
- **Packaging:** `pip wheel .` built `course_compass-0.1.0-py3-none-any.whl`. Installed in a fresh venv, the
  `course-compass` console script ran `--version` and `ask --local` correctly.
- **Demo dependency check:** `grep -nEi '(src|href)\s*=\s*["'\'']?(https?:)?//' demo/index.html` and
  `grep -nEi '<script[^>]+src|<link|@import|fetch\(|XMLHttpRequest' demo/index.html` both found **no matches**.
  The demo's JS was also run under Node with a minimal fake DOM: it indexed the sample notes (7 chunks, 13
  definitions), answered with cited chunks, refused off-topic and HTML-injection questions, and generated 5
  quiz items, each with 4 distinct options including the correct one.

## Changes to pre-existing code

The existing modules were kept. One bug was fixed:

- `review.study_plan` could hand out **negative minutes** when there were more topics than minutes (e.g. 8
  topics in 5 minutes gave `[-2, 1, 1, ...]`), because each block was floored to at least 1 and the shortfall
  was taken from the first block. It now uses largest-remainder rounding, so blocks always add up to exactly
  the requested minutes and zero-minute blocks are dropped. Regression test:
  `test_study_plan_never_negative_and_sums_to_budget`.

## Files

```text
.gitignore  LICENSE  README.md  SECURITY.md  SPEC.md  BUILD_REPORT.md  pyproject.toml
course_compass/  __init__.py __main__.py cli.py chunking.py retrieval.py answering.py quiz.py
                 review.py store.py ingest.py llm.py prompts.py evaluation.py
tests/           __init__.py helpers.py test_answering.py test_chunking.py test_cli.py test_demo.py
                 test_docs.py test_evaluation.py test_ingest.py test_llm.py test_quiz.py
                 test_retrieval.py test_review.py test_store.py examples/questions.json
examples/notes/  cell-respiration.md elasticity.md hypothesis-testing.md water-cycle.md
demo/            index.html
docs/            ARCHITECTURE.md LEARNING.md PROMPTS.md
```

New in this build: `cli.py`, all of `tests/`, `demo/index.html`, all docs, `README.md`, `SECURITY.md`,
`LICENSE`, `pyproject.toml`, `.gitignore`, this report. An `eval` command was also added (it isn't in the spec's
CLI list) so the measurements in `LEARNING.md` can be reproduced.

## Limitations (honest)

- **Not tested against a real hosted LLM.** No API key was used, as the spec requires. The LLM path was
  checked with mocked transports in tests and a local fake server. How well a particular model follows
  the prompts (especially "distractors verbatim from the pool") is unmeasured. Invalid output is rejected,
  so the risk is error messages, not bad output being shown.
- **TF-IDF misses paraphrase** ("produce" vs "yields"). Top-1 is 89% on a small 18-question set.
- **Near-miss off-topic questions pass the 0.15 threshold** (4 of 8 unanswerable benchmark questions score
  0.15–0.29). In LLM mode the model can still refuse via `in_notes: false`. In local mode the closest
  chunks are quoted with their scores and the student has to judge.
- **Validation can't prove support.** It checks that cited chunk ids exist, not that the chunk entails the
  sentence. For LLM quizzes it checks the source chunk and distractors, not that the "correct" answer is right.
- **Local quizzes only use `- **Term**: definition` lines**; facts written as prose are never quizzed.
- **The benchmark is small** (26 questions, 4 short generic notes), so percentages move about 5.6 points per question.
- The demo page was verified statically and under Node with a fake DOM, not in a real browser (none is
  installed in the build environment).

## Unfinished

Nothing, apart from the limitations above. Not done, as instructed: no git repository created, nothing
pushed, no expansion work.
