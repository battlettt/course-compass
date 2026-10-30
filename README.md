# Course Compass

A study companion that turns **your own course notes** into cited answers, quizzes and a spaced-review plan,
grounded in those notes so it cannot invent facts.

- **Ask**: ask a question and get an answer that cites the exact note chunks it came from, or an honest
  *"Not in your notes."* when nothing matches well enough.
- **Quiz**: multiple-choice questions generated *from* your notes. Wrong options are real terms from your other
  notes, never invented. Every result is tracked per topic.
- **Review & plan**: an SM-2 spaced-review queue, plus a study plan that gives the most time to your weakest topics.
- **Simplify**: explains a lecture's notes in layers, first one plain paragraph and then the full-depth version.
- **Examples**: real-life examples of a concept, each labelled as *from your notes* or *illustrative*.
- **Reminders**: a "due today" digest and an `.ics` calendar of upcoming reviews.
- **Progress**: per-topic accuracy trend over time, shown next to the cards that are due.
- **Cram**: a short rapid-fire quiz on your weakest topics before an exam, recorded like any other quiz.

Answering and quiz writing use an LLM you choose: **Claude through your signed-in Claude Code CLI** (no key,
no signup beyond the Claude account you already have) or any OpenAI-compatible chat-completions API with your
own key. Without either, a clearly labelled **local fallback** runs entirely offline. Both LLM paths have been
run against a real model on a 20-question, 3-quiz evaluation: see [docs/EVAL_LLM.md](docs/EVAL_LLM.md).

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

### 6. School features: simplify, examples, reminders, progress, cram

Every header says what produced the output. **LLM features** (`simplify`, `examples`, and `cram` when an
LLM writes the questions) use the same providers as `ask` and `quiz` and name the one that ran, e.g.
`== SIMPLIFY by LLM (Claude CLI: sonnet), ... ==`. **Local-data features** (`due`, `progress`,
`export --format ics`, and `plan`) say `local data, no LLM`. Without an LLM, `simplify` and `examples` fall back
to a clearly labelled extract of your notes. They never pass local output off as LLM output. With `--llm` or
`--provider claude|openai` a missing LLM is an error instead.

**Simplify** (LLM). Pick the notes with a query (TF-IDF lookup, same refusal threshold as `ask`), `--topic`,
`--chunk ID` (repeatable) or `--file NOTE.md`. Layer 1 is one plain paragraph of at most about 90 words;
layer 2 is the full-depth version with a `[chunk-id]` on every sentence. Both are validated (one paragraph,
known ids, every layer-2 sentence cited), with one repair and then rejection, as for `ask`.

```console
$ python3 -m course_compass simplify --topic elasticity --provider claude
== SIMPLIFY by LLM (Claude CLI: sonnet), grounded in your notes: topic "elasticity" ==

-- Layer 1: the simple version --
Elasticity tells you how much buying changes when something else changes, usually the price. If a small price
rise makes people buy a lot less, demand is elastic. If people barely change what they buy, it is inelastic. ...
[elasticity#1][elasticity#2][elasticity#4] ...

-- Layer 2: the full-depth version --
Elasticity measures how strongly one quantity responds to a change in another, and in markets it is expressed in
percentage terms so that units do not matter [elasticity#1]. Price elasticity of demand is ... [elasticity#2]. ...
```

With no LLM the header reads `SIMPLIFY: LOCAL EXTRACTIVE APPROXIMATION (no LLM, not a simplification)`, layer 1 is
the three most central sentences quoted verbatim, and layer 2 is the notes themselves.

**Examples** (LLM). Each example is either `[FROM YOUR NOTES: chunk-id]` (the notes state it, and it must cite
that chunk) or `[ILLUSTRATIVE, not from your notes; illustrates chunk-id]` (the model's own example, which may
cite nothing). The code rejects an example labelled grounded with no citation, or one that shares almost no
wording with the chunk it cites, and an illustrative example that claims a citation.

