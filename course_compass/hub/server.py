"""The hub's local web app: a JSON API plus the single-page UI in course_compass/web/.

Security model (it serves your school data, so it is locked down by default):
  - Binds to 127.0.0.1 only unless you pass --host.
  - Every API call needs the per-launch token that is embedded in the page.
    Other websites you visit cannot read the page, so they cannot get the
    token, so they cannot drive the API (no CSRF).
  - The Host header must be one we serve (blocks DNS-rebinding attacks).
  - A strict Content-Security-Policy: scripts only from this server, no
    inline script, no outside connections.
  - The calendar feed is the one exception to the token: it lives at
    /feed/<secret>/<name>.ics so Apple Calendar can poll it. The secret is
    compared in constant time and can be rotated in Settings.
"""

from __future__ import annotations

import hmac
import json
import mimetypes
import re
import secrets
import threading
import traceback
from datetime import date, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from ..answering import AnswerError, answer_question
from ..chunking import Chunk
from ..explain import ExplainError, real_life_examples, select_chunks, simplify
from ..llm import LLMError
from ..progress import cram_topics, split_questions, topic_progress
from ..quiz import QuizError, QuizItem, llm_quiz, local_quiz
from ..reminders import due_digest
from ..retrieval import TfidfIndex
from ..review import Card, new_card, quality_from_correct, schedule as sm2_schedule, topic_stats
from ..store import load_db, save_db
from . import planner
from .checklist import checklist_from_text, search
from .db import Hub, HubError
from .examprep import exam_plan, plan_to_dict
from .grades import standing
from .ics import ICSError, build_feed
from .jobs import full_refresh, llm_client
from .notes import chunk_course, course_slug, topic_titles
from .notify import digest
from .sync import sync_ical
from .syllabus import Syllabus
from .timeutil import local_tz, parse_iso, to_iso, utcnow, week_monday
from .tutor import TutorError, check_work, explain_back, walkthrough

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
MAX_BODY = 6_000_000
FEEDS = {"all": "Course Compass", "deadlines": "Course Compass: deadlines", "exams": "Course Compass: exams",
         "study": "Course Compass: study plan", "classes": "Course Compass: classes"}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; "
       "object-src 'none'")


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class App:
    """Everything the handlers need, shared across request threads."""

    def __init__(self, hub: Hub, host: str = "127.0.0.1", port: int = 8765, extra_hosts: list[str] | None = None,
                 client_factory=None):
        self.hub = hub
        self.token = secrets.token_urlsafe(24)
        self.host, self.port = host, port
        self.extra_hosts = [h.lower() for h in (extra_hosts or [])]
        self._index_lock = threading.Lock()
        self._index_key = None
        self._chunks: list[Chunk] = []
        self._index: TfidfIndex | None = None
        self.study_lock = threading.RLock()
        self.sync_state = {"running": False, "lines": [], "started": None, "finished": None, "ok": None}
        self.client_factory = client_factory or (lambda: llm_client(self.hub))

    # hosts -------------------------------------------------------------------
    def allowed_host(self, header: str | None) -> bool:
        if not header:
            return False
        host = header.lower()
        allowed = {f"127.0.0.1:{self.port}", f"localhost:{self.port}", f"[::1]:{self.port}"}
        if self.host not in ("127.0.0.1", "localhost", "::1", "0.0.0.0"):
            allowed.add(f"{self.host}:{self.port}")
        allowed.update(self.extra_hosts)
        return host in allowed

    # notes index cache ---------------------------------------------------------
    def chunks_and_index(self) -> tuple[list[Chunk], TfidfIndex]:
        path = self.hub.study_db
        key = path.stat().st_mtime_ns if path.exists() else None
        with self._index_lock:
            if key != self._index_key or self._index is None:
                db = load_db(path, must_exist=False)
                self._chunks = [Chunk.from_dict(raw) for raw in db["chunks"]]
                self._index = TfidfIndex(self._chunks)
                self._index_key = key
            return self._chunks, self._index

    def course_chunks(self, code: str | None) -> list[Chunk]:
        chunks, _ = self.chunks_and_index()
        if not code:
            return chunks
        return [c for c in chunks if chunk_course(c.source) == code]

    def study_db(self) -> dict:
        return load_db(self.hub.study_db, must_exist=False)


# ---------------------------------------------------------------- helpers

