import json
import unittest

from course_compass.llm import LLMTimeoutError
from course_compass.llm_eval import (CorruptFirstReplyClient, RecordingClient, classify_first_reply,
                                     corrupt_reply, eval_question, eval_quiz, has_key_facts, longest_shared_run,
                                     probe_repair, rescore, run_eval, score_answer_text, score_quiz_item,
                                     subject_map, summarize, support)
from course_compass.quiz import QuizItem, extract_facts, normalize
from course_compass.retrieval import TfidfIndex
from tests.helpers import FakeClient, sample_chunks

TYPE_I = "hypothesis-testing#4"
TYPE_I_ANSWER = json.dumps({"in_notes": True, "citations": [TYPE_I],
                            "answer": f"A Type I error is rejecting a null hypothesis that is actually true [{TYPE_I}]."})
QUIZ_ITEM = {"question": "Which measure divides the change in quantity by the change in consumer income?",
             "correct": "Income elasticity",
             "distractors": ["Cross-price elasticity", "Unit elastic", "Elastic demand"],
             "source_chunk_id": "elasticity#3"}
SPEC = {
    "subjects": {"economics": ["elasticity"], "statistics": ["hypothesis-testing"],
                 "science": ["water-cycle", "cell-respiration"]},
    "questions": [
        {"id": "type1", "question": "What is a Type I error?", "topic": "hypothesis-testing",
         "phrase": "rejecting a null hypothesis that is actually true", "key_facts": [["true"]]},
        {"id": "far", "question": "Who won the 1998 football world cup?", "answerable": False, "kind": "far"},
        {"id": "near", "question": "What was the price of oil in 2008?", "answerable": False, "kind": "near"},
    ],
    "quizzes": [{"id": "quiz-el", "topic": "elasticity", "n": 1}],
}


class Echo:
    """Replies based on the question, so it works under a thread pool."""

    label = "fake"

    def chat(self, messages, temperature=0.0):
        text = messages[-1]["content"]
        if "Write 1 questions" in text:
            return json.dumps({"items": [QUIZ_ITEM]})
        if "price of oil" in text:
            return '{"in_notes": false, "answer": "", "citations": []}'
        if "SOURCE TEXT" in text:  # quiz judge: pick the option line holding the key
            letter = next(line[0] for line in text.splitlines() if line.endswith(") Income elasticity"))
            return json.dumps({"letter": letter, "also_correct": []})
        if "ANSWER:" in text:  # grounding judge
            return '{"supported": true, "unsupported_claims": []}'
        return TYPE_I_ANSWER


class TextHelperTests(unittest.TestCase):
    def test_support_counts_content_words(self):
        self.assertEqual(support("Supply is elastic [x#1].", "supply is very elastic"), 1.0)
        self.assertEqual(support("Supply is elastic.", "demand only"), 0.0)
        self.assertIsNone(support("It is.", "anything"))

    def test_key_facts_need_one_alternative_per_group(self):
        self.assertTrue(has_key_facts("About 6 patients [a#1].", [["6", "six"]]))
        self.assertFalse(has_key_facts("About 16 patients.", [["6", "six"]]))
        self.assertFalse(has_key_facts("Longest path only.", [["longest"], ["float"]]))

    def test_longest_shared_run(self):
        self.assertEqual(longest_shared_run("the quick brown fox", "a quick brown fox jumps"), 3)
        self.assertEqual(longest_shared_run("nothing shared", "at all"), 0)

    def test_sentence_scoring(self):
        chunks = {"a#1": "Supply is more elastic in the long run because firms can build capacity."}
        good = score_answer_text("Supply is more elastic in the long run [a#1]. Firms build capacity [a#1].", chunks)
        self.assertEqual((good["sentences"], good["cited_sentences"], good["supported_sentences"]), (2, 2, 2))
        bad = score_answer_text("Supply is elastic [a#1]. Taxes fall on consumers.", chunks)
        self.assertEqual((bad["cited_sentences"], bad["supported_sentences"], bad["min_support"]), (1, 1, 0.0))
        wrong_cite = score_answer_text("Taxes fall on consumers [a#1].", chunks)
        self.assertEqual(wrong_cite["supported_sentences"], 0)


