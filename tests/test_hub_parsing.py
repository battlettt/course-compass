"""Hub parsers: syllabus, calendar files, HTML sanitizer, file extraction, checklists."""

from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from course_compass.hub.checklist import checklist_from_text
from course_compass.hub.extract import ExtractError, extract_file
from course_compass.hub.htmltext import safe_url, sanitize, to_text
from course_compass.hub.ics import build_feed, parse_calendar
from course_compass.hub.syllabus import conflicts, find_dates, parse_syllabus

TZ = ZoneInfo("America/Toronto")

OUTLINE = """STAT 230 Course Outline
Instructor: Dr. Ada Lovelace, ada.lovelace@example.edu
TA: Sam Chen, sam.chen@example.edu
Office hours: Tuesdays 2:00-3:30 PM in MC 6012

## Assessment
Assignments (5): 30%
Weekly quizzes: 10%
Midterm Exam: 25%
Final Exam: 35%

## Key dates
Midterm Exam: Wednesday, October 21, 7:00-8:50 PM
Assignment 3 due Nov 6

## Late policy
Late work loses 10% per day, up to 3 days.
"""


class SyllabusTests(unittest.TestCase):
    def setUp(self):
        self.parsed = parse_syllabus(OUTLINE, date(2026, 9, 8), date(2026, 12, 22), date(2026, 9, 10))

    def test_grading_scheme_with_quotes(self):
        names = {c.name: c.weight for c in self.parsed.components}
        self.assertEqual(names["Assignments (5)"], 30)
        self.assertEqual(names["Final Exam"], 35)
        self.assertEqual(self.parsed.total_weight, 100)
        for component in self.parsed.components:
            self.assertIn(str(int(component.weight)), component.quote)

    def test_exam_date_and_time(self):
        exam = [d for d in self.parsed.dates if d.kind == "exam"][0]
        self.assertEqual(exam.date, "2026-10-21")
        self.assertEqual((exam.start_time, exam.end_time), ("19:00", "20:50"))
        self.assertEqual(exam.label, "Midterm Exam")

    def test_contacts_office_hours_late_policy(self):
        emails = {c["email"] for c in self.parsed.contacts}
        self.assertEqual(emails, {"ada.lovelace@example.edu", "sam.chen@example.edu"})
        self.assertTrue(self.parsed.office_hours)
        self.assertTrue(any("10% per day" in x["quote"] for x in self.parsed.late_policy))
        self.assertFalse(any(x["quote"].startswith("#") for x in self.parsed.late_policy))

    def test_year_inferred_from_winter_term(self):
        found = find_dates("Final exam Jan 4", date(2026, 9, 8), date(2027, 1, 10), date(2026, 9, 10))
        self.assertEqual(found[0][0], date(2027, 1, 4))

    def test_conflict_with_learn_date(self):
        items = [{"kind": "exam", "title": "Midterm exam", "due": "2026-10-23T23:00:00Z", "uid": "x", "source": "d2l"},
                 {"kind": "exam", "title": "Midterm exam", "due": "2026-10-30T23:00:00Z", "uid": "y",
                  "source": "manual"}]
        found = conflicts(self.parsed, items, TZ)
        self.assertEqual([(f["item_uid"], f["syllabus_date"], f["learn_date"]) for f in found],
                         [("x", "2026-10-21", "2026-10-23")])
        same_day = [{**items[0], "due": "2026-10-21T23:00:00Z"}]
        self.assertEqual(conflicts(self.parsed, same_day, TZ), [])

    def test_round_trip(self):
        from course_compass.hub.syllabus import Syllabus
        again = Syllabus.from_dict(self.parsed.to_dict())
        self.assertEqual(again.total_weight, self.parsed.total_weight)


ICS = """BEGIN:VCALENDAR\r
VERSION:2.0\r
BEGIN:VEVENT\r
UID:lec-1\r
SUMMARY:STAT 230 Lecture\r
DTSTART;TZID=America/Toronto:20260909T083000\r
DTEND;TZID=America/Toronto:20260909T092000\r
RRULE:FREQ=WEEKLY;BYDAY=MO,WE;COUNT=6\r
EXDATE;TZID=America/Toronto:20260914T083000\r
LOCATION:MC 2065\r
BEGIN:VALARM\r
TRIGGER:-PT15M\r
END:VALARM\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:holiday\r
SUMMARY:Reading week\r
DTSTART;VALUE=DATE:20261012\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:gone\r
SUMMARY:Cancelled\r
STATUS:CANCELLED\r
DTSTART:20261001T120000Z\r
END:VEVENT\r
END:VCALENDAR\r
"""


