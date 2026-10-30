# Real-model evaluation (Claude CLI provider)

Until this eval, the LLM paths of Course Compass had only been tested against a mocked HTTP server and a fake
client. This document records a **real** evaluation: every number below comes from real `claude -p` calls
made through `ClaudeCLIClient` (the same code path as `--provider claude`), using a signed-in Claude Code Pro
account. Nothing here is mocked. The fakes live only in the unit tests.

Run it yourself (from the repo root, with Claude Code installed and signed in):

```bash
python3 scripts/eval_llm.py                                   # default model (sonnet), ~3 minutes
COURSE_COMPASS_CLAUDE_MODEL=haiku python3 scripts/eval_llm.py --tag haiku
python3 scripts/eval_llm.py --judge none --no-repair-probes   # fewest model calls (~25)
python3 scripts/eval_llm.py --rescore .course-compass/eval/run-results.json   # re-score, no calls
```

If `claude` is not on your `PATH`, set `COURSE_COMPASS_CLAUDE_BIN=/full/path/to/claude`. Results (every answer,
quiz item, raw score) are written to `.course-compass/eval/<tag>-results.json`, which is git-ignored.
`--provider openai` runs the same eval against an OpenAI-compatible API with your key.

## Eval set

`examples/eval/llm_eval.json`, run over 8 notes / 42 chunks: the 4 original sample notes in `examples/notes/`
plus 4 written for this eval in `examples/eval-notes/`:

| Subject | Notes |
|---|---|
| Management engineering | `operations-management.md` (bottleneck, utilization, Little's Law, EOQ, safety stock, reorder point), `project-management.md` (WBS, critical path, float, crashing, PERT, earned value), `decision-analysis.md` (EMV, decision trees, maximin/maximax/minimax regret, EVPI, sensitivity) |
| Economics | `supply-and-demand.md` (equilibrium, shortage/surplus, price ceilings/floors, complements/substitutes, shifts), plus the existing `elasticity.md` |
| Statistics, science | existing `hypothesis-testing.md`, `water-cycle.md`, `cell-respiration.md` (distractor pool and retrieval noise) |

- **14 answerable questions** across operations (4), project management (4), decision analysis (3), supply and
  demand (2) and elasticity (1). Each has the phrase its supporting chunk must contain and "key facts" the
  answer must mention (e.g. Little's Law → `6`). Retrieval puts the right chunk in the top 4 for all 14, so this
  eval measures the model, not TF-IDF.
- **6 off-topic questions** that the notes do **not** answer. One is far off ("Who won the 1998 football world
  cup?") and gets refused by the retrieval threshold. Five are deliberate **near misses** that share words with
  the notes and pass the threshold, so only the model can refuse them: the price of oil in 2008, who invented
  the critical path method, *the formula for the standard deviation of a PERT activity* (the notes give only the
  mean; a model that knows `(p − o) / 6` is tempted), how many days of safety stock a supermarket should hold for
  milk, and how kanban limits work in process.
- **3 quiz generations** of 4 questions each (project management, supply and demand, decision analysis): 12 items.
- **4 forced-repair probes**: the first model reply is deliberately corrupted (an invented citation id, or an
  invented distractor "Banana republic"), so the real model must repair it through the multi-turn repair prompt.
  Without this, the repair path would go untested: the model's own first replies were always valid.

## How each property is measured

| Property | Measure | How |
|---|---|---|
| 1. Grounding | sentences with their own citation; sentences "lexically supported" (≥ 60% of their content words, after the same stemming retrieval uses, appear in the chunks *they* cite); answers fully supported; **LLM judge** verdict | Lexical check in `llm_eval.score_answer_text`. A second model call (the judge) is shown only the cited chunks and the answer and asked whether every claim is stated in them. |
| 2. Citation fidelity | final citations ⊆ retrieved chunks; answer cites the chunk that actually holds the fact; **raw first replies** citing an id that was not sent | Raw replies are recorded *before* validation, so invented ids would show even when repair hides them. |
| 3. Refusal | off-topic refused (by threshold, or by the model saying `in_notes: false`) vs answered (= hallucinated); answerable questions wrongly refused | Off-topic answers are counted as hallucinations however good they sound. |
| 4. Structured output | first reply valid; needed the repair; **rejected after the one repair**; forced-repair probes repaired | From the recorded call count and re-validating the first raw reply. |
| 5. Quiz quality | key in source chunk; **blind-solve judge** agrees with the key; judge finds a second correct option; key is a note term (same kind as the distractors); distractors same topic / from an unrelated subject; question copies ≥ 8 consecutive words of its chunk; longest option is the key (length giveaway) | The blind-solve judge gets only the source chunk, the question and the shuffled options. Plus a manual read of every item (below). |

The judge is the same provider and model as the system under test (Sonnet judging Sonnet), so treat its
verdicts as a second opinion, not ground truth. That's why the lexical checks and a manual read sit next to it.

## Results: before and after the prompt/validation fixes

Model: `sonnet` (the default; it resolved to Claude Sonnet 5.5), Claude Code CLI 2.1.302, run on 2026-10-30.
Because `claude -p` has no temperature setting, each configuration was run **twice** to show run-to-run
variance. "Before" is the code as it was at the start of this work, "after" is the final code.

| Metric | Before, run 1 | Before, run 2 | After, run 1 | After, run 2 |
|---|---|---|---|---|
| **Grounding** | | | | |
| Sentences carrying their own citation | 23/26 (88%) | 24/24 (100%) | 28/28 (100%) | 30/30 (100%) |
| Sentences lexically supported by the chunk they cite | 23/26 (88%) | 24/24 (100%) | 28/28 (100%) | 30/30 (100%) |
| Answers fully supported (lexical) | 11/14 | 14/14 | 14/14 | 14/14 |
| Answers judged fully supported (LLM judge) | 14/14 | 13/14 | 14/14 | 14/14 |
| Answers containing the key facts | 14/14 | 14/14 | 14/14 | 14/14 |
| **Citation fidelity** | | | | |
| Final citations all within the retrieved chunks | 14/14 | 14/14 | 14/14 | 14/14 |
| Answer cites the chunk that holds the fact | 14/14 | 14/14 | 14/14 | 14/14 |
| Raw first replies citing an id that was never sent | 0/19 | 0/19 | 0/19 | 0/19 |
| **Refusal** | | | | |
| Off-topic refused (total) | 6/6 | 6/6 | 6/6 | 6/6 |
| … by the retrieval threshold | 1/6 | 1/6 | 1/6 | 1/6 |
| … by the model (`in_notes: false`), of the 5 near misses that reached it | 5/5 | 5/5 | 5/5 | 5/5 |
| Off-topic answered (hallucinated) | **0/6** | **0/6** | **0/6** | **0/6** |
| Answerable questions wrongly refused | 0/14 | 0/14 | 0/14 | 0/14 |
| **Structured output** | | | | |
| `ask` first reply valid | 19/19 | 19/19 | 19/19 | 19/19 |
| `quiz` first reply valid | 3/3 | 3/3 | 3/3 | 3/3 |
| **Rejected after the one repair attempt** | **0/22** | **0/22** | **0/22** | **0/22** |
| Forced-repair probes repaired by the real model | (not run) | (not run) | 4/4 | 4/4 |
| **Quiz quality (12 items per run)** | | | | |
| Generations accepted | 3/3 | 3/3 | 3/3 | 3/3 |
| Key appears in its source chunk | 12/12 | 12/12 | 12/12 | 12/12 |
| Blind-solve judge picks the key | 12/12 | 12/12 | 12/12 | 12/12 |
| Judge finds a second correct option | 0/12 | 0/12 | 0/12 | 0/12 |
| Key is a note term, the same kind as the distractors | 11/12 | 12/12 | 12/12 | 12/12 |
| Distractors from the same topic | 21/36 (58%) | 21/36 (58%) | 34/36 (94%) | 34/36 (94%) |
| Distractors from an unrelated subject | 3/36 | 1/36 ("Fermentation") | 0/36 | 0/36 |
| **Weak distractors, manual read** (dismissable without knowing the answer) | 7/36, in 6 items | 6/36, in 5 items | 2/36, in 2 items | 2/36, in 2 items |
| Questions copying ≥ 8 consecutive words of the chunk | 11/12 | 12/12 | 2/12 | 1/12 |
| Key is much the longest option (length giveaway) | 1/12 | 1/12 | 1/12 | 1/12 |
| **Latency** | | | | |
| Mean seconds per `ask` / per `quiz` | 7.1 / 10.0 | 11.1 / 11.3 † | 7.1 / 9.2 | 9.5 / 9.9 † |

† Run 2s ran concurrently with other eval runs (up to 6 parallel CLI calls), so their latency is inflated. Run 1
latencies are the representative ones.

One intermediate run (after fixes 1, 3, 4 and 5 but before fix 2 and the final wording of rule 5) is not in the
table. It scored like the final runs, except the judge flagged 1/14 answers (defect 2) and the manual read found
4 weak same-topic distractors, which is what prompted those last two changes.

"Rejected after repair" counts all 22 model-generated outputs per run (19 answers that reached the model + 3
quizzes). The one far-off question never reaches the model.

### What was already good (before any fix)

- **Refusal is the strongest result.** All 5 near-miss questions got past the retrieval threshold and the model
  refused every one, in every run, including the PERT standard-deviation question, where a correct formula from
  outside knowledge was easy to supply. Zero hallucinated answers in 24 off-topic attempts across 4 runs (plus
  12 more on Haiku).
- **Citations were never invented.** 0 of 76 raw first replies across the 4 runs cited an id that wasn't sent.
- **JSON was always valid first time**, so the "rejected after repair" rate is 0 in every run. Because the
  repair path therefore never ran naturally, the forced-repair probes were added. The real model fixed 4 of 4
  corrupted replies in every run that had probes (16 of 16 in total, Haiku and the intermediate run included)
  through the multi-turn transcript.
- **Answer keys were right.** In every run the blind-solve judge, given only the source chunk, picked the keyed
  option for all 12 items and found no second correct option.

### Defects the eval exposed, and what changed

1. **Uncited sentences (ask).** Run 1 had 3 answers whose lead-in sentence carried no citation, e.g. *"A price
   ceiling is a legal maximum price. When it is set below … it causes a shortage [supply-and-demand#2]."* The
   prompt asked for a citation on every sentence, but the code only checked that *some* citation existed. Run 2
   happened not to show it, which is exactly why a prompt rule alone isn't enough.
   **Fix:** rule 2 of `ANSWER_SYSTEM` now says *end EVERY sentence with its citation*, with an example. More
   importantly, `validate_answer_payload` now rejects any sentence without its own `[chunk-id]` (new
   `uncited_sentences`), quoting it in the repair prompt. **After: 58/58 sentences cited, guaranteed by code.**
2. **Unsupported reasoning (ask).** In 2 of the runs (before run 2, and an intermediate after-run) the elasticity
   answer added *"This is because inelastic demand means quantity responds less than proportionally"*. The notes
   state the definition and the revenue effect but never link them. Citations and lexical overlap both looked
   fine; only the LLM judge caught it. **Fix:** rule 1 now forbids *reasons, mechanisms or consequences the chunks
   do not state, even if they follow logically.* **After: the judge passed 28/28 answers.** It's a prompt-only
   fix, so it reduces the problem rather than ruling it out.
3. **Off-subject and wrong-kind distractors (quiz).** The pool was one flat list of every term, and the model
   filled the third slot with anything: *Maximin criterion* for a price-ceiling question, *Fermentation* (biology)
   for *Crashing*, *Throughput* and *Safety stock* in project management. **Fix:** the pool is now split into
   SAME TOPIC and OTHER TOPICS, and rule 5 asks for terms of the *same kind of concept*. A first version ("prefer
   same topic") overcorrected into same-topic terms of the wrong kind, so the wording now says a related
   other-topic term beats a same-topic term of a different kind. **After: 0/72 off-subject; weak distractors
   down from 13/72 to 4/72.**
4. **Answer-type giveaway (quiz).** One item keyed a full definition sentence (*"The budgeted cost of the work
   actually completed so far"*) against three bare terms. **Fix (code):** the key must be a pool term **and**
   appear in its source chunk (`validate_item(..., pool, chunk_text)`), with repair otherwise. This also closes
   most of an old README limitation ("quiz answers aren't verified against the chunk"). **After: 24/24.**
5. **Questions copy the definition word for word (quiz).** 23/24 questions shared 8+ consecutive words with their
   chunk, so a student could match words instead of recalling the concept. **Fix:** rule 3 asks for a paraphrase.
   **After: 3/24.**

### Defects not fixed (plainly)

- **2 weak distractors per run remain, both in supply and demand.** The notes define only two "relationship
  between goods" terms (*complements*, *substitutes*), so a question about one has one good distractor and the
  model fills the rest with market situations (*Shortage*, *Surplus*). The pool can only contain what the notes
  define. Better notes fix it; a prompt can't.
- **1 length giveaway per run:** *Expected value of perfect information* is simply much longer than any other
  decision-analysis term. Same cause: a property of the notes' vocabulary.
- **Questions sometimes add an outside example** ("such as printers and ink", "like tea and coffee"). It makes
  the question better, the key is still from the notes, and the judge agreed with every key, but strictly it is
  outside knowledge in the question text. Left as is, and noted here.
- **Grounding of reasoning is checked by an LLM judge only.** The lexical check can't see an unsupported
  "because"; the judge can, but it's the same model family. Citations remain the student's check.

### Model choice: Sonnet vs Haiku

Same eval with `COURSE_COMPASS_CLAUDE_MODEL=haiku` (judge also Haiku):

| Metric | Haiku before | Haiku after | Sonnet after (run 1) |
|---|---|---|---|
| Off-topic refused / hallucinated | 6/6 / 0/6 | 6/6 / 0/6 | 6/6 / 0/6 |
| Sentences cited and supported | 20/21 | 21/21 | 28/28 |
| Rejected after repair | 0/22 | 0/22 | 0/22 |
| `quiz` first reply valid | 3/3 | 2/3 (invented distractor "Payoff table", repaired) | 3/3 |
| Questions copying the chunk | 8/12 | **8/12** (ignores the paraphrase rule) | 2/12 |
| Distractors same topic / off-subject | 36/36 / 0 | 36/36 / 0 | 34/36 / 0 |
| Mean seconds per `ask` | 7.5 | 5.4 | 7.1 |

Both models are safe on refusal and grounding on this set. Sonnet follows the quiz-writing rules better
(paraphrase, no invented terms), and through the CLI it isn't meaningfully slower, because process start-up
dominates a ~7 s call. Hence `sonnet` is the default. Haiku is a reasonable setting if usage allowance matters
more than quiz polish.

## Caveats

- **Small set.** 20 questions, 12 quiz items and 4 probes per run on short, cleanly written notes. A 0/6
  hallucination rate is encouraging, not a guarantee; messier real notes will be harder.
- **The judge is not independent.** It is the same model family as the generator. The lexical checks and the
  manual read of every quiz item are there to cross-check it, and the manual "weak distractor" count is one
  person's judgement.
- **Variance.** No temperature control in `claude -p`. Two runs per configuration showed defects that appear in
  one run and not the other (defects 1 and 2), so single-run numbers are noisy.
- **The OpenAI-compatible provider was not re-evaluated** here (no key was used). The eval script supports it
  with `--provider openai`.
