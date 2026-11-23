"""Sync LEARN (D2L) into the hub: courses, work, announcements, content files, grades, syllabus.

Each course is synced on its own, so one failing endpoint (a course with
quizzes turned off answers 403/404, for example) costs only that piece. Items
are only treated as removed when the endpoint that lists them answered.
The first sync of a course records one summary change instead of dozens of
"new item" changes.
"""

from __future__ import annotations

import re
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse

from . import ics as ics_mod
from .d2l import D2LClient, D2LError, NotFoundError, SessionExpiredError
from .db import Hub
from .htmltext import sanitize, to_text
from .syllabus import conflicts, parse_syllabus
from .timeutil import local_tz, parse_iso, to_iso, utcnow

SYLLABUS_RE = re.compile(r"\b(syllabus|course outline|outline|course information|course info)\b", re.I)
EXAM_TITLE_RE = re.compile(r"\b(midterm|mid-term|final exam|exam|test \d|term test)\b", re.I)
PUBLIC_OUTLINE_HOSTS = {"outline.uwaterloo.ca"}
SUBMISSION_LOOKBACK_DAYS = 21
MAX_DOWNLOAD_BYTES = 60_000_000


@dataclass
class SyncReport:
    courses: int = 0
    new_items: int = 0
    changed_items: int = 0
    removed_items: int = 0
    announcements: int = 0
    files_downloaded: int = 0
    grades: int = 0
    errors: list[str] = field(default_factory=list)
    login_needed: bool = False

    def summary(self) -> str:
        parts = [f"{self.courses} course(s)", f"{self.new_items} new item(s)", f"{self.changed_items} changed",
                 f"{self.removed_items} removed", f"{self.announcements} new announcement(s)",
                 f"{self.files_downloaded} file(s) downloaded", f"{self.grades} new grade(s)"]
        text = ", ".join(parts)
        if self.login_needed:
            text += ". LEARN sign-in needed"
        if self.errors:
            text += f". {len(self.errors)} problem(s): " + "; ".join(self.errors[:5])
        return text


# ---------------------------------------------------------------- mapping D2L JSON

def course_code(name: str, code: str) -> str:
    for text in (name, code):
        match = re.search(r"\b([A-Z]{2,6})\s?-?\s?(\d{3}[A-Z]?)\b", text or "")
        if match:
            return f"{match.group(1)} {match.group(2)}"
    return (code or name or "COURSE")[:16]


def course_term(name: str, code: str) -> str:
    match = re.search(r"\b(Fall|Winter|Spring|Summer)\s+(20\d\d)\b", f"{name} {code}", re.I)
    if match:
        return f"{match.group(1).title()} {match.group(2)}"
    match = re.search(r"(?:^|[_\-\s.])(1\d)(\d)([159])\b", code or "")     # Waterloo term codes like 1269 = Fall 2026
    if match:
        year = 1900 + int(match.group(1)) * 10 + int(match.group(2))
        season = {"1": "Winter", "5": "Spring", "9": "Fall"}.get(match.group(3))
        if season:
            return f"{season} {year}"
    return ""


def current_enrollments(enrollments: list[dict], now: datetime) -> list[dict]:
    out = []
    for enrollment in enrollments:
        org = enrollment.get("OrgUnit") or {}
        access = enrollment.get("Access") or {}
        if (org.get("Type") or {}).get("Id") not in (3, None):
            continue
        if access.get("CanAccess") is False or access.get("IsActive") is False:
            continue
        start, end = parse_iso(access.get("StartDate")), parse_iso(access.get("EndDate"))
        if end and end < now - timedelta(days=14):
            continue
        if start and start > now + timedelta(days=30):
            continue
        out.append({"id": int(org["Id"]), "name": org.get("Name", ""), "code_raw": org.get("Code", ""),
                    "start": to_iso(start), "end": to_iso(end)})
    return out


