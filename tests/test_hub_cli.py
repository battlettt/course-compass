"""The hub commands of the `course-compass` CLI, run against demo data."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from course_compass.cli import main


def run(*argv) -> tuple[int, str]:
    out = io.StringIO()
    with redirect_stdout(out):
        code = main(list(argv))
    return code, out.getvalue()


class HubCLITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp()
        code, out = run("demo", "--home", cls.home, "--no-serve")
        assert code == 0, out

    def test_today_lists_open_work(self):
        code, out = run("today", "--home", self.home, "--days", "14")
        self.assertEqual(code, 0)
        self.assertIn("STAT 230", out)

    def test_grades_shows_what_you_need(self):
        code, out = run("grades", "--home", self.home, "--target", "85")
        self.assertEqual(code, 0)
        self.assertIn("for 85%", out)

    def test_calendar_files(self):
        folder = tempfile.mkdtemp()
        code, _ = run("calendar", "--home", self.home, "--out-dir", folder, "--feed", "exams")
        self.assertEqual(code, 0)
        text = (Path(folder) / "course-compass-exams.ics").read_text()
        self.assertIn("BEGIN:VCALENDAR", text)

    def test_digest(self):
        code, out = run("digest", "--home", self.home)
        self.assertEqual(code, 0)
        self.assertIn("## Due in the next 3 days", out)

    def test_timer(self):
        self.assertEqual(run("timer", "start", "--home", self.home, "--course", "stat230")[0], 0)
        code, out = run("timer", "stop", "--home", self.home)
        self.assertIn("Logged", out)

    def test_schedule_dry_run_installs_nothing(self):
        code, out = run("schedule", "--home", self.home, "--dry-run", "--times", "7:30,18:00")
        self.assertEqual(code, 0)
        self.assertIn("StartCalendarInterval", out)
        self.assertIn("<integer>18</integer>", out)


if __name__ == "__main__":
    unittest.main()
