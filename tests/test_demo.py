import re
import unittest

from tests.helpers import ROOT

DEMO = (ROOT / "demo" / "index.html").read_text(encoding="utf-8")


class DemoPageTests(unittest.TestCase):
    def test_no_external_resources(self):
        self.assertIsNone(re.search(r"""(src|href)\s*=\s*["']?\s*(https?:)?//""", DEMO, re.I))
        self.assertIsNone(re.search(r"<script[^>]*\bsrc=", DEMO, re.I))
        self.assertIsNone(re.search(r"<link\b|@import|url\(", DEMO, re.I))

    def test_no_network_calls_or_keys(self):
        for pattern in (r"\bfetch\(", r"XMLHttpRequest", r"WebSocket", r"sendBeacon", r"api[_-]?key"):
            self.assertIsNone(re.search(pattern, DEMO, re.I), pattern)

    def test_never_renders_notes_as_html(self):
        self.assertNotIn("innerHTML", DEMO)
        self.assertNotIn("document.write", DEMO)

    def test_labels_itself_as_local_and_matches_python_threshold(self):
        from course_compass.answering import DEFAULT_THRESHOLD
        self.assertIn("no AI model", DEMO)
        self.assertIn(f"THRESHOLD = {DEFAULT_THRESHOLD}", DEMO)


if __name__ == "__main__":
    unittest.main()
