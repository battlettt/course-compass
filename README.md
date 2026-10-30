# Course Compass

A study companion that turns **your own course notes** into cited answers, quizzes and a spaced-review plan,
grounded in those notes so it cannot invent facts.

- **Ask**: ask a question and get an answer that cites the exact note chunks it came from, or an honest
  *"Not in your notes."* when nothing matches well enough.
- **Quiz**: multiple-choice questions generated *from* your notes. Wrong options are real terms from your other
  notes, never invented. Every result is tracked per topic.
- **Review & plan**: an SM-2 spaced-review queue, plus a study plan that gives the most time to your weakest topics.

Answering and quiz writing use an LLM you choose (any OpenAI-compatible chat-completions API, your own key).
Without a key, a clearly labelled **local fallback** runs entirely offline.

Python 3.11+, **standard library only**: nothing to `pip install`.

## Why it matters

General chatbots answer from training data. When they're wrong they sound exactly as confident as when they're
right, and they don't know how *your* course defines things. For revision that's the worst failure mode.
Course Compass makes the model's job small and checkable: retrieve a few chunks of your notes, answer only from
them, cite each one, and refuse when the notes don't cover it. The code then **validates** what the model
returns: unknown citation ids, malformed JSON, or invented distractors get one repair attempt and are then
rejected with an error, never shown silently.

## Quick start

```bash
git clone <this repo> && cd course-compass
python3 -m course_compass ingest examples/notes --db .course-compass/db.json
python3 -m course_compass ask "What is a p-value?" --db .course-compass/db.json
python3 -m course_compass quiz --topic elasticity --n 5 --db .course-compass/db.json
python3 -m course_compass review --db .course-compass/db.json
python3 -m course_compass plan --db .course-compass/db.json
python3 -m course_compass export --format markdown --db .course-compass/db.json --out study-sheet.md
```

`--db` defaults to `.course-compass/db.json`, so you can leave it off. Optional:
`pip install .` adds a `course-compass` command that does the same as `python3 -m course_compass`.

**Static demo:** open `demo/index.html` in a browser (it also works from GitHub Pages). It runs keyword
retrieval with cited chunks and a 5-question quiz on pasted or sample notes, fully client-side, with no
network calls and no key.

## Walkthrough with real output

All output below is from a real run on the sample notes with no API key set (local fallback).

### 1. Ingest

```console
$ python3 -m course_compass ingest examples/notes --db .course-compass/db.json
Ingested 4 note file(s) into 21 chunks (max 80 words, overlap 20) -> .course-compass/db.json
  examples/notes/cell-respiration.md: 4 chunks
  examples/notes/elasticity.md: 6 chunks
  examples/notes/hypothesis-testing.md: 6 chunks
  examples/notes/water-cycle.md: 5 chunks
```

Notes are `.md`, `.markdown` or `.txt`. Headings split sections. Lines like `- **Term**: definition` become
quiz material. Paths must be inside the current directory unless you add `--allow-dir DIR`.

### 2. Ask

```console
$ python3 -m course_compass ask "What is a p-value?" --local
== LOCAL FALLBACK (extractive, no LLM): quoted verbatim from your notes ==
[hypothesis-testing#3] (Hypothesis Testing Basics > Key terms, score 0.29)
… - **P-value**: the probability of observing data at least as extreme as the sample, assuming the null hypothesis is true.
- **Significance level**: the threshold alpha, often 0.05, chosen before the test, below which a p-value leads us to reject the null hypothesis.
...
[hypothesis-testing#6] (Hypothesis Testing Basics > Reading a result, score 0.22)
If the p-value is 0.03 and alpha is 0.05, we reject the null hypothesis. ...

Sources:
  [hypothesis-testing#3] examples/notes/hypothesis-testing.md > Key terms (score 0.29)
  [hypothesis-testing#6] examples/notes/hypothesis-testing.md > Reading a result (score 0.22)

$ python3 -m course_compass ask "What is the capital of Australia?"
No COURSE_COMPASS_API_KEY set: using the LOCAL fallback (no LLM).
== Refusal ==
Not in your notes. (best match score 0.00 is below the 0.15 threshold)
```