class RecordingTests(unittest.TestCase):
    def test_records_replies_and_errors(self):
        recorder = RecordingClient(FakeClient("one", LLMTimeoutError("slow")))
        self.assertEqual(recorder.chat([{"role": "user", "content": "hi"}]), "one")
        with self.assertRaises(LLMTimeoutError):
            recorder.chat([{"role": "user", "content": "again"}])
        self.assertEqual(recorder.calls[0]["reply"], "one")
        self.assertIn("LLMTimeoutError", recorder.calls[1]["error"])

    def test_classify_first_reply(self):
        calls = [{"reply": json.dumps({"in_notes": True, "answer": "x [b#9].", "citations": ["a#1"]})}]
        valid, errors, unknown = classify_first_reply(calls, {"a#1"})
        self.assertFalse(valid)
        self.assertEqual(unknown, ["b#9"])
        self.assertEqual(classify_first_reply([], {"a#1"}), (None, [], []))

    def test_corrupt_reply(self):
        answer = json.loads(corrupt_reply(TYPE_I_ANSWER))
        self.assertEqual(answer["citations"], ["made-up-topic#99"])
        self.assertNotIn(TYPE_I, answer["answer"])
        quiz = json.loads(corrupt_reply(json.dumps({"items": [QUIZ_ITEM]})))
        self.assertEqual(quiz["items"][0]["distractors"][0], "Banana republic")
        self.assertEqual(corrupt_reply("not json"), "not json")
        client = CorruptFirstReplyClient(FakeClient(TYPE_I_ANSWER, TYPE_I_ANSWER))
        self.assertNotEqual(client.chat([]), TYPE_I_ANSWER)
        self.assertEqual(client.chat([]), TYPE_I_ANSWER)


class QuizScoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chunks = sample_chunks()
        cls.text = {c.id: c.text for c in cls.chunks}
        cls.term_topic = {normalize(f.term): f.topic for f in extract_facts(cls.chunks)}

    def item(self, **changes):
        raw = {**QUIZ_ITEM, **changes}
        return QuizItem("x", "elasticity", raw["question"], raw["correct"], raw["distractors"],
                        raw["source_chunk_id"], "llm")

    def test_good_item(self):
        score = score_quiz_item(self.item(), self.text, self.term_topic, subject_map(SPEC))
        self.assertTrue(score.key_in_source)
        self.assertTrue(score.correct_is_note_term)
        self.assertEqual(score.same_topic_distractors, 3)
        self.assertEqual(score.off_subject_distractors, 0)
        self.assertFalse(score.longest_option_is_correct)
        self.assertFalse(score.answer_in_question)

    def test_giveaways_are_flagged(self):
        score = score_quiz_item(
            self.item(correct="the percentage change in quantity demanded divided by consumer income",
                      distractors=["Catchment", "P-value", "Unit elastic"],
                      question="What is income elasticity? the percentage change in quantity demanded divided by"),
            self.text, self.term_topic, subject_map(SPEC))
        self.assertFalse(score.correct_is_note_term)
        self.assertTrue(score.longest_option_is_correct)
        self.assertEqual(score.off_subject_distractors, 2)
        self.assertGreaterEqual(score.copied_run, 8)


class RunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chunks = sample_chunks()
        cls.index = TfidfIndex(cls.chunks)

    def test_answerable_question_scored(self):
        score = eval_question(SPEC["questions"][0], self.index, self.chunks, FakeClient(TYPE_I_ANSWER))
        self.assertEqual(score.status, "answered")
        self.assertTrue(score.first_reply_valid)
        self.assertTrue(score.cites_expected_chunk)
        self.assertTrue(score.key_facts_present)
        self.assertEqual((score.sentences, score.supported_sentences), (1, 1))

    def test_repaired_and_rejected_answers(self):
        bad = json.dumps({"in_notes": True, "answer": "Made up [physics#9].", "citations": ["physics#9"]})
        repaired = eval_question(SPEC["questions"][0], self.index, self.chunks, FakeClient(bad, TYPE_I_ANSWER))
        self.assertEqual((repaired.status, repaired.calls, repaired.first_reply_valid), ("answered", 2, False))
        self.assertEqual(repaired.raw_unknown_citations, ["physics#9"])
        rejected = eval_question(SPEC["questions"][0], self.index, self.chunks, FakeClient(bad, bad))
        self.assertEqual(rejected.status, "rejected")
        errored = eval_question(SPEC["questions"][0], self.index, self.chunks, FakeClient(LLMTimeoutError("t")))
        self.assertEqual(errored.status, "error")

    def test_refusals_by_threshold_and_by_llm(self):
        far = eval_question(SPEC["questions"][1], self.index, self.chunks, FakeClient())
        self.assertEqual((far.status, far.calls), ("refused_threshold", 0))
        near = eval_question(SPEC["questions"][2], self.index, self.chunks,
                             FakeClient('{"in_notes": false, "answer": "", "citations": []}'))
        self.assertEqual(near.status, "refused_llm")
        hallucinated = eval_question(SPEC["questions"][2], self.index, self.chunks, FakeClient(
            json.dumps({"in_notes": True, "answer": "About $100 [elasticity#2].", "citations": ["elasticity#2"]})))
        self.assertEqual(hallucinated.status, "answered")

    def test_quiz_scored(self):
        score = eval_quiz(SPEC["quizzes"][0], self.chunks, FakeClient(json.dumps({"items": [QUIZ_ITEM]})),
                          subject_of=subject_map(SPEC))
        self.assertEqual((score.status, score.calls, score.first_reply_valid), ("accepted", 1, True))
        self.assertEqual(len(score.items), 1)

    def test_repair_probe(self):
        probe = probe_repair("type1", SPEC, self.index, self.chunks, FakeClient(TYPE_I_ANSWER, TYPE_I_ANSWER))
        self.assertEqual((probe.outcome, probe.calls), ("repaired", 2))
        probe = probe_repair("quiz-el", SPEC, self.index, self.chunks,
                             FakeClient(json.dumps({"items": [QUIZ_ITEM]}), json.dumps({"items": [QUIZ_ITEM]})))
        self.assertEqual(probe.outcome, "repaired")

    def test_run_eval_summary_and_rescore(self):
        spec = dict(SPEC, repair_probes=["type1"])
        results = run_eval(spec, self.chunks, Echo(), judge=Echo(), workers=2)
        summary = results["summary"]
        self.assertEqual(summary["answerable"]["answered"], {"n": 1, "of": 1, "pct": 100.0})
        self.assertEqual(summary["refusal"]["off_topic_refused"]["n"], 2)
        self.assertEqual(summary["refusal"]["off_topic_answered_hallucinated"]["n"], 0)
        self.assertEqual(summary["grounding"]["answers_judged_supported"]["n"], 1)
        self.assertEqual(summary["structured_output"]["forced_repair_probes_repaired"]["n"], 1)
        self.assertEqual(summary["quiz"]["items"], 1)
        self.assertEqual(summary["quiz"]["judge_agrees_with_key"]["n"], 1)
        from dataclasses import asdict
        saved = {"answers": [asdict(a) for a in results["answers"]],
                 "quizzes": [asdict(q) for q in results["quizzes"]],
                 "repair_probes": [asdict(p) for p in results["repair_probes"]]}
        again = rescore(json.loads(json.dumps(saved)), spec, self.chunks)
        self.assertEqual(again["summary"], summary)

    def test_summarize_handles_empty(self):
        self.assertIsNone(summarize([], [])["refusal"]["off_topic_refused"]["pct"])


if __name__ == "__main__":
    unittest.main()
