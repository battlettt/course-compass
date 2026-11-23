"""SQLite storage for the school hub (courses, work items, announcements, files, grades, plans, time).

One file, `hub.sqlite3`, inside the Course Compass home folder. One connection
guarded by a lock, because the web server is threaded and SQLite connections
must not be used from two threads at once.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS courses (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    term TEXT NOT NULL DEFAULT '',
    start TEXT, end TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    hidden INTEGER NOT NULL DEFAULT 0,
    color TEXT NOT NULL DEFAULT '',
    target REAL,
    url TEXT NOT NULL DEFAULT '',
    synced TEXT
);
CREATE TABLE IF NOT EXISTS items (
    uid TEXT PRIMARY KEY,
    course_id INTEGER,
    kind TEXT NOT NULL,              -- assignment, quiz, exam, event, class, manual
    title TEXT NOT NULL,
    due TEXT,                        -- UTC ISO; for events the start
    end TEXT,
    all_day INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL,            -- d2l, syllabus, ical, manual
    url TEXT NOT NULL DEFAULT '',
    details TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    weight REAL,
    status TEXT NOT NULL DEFAULT 'open',   -- open, submitted, done
    est_hours REAL,
    est_manual INTEGER NOT NULL DEFAULT 0,
    hidden INTEGER NOT NULL DEFAULT 0,
    first_seen TEXT NOT NULL,
    updated TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS items_due ON items(due);
CREATE TABLE IF NOT EXISTS subtasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_uid TEXT NOT NULL,
    text TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS announcements (
    uid TEXT PRIMARY KEY,
    course_id INTEGER,
    title TEXT NOT NULL,
    body_html TEXT NOT NULL DEFAULT '',
    body_text TEXT NOT NULL DEFAULT '',
    posted TEXT,
    url TEXT NOT NULL DEFAULT '',
    attachments TEXT NOT NULL DEFAULT '[]',
    read INTEGER NOT NULL DEFAULT 0,
    first_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS files (
    uid TEXT PRIMARY KEY,
    course_id INTEGER,
    module TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'file',     -- file, link, page
    url TEXT NOT NULL DEFAULT '',
    local_path TEXT NOT NULL DEFAULT '',
    modified TEXT,
    size INTEGER,
    text_status TEXT NOT NULL DEFAULT '',  -- '', ok, empty, unsupported, error, needs-ocr
    first_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS grades (
    uid TEXT PRIMARY KEY,
    course_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    points_num REAL, points_den REAL,
    weighted_num REAL, weighted_den REAL,
    displayed TEXT NOT NULL DEFAULT '',
    gtype TEXT NOT NULL DEFAULT 'item',    -- item, category, final
    is_final INTEGER NOT NULL DEFAULT 0,
    updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS whatif (
    course_id INTEGER NOT NULL,
    component TEXT NOT NULL,
    percent REAL NOT NULL,
    PRIMARY KEY (course_id, component)
);
CREATE TABLE IF NOT EXISTS syllabus (
    course_id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    text TEXT NOT NULL,
    parsed TEXT NOT NULL,
    updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    course_id INTEGER,
    kind TEXT NOT NULL,     -- new_item, due_changed, removed_item, new_announcement, new_file, new_grade, grade_changed, syllabus_conflict, login_needed
    title TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    ref TEXT NOT NULL DEFAULT '',
    notified INTEGER NOT NULL DEFAULT 0,
    read INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS time_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER,
    item_uid TEXT,
    start TEXT NOT NULL,
    end TEXT,
    kind TEXT NOT NULL DEFAULT 'focus',   -- focus, manual
    note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS plan_blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_uid TEXT,
    course_id INTEGER,
    title TEXT NOT NULL,
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'work',    -- work, exam-prep
    done INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started TEXT NOT NULL,
    finished TEXT,
    ok INTEGER,
    source TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT ''
);
"""