def dropbox_item(folder: dict, ou: int, base: str) -> dict | None:
    if folder.get("IsHidden"):
        return None
    due = folder.get("DueDate") or ((folder.get("Availability") or {}).get("EndDate"))
    instructions = folder.get("CustomInstructions") or {}
    details = instructions.get("Text") or to_text(instructions.get("Html") or "")
    return {"uid": f"d2l:dropbox:{ou}:{folder['Id']}", "course_id": ou, "kind": "assignment",
            "title": folder.get("Name") or "Assignment", "due": to_iso(parse_iso(due)), "source": "d2l",
            "url": f"{base}/d2l/lms/dropbox/user/folder_submit_files.d2l?db={folder['Id']}&ou={ou}",
            "details": details[:6000]}


def quiz_item(quiz: dict, ou: int, base: str) -> dict | None:
    if quiz.get("IsActive") is False:
        return None
    due = quiz.get("DueDate") or quiz.get("EndDate")
    description = (quiz.get("Description") or {}).get("Text") or {}
    if isinstance(description, dict):
        description = description.get("Text") or ""
    title = quiz.get("Name") or "Quiz"
    return {"uid": f"d2l:quiz:{ou}:{quiz['QuizId']}", "course_id": ou,
            "kind": "exam" if EXAM_TITLE_RE.search(title) else "quiz", "title": title,
            "due": to_iso(parse_iso(due)), "source": "d2l",
            "url": f"{base}/d2l/lms/quizzing/user/quiz_summary.d2l?qi={quiz['QuizId']}&ou={ou}",
            "details": str(description)[:4000]}


def calendar_item(event: dict, ou: int, base: str) -> dict | None:
    entity = event.get("AssociatedEntity") or {}
    entity_type = (entity.get("AssociatedEntityType") or "").lower()
    if "dropbox" in entity_type or "quiz" in entity_type:
        return None       # already listed from the dropbox / quiz endpoints
    title = event.get("Title") or "Event"
    all_day = bool(event.get("IsAllDayEvent"))
    if all_day and event.get("StartDay"):
        start = to_iso(parse_iso(event["StartDay"][:10]).replace(hour=12))
    else:
        start = to_iso(parse_iso(event.get("StartDateTime")))
    if not start:
        return None
    return {"uid": f"d2l:cal:{ou}:{event.get('CalendarEventId')}", "course_id": ou,
            "kind": "exam" if EXAM_TITLE_RE.search(title) else "event", "title": title, "due": start,
            "end": None if all_day else to_iso(parse_iso(event.get("EndDateTime"))), "all_day": all_day,
            "source": "d2l", "location": event.get("LocationName") or "",
            "details": to_text(event.get("Description") or "")[:4000],
            "url": f"{base}/d2l/le/calendar/{ou}/event/{event.get('CalendarEventId')}/detailsview"}


def walk_toc(toc: dict) -> list[dict]:
    """Flatten the content table of contents into topics with their module path."""
    out = []

    def visit(module: dict, path: list[str]) -> None:
        here = path + [module.get("Title") or "Module"]
        for topic in module.get("Topics") or []:
            if topic.get("IsHidden") or topic.get("IsBroken"):
                continue
            out.append({**topic, "_module": " / ".join(here)})
        for child in module.get("Modules") or []:
            visit(child, here)

    for module in toc.get("Modules") or []:
        visit(module, [])
    return out


def safe_name(text: str, limit: int = 80) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", text).strip(" ._") or "file"
    return cleaned[:limit]


def topic_filename(topic: dict) -> str:
    raw = unquote(urlparse(topic.get("Url") or "").path.rsplit("/", 1)[-1])
    title = topic.get("Title") or "file"
    if raw and "." in raw:
        name = raw
    else:
        name = title
    return safe_name(name)


# ---------------------------------------------------------------- the sync

