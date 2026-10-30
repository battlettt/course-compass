import json
import unittest

from course_compass import explain
from course_compass.explain import (GROUNDED, ILLUSTRATIVE, ExplainError, provider_name, real_life_examples,
                                    select_chunks, simplify)
from course_compass.llm import LLMConfig, LLMHTTPError
from tests.helpers import FakeClient, sample_chunks


class ClaudeFake(FakeClient):
    label = "Claude CLI: sonnet"


SIMPLE = "Elasticity is how much people change what they buy when the price changes [elasticity#1]."
FULL = ("Price elasticity of demand is the percentage change in quantity demanded divided by the percentage "
        "change in price [elasticity#2]. Elastic demand has an absolute value greater than one [elasticity#2].")


def simplify_reply(simple=SIMPLE, full=FULL):
    return json.dumps({"simple": simple, "full": full, "citations": ["elasticity#1", "elasticity#2"]})


class SelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chunks = sample_chunks()

    def test_topic_selects_whole_topic_in_note_order(self):
        selection = select_chunks(self.chunks, topic="elasticity")
        ids = [chunk.id for chunk in selection.chunks]
        self.assertEqual(ids, [f"elasticity#{i}" for i in range(1, 7)])
        self.assertIsNone(selection.refusal)

    def test_chunk_ids_file_and_query(self):
        self.assertEqual([c.id for c in select_chunks(self.chunks, chunk_ids=["elasticity#4"]).chunks],
                         ["elasticity#4"])
        by_file = select_chunks(self.chunks, source="water-cycle.md")
        self.assertTrue(by_file.chunks and all(c.topic == "water-cycle" for c in by_file.chunks))
        by_query = select_chunks(self.chunks, query="What is a p-value?")
        self.assertTrue(any(c.topic == "hypothesis-testing" for c in by_query.chunks))

    def test_off_topic_query_is_refused_not_explained(self):
        selection = select_chunks(self.chunks, query="capital of Australia")
        self.assertEqual(selection.chunks, [])
        self.assertIn("below the 0.15 threshold", selection.refusal)

    def test_bad_selection_is_an_error(self):
        with self.assertRaises(ExplainError):
            select_chunks(self.chunks)
        with self.assertRaises(ExplainError):
            select_chunks(self.chunks, query="x", topic="elasticity")
        with self.assertRaises(ExplainError):
            select_chunks(self.chunks, chunk_ids=["elasticity#99"])
        with self.assertRaises(ExplainError):
            select_chunks(self.chunks, source="nope.md")


class SimplifyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.selection = select_chunks(sample_chunks(), topic="elasticity")

    def test_llm_output_has_two_layers_and_names_provider(self):
        client = ClaudeFake(simplify_reply())
        result = simplify(self.selection, client)
        self.assertEqual(result.mode, "llm")
        self.assertEqual(result.simple, SIMPLE)
        self.assertEqual(result.full, FULL)
        self.assertIn("Claude CLI: sonnet", result.header)
        self.assertNotIn("LOCAL", result.header)
        self.assertEqual(result.citations, ["elasticity#1", "elasticity#2"])
        self.assertEqual(len(client.calls), 1)
        sent = client.calls[0][1]["content"]
        self.assertIn("[elasticity#6]", sent)  # every selected chunk goes to the model

    def test_simple_layer_must_be_one_short_paragraph(self):
        two_paragraphs = SIMPLE + "\n\n" + SIMPLE
        _, errors = explain.validate_simplify_payload(simplify_reply(simple=two_paragraphs), {"elasticity#1", "elasticity#2"})
        self.assertTrue(any("ONE paragraph" in e for e in errors))
        long = " ".join(["word"] * 130) + " [elasticity#1]."
        _, errors = explain.validate_simplify_payload(simplify_reply(simple=long), {"elasticity#1", "elasticity#2"})
        self.assertTrue(any("words; keep it" in e for e in errors), errors)

    def test_full_layer_needs_a_citation_per_sentence_and_known_ids(self):
        bad_full = "Elasticity is a ratio. It matters a lot [elasticity#2]."
        _, errors = explain.validate_simplify_payload(simplify_reply(full=bad_full), {"elasticity#1", "elasticity#2"})
        self.assertTrue(any("uncited" in e for e in errors))
        invented = FULL.replace("elasticity#2", "elasticity#99")
        _, errors = explain.validate_simplify_payload(simplify_reply(full=invented), {"elasticity#1", "elasticity#2"})
        self.assertTrue(any("elasticity#99" in e for e in errors))

    def test_one_repair_then_accept_or_reject(self):
        client = ClaudeFake("not json", simplify_reply())
        self.assertEqual(simplify(self.selection, client).simple, SIMPLE)
        self.assertEqual(len(client.calls), 2)
        self.assertIn("could not be used", client.calls[1][-1]["content"])
        with self.assertRaises(ExplainError):
            simplify(self.selection, ClaudeFake("not json", "{}"))

    def test_llm_failure_becomes_explain_error(self):
        with self.assertRaises(ExplainError):
            simplify(self.selection, ClaudeFake(LLMHTTPError("HTTP 500")))

    def test_without_llm_it_is_a_labelled_extract_not_a_simplification(self):
        result = simplify(self.selection, None)
        self.assertEqual(result.mode, "local")
        self.assertIn("LOCAL EXTRACTIVE APPROXIMATION (no LLM, not a simplification)", result.header)
        self.assertNotIn("by LLM", result.header)
        notes = "\n".join(chunk.text.replace("**", "") for chunk in self.selection.chunks)
        for sentence in explain.split_sentences(explain._CITATION_RE.sub("", result.simple)):
            self.assertIn(sentence.strip(), notes)  # every layer-1 sentence is verbatim from the notes
        self.assertIn("[elasticity#1]", result.full)
        self.assertNotIn("… ", result.full)  # overlap carry-over is not repeated

    def test_provider_name_for_openai_compatible_client(self):
        class Http(FakeClient):
            config = LLMConfig(api_key="k", model="gpt-4o-mini")
        self.assertEqual(provider_name(Http()), "OpenAI-compatible API: gpt-4o-mini")
        self.assertEqual(provider_name(ClaudeFake()), "Claude CLI: sonnet")


