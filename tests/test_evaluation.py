import unittest

from course_compass.evaluation import evaluate, load_questions
from tests.helpers import QUESTIONS, sample_chunks


class RetrievalBenchmarkTests(unittest.TestCase):
    """Pins the numbers reported in docs/LEARNING.md so they cannot silently drift."""

    def test_default_settings_benchmark(self):
        questions = load_questions(QUESTIONS)
        result = evaluate(sample_chunks(80, 20), questions, 80, 20)
        self.assertEqual(result.answerable, 18)
        self.assertEqual(result.top1, 16)
        self.assertEqual(result.top3, 18)
        self.assertEqual(result.false_refusals, 0)
        self.assertEqual((result.correct_refusals, result.unanswerable), (4, 8))

    def test_higher_threshold_trades_recall_for_refusals(self):
        questions = load_questions(QUESTIONS)
        strict = evaluate(sample_chunks(), questions, 80, 20, threshold=0.30)
        self.assertEqual(strict.correct_refusals, 8)
        self.assertEqual(strict.false_refusals, 5)


if __name__ == "__main__":
    unittest.main()