(The `…` marks words carried over from the previous chunk as overlap.) With a key set, the header reads
`== LLM answer, grounded in your notes ==` and the answer is a short summary with inline `[chunk-id]`
citations, each checked against the chunks that were actually retrieved.

### 3. Quiz

```console
$ python3 -m course_compass quiz --topic elasticity --n 5
No COURSE_COMPASS_API_KEY set: using the LOCAL fallback (no LLM).
== LOCAL FALLBACK quiz (template questions built from your definitions, no LLM) ==

Q1. [elasticity] Which term from your notes matches this definition: "the percentage change in quantity demanded divided by the percentage change in consumer income"?
   A) Elastic demand
   B) Cross-price elasticity
   C) Income elasticity
   D) Inelastic demand
...
Saved 5 item(s) to your review queue. Answer them with `quiz ... --answers B,A,...` (same --seed), `quiz -i`, or `review`.

$ python3 -m course_compass quiz --topic elasticity --n 5 --answers C,C,A,C,D
...
Q1: you chose C: correct  (source elasticity#3)
Q2: you chose C: correct  (source elasticity#2)
...
Score: 5/5. Results saved; run `plan` to see weak topics.
```

Use `-i` to answer at the prompt instead. `--topic` takes a topic slug or any word from a note's title
(`hypothesis` → `hypothesis-testing`).

### 4. Review and plan

```console
$ python3 -m course_compass review --today 2026-10-31
5 card(s) due on 2026-10-31 (most overdue and hardest first):
  1. [elasticity] due 2026-10-31, ease 2.50, interval 1d
     Which term from your notes matches this definition: "demand whose elasticity has an absolute value less than one, ..."?
...
$ python3 -m course_compass review --today 2026-10-31 --answers C,C
Q1: you chose C: correct  (source elasticity#2)
Q2: you chose C: wrong - answer A) Elastic demand  (source elasticity#2)

Reviewed 2 card(s): 1 correct. Next due dates updated.

$ python3 -m course_compass plan --today 2026-10-31
Study plan for 2026-10-31 (60 minutes, weakest topics first):
  1. cell-respiration        18 min  weakness 0.50  (not quizzed yet, 0 card(s) due)
  2. hypothesis-testing      17 min  weakness 0.50  (not quizzed yet, 0 card(s) due)
  3. water-cycle             17 min  weakness 0.50  (not quizzed yet, 0 card(s) due)
  4. elasticity               8 min  weakness 0.22  (6/7 correct, 3 card(s) due)
```

`--today` is optional (default: today). It's handy for planning ahead.

### 5. Export a study sheet

`export --format markdown` prints (or writes with `--out`) a printable sheet with three sections: a
**weak topics** table, **key facts** for each topic (weakest first), each with its chunk id, and **quiz misses**
with the correct answer and how many times you missed it. `--format json` gives the same data as JSON.

### Commands

| Command | What it does | Useful options |
|---|---|---|
| `ingest PATHS...` | Chunk and index notes (replaces previous chunks) | `--allow-dir`, `--max-words` (80), `--overlap` (20) |
| `ask "QUESTION"` | Cited answer or refusal | `--llm` / `--local`, `--threshold` (0.15), `-k` (4) |
| `quiz` | Generate questions, save them as review cards, optionally grade | `--topic`, `--n` (5, max 20), `--answers`, `-i`, `--seed`, `--llm` / `--local` |
| `review` | Due cards, most overdue / hardest first | `--answers`, `-i`, `--limit` (10), `--today` |
| `plan` | Minutes per topic, weakest first | `--minutes` (60), `--today` |
| `export` | Study sheet | `--format markdown\|json`, `--out FILE` |
| `eval PATHS...` | Retrieval benchmark across chunk sizes | `--questions`, `--sizes`, `--overlaps`, `--threshold` |

Exit codes: `0` success (a "Not in your notes." refusal counts as success), `2` for any error (bad input,
no database, missing key with `--llm`, LLM output rejected after repair). Errors go to stderr as `error: ...`.

