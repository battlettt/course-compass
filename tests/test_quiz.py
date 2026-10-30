import json
import unittest

from course_compass.quiz import (QuizError, QuizItem, extract_facts, llm_quiz, local_quiz, normalize,
                                 resolve_topic, validate_item)
from tests.helpers import FakeClient, sample_chunks

GOOD_ITEM = {
    "question": "What measures quantity response to consumer income changes?",
    "correct": "Income elasticity",
    "distractors": ["Cross-price elasticity", "Unit elastic", "Elastic demand"],
    "source_chunk_id": "elasticity#3",
}


def reply(*items):
    return json.dumps({"items": list(items)})


class LocalQuizTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chunks = sample_chunks()
        cls.ids = {c.id for c in cls.chunks}
        cls.terms = {normalize(f.term) for f in extract_facts(cls.chunks)}

    def test_extracts_definitions_from_bold_terms(self):
        facts = {f.term: f for f in extract_facts(self.chunks)}
        self.assertIn("P-value", facts)
        self.assertEqual(facts["P-value"].topic, "hypothesis-testing")
        self.assertTrue(facts["P-value"].definition.startswith("the probability of observing data"))

    def test_overlap_lines_are_not_double_counted(self):
        terms = [f.term for f in extract_facts(self.chunks)]
        self.assertEqual(len(terms), len(set(terms)))

    def test_local_quiz_items_are_valid(self):
        items = local_quiz(self.chunks, "elasticity", 5)
        self.assertEqual(len(items), 5)
        for item in items:
            self.assertEqual(item.origin, "local")
            self.assertEqual(item.topic, "elasticity")
            self.assertEqual(validate_item(item.to_dict(), self.ids, self.terms), [])

    def test_correct_answer_present_and_distractors_distinct(self):
        for item in local_quiz(self.chunks, None, 10):
            options = item.options()
            self.assertEqual(len(options), 4)
            self.assertEqual(len({normalize(o) for o in options}), 4)
            self.assertIn(item.correct, options)
            self.assertEqual(options["ABCD".index(item.correct_letter())], item.correct)

    def test_distractors_come_from_notes_and_prefer_same_topic(self):
        topic_of_term = {f.term: f.topic for f in extract_facts(self.chunks)}
        for item in local_quiz(self.chunks, "water-cycle", 5):
            for distractor in item.distractors:
                self.assertEqual(topic_of_term[distractor], "water-cycle")

    def test_deterministic_for_same_seed(self):
        first = [i.id for i in local_quiz(self.chunks, None, 5, seed=3)]
        self.assertEqual(first, [i.id for i in local_quiz(self.chunks, None, 5, seed=3)])

    def test_no_definitions_is_a_clear_error(self):
        from course_compass.chunking import chunk_document
        plain = chunk_document("# Notes\n\nJust prose without definitions.", "plain.md")
        with self.assertRaisesRegex(QuizError, "No definitions"):
            local_quiz(plain, None, 3)

    def test_bad_question_count(self):
        with self.assertRaises(QuizError):
            local_quiz(self.chunks, None, 0)
        with self.assertRaises(QuizError):
            local_quiz(self.chunks, None, 21)

    def test_resolve_topic(self):
        self.assertEqual(resolve_topic("elasticity", self.chunks), "elasticity")
        self.assertEqual(resolve_topic("Hypothesis", self.chunks), "hypothesis-testing")
        self.assertIsNone(resolve_topic(None, self.chunks))
        with self.assertRaisesRegex(QuizError, "Known topics"):
            resolve_topic("chemistry", self.chunks)

    def test_item_round_trip(self):
        item = local_quiz(self.chunks, None, 1)[0]
        self.assertEqual(QuizItem.from_dict(item.to_dict()), item)


class ValidateItemTests(unittest.TestCase):
    IDS = {"elasticity#3"}

    def test_good_item(self):
        self.assertEqual(validate_item(GOOD_ITEM, self.IDS), [])

    def test_wrong_distractor_count(self):
        item = dict(GOOD_ITEM, distractors=["Unit elastic", "Elastic demand"])
        self.assertIn("needs exactly 3 non-empty distractors", validate_item(item, self.IDS))

    def test_duplicate_distractors(self):
        item = dict(GOOD_ITEM, distractors=["Unit elastic", "unit elastic.", "Elastic demand"])
        self.assertIn("distractors are not distinct", validate_item(item, self.IDS))

    def test_distractor_equal_to_correct(self):
        item = dict(GOOD_ITEM, distractors=["Income elasticity", "Unit elastic", "Elastic demand"])
        self.assertIn("a distractor repeats the correct answer", validate_item(item, self.IDS))

    def test_unknown_source_chunk_and_missing_fields(self):
        errors = validate_item({"distractors": "x", "source_chunk_id": "nope#1"}, self.IDS)
        self.assertEqual(len(errors), 4)

    def test_invented_distractor_rejected_with_pool(self):
        pool = {normalize(t) for t in ["Cross-price elasticity", "Unit elastic"]}
        errors = validate_item(GOOD_ITEM, self.IDS, pool)
        self.assertTrue(any("term pool" in e for e in errors))


class LLMQuizTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chunks = sample_chunks()

    def test_valid_llm_quiz(self):
        client = FakeClient(reply(GOOD_ITEM))
        items = llm_quiz(self.chunks, "elasticity", 1, client)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].origin, "llm")
        self.assertEqual(items[0].topic, "elasticity")
        self.assertIn("DISTRACTOR POOL", client.calls[0][1]["content"])
        self.assertIn("- Catchment", client.calls[0][1]["content"])

    def test_invented_distractor_is_repaired_once(self):
        bad = dict(GOOD_ITEM, distractors=["Banana", "Unit elastic", "Elastic demand"])
        client = FakeClient(reply(bad), reply(GOOD_ITEM))
        items = llm_quiz(self.chunks, "elasticity", 1, client)
        self.assertEqual(items[0].distractors, GOOD_ITEM["distractors"])
        self.assertIn("Banana", client.calls[1][-1]["content"])

    def test_bad_json_twice_is_rejected(self):
        client = FakeClient("{not json", '{"items": []}')
        with self.assertRaisesRegex(QuizError, "rejected after one repair"):
            llm_quiz(self.chunks, "elasticity", 1, client)

    def test_source_chunk_outside_topic_is_rejected(self):
        wrong = dict(GOOD_ITEM, source_chunk_id="water-cycle#2")
        client = FakeClient(reply(wrong), reply(wrong))
        with self.assertRaisesRegex(QuizError, "not a known chunk"):
            llm_quiz(self.chunks, "elasticity", 1, client)

    def test_too_many_items_rejected(self):
        client = FakeClient(reply(GOOD_ITEM, GOOD_ITEM), reply(GOOD_ITEM, GOOD_ITEM))
        with self.assertRaisesRegex(QuizError, "at most 1"):
            llm_quiz(self.chunks, "elasticity", 1, client)


if __name__ == "__main__":
    unittest.main()