class Syncer:
    def __init__(self, hub: Hub, client: D2LClient, now: datetime | None = None, download: bool = True,
                 log: Callable[[str], None] = lambda message: None, public_opener: Callable | None = None):
        self.hub = hub
        self.client = client
        self.base = client.base
        self.now = now or utcnow()
        self.stamp = to_iso(self.now)
        self.download = download
        self.log = log
        self.report = SyncReport()
        self.public_open = public_opener or urllib.request.urlopen

    def run(self) -> SyncReport:
        run_id = self.hub.start_run("d2l", self.stamp)
        try:
            self.client.whoami()
            courses = current_enrollments(self.client.enrollments(), self.now)
            for course in courses:
                self.sync_course(course)
        except SessionExpiredError as exc:
            self.report.login_needed = True
            self.report.errors.append(str(exc))
            last = self.hub.one("SELECT at FROM changes WHERE kind='login_needed' ORDER BY id DESC LIMIT 1")
            if not last or parse_iso(last["at"]) < self.now - timedelta(hours=12):
                self.hub.add_change(self.stamp, "login_needed", "Sign in to LEARN again",
                                    detail="Run `course-compass login`, then sync.")
        except D2LError as exc:
            self.report.errors.append(str(exc))
        self.hub.finish_run(run_id, not self.report.errors, self.report.summary(), to_iso(utcnow()))
        return self.report

    def sync_course(self, enrollment: dict) -> None:
        ou = enrollment["id"]
        existing = self.hub.course(ou)
        first = existing is None or not existing.get("synced")
        code = course_code(enrollment["name"], enrollment["code_raw"])
        self.hub.upsert_course({"id": ou, "code": code, "name": enrollment["name"],
                                "term": course_term(enrollment["name"], enrollment["code_raw"]),
                                "start": enrollment["start"], "end": enrollment["end"],
                                "url": f"{self.base}/d2l/home/{ou}"}, self.stamp)
        if existing and existing.get("hidden"):
            return
        self.report.courses += 1
        self.log(f"Syncing {code}...")
        before = self.report.new_items + self.report.announcements
        seen: dict[str, set[str]] = {}

        def guarded(label: str, fn):
            try:
                return fn()
            except SessionExpiredError:
                raise
            except NotFoundError:
                return None
            except D2LError as exc:
                self.report.errors.append(f"{code} {label}: {exc}")
                return None

        folders = guarded("assignments", lambda: self.client.dropbox_folders(ou))
        if folders is not None:
            seen["dropbox"] = set()
            for folder in folders:
                item = dropbox_item(folder, ou, self.base)
                if item:
                    item["status"] = guarded("submissions", lambda f=folder, i=item: self._submitted(ou, f, i))
                    self._save_item(item, code, quiet=first)
                    seen["dropbox"].add(item["uid"])
        quizzes = guarded("quizzes", lambda: self.client.quizzes(ou))
        if quizzes is not None:
            seen["quiz"] = set()
            for quiz in quizzes:
                item = quiz_item(quiz, ou, self.base)
                if item:
                    self._save_item(item, code, quiet=first)
                    seen["quiz"].add(item["uid"])
        window_start = to_iso(self.now - timedelta(days=60))
        window_end = to_iso(self.now + timedelta(days=200))
        events = guarded("calendar", lambda: self.client.calendar_events(ou, window_start, window_end))
        if events is not None:
            seen["cal"] = set()
            for event in events:
                item = calendar_item(event, ou, self.base)
                if item:
                    self._save_item(item, code, quiet=first)
                    seen["cal"].add(item["uid"])
        for kind, uids in seen.items():
            self._remove_missing(ou, f"d2l:{kind}:{ou}:", uids, code, quiet=first)

        news = guarded("announcements", lambda: self.client.news(ou))
        for entry in news or []:
            self._save_announcement(entry, ou, code, quiet=first)

        toc = guarded("content", lambda: self.client.toc(ou))
        topics = walk_toc(toc) if toc else []
        for topic in topics:
            self._save_topic(topic, ou, code, quiet=first)

        grades = guarded("grades", lambda: self.client.grade_values(ou))
        for value in grades or []:
            self._save_grade(value, ou, code, quiet=first)
        final = guarded("final grade", lambda: self.client.final_grade(ou))
        if final:
            self._save_grade({**final, "GradeObjectIdentifier": "final", "GradeObjectName": "Final grade",
                              "GradeObjectTypeName": "Final"}, ou, code, quiet=first, is_final=True)

        overview = guarded("overview", lambda: self.client.overview(ou))
        self._syllabus(ou, code, topics, overview, enrollment)

        if first:
            added = self.report.new_items + self.report.announcements - before
            self.hub.add_change(self.stamp, "course_added", f"Added {code}", course_id=ou,
                                detail=f"{added} items and announcements imported from LEARN.")

    # pieces --------------------------------------------------------------------
    def _submitted(self, ou: int, folder: dict, item: dict) -> str | None:
        due = parse_iso(item.get("due"))
        if due and due < self.now - timedelta(days=SUBMISSION_LOOKBACK_DAYS):
            return None
        entries = self.client.my_submissions(ou, folder["Id"])
        for entry in entries or []:
            if entry.get("Submissions"):
                return "submitted"
        return None

    def _save_item(self, item: dict, code: str, quiet: bool) -> None:
        result, old = self.hub.upsert_item(item, self.stamp)
        if result == "new":
            self.report.new_items += 1
            if not quiet:
                when = self._fmt(item.get("due"))
                self.hub.add_change(self.stamp, "new_item", f"{code}: {item['title']}", course_id=item["course_id"],
                                    detail=f"Due {when}" if when else "No due date", ref=item["uid"])
        elif result == "changed":
            self.report.changed_items += 1
            if old and old.get("due") != item.get("due") and not quiet:
                self.hub.add_change(self.stamp, "due_changed", f"{code}: {item['title']} moved",
                                    course_id=item["course_id"],
                                    detail=f"{self._fmt(old.get('due')) or 'no date'} → "
                                           f"{self._fmt(item.get('due')) or 'no date'}", ref=item["uid"])

    def _remove_missing(self, ou: int, prefix: str, seen: set[str], code: str, quiet: bool) -> None:
        rows = self.hub.query("SELECT uid, title FROM items WHERE course_id=? AND uid LIKE ?", (ou, prefix + "%"))
        for row in rows:
            if row["uid"] not in seen:
                self.hub.delete_item(row["uid"])
                self.report.removed_items += 1
                if not quiet:
                    self.hub.add_change(self.stamp, "removed_item", f"{code}: {row['title']} removed from LEARN",
                                        course_id=ou, ref=row["uid"])

    def _fmt(self, iso: str | None) -> str:
        if not iso:
            return ""
        return parse_iso(iso).astimezone(local_tz()).strftime("%a %b %-d, %-I:%M %p")

    def _save_announcement(self, entry: dict, ou: int, code: str, quiet: bool) -> None:
        if entry.get("IsHidden") or entry.get("IsPublished") is False:
            return
        uid = f"d2l:news:{ou}:{entry['Id']}"
        body = entry.get("Body") or {}
        raw_html = body.get("Html") or ""
        text = body.get("Text") or to_text(raw_html)
        posted = to_iso(parse_iso(entry.get("StartDate") or entry.get("CreatedDate")))
        attachments = [{"name": a.get("FileName", "attachment"), "size": a.get("Size")}
                       for a in entry.get("Attachments") or []]
        with self.hub.tx() as conn:
            old = conn.execute("SELECT uid FROM announcements WHERE uid=?", (uid,)).fetchone()
            conn.execute(
                """INSERT INTO announcements (uid, course_id, title, body_html, body_text, posted, url, attachments,
                     read, first_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(uid) DO UPDATE SET title=excluded.title, body_html=excluded.body_html,
                     body_text=excluded.body_text, posted=excluded.posted, attachments=excluded.attachments""",
                (uid, ou, entry.get("Title") or "Announcement", sanitize(raw_html, self.base), text[:20000],
                 posted, f"{self.base}/d2l/le/news/{ou}/{entry['Id']}/view", _json(attachments),
                 int(quiet), self.stamp))
        if old is None:
            self.report.announcements += 1
            if not quiet:
                self.hub.add_change(self.stamp, "new_announcement", f"{code}: {entry.get('Title') or 'Announcement'}",
                                    course_id=ou, detail=text[:200], ref=uid)

    def _save_topic(self, topic: dict, ou: int, code: str, quiet: bool) -> None:
        uid = f"d2l:topic:{ou}:{topic['TopicId']}"
        kind = {"File": "file", "Link": "link"}.get(topic.get("TypeIdentifier"), "page")
        url = topic.get("Url") or ""
        if kind == "link":
            link = url
        else:
            link = f"{self.base}/d2l/le/content/{ou}/viewContent/{topic['TopicId']}/View"
        modified = to_iso(parse_iso(topic.get("LastModifiedDate")))
        old = self.hub.one("SELECT * FROM files WHERE uid=?", (uid,))
        local_path = old["local_path"] if old else ""
        size = old["size"] if old else None
        needs = kind == "file" and self.download and (not old or old["modified"] != modified or not local_path
                                                      or not Path(local_path).exists())
        if needs:
            try:
                data = self.client.topic_file(ou, topic["TopicId"])
                target = self._file_path(code, topic)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                local_path, size = str(target), len(data)
                self.report.files_downloaded += 1
            except SessionExpiredError:
                raise
            except D2LError as exc:
                self.report.errors.append(f"{code} file {topic.get('Title')}: {exc}")
        with self.hub.tx() as conn:
            conn.execute(
                """INSERT INTO files (uid, course_id, module, title, kind, url, local_path, modified, size, first_seen)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(uid) DO UPDATE SET module=excluded.module, title=excluded.title, kind=excluded.kind,
                     url=excluded.url, local_path=excluded.local_path, modified=excluded.modified,
                     size=excluded.size, text_status=CASE WHEN files.modified IS excluded.modified
                     THEN files.text_status ELSE '' END""",
                (uid, ou, topic["_module"], topic.get("Title") or "Untitled", kind, link, local_path, modified,
                 size, self.stamp))
        if old is None and not quiet:
            self.hub.add_change(self.stamp, "new_file", f"{code}: {topic.get('Title')}", course_id=ou,
                                detail=topic["_module"], ref=uid)

    def _file_path(self, code: str, topic: dict) -> Path:
        folder = self.hub.files_dir / safe_name(code.replace(" ", ""), 20)
        for part in topic["_module"].split(" / ")[:4]:
            folder = folder / safe_name(part, 50)
        name = topic_filename(topic)
        stem, dot, ext = name.rpartition(".")
        unique = f"{stem}-{topic['TopicId']}.{ext}" if dot else f"{name}-{topic['TopicId']}"
        target = folder / unique
        root = self.hub.files_dir.resolve()
        if not target.resolve().is_relative_to(root):
            raise D2LError("unsafe file path")
        return target

    def _save_grade(self, value: dict, ou: int, code: str, quiet: bool, is_final: bool = False) -> None:
        uid = f"d2l:grade:{ou}:{value.get('GradeObjectIdentifier')}"
        type_name = (value.get("GradeObjectTypeName") or "").lower()
        gtype = "final" if is_final else "category" if "category" in type_name or value.get("GradeObjectType") == 9 \
            else "item"
        row = (value.get("PointsNumerator"), value.get("PointsDenominator"), value.get("WeightedNumerator"),
               value.get("WeightedDenominator"), value.get("DisplayedGrade") or "")
        old = self.hub.one("SELECT * FROM grades WHERE uid=?", (uid,))
        with self.hub.tx() as conn:
            conn.execute(
                """INSERT INTO grades (uid, course_id, name, points_num, points_den, weighted_num, weighted_den,
                     displayed, gtype, is_final, updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(uid) DO UPDATE SET name=excluded.name, points_num=excluded.points_num,
                     points_den=excluded.points_den, weighted_num=excluded.weighted_num,
                     weighted_den=excluded.weighted_den, displayed=excluded.displayed, gtype=excluded.gtype,
                     updated=excluded.updated""",
                (uid, ou, value.get("GradeObjectName") or "Grade item", *row, gtype, int(is_final), self.stamp))
        old_row = (old["points_num"], old["points_den"], old["weighted_num"], old["weighted_den"],
                   old["displayed"]) if old else None
        if old is None:
            self.report.grades += 1
        if quiet or old_row == row or row[0] is None and not row[4]:
            return
        kind = "new_grade" if old is None or (old["points_num"] is None and not old["displayed"]) else "grade_changed"
        shown = row[4] or (f"{row[0]:g}/{row[1]:g}" if row[0] is not None and row[1] else "")
        self.hub.add_change(self.stamp, kind, f"{code}: {value.get('GradeObjectName')} graded", course_id=ou,
                            detail=shown, ref=uid)

    # syllabus ----------------------------------------------------------------
    def _syllabus(self, ou: int, code: str, topics: list[dict], overview: dict | None, enrollment: dict) -> None:
        from .extract import ExtractError, extract_file

        text, source = "", ""
        for topic in topics:
            title = f"{topic.get('Title', '')} {topic.get('Url', '')}"
            if not SYLLABUS_RE.search(title):
                continue
            uid = f"d2l:topic:{ou}:{topic['TopicId']}"
            row = self.hub.one("SELECT local_path FROM files WHERE uid=?", (uid,))
            if row and row["local_path"] and Path(row["local_path"]).exists():
                try:
                    text = extract_file(Path(row["local_path"]), topic.get("Title")).markdown
                    source = f"LEARN content: {topic.get('Title')}"
                    break
                except ExtractError:
                    continue
            url = topic.get("Url") or ""
            if urlparse(url).hostname in PUBLIC_OUTLINE_HOSTS and urlparse(url).scheme == "https":
                text = self._fetch_public_outline(url)
                if text:
                    source = f"Course outline: {url}"
                    break
        if not text and overview:
            description = (overview.get("Description") or {})
            text = description.get("Text") or to_text(description.get("Html") or "")
            source = "LEARN course overview" if text.strip() else ""
        if not text.strip():
            return
        term_start = parse_iso(enrollment.get("start"))
        term_end = parse_iso(enrollment.get("end"))
        parsed = parse_syllabus(text, term_start.date() if term_start else None,
                                term_end.date() if term_end else None, self.now.date())
        save_syllabus(self.hub, ou, code, source, text, parsed, self.stamp)


    def _fetch_public_outline(self, url: str) -> str:
        """Public Waterloo course outlines need no sign-in, so they are fetched WITHOUT the LEARN cookies."""
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "CourseCompass/0.2"})
            with self.public_open(request, timeout=20) as response:
                if urlparse(response.geturl()).hostname not in PUBLIC_OUTLINE_HOSTS:
                    return ""
                return to_text(response.read(3_000_000).decode("utf-8", errors="replace"), markdown_headings=True)
        except (OSError, ValueError):
            return ""