```console
$ python3 -m course_compass examples "inelastic demand" --provider claude
== EXAMPLES by LLM (Claude CLI: sonnet) for "inelastic demand": grounded ones cite your notes ==
  1. [FROM YOUR NOTES: elasticity#4, elasticity#2]
     Basic medicine is a necessity, so demand for it tends to be inelastic: ...
  3. [ILLUSTRATIVE, not from your notes; illustrates elasticity#2]
     Insulin for a person with type 1 diabetes has no close substitute and is essential, so ...
```

Without an LLM, `examples` quotes note sentences that mention a use or case ("such as", "use ... to predict",
"during ...") and makes nothing up.

**Due today** (local). The SM-2 queue grouped by topic: most overdue and hardest first, in plain words.

```console
$ python3 -m course_compass due --today 2026-11-01
== Due for review on 2026-11-01 (local data, no LLM: your SM-2 review queue) ==
6 card(s) across 2 topic(s), most overdue and hardest first.

cell-respiration: 3 card(s), oldest 1 day overdue
  - 1 day overdue, shaky (ease 1.96): Which term from your notes matches this definition: "the first stage, ..."?
...
elasticity: 3 card(s), oldest due today, 2 you keep finding hard
  - due today, hard (ease 1.42): Which term from your notes matches this definition: "demand whose ..."?
```

"new" means never answered, "hard" means ease below 1.7, "shaky" means below the starting 2.5, and "steady"
covers everything else.

