import unittest
from datetime import date

from course_compass.progress import cram_topics, split_questions, topic_progress
from course_compass.review import TopicStat
from course_compass.store import empty_db

TODAY = date(2026, 11, 2)
TOPICS = ["elasticity", "water-cycle", "cell-respiration"]


def attempt(item, topic, correct, day, session=None):
    raw = {"item_id": item, "topic": topic, "correct": correct, "chosen": "A", "date": day}
    if session:
        raw["session"] = session
    return raw


def db_with(attempts, due=()):
    db = empty_db()
    db["attempts"] = list(attempts)
    for item, topic, when in due:
        db["quiz_items"][item] = {"topic": topic}
        db["cards"][item] = {"ease": 2.0, "interval": 1, "repetitions": 0, "due": when}
    return db


class ProgressTests(unittest.TestCase):
    def test_no_attempts_means_no_trend(self):
        rows = topic_progress(db_with([]), TOPICS, TODAY)
        for row in rows:
            self.assertEqual(row.movement, "no history")
            self.assertIsNone(row.early)
            self.assertEqual(row.sessions, [])
            self.assertEqual(row.trend_text, "not quizzed yet")
            self.assertIn("no history", row.advice)

    def test_improving_slipping_and_too_few(self):
        attempts = [
            attempt("e1", "elasticity", False, "2026-10-30"), attempt("e2", "elasticity", False, "2026-10-30"),
            attempt("e1", "elasticity", True, "2026-10-31"), attempt("e2", "elasticity", True, "2026-11-01"),
            attempt("w1", "water-cycle", True, "2026-10-30"), attempt("w2", "water-cycle", True, "2026-10-30"),
            attempt("w1", "water-cycle", False, "2026-11-01"), attempt("w2", "water-cycle", False, "2026-11-01"),
            attempt("c1", "cell-respiration", True, "2026-11-01"),
        ]
        rows = {row.topic: row for row in topic_progress(db_with(attempts), TOPICS, TODAY)}
        self.assertEqual(rows["elasticity"].movement, "improving")
        self.assertEqual((rows["elasticity"].early, rows["elasticity"].recent), (0.0, 1.0))
        self.assertEqual(rows["elasticity"].trend_text, "improving 0% -> 100%")
        self.assertEqual(rows["elasticity"].sessions,
                         [("2026-10-30", 0, 2), ("2026-10-31", 1, 1), ("2026-11-01", 1, 1)])
        self.assertEqual(rows["water-cycle"].movement, "slipping")
        self.assertEqual(rows["cell-respiration"].movement, "not enough history")
        self.assertIn("need 4", rows["cell-respiration"].trend_text)

    def test_order_is_weakest_first_and_history_sorted_by_date(self):
        attempts = [attempt("e1", "elasticity", True, "2026-11-01"),   # appended out of date order
                    attempt("e1", "elasticity", False, "2026-10-30"),
                    attempt("e1", "elasticity", False, "2026-10-30"),
                    attempt("e1", "elasticity", True, "2026-11-01"),
                    attempt("w1", "water-cycle", True, "2026-10-30")]
        rows = topic_progress(db_with(attempts), TOPICS, TODAY)
        # weakness: cell-respiration 0.50 (no attempts wins the tie), elasticity 0.50, water-cycle 0.33
        self.assertEqual([r.topic for r in rows], ["cell-respiration", "elasticity", "water-cycle"])
        self.assertEqual(rows[1].movement, "improving")  # chronological, not append order

    def test_due_cards_are_tied_to_the_trend(self):
        attempts = [attempt("w1", "water-cycle", True, "2026-10-28"), attempt("w2", "water-cycle", True, "2026-10-28"),
                    attempt("w1", "water-cycle", False, "2026-11-01"), attempt("w2", "water-cycle", True, "2026-11-01", "cram")]
        db = db_with(attempts, due=[("w1", "water-cycle", "2026-11-02"), ("w2", "water-cycle", "2026-11-02"),
                                    ("w3", "water-cycle", "2026-12-01")])
        row = next(r for r in topic_progress(db, TOPICS, TODAY) if r.topic == "water-cycle")
        self.assertEqual(row.due, 2)
        self.assertEqual(row.due_missed, 1)
        self.assertEqual(row.cram_attempts, 1)
        self.assertEqual(row.movement, "slipping")
        self.assertEqual(row.advice, "Slipping and 2 card(s) due, 1 missed last time: review these first.")


class CramSelectionTests(unittest.TestCase):
    STATS = [TopicStat("elasticity", 10, 9), TopicStat("water-cycle", 4, 1), TopicStat("cell-respiration", 0, 0),
             TopicStat("hypothesis-testing", 6, 3)]

    def test_weakest_first(self):
        self.assertEqual(cram_topics(self.STATS, None, 3), ["water-cycle", "cell-respiration", "hypothesis-testing"])

    def test_unquizzable_topics_are_skipped(self):
        self.assertEqual(cram_topics(self.STATS, {"elasticity", "hypothesis-testing"}, 2),
                         ["hypothesis-testing", "elasticity"])

    def test_bad_count(self):
        with self.assertRaises(ValueError):
            cram_topics(self.STATS, None, 0)

    def test_split_gives_weakest_topic_the_extra_question(self):
        self.assertEqual(split_questions(["a", "b"], 5), {"a": 3, "b": 2})
        self.assertEqual(split_questions(["a", "b", "c"], 2), {"a": 1, "b": 1})


if __name__ == "__main__":
    unittest.main()