def save_syllabus(hub: Hub, ou: int, code: str, source: str, text: str, parsed, stamp: str) -> None:
    """Store a parsed outline, add its exam dates as items, and flag dates that disagree with LEARN."""
    with hub.tx() as conn:
        conn.execute("""INSERT INTO syllabus (course_id, source, text, parsed, updated) VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(course_id) DO UPDATE SET source=excluded.source, text=excluded.text,
                          parsed=excluded.parsed, updated=excluded.updated""",
                     (ou, source, text[:400_000], _json(parsed.to_dict()), stamp))
    learn_items = hub.items(course_id=ou, include_hidden=True)
    tz = local_tz()
    for key_date in parsed.dates:
        if key_date.kind != "exam":
            continue
        duplicate = any(item["source"] == "d2l" and item.get("due") and
                        abs((parse_iso(item["due"]).astimezone(tz).date() -
                             datetime.fromisoformat(key_date.date).date()).days) <= 1 and
                        EXAM_TITLE_RE.search(item["title"]) for item in learn_items)
        if duplicate:
            continue
        hour, minute = (int(x) for x in (key_date.start_time or "09:00").split(":"))
        start = datetime.fromisoformat(key_date.date).replace(hour=hour, minute=minute, tzinfo=tz)
        end = None
        if key_date.end_time:
            eh, em = (int(x) for x in key_date.end_time.split(":"))
            end = to_iso(start.replace(hour=eh, minute=em))
        slug = re.sub(r"[^a-z0-9]+", "-", key_date.label.lower()).strip("-")[:40]
        hub.upsert_item({"uid": f"syllabus:{ou}:{slug}:{key_date.date}", "course_id": ou, "kind": "exam",
                         "title": key_date.label or "Exam", "due": to_iso(start), "end": end,
                         "all_day": key_date.start_time is None, "source": "syllabus",
                         "details": f"From the course outline: \"{key_date.quote}\"", "weight": key_date.weight},
                        stamp)
    for clash in conflicts(parsed, learn_items, tz):
        ref = f"conflict:{clash['item_uid']}:{clash['syllabus_date']}"
        if not hub.one("SELECT id FROM changes WHERE ref=?", (ref,)):
            hub.add_change(stamp, "syllabus_conflict", f"{code}: {clash['label']} dates disagree", course_id=ou,
                           detail=f"Outline says {clash['syllabus_date']}, LEARN says {clash['learn_date']}. "
                                  "Check with the instructor.", ref=ref)