**Calendar export** (local). `export --format ics` writes an RFC 5545 calendar that Google Calendar, Apple
Calendar and Outlook can import. It has **one all-day event per topic per due date** ("Review elasticity:
3 cards", with the questions in the description), not one per card, because ten card events on one day are
noise in a calendar. Overdue cards go on `--today`. `--days` (default 14) sets how far ahead to look. Text is
escaped and lines are folded at 75 octets with CRLF endings. UIDs are stable, so re-importing updates events
instead of duplicating them.

```console
$ python3 -m course_compass export --format ics --days 30 --out reviews.ics
Wrote 3 review event(s) (7 card(s), one all-day event per topic per due date, 2026-11-01 to +30 days; local data, no LLM) to reviews.ics
```

**Progress** (local). For each topic, weakest first: the record, the **trend** (accuracy over the first half
of that topic's attempts against the second half, after at least 4 attempts), accuracy by day, the due cards
and how many of them you missed last time, and one sentence linking the two. With no attempts it says so and
shows no trend. `plan` now shows the same trend on each line.

```console
$ python3 -m course_compass progress --today 2026-11-01
== Progress on 2026-11-01 (local data, no LLM: your quiz and review history) ==
13 attempt(s) since 2026-10-30. Weakest topics first. ...

1. cell-respiration (0/3 correct, weakness 0.80)
   trend: 3 attempt(s), need 4 for a trend
   by day: 2026-10-30 0/3  (3 from cram)
   due now: 3 card(s), 3 missed last time
   Too little history for a trend yet; 3 card(s) due, 3 missed last time.
...
4. elasticity (5/10 correct, weakness 0.50)
   trend: improving 20% -> 80%
   by day: 2026-10-30 1/6, 2026-10-31 4/4  (2 from cram)
   due now: 3 card(s)
   Improving; 3 card(s) due, and reviewing them on time locks the gain in.
```

**Cram** (LLM or local, same labels as `quiz`). A short session (`--n`, default 5, max 10) on the `--topics`
weakest topics (default 2) that have definitions to quiz on. The questions are dealt round-robin from the
weakest topic, so it never gets fewer than the others. Answers go through the same grading as `quiz`, so they
update the same SM-2 cards and attempt history (marked `session: cram`), and `progress` and `plan` see them.
The default local seed is the number of attempts so far, so each new session differs while showing and then
answering one session gives the same questions.

```console
$ python3 -m course_compass cram --n 5 --today 2026-10-30
== CRAM: 5 rapid-fire question(s), weakest topics first: cell-respiration (weakness 0.50), elasticity (weakness 0.50) ==
== LOCAL FALLBACK quiz (template questions built from your definitions, no LLM) ==
...
$ python3 -m course_compass cram --n 5 --today 2026-10-30 --answers D,C,A,C,C
...
Cram score: 5/5. Saved to the same review cards and history; run `progress` to see the movement.
```

### Commands

| Command | What it does | Useful options |
|---|---|---|
| `ingest PATHS...` | Chunk and index notes (replaces previous chunks) | `--allow-dir`, `--max-words` (80), `--overlap` (20) |
| `ask "QUESTION"` | Cited answer or refusal | `--provider claude\|openai\|local`, `--llm` / `--local`, `--threshold` (0.15), `-k` (4) |
| `quiz` | Generate questions, save them as review cards, optionally grade | `--topic`, `--n` (5, max 20), `--answers`, `-i`, `--seed`, `--provider`, `--llm` / `--local` |
| `review` | Due cards, most overdue / hardest first | `--answers`, `-i`, `--limit` (10), `--today` |
| `plan` | Minutes per topic, weakest first, with each topic's trend | `--minutes` (60), `--today` |
| `export` | Study sheet, or an `.ics` calendar of upcoming reviews | `--format markdown\|json\|ics`, `--days` (14, ics), `--out FILE` |
| `simplify [QUERY]` | Layered explanation: simple paragraph, then full depth (LLM; labelled extract without one) | `--topic`, `--chunk ID`, `--file`, `--provider`, `--llm` / `--local` |
| `examples "CONCEPT"` | Real-life examples labelled from-your-notes or illustrative (LLM; labelled extract without one) | `--n` (4, max 8), `--topic`, `--chunk`, `--file`, `--provider` |
| `due` | Cards due today by topic, most overdue / hardest first (local) | `--limit` (5 per topic), `--today` |
| `progress` | Per-topic trend over time and due cards (local) | `--today` |
| `cram` | Short quiz on the weakest topics, recorded to SM-2 and history | `--n` (5, max 10), `--topics` (2), `--answers`, `-i`, `--seed`, `--provider` |
| `eval PATHS...` | Retrieval benchmark across chunk sizes | `--questions`, `--sizes`, `--overlaps`, `--threshold` |

Exit codes: `0` success (a "Not in your notes." refusal counts as success), `2` for any error (bad input,
no database, missing key with `--llm`, a requested LLM provider that is unavailable, LLM output rejected after
repair). Errors go to stderr as `error: ...`.

## LLM setup

### Choosing a provider

| How you ask | What runs |
|---|---|
| `--provider claude` or `COURSE_COMPASS_PROVIDER=claude` | Claude via your signed-in Claude Code CLI. **Error** (exit 2) if `claude` is missing or not signed in. |
| `--provider openai` or `COURSE_COMPASS_PROVIDER=openai` | The OpenAI-compatible API. **Error** if `COURSE_COMPASS_API_KEY` is not set. |
| `--provider local`, `--local`, or `COURSE_COMPASS_PROVIDER=local` | The offline fallback, labelled as such. |
| `--llm` | The provider in `COURSE_COMPASS_PROVIDER` if set, otherwise `openai` (so it needs a key, as before). |
| nothing | `openai` if `COURSE_COMPASS_API_KEY` is set, otherwise the local fallback with a one-line notice (as before). |

The flag wins over the environment variable. An LLM you asked for is never silently swapped for the local
fallback: if it can't run, the command fails with the reason. The output header always names what ran:
`== LLM answer (Claude CLI: sonnet), grounded in your notes ==`, `== LLM answer, grounded in your notes ==`
(OpenAI-compatible), or `== LOCAL FALLBACK (extractive, no LLM) ... ==`.

### Claude CLI (no API key)

Uses the [Claude Code](https://claude.com/claude-code) CLI you're already signed into (e.g. a Pro plan). Each
model call is one non-interactive `claude -p --model <model> --output-format json --tools ""` run with
Course Compass's own system prompt; your notes go in on stdin. No key is read, stored or sent by Course Compass.

```bash
claude                      # once, interactively: sign in with /login if you haven't
export COURSE_COMPASS_PROVIDER=claude
python3 -m course_compass ask "Why is supply more elastic in the long run?"
python3 -m course_compass quiz --topic elasticity --n 4
```

| Variable | Meaning | Default |
|---|---|---|
| `COURSE_COMPASS_CLAUDE_MODEL` | Model alias or full name passed to `--model` | `sonnet` |
| `COURSE_COMPASS_CLAUDE_BIN` | Path to the `claude` binary, if it isn't on `PATH` | `claude` |
| `COURSE_COMPASS_CLAUDE_TIMEOUT` | Seconds per call before it fails | `120` |

**Why `sonnet` by default:** on the real eval Sonnet and Haiku were equally fast through the CLI (about 7 s per
answer, because CLI start-up dominates), so Haiku's speed advantage disappears. Sonnet is the stronger model at
the same latency, and you get more margin on refusals and grounding for notes harder than ours.
`COURSE_COMPASS_CLAUDE_MODEL=haiku` is a fine choice if you want to use less of your plan's usage allowance
(Haiku scored the same on our set); `opus` works too but is slower. Each `ask` is one model call (two if the
reply needed a repair); each `quiz` is one or two.

### OpenAI-compatible API (bring your own key)

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
- Everything in review, plan, export, due, progress and cram (local cram uses the template quiz).
- `simplify` and `examples` as a labelled extract only: key sentences and "such as"/"use ... to" sentences
  quoted from your notes. They don't simplify or invent examples.

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
- **LLM quiz answers are only partly verified.** The key must be a term from your notes that appears in the
  question's source chunk, and distractors must come from your notes. Whether the question really points to
  that term is up to the model (and you). On the eval a blind second model call agreed with every key, but
  that's a check by the same model family, not proof.
- **The Claude CLI provider can't fix the temperature.** `claude -p` has no temperature flag, so repeated runs
  vary slightly (see the run-to-run numbers in [docs/EVAL_LLM.md](docs/EVAL_LLM.md)). It also takes about 7 s
  per call, and every call counts against your Claude plan's usage limits.
- **The sample notes are generic** textbook-level summaries written for this project, not any real course.
  The benchmark is small (26 questions on 4 short notes), so treat its percentages as rough.
- One local JSON file, one user. No sync, no accounts.

## Tests

```bash
python3 -m unittest discover
```

All tests use the standard library, fake every LLM call (an injected HTTP opener or CLI runner), and never
touch the network or start a model. They cover chunking, retrieval ranking, answering and refusal, the HTTP
client, the Claude CLI client, provider selection, quiz validation, the eval scorers, SM-2 scheduling,
storage, path guards, the demo page, full CLI runs on the sample notes, and the school features: simplify
layers, grounded-vs-illustrative example labels, `.ics` round trips, progress with and without history, and
cram topic choice and SM-2 recording.

The real-model evaluation is separate, because it needs a signed-in Claude Code CLI (or a key) and takes about
3 minutes:

```bash
python3 scripts/eval_llm.py                     # Claude CLI, default model; see docs/EVAL_LLM.md
```

## Project layout

```text
course_compass/   cli, chunking, retrieval, answering, quiz, review, store, llm, prompts, ingest, evaluation,
                  llm_eval (real-model eval scoring), explain (simplify + examples), reminders (due digest +
                  .ics), progress (trends + cram topic choice)
tests/            unittest suite + tests/examples/questions.json (retrieval benchmark)
examples/notes/   4 original sample notes
examples/eval-notes/  4 more notes for the LLM eval (operations, project management, decision analysis,
                  supply and demand)
examples/eval/    llm_eval.json: the real-model eval set (20 questions, 3 quizzes)
scripts/          eval_llm.py: runs the real-model eval
demo/index.html   static, offline demo
docs/             LEARNING.md (concepts + measurements), ARCHITECTURE.md, PROMPTS.md (verbatim prompts),
                  EVAL_LLM.md (real-model eval: method, numbers, fixes)
```

## License

MIT, see [LICENSE](LICENSE).
