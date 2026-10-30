# What this project teaches

This is the "why" behind Course Compass. Each section explains one idea, shows the numbers measured on the
four sample notes in `examples/notes/`, and ends with something to try. All numbers come from commands in
this repo, so you can reproduce them.

The benchmark is `tests/examples/questions.json`: **18 answerable questions** (each tagged with the topic and a
phrase the correct chunk must contain) and **8 unanswerable ones**. Four of the unanswerable ones are easy
(no word overlap, e.g. "Who won the 1998 football world cup?"). Four are deliberate *near misses* that share
words with the notes ("What is a chi-square **test**?", "What was the **price** of oil in 2008?").

```bash
python3 -m course_compass eval examples/notes --questions tests/examples/questions.json \
    --sizes 40,80,120 --overlaps 0,20
```

---

## 1. Retrieval-augmented generation in one paragraph

A language model on its own answers from whatever it absorbed in training. That is useless for revision
when your lecturer defines "power" in a particular way, and dangerous when the model is confidently wrong.
Retrieval-augmented generation (RAG) splits the problem in two:

1. **Retrieve**: find the few passages of *your* notes most related to the question (here, TF-IDF over chunks).
2. **Generate**: give the model only those passages and tell it to answer from them, citing which one it used.

The model's job shrinks from "know everything" to "read four paragraphs and summarise". That smaller job
is much easier to get right and, more importantly, **much easier to check**.

## 2. Chunking: size and overlap trade-offs

Notes are split at markdown headings first (a chunk never mixes "Errors in testing" with "Reading a result"),
then packed into chunks of at most `max_words` words. Consecutive chunks inside one section share
`overlap` words so a fact sitting on a boundary is not cut in half.

Measured on the sample notes (top-1 = the best-scoring chunk contains the expected phrase; top-3 = one of the
first three does; false refusals = answerable questions scored below the 0.15 threshold; mean words
includes the carried-over overlap words):

| max_words | overlap | chunks | mean words/chunk | top-1 | top-3 | false refusals |
|---:|---:|---:|---:|---:|---:|---:|
| 30 | 0 | 48 | 20.4 | 13/18 (72%) | 18/18 | 0 |
| 30 | 20 | 48 | 33.6 | 15/18 (83%) | 18/18 | 0 |
| 40 | 0 | 32 | 30.5 | 15/18 (83%) | 17/18 (94%) | 0 |
| 40 | 20 | 32 | 41.0 | 13/18 (72%) | 18/18 | 0 |
| 60 | 20 | 24 | 47.7 | 15/18 (83%) | 18/18 | 0 |
| 80 | 0 | 21 | 46.5 | 14/18 (78%) | 18/18 | 1 |
| **80** | **20** | **21** | **51.5** | **16/18 (89%)** | **18/18 (100%)** | **0** |
| 120 | 20 | 17 | 58.7 | 16/18 (89%) | 18/18 | 1 |
| 200 | 20 | 16 | 61.1 | 16/18 (89%) | 18/18 | 1 |

What the table teaches:

- **Small chunks are precise but fragile.** At 30–40 words a definition line is often a chunk by itself, which
  is great when the question uses the same words and bad when the explanation sits in the next chunk.
  Results also swing a lot with overlap (72% ↔ 83%) because tiny chunks are sensitive to where the
  boundaries fall.
- **Big chunks are robust but blunt.** Past ~120 words most sections become one chunk, so top-1 stops
  improving, and a long chunk "dilutes" the query words: the normalised TF-IDF score drops, and one answerable
  question fell under the refusal threshold. Big chunks also cost more LLM tokens per answer.
- **Overlap helps most at the default size.** 80/0 → 80/20 took top-1 from 14 to 16 and removed the only false
  refusal. The overlapping words are marked with a leading `…` so the quiz generator never mistakes a
  carried-over half line for a fresh definition.
- **80 words / 20 overlap is the default** because it was the best row measured. On notes this small,
  one question is 5.6 percentage points, so treat the differences as hints, not laws.

