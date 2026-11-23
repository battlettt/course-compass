"""LEARN sync against a fake Brightspace API, the calendar-feed fallback, and the local web server's guards.

No test talks to a real LEARN site. The fake follows the documented Brightspace response shapes.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from course_compass.hub.d2l import D2LClient, D2LError, Session, SessionExpiredError, load_session, \
    parse_cookie_header, save_session
from course_compass.hub.db import Hub
from course_compass.hub.sync import Syncer, course_code, course_term, fetch_ical, sync_ical

BASE = "https://learn.example.edu"
NOW = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)
OU = 4242
LE = f"/d2l/api/le/1.100/{OU}"


class Resp:
    def __init__(self, body: bytes, url: str = ""):
        self._body = io.BytesIO(body)
        self._url = url

    def read(self, n=-1):
        return self._body.read(n)

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def learn_state():
    return {
        "/d2l/api/lp/1.64/users/whoami": {"FirstName": "Test", "LastName": "Student", "Identifier": "1"},
        "/d2l/api/lp/1.64/enrollments/myenrollments/": {
            "PagingInfo": {"HasMoreItems": False},
            "Items": [
                {"OrgUnit": {"Id": OU, "Name": "STAT 230 - Probability (Fall 2026)", "Code": "STAT230-1269",
                             "Type": {"Id": 3}},
                 "Access": {"CanAccess": True, "IsActive": True, "StartDate": "2026-09-08T04:00:00.000Z",
                            "EndDate": "2026-12-23T04:59:00.000Z"}},
                {"OrgUnit": {"Id": 99, "Name": "Old course", "Code": "OLD-1229", "Type": {"Id": 3}},
                 "Access": {"CanAccess": True, "IsActive": True, "EndDate": "2023-04-30T00:00:00.000Z"}}]},
        f"{LE}/dropbox/folders/": [
            {"Id": 11, "Name": "Assignment 3", "DueDate": "2026-11-06T04:59:00.000Z", "IsHidden": False,
             "CustomInstructions": {"Text": "1. Do question 1.\n2. Do question 2.", "Html": ""}},
            {"Id": 12, "Name": "Hidden one", "DueDate": "2026-11-20T04:59:00.000Z", "IsHidden": True}],
        f"{LE}/dropbox/folders/11/submissions/mysubmissions/": [],
        f"{LE}/quizzes/": {"Objects": [{"QuizId": 5, "Name": "Midterm exam", "DueDate": "2026-11-12T23:00:00.000Z",
                                        "IsActive": True}], "Next": None},
        f"{LE}/calendar/events/myEvents/": {"Objects": [
            {"CalendarEventId": 7, "Title": "Tutorial", "StartDateTime": "2026-11-04T18:30:00.000Z",
             "EndDateTime": "2026-11-04T19:20:00.000Z", "IsAllDayEvent": False, "LocationName": "MC 4020"},
            {"CalendarEventId": 8, "Title": "Assignment 3 due", "StartDateTime": "2026-11-06T04:59:00.000Z",
             "AssociatedEntity": {"AssociatedEntityType": "D2L.LE.Dropbox.Dropbox"}}], "Next": None},
        f"{LE}/news/": [{"Id": 31, "Title": "Room change", "StartDate": "2026-11-01T14:00:00.000Z",
                         "IsHidden": False, "IsPublished": True,
                         "Body": {"Html": "<p>Midterm is in <b>PAC</b><script>x()</script></p>", "Text": ""}}],
        f"{LE}/content/toc": {"Modules": [{"Title": "Course info", "Modules": [], "Topics": [
            {"TopicId": 61, "Title": "Course outline", "TypeIdentifier": "File", "Url": "/content/outline.txt",
             "LastModifiedDate": "2026-09-01T00:00:00.000Z"}]}]},
        f"{LE}/content/topics/61/file": b"Assignments: 30%\nMidterm exam: 30%\nFinal exam: 40%\n"
                                        b"Midterm exam: Nov 12, 6:00-7:50 PM\n",
        f"{LE}/grades/values/myGradeValues/": [
            {"GradeObjectIdentifier": "1", "GradeObjectName": "Assignment 1", "PointsNumerator": 18,
             "PointsDenominator": 20, "DisplayedGrade": "90 %", "GradeObjectTypeName": "Numeric"}],
        f"{LE}/grades/final/values/myGradeValue": 404,
        f"{LE}/overview": 404,
    }


class FakeLearn:
    def __init__(self, state=None):
        self.state = state or learn_state()
        self.requests: list[urllib.request.Request] = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        url = urlparse(request.full_url)
        value = self.state.get(url.path)
        if value is None or isinstance(value, int):
            code = value or 404
            raise urllib.error.HTTPError(request.full_url, code, "error", {}, io.BytesIO(b""))
        body = value if isinstance(value, bytes) else json.dumps(value).encode()
        return Resp(body, request.full_url)


def make_client(fake):
    return D2LClient(Session(base=BASE, cookies={"d2lSessionVal": "abc", "d2lSecureSessionVal": "def"}), fake,
                     sleep=lambda s: None)


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.hub = Hub(tempfile.mkdtemp())
        self.fake = FakeLearn()

    def run_sync(self, now=NOW):
        return Syncer(self.hub, make_client(self.fake), now=now).run()

    def test_first_sync_imports_everything_quietly(self):
        report = self.run_sync()
        self.assertEqual(report.errors, [])
        courses = self.hub.courses()
        self.assertEqual([(c["id"], c["code"]) for c in courses], [(OU, "STAT 230")])     # old course skipped
        uids = {i["uid"] for i in self.hub.items()}
        self.assertIn(f"d2l:dropbox:{OU}:11", uids)
        self.assertNotIn(f"d2l:dropbox:{OU}:12", uids)          # hidden folder
        self.assertIn(f"d2l:cal:{OU}:7", uids)
        self.assertNotIn(f"d2l:cal:{OU}:8", uids)               # duplicate of the dropbox folder
        exam = self.hub.item(f"d2l:quiz:{OU}:5")
        self.assertEqual(exam["kind"], "exam")
        kinds = [c["kind"] for c in self.hub.changes(limit=50)]
        self.assertEqual(kinds, ["course_added"])               # no flood of alerts on the first sync

    def test_announcement_html_is_sanitized(self):
        self.run_sync()
        row = self.hub.one("SELECT body_html FROM announcements")
        self.assertIn("<b>PAC</b>", row["body_html"])
        self.assertNotIn("script", row["body_html"])

    def test_cookies_go_only_to_learn(self):
        self.run_sync()
        for request in self.fake.requests:
            self.assertEqual(urlparse(request.full_url).netloc, "learn.example.edu")
            self.assertIn("d2lSessionVal=abc", request.get_header("Cookie"))
        with self.assertRaises(D2LError):
            make_client(self.fake).get_json("https://evil.example.com/steal")

    def test_file_download_and_syllabus(self):
        self.run_sync()
        files = self.hub.query("SELECT * FROM files")
        self.assertEqual(len(files), 1)
        self.assertTrue(os.path.exists(files[0]["local_path"]))
        self.assertTrue(files[0]["local_path"].startswith(str(self.hub.files_dir)))
        syllabus = self.hub.one("SELECT * FROM syllabus WHERE course_id=?", (OU,))
        parsed = json.loads(syllabus["parsed"])
        self.assertEqual(sum(c["weight"] for c in parsed["components"]), 100)

    def test_second_sync_reports_changes(self):
        self.run_sync()
        self.fake.state[f"{LE}/dropbox/folders/"][0]["DueDate"] = "2026-11-08T04:59:00.000Z"
        self.fake.state[f"{LE}/news/"].append({"Id": 32, "Title": "Solutions posted",
                                               "StartDate": "2026-11-02T14:00:00.000Z", "Body": {"Text": "x"}})
        self.fake.state[f"{LE}/grades/values/myGradeValues/"].append(
            {"GradeObjectIdentifier": "2", "GradeObjectName": "Assignment 2", "PointsNumerator": 15,
             "PointsDenominator": 20, "DisplayedGrade": "75 %"})
        self.fake.state[f"{LE}/calendar/events/myEvents/"]["Objects"] = []
        self.fake.state[f"{LE}/dropbox/folders/11/submissions/mysubmissions/"] = [{"Submissions": [{"Id": 1}]}]
        self.run_sync()
        kinds = {c["kind"] for c in self.hub.changes(limit=50)}
        self.assertTrue({"due_changed", "new_announcement", "new_grade", "removed_item"} <= kinds)
        self.assertEqual(self.hub.item(f"d2l:dropbox:{OU}:11")["status"], "submitted")

    def test_failed_endpoint_does_not_delete_items(self):
        self.run_sync()
        self.fake.state[f"{LE}/quizzes/"] = 500
        report = self.run_sync()
        self.assertIsNotNone(self.hub.item(f"d2l:quiz:{OU}:5"))
        self.assertTrue(report.errors)

    def test_expired_session(self):
        self.fake.state["/d2l/api/lp/1.64/users/whoami"] = 302
        report = self.run_sync()
        self.assertTrue(report.login_needed)
        self.assertEqual([c["kind"] for c in self.hub.changes()], ["login_needed"])
        self.run_sync()                                         # throttled: no second alert
        self.assertEqual(len(self.hub.changes()), 1)

    def test_forbidden_course_tool_is_not_a_dead_session(self):
        self.fake.state[f"{LE}/grades/values/myGradeValues/"] = 403
        report = self.run_sync()
        self.assertFalse(report.login_needed)
        self.assertEqual(report.errors, [])
        self.assertIsNotNone(self.hub.item(f"d2l:dropbox:{OU}:11"))

    def test_videos_are_linked_not_downloaded(self):
        toc = self.fake.state[f"{LE}/content/toc"]["Modules"][0]["Topics"]
        toc.append({"TopicId": 62, "Title": "Lecture recording", "TypeIdentifier": "File",
                    "Url": "/content/lecture1.mp4"})
        self.run_sync()
        self.assertFalse(any("topics/62/file" in r.full_url for r in self.fake.requests))
        self.assertEqual(self.hub.one("SELECT local_path FROM files WHERE uid=?", (f"d2l:topic:{OU}:62",))["local_path"], "")

    def test_calendar_dates_have_milliseconds(self):
        self.run_sync()
        query = next(r.full_url for r in self.fake.requests if "calendar/events" in r.full_url)
        self.assertIn("startDateTime=2026-09-03T15%3A00%3A00.000Z", query)

    def test_login_page_instead_of_json(self):
        self.fake.state["/d2l/api/lp/1.64/users/whoami"] = b"<!doctype html><title>Sign in</title>"
        with self.assertRaises(SessionExpiredError):
            make_client(self.fake).whoami()

    def test_course_code_and_term(self):
        self.assertEqual(course_code("STAT 230 - Probability (Fall 2026)", "STAT230-1269"), "STAT 230")
        self.assertEqual(course_code("PD 22 Online - Fall 2026", "PD22_081_cel_1269"), "PD 22")
        self.assertEqual(course_code("Engineering Co-op Community", "Engineering Co-op Community"),
                         "Engineering Co-op Community")
        self.assertEqual(course_term("Something", "CS246-1271"), "Winter 2027")
        self.assertEqual(course_term("Something", "STAT230_1269"), "Fall 2026")
        self.assertEqual(course_term("Something", "ECON101"), "")


class SessionTests(unittest.TestCase):
    def test_session_file_is_private_and_repr_hides_cookies(self):
        path = Path(tempfile.mkdtemp()) / "session.json"
        session = Session(base=BASE, cookies=parse_cookie_header("Cookie: d2lSessionVal=s3cret; other=1"))
        save_session(path, session)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertNotIn("s3cret", repr(session))
        self.assertEqual(load_session(path).cookies["d2lSessionVal"], "s3cret")


ICAL = "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:x1\r\nSUMMARY:STAT 230 - Assignment 3\r\n" \
       "DTSTART:20261106T045900Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"


class ICalFallbackTests(unittest.TestCase):
    def test_feed_events_are_imported(self):
        hub = Hub(tempfile.mkdtemp())
        self.assertEqual(sync_ical(hub, ICAL, now=NOW), 1)
        self.assertEqual(hub.items()[0]["source"], "ical")

    def test_feed_url_must_be_the_learn_host(self):
        with self.assertRaises(D2LError):
            fetch_ical("https://evil.example.com/feed.ics", BASE, opener=lambda r, timeout=None: Resp(b""))
        seen = []
        text = fetch_ical("webcal://learn.example.edu/d2l/le/calendar/feed/user/feed.ics?token=t", BASE,
                          opener=lambda r, timeout=None: seen.append(r.full_url) or Resp(ICAL.encode(), r.full_url))
        self.assertTrue(seen[0].startswith("https://"))
        self.assertIn("VEVENT", text)


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from course_compass.hub.demo import seed
        from course_compass.hub.server import App, make_server

        cls.hub = Hub(tempfile.mkdtemp())
        seed(cls.hub)
        cls.app = App(cls.hub, port=0)
        cls.server = make_server(cls.app)
        cls.base = f"http://127.0.0.1:{cls.app.port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def get(self, path, headers=None, method="GET", body=None):
        request = urllib.request.Request(self.base + path, method=method, headers=headers or {},
                                         data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def auth(self):
        return {"X-CC-Token": self.app.token, "Content-Type": "application/json"}

    def test_page_carries_token_and_strict_csp(self):
        status, headers, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(self.app.token.encode(), body)
        csp = headers["Content-Security-Policy"]
        self.assertIn("script-src 'self'", csp)
        self.assertNotIn("unsafe-inline' ; script", csp)
        self.assertEqual(headers.get("X-Frame-Options", "").upper(), "DENY")

    def test_api_needs_token(self):
        self.assertEqual(self.get("/api/state")[0], 403)
        self.assertEqual(self.get("/api/state", {"X-CC-Token": "wrong"})[0], 403)
        status, _, body = self.get("/api/state", self.auth())
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["demo"])

    def test_foreign_host_rejected(self):
        self.assertEqual(self.get("/", {"Host": "evil.example.com"})[0], 421)

    def test_feed_needs_its_own_secret(self):
        feeds = json.loads(self.get("/api/feed", self.auth())[2])["feeds"]
        path = urlparse(feeds["deadlines"]).path
        status, headers, body = self.get(path)
        self.assertEqual(status, 200)
        self.assertIn("text/calendar", headers["Content-Type"])
        self.assertIn(b"BEGIN:VEVENT", body)
        self.assertEqual(self.get(path.replace(path.split("/")[2], "x" * 32))[0], 404)

    def test_rotating_feed_secret_kills_old_link(self):
        old = urlparse(json.loads(self.get("/api/feed", self.auth())[2])["feeds"]["all"]).path
        self.get("/api/feed/rotate", self.auth(), "POST", {})
        self.assertEqual(self.get(old)[0], 404)

    def test_item_update_and_bad_input(self):
        items = json.loads(self.get("/api/items", self.auth())[2])["items"]
        uid = next(i["uid"] for i in items if i["kind"] == "assignment")
        quoted = urllib.request.quote(uid, safe="")
        self.assertEqual(self.get(f"/api/items/{quoted}", self.auth(), "PATCH", {"status": "done"})[0], 200)
        self.assertEqual(self.hub.item(uid)["status"], "done")
        self.assertEqual(self.get(f"/api/items/{quoted}", self.auth(), "PATCH", {"status": "hacked"})[0], 400)
        self.assertEqual(self.get("/api/settings", self.auth(), "PUT", {"daily_cap_hours": "lots"})[0], 400)

    def test_downloaded_html_is_not_rendered(self):
        target = self.hub.files_dir / "x.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("<script>alert(1)</script>")
        with self.hub.tx() as conn:
            conn.execute("INSERT INTO files (uid, course_id, module, title, kind, url, local_path, modified, size, "
                         "first_seen) VALUES ('t:1', 900101, 'm', 't', 'file', '', ?, '', 1, '')", (str(target),))
        status, headers, _ = self.get(f"/files/t%3A1?t={self.app.token}")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/plain"))
        self.assertEqual(self.get("/files/t%3A1?t=wrong")[0], 403)


if __name__ == "__main__":
    unittest.main()