## LLM setup (bring your own key)

| Variable | Meaning | Default |
|---|---|---|
| `COURSE_COMPASS_API_KEY` | Your API key. Required for LLM mode. Never stored or printed. | *(unset → local fallback)* |
| `COURSE_COMPASS_MODEL` | Model name sent to the API | `gpt-4o-mini` |
| `COURSE_COMPASS_BASE_URL` | Any OpenAI-compatible `/chat/completions` endpoint | `https://api.openai.com/v1` |

```bash
export COURSE_COMPASS_API_KEY=...            # your key, from your provider
export COURSE_COMPASS_MODEL=your-model-name  # optional
export COURSE_COMPASS_BASE_URL=https://your-provider.example/v1   # optional
python3 -m course_compass ask "Why is supply more elastic in the long run?" --llm
```

The base URL must be `https://` (plain `http://` is accepted only for `localhost`, e.g. a local model server).
Requests time out after 30 s. With a key set, LLM mode is the default. `--local` forces the offline fallback,
and `--llm` makes a missing key an error instead of a silent fallback. See [SECURITY.md](SECURITY.md) for exactly
what is sent.

## What the local fallback can and cannot do

**Can:**
- Retrieve the best chunks with TF-IDF and quote them **verbatim** with ids and scores.
- Refuse questions whose best match is below the threshold.
- Build template quizzes ("Which term matches this definition?") from `- **Term**: definition` lines, with
  distractors taken from your other defined terms.
- Everything in review, plan and export (none of these need an LLM).

**Cannot:**
- Summarise or rephrase. You get the raw chunks, not an answer sentence.
- Judge relevance beyond word overlap. A question like "What was the price of oil in 2008?" shares the word
  *price* with the elasticity notes, scores 0.29, and gets those chunks quoted instead of a refusal. (In LLM
  mode the model can still answer `in_notes: false`.)
- Quiz on prose. Facts that aren't written as definition lines are never asked about.

It is always labelled `LOCAL FALLBACK ... no LLM` and never presents its output as model-generated.

## Limitations (plainly)

- **TF-IDF retrieval misses paraphrase.** It matches words, not meaning: "What does glycolysis *produce*?"
  doesn't find the chunk that says glycolysis "*yields* two pyruvate molecules" first. On the 18-question
  benchmark, top-1 accuracy is 16/18 (89%) and top-3 is 18/18. See [docs/LEARNING.md](docs/LEARNING.md).
- **The refusal threshold is a blunt tool.** On our set, no threshold refuses all near-miss questions without
  also refusing real ones (at 0.30, 8/8 unanswerable are refused but so are 5/18 answerable ones).
- **The LLM can still be wrong.** Validation guarantees that citations *exist* and the JSON is well formed. It
  cannot prove that a cited chunk actually *supports* the sentence. **Citations are your check**: open the chunk.
- **LLM quiz answers aren't verified against the chunk text.** The source chunk must exist and distractors must
  come from your notes, but whether the "correct" answer is right is up to the model (and you).
- **The sample notes are generic** textbook-level summaries written for this project, not any real course.
  The benchmark is small (26 questions on 4 short notes), so treat its percentages as rough.
- One local JSON file, one user. No sync, no accounts.

## Tests

```bash
python3 -m unittest discover
```

All tests use the standard library, mock every LLM HTTP call, and never touch the network. They cover
chunking, retrieval ranking, answering and refusal, the HTTP client, quiz validation, SM-2 scheduling,
storage, path guards, the demo page, and full CLI runs on the sample notes. The exact count is in
[BUILD_REPORT.md](BUILD_REPORT.md).

## Project layout

```text
course_compass/   cli, chunking, retrieval, answering, quiz, review, store, llm, prompts, ingest, evaluation
tests/            unittest suite + tests/examples/questions.json (retrieval benchmark)
examples/notes/   4 original sample notes
demo/index.html   static, offline demo
docs/             LEARNING.md (concepts + measurements), ARCHITECTURE.md, PROMPTS.md (verbatim prompts)
```

## License

MIT, see [LICENSE](LICENSE).
