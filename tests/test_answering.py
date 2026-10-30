import json
import unittest

from course_compass.answering import (LOCAL_LABEL, MAX_QUESTION_CHARS, AnswerError, answer_question,
                                      parse_json_object, validate_answer_payload)
from course_compass.llm import LLMTimeoutError
from course_compass.retrieval import TfidfIndex
from tests.helpers import FakeClient, sample_chunks


class AnswerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chunks = sample_chunks()
        cls.index = TfidfIndex(cls.chunks)
        cls.ids = {c.id for c in cls.chunks}

    def test_local_fallback_is_labelled_and_verbatim(self):
        answer = answer_question("What is a Type I error?", self.index)
        self.assertEqual(answer.mode, "local")
        self.assertEqual(answer.label, LOCAL_LABEL)
        self.assertIn("no LLM", answer.label)
        first = next(c for c in self.chunks if c.id == answer.citations[0])
        self.assertIn(first.text, answer.text)

    def test_local_citations_are_real_chunk_ids(self):
        answer = answer_question("How does elasticity affect total revenue?", self.index)
        self.assertTrue(answer.citations)
        self.assertTrue(set(answer.citations) <= self.ids)
        for cid in answer.citations:
            self.assertIn(f"[{cid}]", answer.text)

    def test_refusal_below_threshold(self):
        answer = answer_question("Who won the 1998 football world cup?", self.index)
        self.assertTrue(answer.refused)
        self.assertTrue(answer.text.startswith("Not in your notes."))
        self.assertEqual(answer.citations, [])

    def test_threshold_is_configurable(self):
        question = "What was the price of oil in 2008?"
        self.assertFalse(answer_question(question, self.index).refused)
        self.assertTrue(answer_question(question, self.index, threshold=0.5).refused)

    def test_refusal_does_not_call_llm(self):
        client = FakeClient()
        answer = answer_question("How do I bake sourdough bread?", self.index, client=client)
        self.assertTrue(answer.refused)
        self.assertEqual(client.calls, [])

    def test_empty_and_oversized_questions_rejected(self):
        with self.assertRaises(AnswerError):
            answer_question("   ", self.index)
        with self.assertRaises(AnswerError):
            answer_question("x" * (MAX_QUESTION_CHARS + 1), self.index)

    def test_empty_index_gives_clear_error(self):
        with self.assertRaisesRegex(AnswerError, "ingest"):
            answer_question("What is ATP?", TfidfIndex([]))

    def test_llm_answer_with_valid_citations(self):
        reply = json.dumps({"in_notes": True,
                            "answer": "A Type I error rejects a true null [hypothesis-testing#4].",
                            "citations": ["hypothesis-testing#4"]})
        client = FakeClient(reply)
        answer = answer_question("What is a Type I error?", self.index, client=client)
        self.assertEqual(answer.mode, "llm")
        self.assertEqual(answer.citations, ["hypothesis-testing#4"])
        prompt = client.calls[0][1]["content"]
        self.assertIn("[hypothesis-testing#4]", prompt)
        self.assertIn("QUESTION: What is a Type I error?", prompt)

    def test_llm_invented_citation_is_repaired_once(self):
        bad = json.dumps({"in_notes": True, "answer": "Made up [physics#9].", "citations": ["physics#9"]})
        good = json.dumps({"in_notes": True, "answer": "Fixed [hypothesis-testing#4].",
                           "citations": ["hypothesis-testing#4"]})
        client = FakeClient(bad, good)
        answer = answer_question("What is a Type I error?", self.index, client=client)
        self.assertEqual(answer.text, "Fixed [hypothesis-testing#4].")
        self.assertEqual(len(client.calls), 2)
        self.assertIn("physics#9", client.calls[1][-1]["content"])

    def test_llm_invalid_twice_is_rejected_not_shown(self):
        client = FakeClient("not json at all", '{"in_notes": true, "answer": "no cites", "citations": []}')
        with self.assertRaisesRegex(AnswerError, "rejected after one repair"):
            answer_question("What is a Type I error?", self.index, client=client)

    def test_llm_says_not_in_notes(self):
        client = FakeClient('{"in_notes": false, "answer": "", "citations": []}')
        answer = answer_question("What was the price of oil in 2008?", self.index, client=client)
        self.assertTrue(answer.refused)

    def test_llm_transport_error_becomes_answer_error(self):
        client = FakeClient(LLMTimeoutError("timed out"))
        with self.assertRaisesRegex(AnswerError, "timed out"):
            answer_question("What is a Type I error?", self.index, client=client)


class PayloadTests(unittest.TestCase):
    def test_fenced_json_is_accepted(self):
        self.assertEqual(parse_json_object('```json\n{"a": 1}\n```'), {"a": 1})

    def test_non_object_rejected(self):
        with self.assertRaises(ValueError):
            parse_json_object("[1, 2]")

    def test_inline_citations_count_and_are_checked(self):
        data, errors = validate_answer_payload(
            '{"in_notes": true, "answer": "x [a#1] y [b#2]", "citations": []}', {"a#1"})
        self.assertEqual(data["citations"], ["a#1", "b#2"])
        self.assertTrue(any("b#2" in e for e in errors))

    def test_in_notes_must_be_bool(self):
        _, errors = validate_answer_payload('{"in_notes": "yes", "answer": "", "citations": []}', set())
        self.assertTrue(errors)


if __name__ == "__main__":
    unittest.main()