def _json(value) -> str:
    import json
    return json.dumps(value)


# ---------------------------------------------------------------- LEARN calendar feed (no session needed)

def sync_ical(hub: Hub, text: str, now: datetime | None = None, source: str = "ical") -> int:
    """Import events from LEARN's calendar subscription feed (or a timetable .ics). Returns items saved."""
    now = now or utcnow()
    stamp = to_iso(now)
    courses = hub.courses(include_hidden=True)
    saved = 0
    seen = set()
    for event in ics_mod.parse_calendar(text):
        haystack = f"{event.summary} {event.location} {event.description}"
        course_id = None
        for course in courses:
            compact = course["code"].replace(" ", "")
            if course["code"] in haystack or compact in haystack.replace(" ", ""):
                course_id = course["id"]
                break
        if source == "class":
            kind = "class"
        elif EXAM_TITLE_RE.search(event.summary):
            kind = "exam"
        elif re.search(r"\b(due|submission|assignment|quiz)\b", event.summary, re.I):
            kind = "assignment"
        else:
            kind = "event"
        item = ics_mod.event_to_item(event, source, course_id, kind)
        seen.add(item["uid"])
        result, _ = hub.upsert_item(item, stamp)
        saved += result != "same"
    for row in hub.query("SELECT uid FROM items WHERE source=?", (source,)):
        if row["uid"] not in seen:
            hub.delete_item(row["uid"])
    dedupe_ical(hub)
    return saved


