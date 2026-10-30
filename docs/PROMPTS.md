# Prompts

Every prompt Course Compass sends is below, copied verbatim from `course_compass/prompts.py`
(a test, `tests/test_docs.py`, fails if this file and the code drift apart). After each
template is a line-by-line note on why each instruction is there.

The design rule behind all of them: **the prompt asks, the code checks.** No instruction
is trusted on its own. Each one has a matching validation step in `answering.py` or `quiz.py`,
and output that fails validation gets one repair attempt and is then rejected.

## 1. Answering: system prompt (`ANSWER_SYSTEM`)

```text
You are Course Compass, a study assistant that answers ONLY from the student's own notes.
Rules:
1. Use only facts stated in the NOTE CHUNKS provided. Do not use outside knowledge, even if you are sure it is correct, and do not add reasons, mechanisms or consequences the chunks do not state, even if they follow logically.
2. End EVERY sentence with the id of the chunk that supports it, in square brackets, before the full stop, e.g. "Supply is more elastic in the long run [elasticity#6]." A sentence without its own citation is rejected, even a definition or a restatement.
3. Only cite ids that appear in the NOTE CHUNKS. Never invent an id.
4. If the chunks do not contain the answer, set "in_notes" to false and leave "answer" empty. Do not guess.
5. Keep the answer short: at most 5 sentences, in plain language a student can revise from.
6. Reply with ONLY a JSON object, no markdown fences, matching this schema:
{"in_notes": true|false, "answer": "string with [chunk-id] citations", "citations": ["chunk-id", ...]}
```