def _int(value, name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ApiError(400, f"{name} must be a whole number") from None


def _num(value, name: str, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ApiError(400, f"{name} must be a number") from None
    if not low <= number <= high:
        raise ApiError(400, f"{name} must be between {low:g} and {high:g}")
    return number


def item_public(item: QuizItem) -> dict:
    return {"id": item.id, "topic": item.topic, "question": item.question, "options": item.options(),
            "source_chunk_id": item.source_chunk_id, "origin": item.origin}


def syllabus_for(hub: Hub, course_id: int) -> tuple[Syllabus | None, dict | None]:
    row = hub.one("SELECT * FROM syllabus WHERE course_id=?", (course_id,))
    if not row:
        return None, None
    return Syllabus.from_dict(json.loads(row["parsed"])), row


def course_standing(hub: Hub, course: dict) -> dict:
    parsed, _ = syllabus_for(hub, course["id"])
    whatif = {r["component"]: r["percent"] for r in hub.query("SELECT * FROM whatif WHERE course_id=?",
                                                                (course["id"],))}
    rows = hub.query("SELECT * FROM grades WHERE course_id=? ORDER BY name", (course["id"],))
    return standing(rows, parsed.components if parsed else [], whatif, course.get("target")).to_dict()


# ---------------------------------------------------------------- API routes

class Routes:
    def __init__(self, app: App):
        self.app = app
        self.hub = app.hub

    # overview ------------------------------------------------------------------
    def get_state(self, q, body):
        hub = self.hub
        last = hub.last_run()
        session = hub.session_file.exists()
        timer = planner.running_timer(hub)
        unread = hub.one("SELECT COUNT(*) n FROM changes WHERE read=0")["n"]
        return {"courses": hub.courses(), "demo": bool(hub.get_setting("demo")), "connected": session,
                "last_sync": last, "timer": timer, "unread_changes": unread,
                "unread_announcements": hub.one("SELECT COUNT(*) n FROM announcements WHERE read=0")["n"],
                "tz": str(local_tz()), "now": to_iso(utcnow()), "sync": self.app.sync_state,
                "provider": hub.get_setting("provider") or "auto",
                "settings": {k: v for k, v in hub.settings().items() if k not in ("feed_token",)}}

    def get_today(self, q, body):
        hub, now, tz = self.hub, utcnow(), local_tz()
        local_now = now.astimezone(tz)
        day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        due = [i for i in hub.items(start=to_iso(now - timedelta(days=2)), end=to_iso(now + timedelta(days=8)),
                                    kinds=("assignment", "quiz", "exam", "manual"))]
        overdue = [i for i in hub.items(start=to_iso(now - timedelta(days=14)), end=to_iso(now),
                                        kinds=("assignment", "quiz", "manual")) if i["status"] == "open"]
        blocks = hub.query("""SELECT plan_blocks.*, courses.code AS course_code, courses.color AS course_color
                              FROM plan_blocks LEFT JOIN courses ON courses.id = plan_blocks.course_id
                              WHERE plan_blocks.start >= ? AND plan_blocks.start < ? ORDER BY plan_blocks.start""",
                           (to_iso(day_start), to_iso(day_start + timedelta(days=1))))
        classes = hub.items(start=to_iso(day_start), end=to_iso(day_start + timedelta(days=1)),
                            kinds=("class", "event"))
        news = hub.query("""SELECT a.uid, a.title, a.posted, a.read, a.body_text, courses.code AS course_code,
                                   courses.color AS course_color FROM announcements a
                            LEFT JOIN courses ON courses.id = a.course_id
                            WHERE courses.hidden IS NOT 1 ORDER BY a.posted DESC LIMIT 6""")
        exams = hub.items(start=to_iso(now), end=to_iso(now + timedelta(days=35)), kinds=("exam",))
        with self.app.study_lock:
            db = self.app.study_db()
        cards = sum(len(g.cards) for g in due_digest(db, local_now.date())) if db["cards"] else 0
        week = planner.week_report(hub, week_monday(local_now.date()))
        return {"due": due, "overdue": overdue, "blocks": blocks, "classes": classes, "announcements": news,
                "exams": exams, "review_cards": cards, "week": week, "changes": hub.changes(limit=8),
                "forecast": planner.forecast(hub, now, weeks=6)}

    # items -----------------------------------------------------------------------
    def get_items(self, q, body):
        course = _int(q["course"], "course") if q.get("course") else None
        kinds = tuple(q["kinds"].split(",")) if q.get("kinds") else None
        return {"items": self.hub.items(start=q.get("start"), end=q.get("end"), course_id=course, kinds=kinds)}

    def post_items(self, q, body):
        title = str(body.get("title", "")).strip()
        if not title or len(title) > 200:
            raise ApiError(400, "title is required (up to 200 characters)")
        due = parse_iso(body.get("due"))
        if due is None:
            raise ApiError(400, "due must be an ISO date or date-time")
        kind = body.get("kind", "manual")
        if kind not in ("manual", "exam", "assignment", "quiz", "class", "event"):
            raise ApiError(400, "unknown kind")
        course = _int(body["course_id"], "course_id") if body.get("course_id") else None
        uid = f"manual:{secrets.token_hex(6)}"
        end = parse_iso(body.get("end")) if body.get("end") else None
        weight = _num(body["weight"], "weight", 0, 100) if body.get("weight") not in (None, "") else None
        self.hub.upsert_item({"uid": uid, "course_id": course, "kind": kind, "title": title, "due": to_iso(due),
                              "end": to_iso(end), "source": "manual", "details": str(body.get("details", ""))[:4000],
                              "location": str(body.get("location", ""))[:200], "weight": weight}, to_iso(utcnow()))
        return {"uid": uid}

    def get_item(self, q, body, uid):
        item = self.hub.item(uid)
        if not item:
            raise ApiError(404, "no such item")
        item["subtasks"] = self.hub.subtasks(uid)
        item["logged_hours"] = planner.logged_hours(self.hub, uid)
        item["estimate"] = item.get("est_hours") or planner.base_estimate(item)
        item["blocks"] = self.hub.query("SELECT * FROM plan_blocks WHERE item_uid=? ORDER BY start", (uid,))
        return item

    def patch_item(self, q, body, uid):
        if not self.hub.item(uid):
            raise ApiError(404, "no such item")
        fields = {}
        if "status" in body:
            fields["status"] = body["status"]
        if "est_hours" in body:
            if body["est_hours"] in (None, ""):
                fields.update(est_hours=None, est_manual=0)
            else:
                fields.update(est_hours=_num(body["est_hours"], "est_hours", 0.25, 200), est_manual=1)
        if "hidden" in body:
            fields["hidden"] = int(bool(body["hidden"]))
        if "weight" in body:
            fields["weight"] = None if body["weight"] in (None, "") else _num(body["weight"], "weight", 0, 100)
        try:
            self.hub.update_item(uid, **fields)
        except HubError as exc:
            raise ApiError(400, str(exc)) from None
        if {"status", "est_hours"} & set(fields):
            planner.replan(self.hub)
        return {"ok": True}

    def delete_item(self, q, body, uid):
        item = self.hub.item(uid)
        if not item:
            raise ApiError(404, "no such item")
        if item["source"] != "manual":
            raise ApiError(400, "only items you added yourself can be deleted; hide LEARN items instead")
        self.hub.delete_item(uid)
        return {"ok": True}

    def post_subtasks(self, q, body, uid):
        item = self.hub.item(uid)
        if not item:
            raise ApiError(404, "no such item")
        if body.get("auto"):
            steps = checklist_from_text(item.get("details") or "")
            if not steps:
                raise ApiError(400, "No numbered questions or deliverables found in the instructions. "
                                    "Add steps yourself.")
            self.hub.set_subtasks(uid, steps)
        else:
            text = str(body.get("text", "")).strip()
            if not text:
                raise ApiError(400, "text is required")
            self.hub.add_subtask(uid, text)
        return {"subtasks": self.hub.subtasks(uid)}

    def patch_subtask(self, q, body, sid):
        self.hub.toggle_subtask(_int(sid, "id"), bool(body.get("done")))
        return {"ok": True}

    def delete_subtask(self, q, body, sid):
        self.hub.delete_subtask(_int(sid, "id"))
        return {"ok": True}

    # courses -----------------------------------------------------------------------
    def get_course(self, q, body, cid):
        hub = self.hub
        course = hub.course(_int(cid, "course"))
        if not course:
            raise ApiError(404, "no such course")
        parsed, row = syllabus_for(hub, course["id"])
        with self.app.study_lock:
            db = self.app.study_db()
        prefix = course_slug(course["code"]) + "-"
        topics = {t: v for t, v in topic_titles(db).items() if t.startswith(prefix)}
        stats = {s.topic: s for s in topic_stats(db["attempts"], list(topics))}
        for topic, info in topics.items():
            stat = stats.get(topic)
            info["accuracy"] = round(stat.correct / stat.attempts * 100) if stat and stat.attempts else None
            info["attempts"] = stat.attempts if stat else 0
        return {
            "course": course,
            "items": hub.items(course_id=course["id"]),
            "announcements": hub.query("SELECT * FROM announcements WHERE course_id=? ORDER BY posted DESC",
                                       (course["id"],)),
            "files": hub.query("SELECT uid, module, title, kind, url, local_path != '' AS downloaded, modified, size,"
                               " text_status FROM files WHERE course_id=? ORDER BY module, title", (course["id"],)),
            "syllabus": {"parsed": parsed.to_dict(), "source": row["source"], "updated": row["updated"],
                         "text": row["text"]} if parsed else None,
            "standing": course_standing(hub, course),
            "grades": hub.query("SELECT * FROM grades WHERE course_id=? ORDER BY gtype='final', name",
                                (course["id"],)),
            "topics": topics,
        }

    def patch_course(self, q, body, cid):
        fields = {}
        if "color" in body:
            if not re.fullmatch(r"#[0-9a-fA-F]{6}", str(body["color"])):
                raise ApiError(400, "color must look like #1a2b3c")
            fields["color"] = body["color"]
        if "hidden" in body:
            fields["hidden"] = int(bool(body["hidden"]))
        if "target" in body:
            fields["target"] = None if body["target"] in (None, "") else _num(body["target"], "target", 0, 100)
        self.hub.update_course(_int(cid, "course"), **fields)
        return {"ok": True}

    def put_whatif(self, q, body, cid):
        course_id = _int(cid, "course")
        with self.hub.tx() as conn:
            for component, value in (body or {}).items():
                if value in (None, ""):
                    conn.execute("DELETE FROM whatif WHERE course_id=? AND component=?", (course_id, component))
                else:
                    conn.execute("INSERT OR REPLACE INTO whatif VALUES (?, ?, ?)",
                                 (course_id, str(component)[:120], _num(value, "what-if score", 0, 120)))
        course = self.hub.course(course_id)
        return course_standing(self.hub, course)

    # announcements and changes ---------------------------------------------------------
    def get_announcements(self, q, body):
        sql = ("SELECT a.*, courses.code AS course_code, courses.color AS course_color FROM announcements a "
               "LEFT JOIN courses ON courses.id = a.course_id WHERE courses.hidden IS NOT 1")
        params = []
        if q.get("course"):
            sql += " AND a.course_id=?"
            params.append(_int(q["course"], "course"))
        return {"announcements": self.hub.query(sql + " ORDER BY a.posted DESC LIMIT 200", params)}

    def post_announcement_read(self, q, body, uid):
        with self.hub.tx() as conn:
            conn.execute("UPDATE announcements SET read=? WHERE uid=?", (int(body.get("read", True)), uid))
        return {"ok": True}

    def get_changes(self, q, body):
        return {"changes": self.hub.changes(limit=200)}

    def post_changes_read(self, q, body):
        with self.hub.tx() as conn:
            conn.execute("UPDATE changes SET read=1")
        return {"ok": True}

    # sync --------------------------------------------------------------------------
    def get_sync(self, q, body):
        return self.app.sync_state

    def post_sync(self, q, body):
        state = self.app.sync_state
        if state["running"]:
            return state
        state.update(running=True, lines=[], started=to_iso(utcnow()), finished=None, ok=None)

        def work():
            try:
                result = full_refresh(self.hub, log=lambda m: state["lines"].append(m))
                state["ok"] = result.ok
            except Exception as exc:   # report anything to the UI rather than dying silently
                state["lines"].append(f"Sync failed: {exc}")
                state["ok"] = False
            finally:
                state["running"] = False
                state["finished"] = to_iso(utcnow())

        threading.Thread(target=work, daemon=True).start()
        return state

    # planner and time -------------------------------------------------------------------
    def get_plan(self, q, body):
        now = utcnow()
        start = q.get("start") or to_iso(now - timedelta(days=1))
        end = q.get("end") or to_iso(now + timedelta(days=14))
        blocks = self.hub.query("""SELECT plan_blocks.*, courses.code AS course_code, courses.color AS course_color
                                   FROM plan_blocks LEFT JOIN courses ON courses.id = plan_blocks.course_id
                                   WHERE plan_blocks.start >= ? AND plan_blocks.start < ? ORDER BY plan_blocks.start""", (start, end))
        work = planner.open_work(self.hub, now)
        scheduled = {}
        for row in self.hub.query("SELECT item_uid, SUM((julianday(end)-julianday(start))*24) h FROM plan_blocks "
                                  "WHERE done=0 AND start >= ? GROUP BY item_uid", (to_iso(now),)):
            scheduled[row["item_uid"]] = round(row["h"] or 0, 2)
        at_risk = [{"uid": w["uid"], "title": w["title"], "due": to_iso(w["due"]),
                    "unscheduled_hours": round(w["hours"] - scheduled.get(w["uid"], 0), 1)}
                   for w in work if w["hours"] - scheduled.get(w["uid"], 0) >= 0.5]
        items = {i["uid"]: i for i in self.hub.items(start=to_iso(now), kinds=("assignment", "quiz", "exam",
                                                                                   "manual"))}
        estimates = [{"uid": w["uid"], "title": w["title"], "due": to_iso(w["due"]), "remaining": w["hours"],
                      "estimate": items.get(w["uid"], {}).get("est_hours"),
                      "manual": bool(items.get(w["uid"], {}).get("est_manual")),
                      "logged": planner.logged_hours(self.hub, w["uid"]),
                      "course_code": items.get(w["uid"], {}).get("course_code"),
                      "course_color": items.get(w["uid"], {}).get("course_color")} for w in work]
        return {"blocks": blocks, "at_risk": at_risk, "estimates": estimates,
                "forecast": planner.forecast(self.hub, now), "settings": {
                    k: self.hub.get_setting(k) for k in ("availability", "daily_cap_hours", "block_minutes",
                                                         "finish_buffer_days", "weekly_goal_hours")}}

    def post_plan(self, q, body):
        plan = planner.replan(self.hub)
        return {"blocks": len(plan.blocks), "at_risk": plan.at_risk}

    def patch_block(self, q, body, bid):
        with self.hub.tx() as conn:
            conn.execute("UPDATE plan_blocks SET done=? WHERE id=?", (int(bool(body.get("done"))), _int(bid, "id")))
        return {"ok": True}

    def get_time(self, q, body):
        day = date.fromisoformat(q["week"]) if q.get("week") else utcnow().astimezone(local_tz()).date()
        report = planner.week_report(self.hub, week_monday(day))
        report["timer"] = planner.running_timer(self.hub)
        report["sessions"] = self.hub.query(
            """SELECT time_sessions.*, courses.code AS course_code, courses.color AS course_color,
                      items.title AS item_title FROM time_sessions
               LEFT JOIN courses ON courses.id = time_sessions.course_id
               LEFT JOIN items ON items.uid = time_sessions.item_uid
               ORDER BY time_sessions.start DESC LIMIT 30""")
        return report

    def post_time_start(self, q, body):
        course = _int(body["course_id"], "course_id") if body.get("course_id") else None
        uid = body.get("item_uid") or None
        if uid and not course:
            item = self.hub.item(uid)
            course = item["course_id"] if item else None
        planner.start_timer(self.hub, course, uid, note=str(body.get("note", "")))
        return {"timer": planner.running_timer(self.hub)}

    def post_time_stop(self, q, body):
        return {"stopped": planner.stop_timer(self.hub)}

    def post_time_log(self, q, body):
        start = parse_iso(body.get("start")) or utcnow()
        course = _int(body["course_id"], "course_id") if body.get("course_id") else None
        try:
            planner.log_time(self.hub, course, body.get("item_uid") or None, start,
                             _int(body.get("minutes"), "minutes"), str(body.get("note", "")))
        except ValueError as exc:
            raise ApiError(400, str(exc)) from None
        return {"ok": True}

    # notes, study and tutor -------------------------------------------------------------
    def _client(self, body):
        if body.get("local"):
            return None
        try:
            return self.app.client_factory()
        except LLMError as exc:
            raise ApiError(400, str(exc)) from None

    def _course_code(self, body) -> str | None:
        if not body.get("course"):
            return None
        course = self.hub.course(_int(body["course"], "course"))
        if not course:
            raise ApiError(404, "no such course")
        return course["code"]

    def post_ask(self, q, body):
        code = self._course_code(body)
        chunks = self.app.course_chunks(code)
        if not chunks:
            raise ApiError(400, "No notes indexed yet. Sync LEARN or add notes, then rebuild the index.")
        index = TfidfIndex(chunks) if code else self.app.chunks_and_index()[1]
        client = self._client(body)
        try:
            answer = answer_question(str(body.get("question", "")), index, client=client)
        except AnswerError as exc:
            raise ApiError(400, str(exc)) from None
        return {"text": answer.text, "mode": answer.mode, "label": answer.label, "citations": answer.citations,
                "hits": [{"id": h.chunk.id, "source": h.chunk.source, "heading": h.chunk.heading,
                          "score": round(h.score, 3), "text": h.chunk.text} for h in answer.hits]}

    def post_simplify(self, q, body):
        code = self._course_code(body)
        chunks = self.app.course_chunks(code)
        try:
            selection = select_chunks(chunks, query=body.get("query") or None, topic=body.get("topic") or None)
            if selection.refusal:
                return {"mode": "refusal", "header": "Not in your notes", "simple": "", "full": "",
                        "citations": [], "note": f"Not in your notes. {selection.refusal}",
                        "sources": [{"id": c.id, "source": c.source, "heading": c.heading}
                                    for c in selection.closest]}
            result = simplify(selection, client=self._client(body))
        except ExplainError as exc:
            raise ApiError(400, str(exc)) from None
        note = "" if result.mode == "llm" else ("Local mode: these are key sentences and the full notes quoted "
                                                "verbatim, not a simplification. Pick an AI provider in Settings.")
        return {"mode": result.mode, "header": result.header, "simple": result.simple, "full": result.full,
                "citations": result.citations, "note": note,
                "sources": [{"id": c.id, "source": c.source, "heading": c.heading} for c in selection.chunks]}

    def post_examples(self, q, body):
        code = self._course_code(body)
        chunks = self.app.course_chunks(code)
        concept = str(body.get("concept", "")).strip()
        try:
            selection = select_chunks(chunks, query=concept or None, topic=body.get("topic") or None)
            result = real_life_examples(concept, selection, client=self._client(body))
        except ExplainError as exc:
            raise ApiError(400, str(exc)) from None
        return {"mode": result.mode, "header": result.header, "note": result.note,
                "examples": [{"kind": e.kind, "text": e.text, "citations": e.citations,
                              "illustrates": e.illustrates} for e in result.examples]}

    def _tutor(self, fn, body, *fields):
        code = self._course_code(body)
        chunks = self.app.course_chunks(code)
        try:
            result = fn(*[str(body.get(f, "")) for f in fields], chunks, client=self._client(body))
        except TutorError as exc:
            raise ApiError(400, str(exc)) from None
        return {"mode": result.mode, "label": result.label, "data": result.data, "sources": result.sources}

    def post_tutor_walkthrough(self, q, body):
        return self._tutor(walkthrough, body, "problem")

    def post_tutor_check(self, q, body):
        return self._tutor(check_work, body, "problem", "attempt")

    def post_tutor_explain(self, q, body):
        return self._tutor(explain_back, body, "concept", "explanation")

    def get_chunk(self, q, body, chunk_id):
        chunks, _ = self.app.chunks_and_index()
        for chunk in chunks:
            if chunk.id == chunk_id:
                return chunk.to_dict()
        raise ApiError(404, "no such passage (the notes may have been re-indexed)")

    def get_study(self, q, body):
        with self.app.study_lock:
            db = self.app.study_db()
        today = utcnow().astimezone(local_tz()).date()
        titles = topic_titles(db)
        progress = topic_progress(db, sorted(titles), today)
        groups = due_digest(db, today)
        return {"topics": titles, "progress": [{"topic": p.topic, "attempts": p.stat.attempts,
                                                "accuracy": round(p.stat.correct / p.stat.attempts * 100)
                                                if p.stat.attempts else None, "trend": p.trend_text,
                                                "movement": p.movement, "advice": p.advice,
                                                "due": p.due} for p in progress],
                "due_cards": sum(len(g.cards) for g in groups),
                "due_topics": [{"topic": g.topic, "cards": len(g.cards)} for g in groups]}

    def post_quiz(self, q, body):
        n = _int(body.get("n", 5), "n")
        if not 1 <= n <= 20:
            raise ApiError(400, "n must be 1 to 20")
        code = self._course_code(body)
        chunks = self.app.course_chunks(code)
        topics = body.get("topics") or ([body["topic"]] if body.get("topic") else None)
        if topics:
            chunks = [c for c in chunks if c.topic in set(topics)]
        if not chunks:
            raise ApiError(400, "No notes for that selection yet.")
        client = self._client(body)
        today = utcnow().astimezone(local_tz()).date()
        try:
            if client is None:
                with self.app.study_lock:
                    attempts = len(self.app.study_db()["attempts"])
                items = local_quiz(chunks, None, n, seed=_int(body.get("seed", attempts), "seed"))
                label = "Local quiz (template questions from your definitions, no LLM)"
            else:
                items = llm_quiz(chunks, None, n, client)
                label = f"LLM quiz ({getattr(client, 'label', 'LLM')}), validated against your notes"
        except QuizError as exc:
            raise ApiError(400, str(exc)) from None
        with self.app.study_lock:
            db = self.app.study_db()
            for item in items:
                db["quiz_items"][item.id] = item.to_dict()
                db["cards"].setdefault(item.id, new_card(today).to_dict())
            save_db(self.hub.study_db, db)
        return {"label": label, "items": [item_public(i) for i in items]}

    def post_cram(self, q, body):
        with self.app.study_lock:
            db = self.app.study_db()
        code = self._course_code(body)
        chunks = self.app.course_chunks(code)
        topics = sorted({c.topic for c in chunks})
        if not topics:
            raise ApiError(400, "No notes yet.")
        weakest = cram_topics(topic_stats(db["attempts"], topics), set(topics), _int(body.get("topics", 2), "topics"))
        n = min(10, _int(body.get("n", 6), "n"))
        items: list[QuizItem] = []
        for topic, count in split_questions(weakest, n).items():
            try:
                items += local_quiz(chunks, topic, count, seed=len(db["attempts"]))
            except QuizError:
                continue
        if not items:
            raise ApiError(400, "Your notes have no definition lines to quiz on for these topics.")
        today = utcnow().astimezone(local_tz()).date()
        with self.app.study_lock:
            db = self.app.study_db()
            for item in items:
                db["quiz_items"][item.id] = item.to_dict()
                db["cards"].setdefault(item.id, new_card(today).to_dict())
            save_db(self.hub.study_db, db)
        return {"label": f"Cram: your weakest topics ({', '.join(weakest)})",
                "items": [item_public(i) for i in items]}

    def get_review(self, q, body):
        with self.app.study_lock:
            db = self.app.study_db()
        today = utcnow().astimezone(local_tz()).date()
        items = []
        for group in due_digest(db, today):
            for card in group.cards:
                items.append(item_public(QuizItem.from_dict(db["quiz_items"][card.item_id])))
        return {"items": items[:_int(q.get("limit", 20), "limit")]}

    def post_answer(self, q, body):
        letter = str(body.get("letter", "")).upper()
        if letter not in ("A", "B", "C", "D"):
            raise ApiError(400, "letter must be A-D")
        today = utcnow().astimezone(local_tz()).date()
        with self.app.study_lock:
            db = self.app.study_db()
            raw = db["quiz_items"].get(str(body.get("item_id")))
            if not raw:
                raise ApiError(404, "no such question")
            item = QuizItem.from_dict(raw)
            correct = item.correct_letter() == letter
            card = Card.from_dict(db["cards"].get(item.id) or new_card(today).to_dict())
            db["cards"][item.id] = sm2_schedule(card, quality_from_correct(correct), today).to_dict()
            db["attempts"].append({"item_id": item.id, "topic": item.topic, "correct": correct, "chosen": letter,
                                   "date": today.isoformat(), "session": "web"})
            save_db(self.hub.study_db, db)
        return {"correct": correct, "answer": item.correct_letter(), "answer_text": item.correct,
                "source_chunk_id": item.source_chunk_id, "next_due": db["cards"][item.id]["due"]}

    # exams --------------------------------------------------------------------------
    def get_exams(self, q, body):
        now = utcnow()
        exams = self.hub.items(start=to_iso(now - timedelta(days=1)), kinds=("exam",))
        return {"exams": exams}

    def post_exam_plan(self, q, body, uid):
        exam = self.hub.item(uid)
        if not exam or exam["kind"] != "exam":
            raise ApiError(404, "no such exam")
        with self.app.study_lock:
            db = self.app.study_db()
        prefix = course_slug(exam["course_code"] or "") + "-"
        titles = {t: v["title"] for t, v in topic_titles(db).items() if t.startswith(prefix)}
        topics = [t for t in (body.get("topics") or list(titles)) if t in titles]
        today = utcnow().astimezone(local_tz()).date()
        exam_day = parse_iso(exam["due"]).astimezone(local_tz()).date()
        minutes = _int(body.get("daily_minutes", 90), "daily_minutes")
        days = exam_plan(exam_day, today, topics, db["attempts"], titles, minutes)
        return {"exam": exam, "topics": [{"topic": t, "title": titles[t], "selected": t in topics} for t in titles],
                "days": plan_to_dict(days)}

    # misc -------------------------------------------------------------------------
    def get_search(self, q, body):
        _, index = self.app.chunks_and_index()
        return {"results": search(self.hub, q.get("q", ""), index)}

    def get_settings(self, q, body):
        settings = self.hub.settings()
        settings.pop("feed_token", None)
        return settings

    def put_settings(self, q, body):
        allowed = {"availability", "daily_cap_hours", "block_minutes", "finish_buffer_days", "alarms_minutes",
                   "notify_macos", "ntfy_url", "focus_minutes", "break_minutes", "weekly_goal_hours",
                   "feed_include_study", "provider", "d2l_ical_url"}
        for key, value in (body or {}).items():
            if key not in allowed:
                raise ApiError(400, f"unknown setting {key}")
            if key == "availability":
                if not isinstance(value, dict) or not all(
                        str(k) in {str(i) for i in range(7)} and isinstance(v, list) and all(
                            isinstance(w, list) and len(w) == 2 and all(re.fullmatch(r"\d{2}:\d{2}", x) for x in w)
                            and w[0] < w[1] for w in v) for k, v in value.items()):
                    raise ApiError(400, "availability must map weekday 0-6 to [[\"HH:MM\", \"HH:MM\"], ...]")
            elif key in ("daily_cap_hours", "weekly_goal_hours"):
                value = _num(value, key, 0.5, 100)
            elif key in ("block_minutes", "focus_minutes", "break_minutes"):
                value = int(_num(value, key, 5, 240))
            elif key == "finish_buffer_days":
                value = int(_num(value, key, 0, 7))
            elif key == "alarms_minutes":
                if not isinstance(value, list) or len(value) > 4:
                    raise ApiError(400, "alarms_minutes must be a list of up to 4 numbers")
                value = [int(_num(v, "alarm", 0, 20160)) for v in value]
            elif key == "provider":
                if value not in ("auto", "claude", "openai", "local"):
                    raise ApiError(400, "provider must be auto, claude, openai or local")
            elif key == "ntfy_url" and value:
                from .notify import check_ntfy_url
                try:
                    value = check_ntfy_url(value)
                except ValueError as exc:
                    raise ApiError(400, str(exc)) from None
            elif key in ("notify_macos", "feed_include_study"):
                value = bool(value)
            self.hub.set_setting(key, value)
        if {"availability", "daily_cap_hours", "block_minutes", "finish_buffer_days"} & set(body or {}):
            planner.replan(self.hub)
        return self.get_settings(q, body)

    def get_feed(self, q, body):
        token = self.hub.feed_token()
        base = f"http://{self.app.host if self.app.host not in ('0.0.0.0',) else '127.0.0.1'}:{self.app.port}"
        return {"feeds": {name: f"{base}/feed/{token}/{name}.ics" for name in FEEDS},
                "note": "Apple Calendar on this Mac can subscribe to these now. For your iPhone, the server must "
                        "be reachable from the phone (see README: Phone access)."}

    def post_feed_rotate(self, q, body):
        self.hub.feed_token(rotate=True)
        return self.get_feed(q, body)

    def get_digest(self, q, body):
        return {"markdown": digest(self.hub)}

    def post_import_ics(self, q, body):
        text = str(body.get("ics", ""))
        kind = body.get("as", "class")
        if kind not in ("class", "ical"):
            raise ApiError(400, "as must be class or ical")
        try:
            saved = sync_ical(self.hub, text, source=kind)
        except ICSError as exc:
            raise ApiError(400, str(exc)) from None
        planner.replan(self.hub)
        return {"saved": saved}

    def post_index(self, q, body):
        from .notes import build_index
        report = build_index(self.hub)
        return {"chunks": report.chunks, "files": report.files, "extracted": report.extracted,
                "skipped": report.skipped[:30]}


ROUTES = [
    ("GET", r"/api/state", "get_state"), ("GET", r"/api/today", "get_today"),
    ("GET", r"/api/items", "get_items"), ("POST", r"/api/items", "post_items"),
    ("GET", r"/api/items/(?P<a>[^/]+)", "get_item"), ("PATCH", r"/api/items/(?P<a>[^/]+)", "patch_item"),
    ("DELETE", r"/api/items/(?P<a>[^/]+)", "delete_item"),
    ("POST", r"/api/items/(?P<a>[^/]+)/subtasks", "post_subtasks"),
    ("PATCH", r"/api/subtasks/(?P<a>\d+)", "patch_subtask"), ("DELETE", r"/api/subtasks/(?P<a>\d+)", "delete_subtask"),
    ("GET", r"/api/courses/(?P<a>\d+)", "get_course"), ("PATCH", r"/api/courses/(?P<a>\d+)", "patch_course"),
    ("PUT", r"/api/courses/(?P<a>\d+)/whatif", "put_whatif"),
    ("GET", r"/api/announcements", "get_announcements"),
    ("POST", r"/api/announcements/(?P<a>[^/]+)/read", "post_announcement_read"),
    ("GET", r"/api/changes", "get_changes"), ("POST", r"/api/changes/read", "post_changes_read"),
    ("GET", r"/api/sync", "get_sync"), ("POST", r"/api/sync", "post_sync"), ("POST", r"/api/index", "post_index"),
    ("GET", r"/api/plan", "get_plan"), ("POST", r"/api/plan", "post_plan"),
    ("PATCH", r"/api/plan/(?P<a>\d+)", "patch_block"),
    ("GET", r"/api/time", "get_time"), ("POST", r"/api/time/start", "post_time_start"),
    ("POST", r"/api/time/stop", "post_time_stop"), ("POST", r"/api/time/log", "post_time_log"),
    ("POST", r"/api/ask", "post_ask"), ("POST", r"/api/simplify", "post_simplify"),
    ("POST", r"/api/examples", "post_examples"),
    ("POST", r"/api/tutor/walkthrough", "post_tutor_walkthrough"), ("POST", r"/api/tutor/check", "post_tutor_check"),
    ("POST", r"/api/tutor/explain", "post_tutor_explain"),
    ("GET", r"/api/chunks/(?P<a>[^/]+)", "get_chunk"),
    ("GET", r"/api/study", "get_study"), ("POST", r"/api/quiz", "post_quiz"), ("POST", r"/api/cram", "post_cram"),
    ("GET", r"/api/review", "get_review"), ("POST", r"/api/answer", "post_answer"),
    ("GET", r"/api/exams", "get_exams"), ("POST", r"/api/exams/(?P<a>[^/]+)/plan", "post_exam_plan"),
    ("GET", r"/api/search", "get_search"), ("GET", r"/api/settings", "get_settings"),
    ("PUT", r"/api/settings", "put_settings"), ("GET", r"/api/feed", "get_feed"),
    ("POST", r"/api/feed/rotate", "post_feed_rotate"), ("GET", r"/api/digest", "get_digest"),
    ("POST", r"/api/import-ics", "post_import_ics"),
]
COMPILED = [(method, re.compile(pattern + r"$"), name) for method, pattern, name in ROUTES]


def feed_items(hub: Hub, name: str) -> list[dict]:
    now = utcnow()
    start = to_iso(now - timedelta(days=30))
    if name == "study":
        rows = hub.query("""SELECT plan_blocks.*, courses.code AS course_code, courses.color AS course_color
                            FROM plan_blocks LEFT JOIN courses ON courses.id = plan_blocks.course_id
                            WHERE plan_blocks.start >= ? ORDER BY plan_blocks.start""", (to_iso(now - timedelta(days=7)),))
        return [{"uid": f"block:{r['id']}:{r['start']}", "kind": "study", "title": f"Study: {r['title']}",
                 "due": r["start"], "end": r["end"], "course_code": r["course_code"],
                 "course_color": r["course_color"], "status": "done" if r["done"] else "open"} for r in rows]
    kinds = {"deadlines": ("assignment", "quiz", "manual"), "exams": ("exam",), "classes": ("class",),
             "all": ("assignment", "quiz", "manual", "exam", "event")}[name]
    items = hub.items(start=start, kinds=kinds)
    if name == "all" and hub.get_setting("feed_include_study"):
        items += feed_items(hub, "study")
    return items


# ---------------------------------------------------------------- HTTP plumbing

def make_handler(app: App):
    routes = Routes(app)

    class Handler(BaseHTTPRequestHandler):
        server_version = "CourseCompass"
        sys_version = ""

        def log_message(self, fmt, *args):   # keep the terminal quiet; never log tokens in URLs
            return

        def _headers(self, status: int, content_type: str, length: int, extra: dict | None = None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Cache-Control", "no-store")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()

        def _json(self, status: int, payload) -> None:
            data = json.dumps(payload, default=str).encode("utf-8")
            self._headers(status, "application/json; charset=utf-8", len(data))
            self.wfile.write(data)

        def _error(self, status: int, message: str) -> None:
            self._json(status, {"error": message})

        def _dispatch(self, method: str) -> None:
            if not app.allowed_host(self.headers.get("Host")):
                self._error(421, "unexpected Host header")
                return
            url = urlparse(self.path)
            path = url.path
            try:
                if path.startswith("/api/"):
                    self._api(method, path, url.query)
                elif method == "GET" and path.startswith("/feed/"):
                    self._feed(path)
                elif method == "GET":
                    self._static(path)
                else:
                    self._error(405, "method not allowed")
            except ApiError as exc:
                self._error(exc.status, str(exc))
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as exc:   # never leak a traceback to the page, but keep it in the terminal
                traceback.print_exc()
                self._error(500, f"internal error: {type(exc).__name__}")

        def _api(self, method: str, path: str, query: str) -> None:
            if not hmac.compare_digest(self.headers.get("X-CC-Token", ""), app.token):
                raise ApiError(403, "missing or wrong API token: reload the page")
            body = {}
            if method in ("POST", "PUT", "PATCH", "DELETE"):
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    raise ApiError(413, "request too large")
                if length:
                    if "application/json" not in (self.headers.get("Content-Type") or ""):
                        raise ApiError(415, "send JSON")
                    try:
                        body = json.loads(self.rfile.read(length).decode("utf-8"))
                    except ValueError:
                        raise ApiError(400, "invalid JSON") from None
                    if not isinstance(body, dict):
                        raise ApiError(400, "JSON body must be an object")
            q = {k: v[-1] for k, v in parse_qs(query).items()}
            for verb, regex, name in COMPILED:
                match = regex.match(path)
                if match and verb == method:
                    args = [unquote(v) for v in match.groupdict().values()]
                    self._json(200, getattr(routes, name)(q, body, *args))
                    return
            raise ApiError(404, "no such API route")

        def _feed(self, path: str) -> None:
            match = re.fullmatch(r"/feed/([A-Za-z0-9_-]{16,64})/([a-z]+)\.ics", path)
            token = app.hub.feed_token()
            if not match or not hmac.compare_digest(match.group(1), token) or match.group(2) not in FEEDS:
                self._error(404, "not found")
                return
            name = match.group(2)
            body = build_feed(feed_items(app.hub, name), FEEDS[name],
                              alarms=app.hub.get_setting("alarms_minutes") if name != "classes" else []).encode()
            self._headers(200, "text/calendar; charset=utf-8", len(body),
                          {"Content-Disposition": f'inline; filename="course-compass-{name}.ics"'})
            self.wfile.write(body)

        def _static(self, path: str) -> None:
            if path in ("/", "/index.html"):
                page = (WEB_DIR / "index.html").read_text(encoding="utf-8")
                page = page.replace("__CC_TOKEN__", app.token)
                data = page.encode("utf-8")
                self._headers(200, "text/html; charset=utf-8", len(data))
                self.wfile.write(data)
                return
            if path.startswith("/files/"):
                self._file(path[len("/files/"):])
                return
            target = (WEB_DIR / path.lstrip("/")).resolve()
            if not target.is_relative_to(WEB_DIR) or not target.is_file():
                self._error(404, "not found")
                return
            data = target.read_bytes()
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if target.suffix == ".js":
                ctype = "text/javascript; charset=utf-8"
            elif target.suffix == ".webmanifest":
                ctype = "application/manifest+json"
            self._headers(200, ctype, len(data))
            self.wfile.write(data)

        def _file(self, uid: str) -> None:
            """Serve a downloaded LEARN file, needing the API token as a query parameter."""
            uid, _, query = unquote(uid).partition("?")
            token = parse_qs(urlparse(self.path).query).get("t", [""])[-1]
            if not hmac.compare_digest(token, app.token):
                self._error(403, "missing token")
                return
            row = app.hub.one("SELECT local_path, title FROM files WHERE uid=?", (uid,))
            root = app.hub.files_dir.resolve()
            if not row or not row["local_path"]:
                self._error(404, "file not downloaded")
                return
            target = Path(row["local_path"]).resolve()
            if not target.is_relative_to(root) or not target.is_file():
                self._error(404, "file not found")
                return
            data = target.read_bytes()
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype in ("text/html", "image/svg+xml", "application/xhtml+xml"):
                ctype = "text/plain; charset=utf-8"    # never render LEARN-supplied HTML/SVG as a live page
            self._headers(200, ctype, len(data), {"Content-Disposition": "inline"})
            self.wfile.write(data)

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_PUT(self):
            self._dispatch("PUT")

        def do_PATCH(self):
            self._dispatch("PATCH")

        def do_DELETE(self):
            self._dispatch("DELETE")

    return Handler


def make_server(app: App) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((app.host, app.port), make_handler(app))
    server.daemon_threads = True
    app.port = server.server_address[1]
    return server


def serve(hub: Hub, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True, out=None,
          extra_hosts: list[str] | None = None) -> None:
    app = App(hub, host, port, extra_hosts)
    server = make_server(app)
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '127.0.0.1') else host}:{app.port}/"
    if out:
        print(f"Course Compass is running at {url}  (Ctrl-C to stop)", file=out)
        if host not in ("127.0.0.1", "localhost", "::1"):
            print("WARNING: listening beyond this Mac. Anyone on the network who can reach this port can load "
                  "the page. Use a private network such as Tailscale, never a public one.", file=out)
    if open_browser:
        import webbrowser
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