def examples_reply(*examples, in_notes=True):
    return json.dumps({"in_notes": in_notes, "examples": list(examples)})


GROUNDED_EX = {"kind": "grounded", "text": "Necessities such as basic medicine tend to be inelastic.",
               "citations": ["elasticity#4"]}
ILLUSTRATIVE_EX = {"kind": "illustrative", "text": "Insulin buyers barely cut back when its price rises.",
                   "citations": [], "illustrates": "elasticity#2"}


class ExamplesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chunks = sample_chunks()
        cls.selection = select_chunks(cls.chunks, query="inelastic demand")

    def test_grounded_and_illustrative_are_kept_apart(self):
        client = ClaudeFake(examples_reply(ILLUSTRATIVE_EX, GROUNDED_EX))
        result = real_life_examples("inelastic demand", self.selection, client, n=2)
        self.assertEqual(result.mode, "llm")
        self.assertIn("Claude CLI: sonnet", result.header)
        self.assertEqual([e.kind for e in result.examples], [GROUNDED, ILLUSTRATIVE])  # grounded first
        grounded, illustrative = result.examples
        self.assertEqual(grounded.citations, ["elasticity#4"])
        self.assertEqual(illustrative.citations, [])
        self.assertEqual(illustrative.illustrates, "elasticity#2")

    def test_validation_rejects_mislabelled_examples(self):
        chunks = self.selection.chunks
        cases = [
            ({**GROUNDED_EX, "citations": []}, "must cite"),
            ({**ILLUSTRATIVE_EX, "citations": ["elasticity#4"]}, "must be []"),
            ({**ILLUSTRATIVE_EX, "illustrates": "elasticity#99"}, '"illustrates"'),
            ({**GROUNDED_EX, "text": "Rocket fuel is priced by orbit."}, "shares almost no wording"),
            ({**GROUNDED_EX, "kind": "made-up"}, '"kind"'),
            ({**GROUNDED_EX, "citations": ["water-cycle#1"]}, "not in the provided chunks"),
        ]
        for raw, expected in cases:
            _, errors = explain.validate_examples_payload(examples_reply(raw), chunks, 4)
            self.assertTrue(any(expected in e for e in errors), (raw, errors))

    def test_mislabelled_reply_gets_one_repair_then_rejected(self):
        bad = examples_reply({**GROUNDED_EX, "citations": []})
        client = ClaudeFake(bad, examples_reply(GROUNDED_EX))
        self.assertEqual(len(real_life_examples("inelastic demand", self.selection, client).examples), 1)
        with self.assertRaises(ExplainError):
            real_life_examples("inelastic demand", self.selection, ClaudeFake(bad, bad))

    def test_llm_can_refuse(self):
        result = real_life_examples("x", self.selection, ClaudeFake(examples_reply(in_notes=False)))
        self.assertEqual(result.mode, "refusal")
        self.assertIn("Not in your notes.", result.note)

    def test_weak_retrieval_refuses_without_calling_the_llm(self):
        client = ClaudeFake()
        selection = select_chunks(self.chunks, query="capital of Australia")
        result = real_life_examples("capital of Australia", selection, client)
        self.assertEqual(result.mode, "refusal")
        self.assertEqual(client.calls, [])

    def test_local_fallback_only_quotes_notes_and_invents_nothing(self):
        result = real_life_examples("inelastic demand", self.selection, None, n=4)
        self.assertEqual(result.mode, "local")
        self.assertIn("LOCAL FALLBACK (extractive, no LLM)", result.header)
        self.assertIn("No illustrative examples are made up", result.note)
        self.assertTrue(result.examples)
        notes = "\n".join(chunk.text for chunk in self.chunks)
        for example in result.examples:
            self.assertEqual(example.kind, GROUNDED)
            self.assertIn(example.text, notes)
            self.assertNotIn(":", example.text.split(" ")[0])  # definition lines are not "examples"

    def test_bad_n(self):
        with self.assertRaises(ExplainError):
            real_life_examples("x", self.selection, None, n=0)


if __name__ == "__main__":
    unittest.main()
