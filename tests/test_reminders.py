import unittest
from datetime import date, datetime, timezone

from course_compass.reminders import (due_digest, escape_text, fold_line, parse_ics, review_events, to_ics,
                                      unescape_text, upcoming_count)
from course_compass.store import empty_db

NOW = datetime(2026, 10, 30, 9, 0, tzinfo=timezone.utc)


def make_db(cards):
    """cards: (item_id, topic, question, due, ease, repetitions)"""
    db = empty_db()
    for item_id, topic, question, due, ease, reps in cards:
        db["quiz_items"][item_id] = {"id": item_id, "topic": topic, "question": question, "correct": "x",
                                     "distractors": ["a", "b", "c"], "source_chunk_id": f"{topic}#1",
                                     "origin": "local"}
        db["cards"][item_id] = {"ease": ease, "interval": 0 if (reps == 0 and ease == 2.5) else 1, "repetitions": reps, "due": due}
    return db


DB = make_db([
    ("a1", "elasticity", "What is elastic demand?", "2026-10-30", 2.5, 1),
    ("a2", "elasticity", "Unit elastic; revenue, price?", "2026-10-28", 1.42, 0),
    ("b1", "water-cycle", "What is evaporation?", "2026-10-25", 2.5, 2),
    ("c1", "hypothesis-testing", "What is a p-value?", "2026-11-03", 2.5, 1),
    ("d1", "cell-respiration", "What is ATP?", "2026-12-25", 2.5, 3),
])
TODAY = date(2026, 10, 30)


class DigestTests(unittest.TestCase):
    def test_groups_by_topic_most_overdue_first(self):
        topics = due_digest(DB, TODAY)
        self.assertEqual([t.topic for t in topics], ["water-cycle", "elasticity"])
        water, elasticity = topics
        self.assertEqual(water.most_overdue, 5)
        self.assertEqual([c.item_id for c in elasticity.cards], ["a2", "a1"])  # overdue, then hard
        self.assertEqual(elasticity.cards[0].difficulty, "hard")
        self.assertEqual(elasticity.cards[1].days_overdue, 0)
        self.assertEqual(upcoming_count(DB, TODAY, 7), 1)

    def test_hardest_breaks_ties_between_equally_overdue_topics(self):
        db = make_db([("x", "b-topic", "q", "2026-10-29", 1.3, 1), ("y", "a-topic", "q", "2026-10-29", 2.5, 1)])
        self.assertEqual([t.topic for t in due_digest(db, TODAY)], ["b-topic", "a-topic"])

    def test_cards_without_quiz_items_are_ignored(self):
        db = make_db([("x", "t", "q", "2026-10-29", 2.5, 1)])
        db["cards"]["orphan"] = {"ease": 2.5, "interval": 1, "repetitions": 1, "due": "2026-10-01"}
        self.assertEqual(sum(len(t.cards) for t in due_digest(db, TODAY)), 1)


class IcsTests(unittest.TestCase):
    def test_escaping_round_trips(self):
        text = 'a\\b; c, d\nnext "line"'
        self.assertEqual(escape_text(text), 'a\\\\b\\; c\\, d\\nnext "line"')
        self.assertEqual(unescape_text(escape_text(text)), text)

    def test_folding_respects_75_octets_and_utf8(self):
        line = "DESCRIPTION:" + "é" * 100 + "x" * 50
        folded = fold_line(line)
        for part in folded.split("\r\n"):
            self.assertLessEqual(len(part.encode("utf-8")), 75)
        self.assertEqual(folded.replace("\r\n ", ""), line)

    def test_export_parses_back_with_correct_dates_and_summaries(self):
        events = review_events(DB, TODAY, days=14)
        text = to_ics(events, now=NOW)
        self.assertTrue(text.startswith("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:"))
        self.assertTrue(text.endswith("END:VCALENDAR\r\n"))
        self.assertNotIn("\n", text.replace("\r\n", ""))  # every line ends with CRLF
        for line in text.split("\r\n"):
            self.assertLessEqual(len(line.encode("utf-8")), 75)
        self.assertEqual(text.count("BEGIN:VEVENT"), text.count("END:VEVENT"))

        parsed = parse_ics(text)
        got = {(e["DTSTART"], e["SUMMARY"]) for e in parsed}
        self.assertEqual(got, {
            ("20261030", "Review elasticity: 2 cards"),     # one due today, one overdue -> today
            ("20261030", "Review water-cycle: 1 card"),      # 5 days overdue -> today
            ("20261103", "Review hypothesis-testing: 1 card"),
        })  # cell-respiration (due 2026-12-25) is past the 14-day horizon
        for event in parsed:
            self.assertEqual(event["DTSTAMP"], "20261030T090000Z")
            start = datetime.strptime(event["DTSTART"], "%Y%m%d").date()
            end = datetime.strptime(event["DTEND"], "%Y%m%d").date()
            self.assertEqual((end - start).days, 1)
            self.assertTrue(event["UID"].endswith("@course-compass"))
        self.assertEqual(len({e["UID"] for e in parsed}), len(parsed))
        elasticity = next(e for e in parsed if "elasticity" in e["SUMMARY"])
        self.assertIn("- Unit elastic; revenue, price?", elasticity["DESCRIPTION"])  # escaped then restored
        self.assertIn("1 of them overdue.", elasticity["DESCRIPTION"])
        self.assertIn("local data, no LLM", elasticity["DESCRIPTION"])

    def test_dates_follow_sm2_schedule(self):
        from course_compass.review import new_card, schedule
        card = schedule(schedule(new_card(TODAY), 4, TODAY), 4, date(2026, 10, 31))  # 1 day, then 6 days
        db = make_db([("z", "elasticity", "q", card.due, card.ease, card.repetitions)])
        parsed = parse_ics(to_ics(review_events(db, TODAY, days=14), now=NOW))
        self.assertEqual(card.due, "2026-11-06")
        self.assertEqual(parsed[0]["DTSTART"], "20261106")

    def test_uid_is_stable_across_exports(self):
        first = [e["UID"] for e in parse_ics(to_ics(review_events(DB, TODAY), now=NOW))]
        second = [e["UID"] for e in parse_ics(to_ics(review_events(DB, TODAY), now=NOW))]
        self.assertEqual(first, second)

    def test_empty_calendar_is_still_valid(self):
        text = to_ics([], now=NOW)
        self.assertEqual(parse_ics(text), [])
        self.assertIn("VERSION:2.0\r\n", text)

    def test_negative_horizon_rejected(self):
        with self.assertRaises(ValueError):
            review_events(DB, TODAY, days=-1)


if __name__ == "__main__":
    unittest.main()