DEFAULT_SETTINGS = {
    "d2l_base": "https://learn.uwaterloo.ca",
    "d2l_ical_url": "",
    "availability": {   # local-time study windows per weekday, 0 = Monday
        "0": [["18:00", "22:00"]], "1": [["18:00", "22:00"]], "2": [["18:00", "22:00"]],
        "3": [["18:00", "22:00"]], "4": [["14:00", "18:00"]],
        "5": [["10:00", "13:00"], ["14:00", "18:00"]], "6": [["12:00", "18:00"]],
    },
    "daily_cap_hours": 4.0,
    "block_minutes": 90,
    "finish_buffer_days": 1,
    "alarms_minutes": [2880, 180],
    "notify_macos": True,
    "ntfy_url": "",
    "focus_minutes": 25,
    "break_minutes": 5,
    "weekly_goal_hours": 25.0,
    "feed_include_study": True,
}

COURSE_COLORS = ["#2563eb", "#db2777", "#059669", "#d97706", "#7c3aed", "#0891b2", "#dc2626", "#65a30d"]


class HubError(Exception):
    pass


def default_home() -> Path:
    raw = os.environ.get("COURSE_COMPASS_HOME", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".course-compass"


class Hub:
    """Data access for the hub. Every public method takes the lock."""

    def __init__(self, home: str | Path | None = None, path: str | Path | None = None):
        self.home = Path(home) if home else default_home()
        self.home.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.home, 0o700)
        except OSError:
            pass
        self.path = Path(path) if path else self.home / "hub.sqlite3"
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)
        found = self.conn.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
        if found is None:
            self.conn.execute("INSERT INTO meta VALUES ('schema', ?)", (str(SCHEMA_VERSION),))
            self.conn.commit()
        elif int(found["value"]) != SCHEMA_VERSION:
            raise HubError(f"{self.path} has schema {found['value']}, expected {SCHEMA_VERSION}.")

    # paths -------------------------------------------------------------
    @property
    def study_db(self) -> Path:
        return self.home / "study.json"

    @property
    def files_dir(self) -> Path:
        return self.home / "files"

    @property
    def notes_dir(self) -> Path:
        return self.home / "notes"

    @property
    def text_dir(self) -> Path:
        return self.home / "text"

    @property
    def session_file(self) -> Path:
        return self.home / "session.json"

    @property
    def browser_profile(self) -> Path:
        return self.home / "browser-profile"

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    @contextmanager
    def tx(self):
        with self.lock:
            try:
                yield self.conn
                self.conn.commit()
            except BaseException:
                self.conn.rollback()
                raise

    def query(self, sql: str, params=()) -> list[dict]:
        with self.lock:
            return [dict(row) for row in self.conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params=()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # settings ------------------------------------------------------------
    def get_setting(self, key: str):
        row = self.one("SELECT value FROM settings WHERE key=?", (key,))
        if row is None:
            value = DEFAULT_SETTINGS.get(key)
            return json.loads(json.dumps(value))  # a copy, so callers can't mutate the defaults
        return json.loads(row["value"])

    def set_setting(self, key: str, value) -> None:
        with self.tx() as conn:
            conn.execute("INSERT INTO settings VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (key, json.dumps(value)))

    def settings(self) -> dict:
        out = {key: self.get_setting(key) for key in DEFAULT_SETTINGS}
        for row in self.query("SELECT key, value FROM settings"):
            out[row["key"]] = json.loads(row["value"])
        return out

    def feed_token(self, rotate: bool = False) -> str:
        """Secret for the calendar feed URL. It works like a password: anyone with it can read the feed."""
        token = self.get_setting("feed_token")
        if rotate or not token:
            token = secrets.token_urlsafe(24)
            self.set_setting("feed_token", token)
        return token

    # courses -------------------------------------------------------------
    def upsert_course(self, course: dict, now: str) -> None:
        with self.tx() as conn:
            existing = conn.execute("SELECT color FROM courses WHERE id=?", (course["id"],)).fetchone()
            color = existing["color"] if existing and existing["color"] else self._next_color(conn)
            conn.execute(
                """INSERT INTO courses (id, code, name, term, start, end, active, color, url, synced)
                   VALUES (:id, :code, :name, :term, :start, :end, :active, :color, :url, :synced)
                   ON CONFLICT(id) DO UPDATE SET code=excluded.code, name=excluded.name, term=excluded.term,
                     start=excluded.start, end=excluded.end, active=excluded.active, url=excluded.url,
                     synced=excluded.synced""",
                {"term": "", "start": None, "end": None, "active": 1, "url": "", **course,
                 "color": color, "synced": now})

    def _next_color(self, conn) -> str:
        used = conn.execute("SELECT COUNT(*) FROM courses").fetchone()[0]
        return COURSE_COLORS[used % len(COURSE_COLORS)]

    def courses(self, include_hidden: bool = False) -> list[dict]:
        sql = "SELECT * FROM courses" + ("" if include_hidden else " WHERE hidden=0") + " ORDER BY code"
        return self.query(sql)

    def course(self, course_id: int) -> dict | None:
        return self.one("SELECT * FROM courses WHERE id=?", (course_id,))

    def update_course(self, course_id: int, **fields) -> None:
        allowed = {"color", "hidden", "target", "name"}
        bad = set(fields) - allowed
        if bad:
            raise HubError(f"Cannot change course field(s): {', '.join(sorted(bad))}")
        if not fields:
            return
        sets = ", ".join(f"{key}=?" for key in fields)
        with self.tx() as conn:
            conn.execute(f"UPDATE courses SET {sets} WHERE id=?", (*fields.values(), course_id))

    # items -----------------------------------------------------------------
    ITEM_FIELDS = ("course_id", "kind", "title", "due", "end", "all_day", "source", "url", "details",
                   "location", "weight")

    def upsert_item(self, item: dict, now: str) -> tuple[str, dict | None]:
        """Insert or update a work item. Returns ("new" | "changed" | "same", previous row)."""
        with self.tx() as conn:
            old = conn.execute("SELECT * FROM items WHERE uid=?", (item["uid"],)).fetchone()
            values = {key: item.get(key) for key in self.ITEM_FIELDS}
            values.setdefault("all_day", 0)
            values["all_day"] = int(bool(values["all_day"]))
            for key in ("url", "details", "location"):
                values[key] = values[key] or ""
            if old is None:
                conn.execute(
                    """INSERT INTO items (uid, course_id, kind, title, due, end, all_day, source, url, details,
                         location, weight, status, first_seen, updated)
                       VALUES (:uid, :course_id, :kind, :title, :due, :end, :all_day, :source, :url, :details,
                         :location, :weight, :status, :now, :now)""",
                    {**values, "uid": item["uid"], "status": item.get("status") or "open", "now": now})
                return "new", None
            old = dict(old)
            changed = any(old.get(key) != values[key] for key in self.ITEM_FIELDS)
            status = item.get("status")
            # D2L can only ever tell us "submitted"; never overwrite a status the student set by hand.
            new_status = old["status"]
            if status == "submitted" and old["status"] == "open":
                new_status = "submitted"
                changed = True
            if changed:
                conn.execute(
                    f"UPDATE items SET {', '.join(f'{k}=:{k}' for k in self.ITEM_FIELDS)}, status=:status, "
                    "updated=:now WHERE uid=:uid",
                    {**values, "uid": item["uid"], "status": new_status, "now": now})
                return "changed", old
            return "same", old

    def items(self, start: str | None = None, end: str | None = None, course_id: int | None = None,
              kinds: tuple[str, ...] | None = None, include_hidden: bool = False) -> list[dict]:
        sql = ["SELECT items.*, courses.code AS course_code, courses.color AS course_color FROM items "
               "LEFT JOIN courses ON courses.id = items.course_id WHERE 1=1"]
        params: list = []
        if not include_hidden:
            sql.append("AND items.hidden=0 AND (courses.hidden IS NULL OR courses.hidden=0)")
        if start:
            sql.append("AND items.due >= ?")
            params.append(start)
        if end:
            sql.append("AND items.due < ?")
            params.append(end)
        if course_id is not None:
            sql.append("AND items.course_id = ?")
            params.append(course_id)
        if kinds:
            sql.append(f"AND items.kind IN ({','.join('?' * len(kinds))})")
            params.extend(kinds)
        sql.append("ORDER BY items.due IS NULL, items.due, items.title")
        return self.query(" ".join(sql), params)

    def item(self, uid: str) -> dict | None:
        return self.one("SELECT items.*, courses.code AS course_code, courses.color AS course_color FROM items "
                        "LEFT JOIN courses ON courses.id = items.course_id WHERE uid=?", (uid,))

    def update_item(self, uid: str, **fields) -> None:
        allowed = {"status", "est_hours", "est_manual", "hidden", "weight", "title", "due", "details", "location"}
        bad = set(fields) - allowed
        if bad:
            raise HubError(f"Cannot change item field(s): {', '.join(sorted(bad))}")
        if "status" in fields and fields["status"] not in ("open", "submitted", "done"):
            raise HubError("status must be open, submitted or done")
        if not fields:
            return
        sets = ", ".join(f"{key}=?" for key in fields)
        with self.tx() as conn:
            conn.execute(f"UPDATE items SET {sets} WHERE uid=?", (*fields.values(), uid))

    def delete_item(self, uid: str) -> None:
        with self.tx() as conn:
            conn.execute("DELETE FROM subtasks WHERE item_uid=?", (uid,))
            conn.execute("DELETE FROM items WHERE uid=?", (uid,))

    # subtasks ---------------------------------------------------------------
    def subtasks(self, uid: str) -> list[dict]:
        return self.query("SELECT * FROM subtasks WHERE item_uid=? ORDER BY position, id", (uid,))

    def set_subtasks(self, uid: str, texts: list[str]) -> None:
        with self.tx() as conn:
            conn.execute("DELETE FROM subtasks WHERE item_uid=?", (uid,))
            for position, text in enumerate(texts):
                conn.execute("INSERT INTO subtasks (item_uid, text, position) VALUES (?, ?, ?)",
                             (uid, text[:300], position))

    def add_subtask(self, uid: str, text: str) -> int:
        with self.tx() as conn:
            pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM subtasks WHERE item_uid=?",
                               (uid,)).fetchone()[0]
            cur = conn.execute("INSERT INTO subtasks (item_uid, text, position) VALUES (?, ?, ?)",
                               (uid, text[:300], pos))
            return cur.lastrowid

    def toggle_subtask(self, subtask_id: int, done: bool) -> None:
        with self.tx() as conn:
            conn.execute("UPDATE subtasks SET done=? WHERE id=?", (int(done), subtask_id))

    def delete_subtask(self, subtask_id: int) -> None:
        with self.tx() as conn:
            conn.execute("DELETE FROM subtasks WHERE id=?", (subtask_id,))

    # changes -----------------------------------------------------------------
    def add_change(self, at: str, kind: str, title: str, course_id: int | None = None,
                   detail: str = "", ref: str = "") -> None:
        with self.tx() as conn:
            conn.execute("INSERT INTO changes (at, course_id, kind, title, detail, ref) VALUES (?, ?, ?, ?, ?, ?)",
                         (at, course_id, kind, title, detail, ref))

    def changes(self, limit: int = 100, unread_only: bool = False) -> list[dict]:
        sql = ("SELECT changes.*, courses.code AS course_code, courses.color AS course_color FROM changes "
               "LEFT JOIN courses ON courses.id = changes.course_id")
        if unread_only:
            sql += " WHERE changes.read=0"
        return self.query(sql + " ORDER BY changes.id DESC LIMIT ?", (limit,))

    # sync runs ----------------------------------------------------------------
    def start_run(self, source: str, now: str) -> int:
        with self.tx() as conn:
            return conn.execute("INSERT INTO sync_runs (started, source) VALUES (?, ?)", (now, source)).lastrowid

    def finish_run(self, run_id: int, ok: bool, summary: str, now: str) -> None:
        with self.tx() as conn:
            conn.execute("UPDATE sync_runs SET finished=?, ok=?, summary=? WHERE id=?",
                         (now, int(ok), summary[:2000], run_id))

    def last_run(self) -> dict | None:
        return self.one("SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1")