class CalendarTests(unittest.TestCase):
    def test_weekly_rule_with_exdate(self):
        events = parse_calendar(ICS)
        lectures = [e for e in events if e.summary == "STAT 230 Lecture"]
        self.assertEqual(len(lectures), 5)             # COUNT=6 minus one EXDATE
        self.assertEqual(lectures[0].start, datetime(2026, 9, 9, 12, 30, tzinfo=timezone.utc))
        self.assertEqual(lectures[0].location, "MC 2065")

    def test_all_day_and_cancelled(self):
        events = parse_calendar(ICS)
        self.assertTrue([e for e in events if e.summary == "Reading week"][0].all_day)
        self.assertFalse([e for e in events if e.summary == "Cancelled"])

    def test_feed_round_trip_with_alarms(self):
        due = datetime(2026, 11, 24, 23, 59, tzinfo=TZ).astimezone(timezone.utc)
        item = {"uid": "d2l:dropbox:1:2", "title": "Assignment 4; part A, B", "kind": "assignment",
                "due": due.strftime("%Y-%m-%dT%H:%M:%SZ"), "course_code": "STAT 230", "all_day": 0,
                "status": "open", "url": "https://learn.example.edu/x", "details": "", "location": "",
                "course_color": "#2f5bd3", "end": None}
        text = build_feed([item], "Course Compass", now=due - timedelta(days=3), alarms=[2880, 180])
        self.assertIn("X-WR-CALNAME:Course Compass", text)
        self.assertEqual(text.count("BEGIN:VALARM"), 2)
        self.assertTrue(all(len(line.encode()) <= 75 for line in text.split("\r\n")))
        back = parse_calendar(text)
        self.assertEqual(len(back), 1)
        self.assertIn("Assignment 4; part A, B", back[0].summary)
        self.assertEqual(back[0].end, due)                # the event ends at the due time


class SanitizerTests(unittest.TestCase):
    def test_strips_script_and_handlers(self):
        out = sanitize('<p onclick="x()">Hi <script>alert(1)</script><b>there</b>'
                       '<a href="javascript:alert(1)">bad</a><a href="/d2l/x">ok</a>'
                       '<iframe src="https://evil"></iframe><img src=x onerror=alert(1)></p>',
                       "https://learn.example.edu")
        self.assertNotIn("script", out)
        self.assertNotIn("onclick", out)
        self.assertNotIn("onerror", out)
        self.assertNotIn("javascript:", out)
        self.assertNotIn("iframe", out)
        self.assertIn("<b>there</b>", out)
        self.assertIn('href="https://learn.example.edu/d2l/x"', out)

    def test_safe_url(self):
        self.assertIsNone(safe_url("javascript:alert(1)"))
        self.assertIsNone(safe_url("data:text/html,x"))
        self.assertEqual(safe_url("mailto:a@b.c"), "mailto:a@b.c")

    def test_text(self):
        self.assertEqual(to_text("<p>One</p><p>Two &amp; three</p>").split(), ["One", "Two", "&", "three"])


def _zip(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return buffer.getvalue()


SLIDE = ('<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
         'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree>'
         '<p:sp><p:txBody><a:p><a:r><a:t>{title}</a:t></a:r></a:p><a:p><a:r><a:t>{body}</a:t></a:r></a:p>'
         '</p:txBody></p:sp></p:spTree></p:cSld></p:sld>')
DOC = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
       '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Elasticity</w:t></w:r></w:p>'
       '<w:p><w:r><w:t>Price elasticity measures responsiveness.</w:t></w:r></w:p></w:body></w:document>')


class ExtractTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def test_pptx_slides_in_order(self):
        path = self.dir / "Week 3.pptx"
        path.write_bytes(_zip({"ppt/slides/slide2.xml": SLIDE.format(title="Second", body="later"),
                               "ppt/slides/slide10.xml": SLIDE.format(title="Tenth", body="last"),
                               "ppt/slides/slide1.xml": SLIDE.format(title="Power", body="P(reject H0 | H1)")}))
        result = extract_file(path)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.pages, 3)
        md = result.markdown
        self.assertLess(md.index("Power"), md.index("Second"))      # slide1, slide2, slide10: numeric order
        self.assertLess(md.index("Second"), md.index("Tenth"))
        self.assertIn("P(reject H0 | H1)", md)

    def test_docx_headings(self):
        path = self.dir / "notes.docx"
        path.write_bytes(_zip({"word/document.xml": DOC}))
        md = extract_file(path).markdown
        self.assertIn("## Elasticity", md)
        self.assertIn("responsiveness", md)

    def test_docx_with_doctype_is_refused(self):
        path = self.dir / "evil.docx"
        path.write_bytes(_zip({"word/document.xml": '<!DOCTYPE x [<!ENTITY a "aaaa">]>' + DOC}))
        with self.assertRaises(ExtractError):
            extract_file(path)

    def test_unsupported(self):
        path = self.dir / "a.exe"
        path.write_bytes(b"MZ")
        with self.assertRaises(ExtractError):
            extract_file(path)


class ChecklistTests(unittest.TestCase):
    def test_numbered_instructions(self):
        steps = checklist_from_text("1. Read section 4.2.\n2. Answer questions 1-5.\n3. Upload one PDF to the dropbox.")
        self.assertEqual(steps[0], "1. Read section 4.2.")
        self.assertIn("2. Answer questions 1-5.", steps)
        self.assertEqual(len(steps), 4)
        self.assertTrue(steps[-1].lower().startswith("proofread"))

    def test_single_sentence_is_not_a_checklist(self):
        self.assertEqual(checklist_from_text("Submit a PDF."), [])


if __name__ == "__main__":
    unittest.main()
