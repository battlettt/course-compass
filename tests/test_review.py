import unittest
from datetime import date

from course_compass.review import (MIN_EASE, Card, TopicStat, due_queue, new_card, quality_from_correct,
                                   schedule, study_plan, topic_stats, weak_topics)

TODAY = date(2026, 10, 30)


class ScheduleTests(unittest.TestCase):
    def test_intervals_grow_on_success(self):
        card, intervals = new_card(TODAY), []
        for _ in range(5):
            card = schedule(card, 4, TODAY)
            intervals.append(card.interval)
        self.assertEqual(intervals[:2], [1, 6])
        self.assertEqual(intervals, sorted(intervals))
        self.assertGreater(intervals[-1], intervals[2])

    def test_failure_resets_interval_and_lowers_ease(self):
        card = schedule(schedule(schedule(new_card(TODAY), 4, TODAY), 4, TODAY), 4, TODAY)
        failed = schedule(card, 1, TODAY)
        self.assertEqual(failed.interval, 1)
        self.assertEqual(failed.repetitions, 0)
        self.assertLess(failed.ease, card.ease)
        self.assertLess(failed.interval, card.interval)

    def test_ease_never_below_minimum(self):
        card = new_card(TODAY)
        for _ in range(10):
            card = schedule(card, 0, TODAY)
        self.assertEqual(card.ease, MIN_EASE)

    def test_due_date_is_today_plus_interval(self):
        card = schedule(schedule(new_card(TODAY), 5, TODAY), 5, TODAY)
        self.assertEqual(card.due, "2026-11-05")

    def test_quality_bounds(self):
        with self.assertRaises(ValueError):
            schedule(new_card(TODAY), 6, TODAY)
        self.assertGreaterEqual(quality_from_correct(True), 3)
        self.assertLess(quality_from_correct(False), 3)


class QueueTests(unittest.TestCase):
    def test_due_queue_orders_overdue_then_hardest(self):
        cards = {
            "future": Card(due="2026-11-02"),
            "today_easy": Card(ease=2.5, due="2026-10-30"),
            "today_hard": Card(ease=1.5, due="2026-10-30"),
            "overdue": Card(ease=2.5, due="2026-10-20"),
        }
        self.assertEqual(due_queue(cards, TODAY), ["overdue", "today_hard", "today_easy"])

    def test_empty_queue(self):
        self.assertEqual(due_queue({}, TODAY), [])
        self.assertEqual(Card.from_dict(Card(due="x").to_dict()), Card(due="x"))


class WeakTopicTests(unittest.TestCase):
    def test_weak_topics_ranked_by_error_rate(self):
        attempts = ([{"topic": "a", "correct": False}] * 3 + [{"topic": "a", "correct": True}]
                    + [{"topic": "b", "correct": True}] * 4)
        ranked = weak_topics(topic_stats(attempts, ["a", "b", "c"]))
        self.assertEqual([s.topic for s in ranked], ["a", "c", "b"])
        self.assertAlmostEqual(ranked[1].weakness, 0.5)

    def test_unseen_topic_is_neutral(self):
        self.assertEqual(TopicStat("x", 0, 0).weakness, 0.5)

    def test_study_plan_spends_most_time_on_weakest(self):
        stats = [TopicStat("strong", 10, 10), TopicStat("weak", 10, 1), TopicStat("new", 0, 0)]
        blocks = study_plan(stats, {"weak": 3}, minutes=60)
        self.assertEqual(blocks[0].topic, "weak")
        self.assertEqual(blocks[0].due_cards, 3)
        self.assertEqual(sum(b.minutes for b in blocks), 60)
        minutes = [b.minutes for b in blocks]
        self.assertEqual(minutes, sorted(minutes, reverse=True))

    def test_study_plan_never_negative_and_sums_to_budget(self):
        stats = [TopicStat(f"t{i}", 0, 0) for i in range(8)]
        blocks = study_plan(stats, {}, minutes=5)
        self.assertEqual(sum(b.minutes for b in blocks), 5)
        self.assertTrue(all(b.minutes > 0 for b in blocks))

    def test_study_plan_validation(self):
        with self.assertRaises(ValueError):
            study_plan([TopicStat("a", 0, 0)], {}, minutes=2)
        self.assertEqual(study_plan([], {}, minutes=30), [])


if __name__ == "__main__":
    unittest.main()