def dedupe_ical(hub: Hub) -> int:
    """Hide feed events that duplicate an API item (same course or unknown course, due within an hour)."""
    from .syllabus import similar

    api = [i for i in hub.items(include_hidden=True) if i["source"] == "d2l" and i.get("due")]
    hidden = 0
    for item in hub.items(include_hidden=True):
        if item["source"] != "ical" or not item.get("due"):
            continue
        due = parse_iso(item["due"])
        for other in api:
            if other["course_id"] not in (None, item["course_id"]) and item["course_id"] is not None:
                continue
            if abs((parse_iso(other["due"]) - due).total_seconds()) <= 3600 and \
                    similar(item["title"], other["title"]) >= 0.4:
                if not item["hidden"]:
                    hub.update_item(item["uid"], hidden=1)
                    hidden += 1
                break
    return hidden


def fetch_ical(url: str, base: str, opener: Callable | None = None) -> str:
    """Download LEARN's calendar feed. The URL carries its own token, so no session cookie is sent."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("https", "webcal") or not parsed.hostname:
        raise D2LError("The LEARN calendar feed URL must start with https:// or webcal://")
    if parsed.hostname != urlparse(base).hostname:
        raise D2LError(f"The calendar feed must come from {urlparse(base).hostname}.")
    url = "https://" + url.strip().split("://", 1)[1]
    request = urllib.request.Request(url, headers={"User-Agent": "CourseCompass/0.2"})
    with (opener or urllib.request.urlopen)(request, timeout=30) as response:
        return response.read(ics_mod.MAX_ICS_BYTES).decode("utf-8", errors="replace")
