"""Hub logic: grade standing, the study planner, time tracking, exam prep, tutor and alerts."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from course_compass.hub.db import Hub
from course_compass.hub.examprep import exam_plan
from course_compass.hub.grades import expected_count, standing
from course_compass.hub.notify import check_ntfy_url, push_text, send_pending
from course_compass.hub.planner import base_estimate, log_time, running_timer, schedule, start_timer, stop_timer, \
    week_report
from course_compass.hub.syllabus import Component
from course_compass.hub.tutor import TutorError, check_work, explain_back, walkthrough
from tests.helpers import FakeClient, sample_chunks

TZ = ZoneInfo("America/Toronto")
COMPONENTS = [Component("Assignments (5)", 30, "", 1), Component("Midterm exam", 25, "", 2),
              Component("Final exam", 45, "", 3)]


def grade(name, num, den=20, gtype="item"):
    return {"name": name, "points_num": num, "points_den": den, "gtype": gtype, "displayed": ""}


class GradeTests(unittest.TestCase):
    def test_syllabus_weights_spread_over_expected_count(self):
        st = standing([grade("Assignment 1", 18), grade("Assignment 2", 16)], COMPONENTS)
        self.assertEqual(st.method, "syllabus")
        self.assertEqual(st.graded_weight, 12.0)            # 2 of 5 assignments, 6% each
        self.assertEqual(st.current_percent, 85.0)
        self.assertEqual(st.remaining_weight, 88.0)

    def test_needed_and_final_scenario(self):
        st = standing([grade("Assignment 1", 18), grade("Assignment 2", 16)], COMPONENTS, target=80)
        need = st.needed()
        self.assertEqual(need["status"], "reachable")
        # (80 - 10.2) / 88 = 79.3%
        self.assertEqual(need["needed_percent"], 79.3)
        self.assertEqual(need["final"]["name"], "Final exam")

    def test_whatif_changes_projection(self):
        rows = [grade("Assignment 1", 18), grade("Assignment 2", 16)]
        base = standing(rows, COMPONENTS)
        low = standing(rows, COMPONENTS, whatif={"Midterm exam": 50})
        self.assertEqual(base.projected(), 85.0)
        self.assertLess(low.projected(), base.projected())

    def test_out_of_reach_and_secured(self):
        rows = [grade("Assignment 1", 2), grade("Midterm", 2)]
        self.assertEqual(standing(rows, COMPONENTS, target=99).needed()["status"], "out-of-reach")
        self.assertEqual(standing(rows, COMPONENTS, target=1).needed()["status"], "secured")

    def test_unweighted_fallback_is_labelled(self):
        st = standing([grade("Lab 1", 10), grade("Lab 2", 20)], [])
        self.assertEqual(st.method, "unweighted")
        self.assertEqual(st.current_percent, 75.0)
        self.assertEqual(standing([], []).method, "none")

    def test_expected_count(self):
        self.assertEqual(expected_count("Assignments (5)"), 5)
        self.assertEqual(expected_count("4 quizzes"), 4)
        self.assertIsNone(expected_count("Final exam"))


MON = datetime(2026, 11, 23, 8, 0, tzinfo=TZ).astimezone(timezone.utc)   # a Monday
EVENINGS = {str(d): [["18:00", "21:00"]] for d in range(7)}


class PlannerTests(unittest.TestCase):
    def test_earliest_deadline_first_and_before_buffer(self):
        work = [{"uid": "b", "course_id": 1, "title": "B", "due": MON + timedelta(days=6), "hours": 2},
                {"uid": "a", "course_id": 1, "title": "A", "due": MON + timedelta(days=3), "hours": 2}]
        plan = schedule(work, EVENINGS, [], MON, daily_cap_hours=3, block_minutes=90, buffer_days=1, tz=TZ)
        self.assertEqual(plan.blocks[0].item_uid, "a")
        for block in plan.blocks:
            due = next(w["due"] for w in work if w["uid"] == block.item_uid)
            self.assertLessEqual(block.end, due - timedelta(days=1))
            self.assertGreaterEqual(block.start.astimezone(TZ).hour, 18)
        self.assertEqual(plan.at_risk, [])
        self.assertAlmostEqual(sum(plan.scheduled_hours.values()), 4.0)

    def test_busy_time_is_avoided(self):
        busy = [(datetime(2026, 11, 23, 18, 0, tzinfo=TZ), datetime(2026, 11, 23, 21, 0, tzinfo=TZ))]
        work = [{"uid": "a", "course_id": 1, "title": "A", "due": MON + timedelta(days=3), "hours": 1}]
        plan = schedule(work, EVENINGS, busy, MON, buffer_days=0, tz=TZ)
        self.assertNotEqual(plan.blocks[0].start.astimezone(TZ).date(), date(2026, 11, 23))

    def test_not_enough_time_is_flagged(self):
        work = [{"uid": "a", "course_id": 1, "title": "A", "due": MON + timedelta(days=1, hours=14), "hours": 20}]
        plan = schedule(work, EVENINGS, [], MON, buffer_days=0, tz=TZ)
        self.assertEqual(plan.at_risk[0]["uid"], "a")
        self.assertGreater(plan.at_risk[0]["unscheduled_hours"], 10)

    def test_daily_cap(self):
        work = [{"uid": str(i), "course_id": 1, "title": str(i), "due": MON + timedelta(days=10), "hours": 3}
                for i in range(4)]
        plan = schedule(work, {str(d): [["08:00", "22:00"]] for d in range(7)}, [], MON, daily_cap_hours=2, tz=TZ)
        per_day: dict = {}
        for block in plan.blocks:
            day = block.start.astimezone(TZ).date()
            per_day[day] = per_day.get(day, 0) + (block.end - block.start).total_seconds() / 3600
        self.assertTrue(all(hours <= 2.0001 for hours in per_day.values()))

    def test_estimates(self):
        self.assertGreater(base_estimate({"kind": "exam", "title": "Final exam", "weight": 40}),
                           base_estimate({"kind": "quiz", "title": "Weekly quiz", "weight": 1}))
        self.assertLessEqual(base_estimate({"kind": "exam", "title": "Final exam", "weight": 90}),
                             base_estimate({"kind": "exam", "title": "Final exam", "weight": 15}) * 3)


class TimeTests(unittest.TestCase):
    def setUp(self):
        self.hub = Hub(tempfile.mkdtemp())

    def test_timer_and_week_report(self):
        start_timer(self.hub, None, None, now=MON)
        self.assertIsNotNone(running_timer(self.hub))
        stopped = stop_timer(self.hub, now=MON + timedelta(minutes=50))
        self.assertIsNotNone(stopped)
        self.assertIsNone(running_timer(self.hub))
        log_time(self.hub, None, None, MON + timedelta(days=1), 30)
        report = week_report(self.hub, date(2026, 11, 23), tz=TZ)
        self.assertAlmostEqual(report["total_actual"], 80 / 60, places=2)
        self.assertAlmostEqual(report["by_day"][0], 50 / 60, places=2)

    def test_log_time_bounds(self):
        with self.assertRaises(ValueError):
            log_time(self.hub, None, None, MON, 0)
        with self.assertRaises(ValueError):
            log_time(self.hub, None, None, MON, 2000)


class ExamPrepTests(unittest.TestCase):
    def test_three_phases_weakest_first(self):
        attempts = [{"topic": "weak", "correct": False, "date": "2026-11-01"},
                    {"topic": "weak", "correct": False, "date": "2026-11-02"},
                    {"topic": "strong", "correct": True, "date": "2026-11-02"}]
        days = exam_plan(date(2026, 12, 10), date(2026, 11, 23), ["strong", "weak"], attempts, {})
        phases = [d.phase for d in days]
        self.assertEqual(phases[0], "learn")
        self.assertEqual(phases[-1], "cram")
        self.assertIn("practice", phases)
        self.assertIn("weak", " ".join(t.title for t in days[0].tasks))
        self.assertEqual(exam_plan(date(2026, 11, 23), date(2026, 11, 23), ["a"], [], {}), [])


class TutorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chunks = sample_chunks()
        cls.ids = [c.id for c in cls.chunks if "hypothesis" in c.id]

    def test_local_walkthrough_has_no_invented_answer(self):
        result = walkthrough("How do I test a hypothesis with a p-value?", self.chunks)
        self.assertEqual(result.mode, "local")
        self.assertEqual(result.data["final_answer"], "")
        self.assertTrue(any(i in result.data["hints"][0] for i in self.ids))

    def test_llm_walkthrough_validates_citations_and_repairs(self):
        bad = json.dumps({"hints": ["a", "b"], "steps": [{"text": "x [made-up#1]", "citations": []}],
                          "final_answer": "42"})
        good = json.dumps({"hints": ["Think about H0", "Compare p to alpha"],
                           "steps": [{"text": f"Use [{self.ids[0]}]", "citations": [self.ids[0]]}],
                           "final_answer": "Reject H0", "outside_knowledge": ""})
        client = FakeClient(bad, good)
        result = walkthrough("How do I test a hypothesis with a p-value?", self.chunks, client)
        self.assertEqual(result.mode, "llm")
        self.assertEqual(result.data["final_answer"], "Reject H0")
        self.assertEqual(len(client.calls), 2)              # one repair attempt

    def test_llm_failure_after_repair_raises(self):
        client = FakeClient("not json", "still not json")
        with self.assertRaises(TutorError):
            check_work("Test H0", "I rejected it", self.chunks, client)

    def test_explain_back_local_scores_coverage(self):
        weak = explain_back("p-value", "it is a number", self.chunks)
        strong_text = " ".join(c.text for c in self.chunks if c.id in self.ids[:2])
        strong = explain_back("p-value", strong_text, self.chunks)
        self.assertLess(weak.data["score"], strong.data["score"])

    def test_empty_problem_rejected(self):
        with self.assertRaises(TutorError):
            walkthrough("   ", self.chunks)


class NotifyTests(unittest.TestCase):
    def test_ntfy_url_rules(self):
        with self.assertRaises(ValueError):
            check_ntfy_url("http://ntfy.sh/longtopicname")
        with self.assertRaises(ValueError):
            check_ntfy_url("https://ntfy.sh/abc")
        self.assertTrue(check_ntfy_url("https://ntfy.sh/a-long-random-topic"))

    def test_push_text_keeps_detail_out(self):
        title, body = push_text({"kind": "new_announcement", "title": "STAT 230: Midterm room",
                                 "detail": "Secret body text"})
        self.assertNotIn("Secret", title + body)

    def test_several_changes_become_one_summary(self):
        hub = Hub(tempfile.mkdtemp())
        hub.set_setting("ntfy_url", "https://ntfy.sh/a-long-random-topic")
        for i in range(5):
            hub.add_change("2026-11-23T12:00:00Z", "new_item", f"STAT 230: Item {i}")
        sent_mac, sent_push = [], []
        count = send_pending(hub, macos=lambda t, b: sent_mac.append((t, b)),
                             ntfy=lambda u, t, b: sent_push.append((t, b)))
        self.assertEqual(count, 5)
        self.assertEqual(len(sent_mac), 1)
        self.assertEqual(len(sent_push), 1)
        self.assertEqual(send_pending(hub, macos=lambda *a: None, ntfy=lambda *a: None), 0)


if __name__ == "__main__":
    unittest.main()