The two questions the default setting misses are instructive:

- *"What increases statistical power?"* ranks the chunk *defining* power (`hypothesis-testing#4`) above the
  chunk explaining what *increases* it (`#5`). Both are relevant; keyword overlap cannot tell "define" from
  "what changes it".
- *"What does glycolysis produce?"* ranks `cell-respiration#3` first because its overlap text mentions
  glycolysis. The right chunk says glycolysis "**yields** two pyruvate molecules": the question's "produce"
  never appears in it. This is the **paraphrase problem**, the main weakness of keyword retrieval (§6).

Both still appear in the top 3, so the LLM, which receives up to 4 chunks, can still answer correctly.

## 3. Why grounding + citation + a refusal threshold beats free generation

Three layers, each catching what the one before misses:

1. **Refusal threshold (retrieval score < 0.15 → "Not in your notes.")** No model call happens at all.
   With no word overlap the score is exactly 0, so off-topic questions are refused cheaply and reliably.
2. **Grounded prompt with `in_notes`.** Keyword overlap can pass the threshold on an off-topic question,
   so the model gets a second chance to refuse.
3. **Citations, validated.** Every claim must cite a chunk id the code actually sent. Invented ids are
   rejected. Valid ids let the student open the source and check.

The threshold trade-off, measured at 80/20:

| threshold | false refusals (of 18 answerable) | correct refusals (of 8 unanswerable) |
|---:|---:|---:|
| 0.10 | 0 | 4 |
| **0.15** (default) | **0** | **4** |
| 0.20 | 3 | 6 |
| 0.25 | 4 | 6 |
| 0.30 | 5 | 8 |

All four easy unanswerables score 0.00 and are refused at any threshold. The four near misses score
0.15–0.29 (e.g. "price of oil" → 0.29 against the elasticity notes; "chi-square test" → 0.26 against
hypothesis testing). To refuse them on score alone you need 0.30, and at that point 5 of 18 real questions
get refused too. **No single threshold separates them.** That is why the default is low (never refuse a
real question) and the second refusal is left to the LLM's `in_notes` judgement.

In local fallback mode there is no second layer: a near-miss question gets the closest chunks quoted with
their scores. The output is still honest (verbatim, labelled, scored), but it is up to you to notice that
a score of 0.29 about elasticity does not answer a question about oil prices.

Why not just let the model answer freely? Because then a wrong answer *looks exactly like* a right one.
With grounding, a wrong answer usually comes with a citation you can open and see doesn't say that.

## 4. How distractor quality is judged

A multiple-choice question is only as good as its wrong options. Course Compass applies these rules, from
hard rules (always enforced) to soft preferences:

1. **Grounded (hard).** Every distractor is a term defined somewhere in your notes. The local generator picks
   them from extracted `- **Term**: definition` lines. LLM output is rejected if any distractor is not in that
   pool. An invented distractor ("Elastic supply curve shift") may teach you a term that does not exist.
2. **Distinct (hard).** Three different options, none equal to the answer after normalising case, spacing and
   a trailing full stop ("Unit elastic" and "unit elastic." count as the same).
3. **Plausible (soft).** Same-topic terms are preferred, then terms of similar length. A definition of
   "inelastic demand" with options *Elastic demand / Unit elastic / Income elasticity* makes you think. With
   options *Catchment / ATP / Glycolysis* you only need to recognise the subject.
4. **Not a giveaway by length (soft).** Similar length stops "pick the longest option" from working.

You can see rule 3 at work: on the sample notes every water-cycle question gets three water-cycle
distractors (`test_distractors_come_from_notes_and_prefer_same_topic`). The flip side: a topic with fewer
than four defined terms has to borrow distractors from other subjects, and those questions get easier.

## 5. How spaced review scheduling works, and why

Forgetting is roughly exponential. Each successful recall makes the memory last longer, so the best time to
review is just before you would forget. Course Compass uses SM-2, the algorithm behind early SuperMemo and
the default in Anki-style tools:

- Each quiz item is a **card** with an *ease* (starts at 2.5), an *interval* in days, and a *due* date.
- An answer becomes a quality score: correct = 4, wrong = 1.
- **Pass:** interval goes 1 day → 6 days → previous interval × ease.
- **Fail:** repetitions reset, interval back to 1 day.
- Ease changes by `0.1 − (5 − q)(0.08 + 0.02(5 − q))`: +0.0 for q=4, −0.54 for q=1, never below 1.3.

Measured intervals from `course_compass.review.schedule`:

| answers | intervals (days) | ease after |
|---|---|---|
| 6 correct (q=4) | 1, 6, 15, 38, 95, 238 | 2.50 |
| 6 perfect (q=5) | 1, 6, 16, 45, 130, 390 | 2.6 → 3.1 |
| ✓ ✓ ✓ ✗ ✓ ✓ ✓ | 1, 6, 15, **1**, 1, 6, 12 | 2.50 → **1.96** |

The last row is the important one. After one miss the card restarts *and* grows more slowly afterwards
(ease 1.96: interval 12 instead of 15 at the same step). Cards you find hard come back more often for good.

The **due queue** shows overdue cards first (oldest due date), then the lowest ease (hardest), so a short
session goes where forgetting is most likely.

**Weak topics** use a Laplace-smoothed error rate, `(wrong + 1) / (attempts + 2)`. An unquizzed topic scores
0.50, not 0 or 1, so one lucky or unlucky answer doesn't dominate the ranking. **The study plan** splits your
minutes in proportion to weakness, using largest-remainder rounding so the blocks always add up to exactly
the time you asked for.

## 6. What would change with real embeddings

TF-IDF matches *words*. Embeddings (dense vectors from a neural model) match *meaning*:

- "What does glycolysis **produce**?" would match "glycolysis **yields** two pyruvate molecules". This fixes
  the paraphrase misses in §2.
- Synonyms and different phrasing ("how come cities flood", "impervious surfaces") would line up.
- Near-miss refusals would get *harder* in one way: "price of oil" really is semantically close to "price
  elasticity", so an embedding threshold would need its own tuning on a question set like ours.

What it would cost: a model download or an embeddings API call (breaking "standard library only, no
network"), and vectors stored per chunk. A common middle ground is **hybrid retrieval**: score with both
TF-IDF and embeddings and combine them, since keyword search still wins on exact terms like "Type II error".

The code is shaped for this: `TfidfIndex.search(query, k, topic)` is the only retrieval interface the rest of
the package uses, so an `EmbeddingIndex` with the same method could replace it.

## 7. Try it yourself

1. **Find the paraphrase limit.** Ask `"What does glycolysis produce?"` and then `"What does glycolysis yield?"`
   with `ask --local`. Compare the cited chunk ids and scores.
2. **Sweep chunk sizes.** Run the `eval` command above with `--sizes 20,50,100,300 --overlaps 0,10,40`. When we ran
   it, 20-word chunks dropped to 61% top-1, and going from overlap 10 to 40 changed nothing at 50, 100 or 300 words.
   Why does a bigger overlap stop helping? (Hint: how many sections in the sample notes are longer than one chunk?)
3. **Move the threshold.** Add `--threshold 0.3` to `eval`, then ask `"What was the price of oil in 2008?"` with
   `ask --local --threshold 0.3` and `--threshold 0.15`.
4. **Write a near miss.** Add your own unanswerable question to `tests/examples/questions.json` that scores
   above 0.15. (Hint: borrow a heading word.) Note: `tests/test_evaluation.py` pins the current numbers, so
   update it too.
5. **Watch the scheduler.** Take a quiz with `--today 2026-01-01`, answer one wrong, then run `review --today`
   on later dates and see which cards come back first.
6. **Break the distractors.** Make a note with only two `- **Term**: definition` lines and run `quiz` on it.
   Read the error, then add terms until it works.
7. **Test the LLM validator.** With a key set, ask something your notes cover only partly and check whether
   every `[chunk-id]` really supports the sentence it is attached to. That check is the reason citations exist.
