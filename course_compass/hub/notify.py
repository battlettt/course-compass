"""Alerts for changes found by a sync, and the morning digest.

Sinks:
  macOS   a Notification Center banner via `osascript` (text passed as
          arguments, never spliced into the AppleScript source)
  ntfy    an optional push to your phone through an ntfy topic URL you set
          (https only). Only a short title and the course code are sent,
          never announcement bodies or grades.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta
from typing import Callable
from urllib.parse import urlparse

from .db import Hub
from .timeutil import countdown, local_tz, parse_iso, to_iso, utcnow

APPLESCRIPT = 'on run argv\ndisplay notification (item 2 of argv) with title (item 1 of argv)\nend run'
PUSH_KINDS = {"new_announcement", "due_changed", "new_grade", "grade_changed", "new_item", "syllabus_conflict",
              "login_needed", "removed_item"}
KIND_LABEL = {"new_announcement": "Announcement", "due_changed": "Due date moved", "new_grade": "New grade",
              "grade_changed": "Grade updated", "new_item": "New work", "removed_item": "Removed",
              "syllabus_conflict": "Date conflict", "login_needed": "Sign-in needed", "new_file": "New file",
              "course_added": "Course added"}


def macos_notify(title: str, body: str, runner: Callable = subprocess.run) -> bool:
    if sys.platform != "darwin" or not shutil.which("osascript"):
        return False
    try:
        runner(["osascript", "-e", APPLESCRIPT, title[:120], body[:240]], capture_output=True, timeout=10,
               check=False)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def check_ntfy_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or not parsed.hostname or len(parsed.path.strip("/")) < 8:
        raise ValueError("ntfy URL must be https://host/<topic>, with a long, hard-to-guess topic name.")
    return url.strip()


def ntfy_push(url: str, title: str, body: str, opener: Callable = urllib.request.urlopen) -> bool:
    try:
        request = urllib.request.Request(check_ntfy_url(url), data=body[:200].encode("utf-8"), method="POST",
                                         headers={"Title": title[:100].encode("ascii", "replace").decode(),
                                                  "Tags": "books"})
        with opener(request, timeout=10):
            return True
    except (OSError, ValueError):
        return False


def push_text(change: dict) -> tuple[str, str]:
    """Short, low-detail text for an alert: no announcement bodies or grade values leave the Mac."""
    label = KIND_LABEL.get(change["kind"], "Update")
    title = f"Course Compass: {label}"
    body = change["title"]
    if change["kind"] == "due_changed":
        body += f" ({change['detail']})"
    return title, body


def send_pending(hub: Hub, macos: Callable = macos_notify, ntfy: Callable = ntfy_push) -> int:
    """Notify about changes not yet notified. Several at once become one summary alert."""
    pending = [c for c in hub.changes(limit=200) if not c["notified"] and c["kind"] in PUSH_KINDS]
    settings = hub.settings()
    if pending:
        if len(pending) <= 3:
            messages = [push_text(c) for c in reversed(pending)]
        else:
            titles = "; ".join(c["title"] for c in pending[:4])
            messages = [("Course Compass", f"{len(pending)} updates: {titles}...")]
        for title, body in messages:
            if settings.get("notify_macos"):
                macos(title, body)
            if settings.get("ntfy_url"):
                ntfy(settings["ntfy_url"], title, body)
    with hub.tx() as conn:
        conn.execute("UPDATE changes SET notified=1 WHERE notified=0")
    return len(pending)


def digest(hub: Hub, now: datetime | None = None, days: int = 3) -> str:
    """Markdown morning digest: what's due soon, today's study blocks, new since yesterday, exams ahead."""
    from ..reminders import due_digest
    from ..store import load_db

    now = now or utcnow()
    tz = local_tz()
    today = now.astimezone(tz)
    lines = [f"# Course Compass, {today.strftime('%A %B %-d')}", ""]
    soon = [i for i in hub.items(start=to_iso(now - timedelta(hours=12)), end=to_iso(now + timedelta(days=days)),
                                 kinds=("assignment", "quiz", "exam", "manual")) if i["status"] == "open"]
    lines.append(f"## Due in the next {days} days ({len(soon)})")
    for item in soon:
        due = parse_iso(item["due"])
        lines.append(f"- **{item['course_code'] or ''} {item['title']}**: "
                     f"{due.astimezone(tz).strftime('%a %-I:%M %p')} ({countdown(due, now)})")
    if not soon:
        lines.append("- Nothing due. Good time to get ahead.")
    start = now.astimezone(tz).replace(hour=0, minute=0, second=0)
    blocks = hub.query("SELECT * FROM plan_blocks WHERE start >= ? AND start < ? ORDER BY start",
                       (to_iso(start), to_iso(start + timedelta(days=1))))
    lines += ["", f"## Today's study plan ({len(blocks)} block(s))"]
    for block in blocks:
        begin = parse_iso(block["start"]).astimezone(tz)
        end = parse_iso(block["end"]).astimezone(tz)
        lines.append(f"- {begin.strftime('%-I:%M')}-{end.strftime('%-I:%M %p')} {block['title']}")
    if not blocks:
        lines.append("- No blocks planned. Run the planner, or enjoy the day.")
    since = to_iso(now - timedelta(hours=24))
    fresh = hub.query("""SELECT changes.*, courses.code AS course_code FROM changes
                         LEFT JOIN courses ON courses.id = changes.course_id WHERE changes.at >= ? ORDER BY changes.id DESC""",
                      (since,))
    lines += ["", f"## New since yesterday ({len(fresh)})"]
    for change in fresh[:15]:
        detail = f": {change['detail'][:120]}" if change["detail"] else ""
        lines.append(f"- {KIND_LABEL.get(change['kind'], 'Update')}: {change['title']}{detail}")
    if not fresh:
        lines.append("- Nothing new on LEARN.")
    exams = hub.items(start=to_iso(now), end=to_iso(now + timedelta(days=21)), kinds=("exam",))
    if exams:
        lines += ["", "## Exams in the next 3 weeks"]
        for exam in exams:
            due = parse_iso(exam["due"])
            lines.append(f"- {exam['course_code'] or ''} {exam['title']}: {due.astimezone(tz).strftime('%a %b %-d')} "
                         f"({countdown(due, now)})")
    if hub.study_db.exists():
        db = load_db(hub.study_db)
        groups = due_digest(db, today.date())
        cards = sum(len(g.cards) for g in groups)
        if cards:
            lines += ["", f"## Spaced review: {cards} card(s) due across {len(groups)} topic(s)"]
    return "\n".join(lines) + "\n"