| Instruction | Why it is there | How the code enforces it |
|---|---|---|
| "answers ONLY from the student's own notes" | Sets the role: a revision aid, not a general tutor. The student will trust it, so it must not mix in facts their course may define differently. | Only the top-k retrieved chunks are ever sent, so the model has nothing else from the notes to draw on. |
| Rule 1: no outside knowledge "even if you are sure it is correct", and no unstated reasons or consequences | Models will "helpfully" add true-but-uncited facts. The second clause names that exact failure. The last clause was added after the real-model eval ([EVAL_LLM.md](EVAL_LLM.md)) caught Sonnet adding a "so the price rise outweighs the drop in quantity" explanation that no chunk states. | Cannot be fully checked automatically, which is why rule 2 exists: an uncited claim stands out. The eval's LLM judge measures it. |
| Rule 2: end EVERY sentence with its `[chunk-id]` | Citations are the student's check on the model. A fixed, greppable format means the code can parse them out of the text. The "every sentence, even a definition" wording was added after the eval found 3 of 14 real answers with an uncited lead-in sentence. | `_CITATION_RE` extracts inline ids; an answer with zero citations is rejected, and so is any sentence without its own citation (`uncited_sentences`), with the offending sentence quoted in the repair prompt. |
| Rule 3: only cite ids that appear; never invent | Models sometimes produce plausible-looking ids (`[elasticity#9]`). | Every cited id (inline or in `citations`) must be in the set sent in the prompt, otherwise repair, then reject. |
| Rule 4: `in_notes: false` instead of guessing | Retrieval can pass the score threshold on word overlap alone ("price of oil" matches the elasticity notes). The model is the second, smarter refusal gate. | `in_notes` must be a real boolean. `false` becomes the same "Not in your notes." refusal the threshold produces. |
| Rule 5: at most 5 sentences, plain language | Short answers are easier to check against the cited chunk and to revise from. | Not enforced; a style request. |
| Rule 6: JSON only, given schema | Makes validation mechanical instead of guessing at prose. | `parse_json_object` (tolerates a ```` ```json ```` fence, nothing else), then field type checks. |

## 2. Answering: user prompt (`ANSWER_USER`)

```text
NOTE CHUNKS:
{chunks}

QUESTION: {question}

Reply with the JSON object only.
```

`{chunks}` is filled by `format_chunks`, one block per retrieved chunk:

```text
[hypothesis-testing#4] (Hypothesis Testing Basics > Errors in testing)
- **Type I error**: rejecting a null hypothesis that is actually true; ...
```

- The id comes first in exactly the citation format the model must reuse, so copying it is the easy path.
- Title and heading give the model context that a bare chunk loses (a chunk of bullets may not say which subject it is about).
- The question comes *after* the chunks, so the instruction it answers is the last thing the model reads.
- The closing "Reply with the JSON object only." repeats rule 6 because models often drift from system-prompt format rules over a long context.

## 3. Quiz generation: system prompt (`QUIZ_SYSTEM`)

```text
You write multiple-choice revision questions from a student's own notes.
Rules:
1. Each question must be answerable from exactly one SOURCE CHUNK; put that chunk's id in "source_chunk_id".
2. The correct answer must be a term copied VERBATIM from the DISTRACTOR POOL that also appears in that source chunk. Ask what the term means, does, or when it applies, as the chunk states it. Do not use outside knowledge.
3. Paraphrase the chunk in the question instead of copying the definition word for word, and never put the answer term itself in the question.
4. Each question has exactly 3 distractors. Copy every distractor VERBATIM from the DISTRACTOR POOL. Never invent a distractor.
5. Pick distractors a student could genuinely confuse with the answer: terms for the same kind of concept (another rule, another relationship between goods, another time measure). Usually these are SAME TOPIC terms, but a closely related OTHER TOPIC term beats a same-topic term of a different kind. Never use a term from an unrelated subject (e.g. a biology term in an economics question).
6. The 3 distractors must be different from each other and from the correct answer, and each must be clearly wrong according to the source chunk.
7. Keep questions under 40 words.
8. Reply with ONLY a JSON object, no markdown fences, matching this schema:
{"items": [{"question": "string", "correct": "string", "distractors": ["string", "string", "string"], "source_chunk_id": "chunk-id"}]}
```

| Instruction | Why it is there | How the code enforces it |
|---|---|---|
| Rule 1: one `source_chunk_id` per question | Every question must trace back to a chunk the student can reread after getting it wrong. | `source_chunk_id` must be one of the chunks sent for this topic. |
| Rule 2: the answer is a pool term that appears in the source chunk | Distractors are pool terms, so a free-text answer is a different *kind* of option and gives itself away (the eval found an item whose key was a whole definition sentence against three bare terms). Requiring the term to appear in the source chunk also checks the key against the notes. | The correct answer must be in the pool **and** appear (normalised) in its source chunk's text, otherwise repair, then reject. |
| Rule 3: paraphrase; never put the answer in the question | Copying the definition word for word lets a student match words instead of recalling the concept: 11 of 12 baseline questions shared 8+ consecutive words with their chunk. | Not enforced (a paraphrase cannot be checked exactly); measured by the eval. |
| Rule 4: distractors copied VERBATIM from the pool; never invent | Invented distractors are the classic way quiz generators produce wrong "facts" (a made-up term that sounds real). Drawing from other notes keeps every option a real course concept. | Each distractor, normalised (case, spaces, trailing full stop), must be in the pool, otherwise repair, then reject. |
| Rule 5: distractors of the same kind, same topic first, never an unrelated subject | The baseline offered "Maximin criterion" as an option for a price-ceiling question: real terms from the notes, but obviously wrong. A first fix ("prefer same topic") overcorrected into same-topic terms of the wrong kind ("Surplus" for a question about complements), so the rule asks for the same *kind* of concept. | Not enforced (plausibility is a judgement); measured by the eval. |
| Rule 6: three distinct distractors, none equal to the answer, each clearly wrong per the chunk | Duplicates give the answer away; a distractor equal to the answer makes two options correct. | Distinctness and "not the correct answer" are checked after normalisation. The eval's blind-solve judge checks for a second correct option. |
| Rule 7: length limit | Long questions are hard to scan. | Not enforced. |
| Rule 8: JSON only, schema | Mechanical validation. | `"items"` must be a non-empty list with at most `n` entries; each item is checked by `validate_item`. |

## 4. Quiz generation: user prompt (`QUIZ_USER`)

```text
SOURCE CHUNKS:
{chunks}

DISTRACTOR POOL (every term defined in the student's notes; the correct answer and all distractors come from here):
SAME TOPIC (best distractors):
{same_topic}
OTHER TOPICS (use only if closely related to the question):
{other_topics}

Write {n} questions. Reply with the JSON object only.
```

- `{chunks}`: up to 8 chunks from the requested topic, in the same format as for answering.
- `{same_topic}` / `{other_topics}`: every bolded term defined anywhere in the notes, one `- Term` per line,
  split into the quiz topic's own terms and everyone else's (`(none)` if a list is empty). The split was added
  after the eval: one flat list gave the model no way to tell a related term from one in a different subject.
  For a quiz across all topics every term is "same topic".
- `{n}`: requested question count (1 to 20).

## 5. Repair prompt (`REPAIR_USER`)

```text
Your previous reply could not be used because:
{errors}

Reply again with ONLY the corrected JSON object, following every rule in the system message.
```

Sent once, as a follow-up turn after the model's own invalid reply, with the exact validation errors listed, for example:

```text
item 1: distractors not taken from the notes' term pool: ['Banana']
```

- Concrete errors repair far better than "try again". The model can fix the one bad field.
- It points back to "every rule in the system message" so the fix does not break a different rule.
- Only **one** repair: if the second reply is still invalid the command fails with
  `LLM ... rejected after one repair attempt: <errors>` and exit code 2. Showing unvalidated output to a
  student who is revising for an exam is worse than showing nothing.

## Temperatures

- Answering: `0.0`. Same chunks and question should give the same answer.
- Quiz first attempt: `0.3`, a little variety between quizzes. Repair: `0.0`, just fix the errors.

## Claude CLI provider: how messages are sent

`ClaudeCLIClient` turns the same message list into one `claude -p` call: system messages become
`--system-prompt` (replacing Claude Code's own agent prompt), and the user content goes on stdin. A
single user message is sent verbatim. A repair exchange (user, assistant, user) is sent as a role-tagged
transcript, so the model sees its own rejected reply:

```text
The conversation so far is below, one turn per tag. Reply to the final user turn only, as the assistant.

<turn role="user">
NOTE CHUNKS: ...
</turn>

<turn role="assistant">
{"in_notes": true, ...}
</turn>

<turn role="user">
Your previous reply could not be used because: ...
</turn>
```

The eval forces this path on purpose (it corrupts the first reply) and the real model repaired 4 of 4.
`claude -p` has no temperature flag, so the temperatures below apply only to the OpenAI-compatible provider.

## The local fallback uses no prompts

Without a key nothing is generated. `ask` quotes the top chunks verbatim under a `LOCAL FALLBACK (extractive, no LLM)`
label, and `quiz` fills one fixed template, `Which term from your notes matches this definition: "..."?`,
from `- **Term**: definition` lines. It never claims to be the LLM.
