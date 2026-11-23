"""Demo data: five made-up courses with work, announcements, grades, outlines and notes.

`course-compass demo` fills a separate home folder (default
~/.course-compass-demo) so you can try every screen without signing in to
LEARN. Dates are relative to today, so the demo always looks current.
Everything here is invented sample data, labelled as such in the UI.
"""

from __future__ import annotations

import shutil
from datetime import datetime, time, timedelta
from pathlib import Path

from .db import Hub
from .htmltext import to_text
from .planner import log_time, replan
from .sync import save_syllabus
from .syllabus import parse_syllabus
from .timeutil import local_tz, to_iso, utcnow

NOTES_DIR = Path(__file__).parent / "demo_notes"
BASE = "https://learn.example.edu"

COURSES = [
    (900101, "STAT 230", "STAT 230 - Probability (Demo)", "stat230-hypothesis-testing.md"),
    (900102, "ECON 101", "ECON 101 - Introduction to Microeconomics (Demo)", "econ101-elasticity.md"),
    (900103, "CS 246", "CS 246 - Object-Oriented Software Development (Demo)", "cs246-object-oriented-design.md"),
    (900104, "BIOL 130", "BIOL 130 - Introductory Cell Biology (Demo)", "biol130-cell-respiration.md"),
    (900105, "EARTH 121", "EARTH 121 - Introductory Earth Sciences (Demo)", "earth121-water-cycle.md"),
]


def _at(today, days: int, hour: int, minute: int = 0) -> str:
    tz = local_tz()
    return to_iso(datetime.combine(today + timedelta(days=days), time(hour, minute), tzinfo=tz))


def outline(code: str, today, extra: list[str], midterm_day: int | None) -> str:
    fmt = lambda d: (today + timedelta(days=d)).strftime("%B %-d, %Y")   # noqa: E731
    midterm = [f"Midterm Exam: {fmt(midterm_day)}, 7:00-8:50 PM"] if midterm_day is not None else []
    return "\n".join([
        f"# {code} Course Outline (demo)",
        "Instructor: Dr. Sample Person, sample.person@example.edu",
        "Teaching Assistant: Alex Example, alex.example@example.edu",
        "Office hours: Tuesdays 2:30-4:00 PM in DC 1350, or by appointment",
        "## Assessment",
        *extra,
        "## Key dates",
        *midterm,
        f"Final Exam: {fmt(34)} (time and room set by the Registrar)",
        "## Late policy",
        "Late submissions lose 10% per day and are not accepted after 48 hours.",
        "Required textbook: see the course LEARN page for the open textbook.",
    ])


def seed(hub: Hub, now: datetime | None = None) -> None:
    now = now or utcnow()
    stamp = to_iso(now)
    tz = local_tz()
    today = now.astimezone(tz).date()
    hub.set_setting("d2l_base", BASE)
    hub.set_setting("demo", True)
    term_start = to_iso(datetime.combine(today - timedelta(days=60), time(0), tzinfo=tz))
    term_end = to_iso(datetime.combine(today + timedelta(days=45), time(0), tzinfo=tz))
    for ou, code, name, _ in COURSES:
        hub.upsert_course({"id": ou, "code": code, "name": name, "term": "Demo term", "start": term_start,
                           "end": term_end, "url": f"{BASE}/d2l/home/{ou}"}, stamp)
    hub.update_course(900101, target=85.0)
    hub.update_course(900103, target=80.0)

    items = [
        (900101, "assignment", "Assignment 4: Hypothesis tests", 1, 23, 59, None, 6.0),
        (900101, "quiz", "Weekly quiz 9", 3, 17, 0, None, 1.0),
        (900101, "assignment", "Assignment 5: Power and sample size", 12, 23, 59, None, 6.0),
        (900102, "assignment", "Problem set 6: Elasticity", 2, 12, 0, None, 4.0),
        (900102, "quiz", "Online quiz: Market structures", 6, 23, 59, None, 2.0),
        (900102, "assignment", "Term paper proposal", 15, 23, 59, None, 10.0),
        (900103, "assignment", "A4: Observer and Decorator", 4, 17, 0, None, 8.0),
        (900103, "assignment", "Project: Design document (DD1)", 18, 17, 0, None, 10.0),
        (900104, "quiz", "Lab quiz 7", 0, 20, 0, None, 1.0),
        (900104, "assignment", "Lab report 4: Cellular respiration", 5, 23, 59, None, 5.0),
        (900105, "assignment", "Field trip reflection", 8, 23, 59, None, 3.0),
        (900103, "assignment", "A3: RAII and the rule of five", -6, 17, 0, None, 8.0),
        (900101, "assignment", "Assignment 3: Discrete distributions", -9, 23, 59, None, 6.0),
    ]
    for index, (ou, kind, title, days, hour, minute, _, weight) in enumerate(items):
        status = "submitted" if days < 0 else "open"
        hub.upsert_item({"uid": f"demo:{ou}:{index}", "course_id": ou, "kind": kind, "title": title,
                         "due": _at(today, days, hour, minute), "source": "d2l", "weight": weight,
                         "url": f"{BASE}/d2l/lms/dropbox/user/folder_submit_files.d2l?db={index}&ou={ou}",
                         "details": DEMO_INSTRUCTIONS.get(title, "Submit a PDF on LEARN."), "status": status},
                        stamp)
    hub.upsert_item({"uid": "demo:900104:midterm", "course_id": 900104, "kind": "exam", "title": "Midterm exam",
                     "due": _at(today, 7, 19), "end": _at(today, 7, 21), "source": "d2l", "location": "PAC 1-8",
                     "weight": 30.0}, stamp)
    hub.upsert_item({"uid": "demo:900102:midterm", "course_id": 900102, "kind": "exam", "title": "Term test 2",
                     "due": _at(today, 11, 18, 30), "end": _at(today, 11, 20), "source": "d2l",
                     "location": "M3 1006", "weight": 20.0}, stamp)
    # lectures this week, so the planner has busy times to work around
    for ou, weekday_hours in ((900101, (8, 30)), (900103, (10, 0)), (900102, (13, 0))):
        for offset in range(0, 14):
            day = today + timedelta(days=offset)
            if day.weekday() in (0, 2, 4):
                start = datetime.combine(day, time(*weekday_hours), tzinfo=tz)
                hub.upsert_item({"uid": f"demo:class:{ou}:{day.isoformat()}", "course_id": ou, "kind": "class",
                                 "title": "Lecture", "due": to_iso(start), "end": to_iso(start + timedelta(minutes=80)),
                                 "source": "ical", "location": "MC 2065"}, stamp)

    news = [
        (900101, "Assignment 4 clarification", "<p>For question 3, use a <strong>two-sided</strong> test at "
         "&alpha; = 0.05. Office hours move to <em>Thursday</em> this week.</p>", 0),
        (900103, "A4 test harness released", "<p>The public tests for A4 are on the course page. "
         "Run <code>make test</code> before you submit.</p><ul><li>Observer: 6 tests</li>"
         "<li>Decorator: 4 tests</li></ul>", 1),
        (900102, "Term test 2 coverage", "<p>Term test 2 covers chapters 4 to 6: elasticity, consumer choice "
         "and costs. Bring a non-programmable calculator.</p>", 2),
        (900104, "Lab section swap form", "<p>If you need to switch lab sections for reading week, fill in the "
         "form by Friday.</p>", 4),
        (900105, "Field trip photos", "<p>Photos from the field trip are posted in the Content area.</p>", 6),
    ]
    with hub.tx() as conn:
        for index, (ou, title, body, days_ago) in enumerate(news):
            conn.execute("""INSERT OR REPLACE INTO announcements (uid, course_id, title, body_html, body_text, posted,
                              url, attachments, read, first_seen) VALUES (?, ?, ?, ?, ?, ?, ?, '[]', ?, ?)""",
                         (f"demo:news:{index}", ou, title, body, to_text(body), _at(today, -days_ago, 9, 15),
                          f"{BASE}/d2l/le/news/{ou}/{index}/view", int(days_ago > 3), stamp))
        for ou, code, _, _ in COURSES:
            for module, title in (("Week 9", "Lecture slides"), ("Week 9", "Practice problems"),
                                  ("Course information", "Course outline")):
                conn.execute("""INSERT OR REPLACE INTO files (uid, course_id, module, title, kind, url, first_seen,
                                  text_status) VALUES (?, ?, ?, ?, 'file', ?, ?, 'ok')""",
                             (f"demo:file:{ou}:{title}", ou, module, f"{code} {title}",
                              f"{BASE}/d2l/le/content/{ou}/home", stamp))
        grades = [
            (900101, "Assignment 1", 18, 20), (900101, "Assignment 2", 17, 20), (900101, "Assignment 3", 19, 20),
            (900101, "Weekly quiz 1-8", 36, 40),
            (900103, "A1: Classes and invariants", 41, 50), (900103, "A2: Inheritance", 38, 50),
            (900103, "A3: RAII and the rule of five", 44, 50),
            (900102, "Problem set 1-5", 44, 50), (900102, "Term test 1", 31, 40),
            (900104, "Lab reports 1-3", 26, 30),
        ]
        for index, (ou, name, num, den) in enumerate(grades):
            conn.execute("""INSERT OR REPLACE INTO grades (uid, course_id, name, points_num, points_den,
                              displayed, gtype, updated) VALUES (?, ?, ?, ?, ?, ?, 'item', ?)""",
                         (f"demo:grade:{index}", ou, name, num, den, f"{num / den * 100:.0f} %", stamp))
    schemes = {
        900101: ["Assignments (5) 30%", "Weekly quizzes 10%", "Midterm exam 25%", "Final exam 35%"],
        900103: ["Assignments (4) 40%", "Project 20%", "Final exam 40%"],
        900102: ["Problem sets 15%", "Term tests (2) 40%", "Term paper 10%", "Final exam 35%"],
        900104: ["Lab reports (5) 25%", "Lab quizzes 5%", "Midterm exam 30%", "Final exam 40%"],
        900105: ["Field trip reflection 15%", "Weekly quizzes 25%", "Final exam 60%"],
    }
    for ou, code, _, _ in COURSES:
        # STAT 230's midterm is only in its outline; BIOL 130's outline disagrees with LEARN on purpose,
        # so the demo shows a date-conflict alert.
        text = outline(code, today, schemes[ou], {900101: 9, 900104: 9}.get(ou))
        parsed = parse_syllabus(text, today - timedelta(days=60), today + timedelta(days=45), today)
        save_syllabus(hub, ou, code, "Demo course outline", text, parsed, stamp)

    changes = [
        ("new_announcement", 900101, "STAT 230: Assignment 4 clarification", "Use a two-sided test for Q3.", 2),
        ("due_changed", 900103, "CS 246: A4: Observer and Decorator moved", "Wed 5:00 PM → Fri 5:00 PM", 20),
        ("new_grade", 900103, "CS 246: A3: RAII and the rule of five graded", "88 %", 26),
        ("new_file", 900102, "ECON 101: Week 9 practice problems", "Week 9", 30),
        ("new_item", 900102, "ECON 101: Term paper proposal", "Due in 15 days", 50),
    ]
    with hub.tx() as conn:
        for kind, ou, title, detail, hours_ago in changes:
            conn.execute("INSERT INTO changes (at, course_id, kind, title, detail, notified) VALUES (?, ?, ?, ?, ?, 1)",
                         (to_iso(now - timedelta(hours=hours_ago)), ou, kind, title, detail))

    for days_ago, ou, uid, minutes in ((1, 900101, "demo:900101:0", 95), (1, 900103, "demo:900103:6", 60),
                                       (2, 900102, "demo:900102:3", 50), (3, 900103, "demo:900103:11", 120),
                                       (4, 900101, "demo:900101:12", 75), (5, 900104, None, 45),
                                       (6, 900103, "demo:900103:11", 150)):
        start = datetime.combine(today - timedelta(days=days_ago), time(19, 0), tzinfo=tz)
        log_time(hub, ou, uid, start, minutes, note="demo session")

    hub.notes_dir.mkdir(parents=True, exist_ok=True)
    for _, code, _, note_file in COURSES:
        folder = hub.notes_dir / code
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(NOTES_DIR / note_file, folder / note_file.split("-", 1)[1])
    from .notes import build_index
    build_index(hub)
    replan(hub, now)


DEMO_INSTRUCTIONS = {
    "Assignment 4: Hypothesis tests": "Question 1: State the null and alternative hypotheses for each scenario.\n"
                                      "Question 2: Compute the test statistic and p-value.\n"
                                      "Question 3: Decide at alpha = 0.05 and explain the Type I error risk.\n"
                                      "Question 4: Estimate the power of the test in part 3.\n"
                                      "Submit one PDF with your work shown.",
    "A4: Observer and Decorator": "1. Implement the Subject and Observer classes.\n"
                                  "2. Add two concrete observers that print the state.\n"
                                  "3. Implement a Decorator that adds borders to a text window.\n"
                                  "4. Write a test harness covering both patterns.\n"
                                  "Submit a4.zip with a Makefile.",
    "Lab report 4: Cellular respiration": "- Introduction with a hypothesis about yeast respiration rate\n"
                                          "- Methods for the CO2 measurement\n"
                                          "- Results table and one graph\n"
                                          "- Discussion linking results to glycolysis and the Krebs cycle",
}
