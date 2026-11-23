"""Work estimates, a study-block scheduler, a workload forecast and time tracking.

Estimates start from the kind of work ("lab" 3 h, "project" 12 h, "midterm"
8 h of prep...), scale with the item's grade weight when it is known, and then
learn from you: once you have logged time on finished items in a course, the
median of actual / estimated hours for that course scales its new estimates.
An estimate you type in yourself is never changed.

The scheduler fills your free study windows (Settings > availability, minus
classes and other timed events) earliest-deadline-first, in blocks of up to
`block_minutes`, never more than `daily_cap_hours` a day and at most half a
day's cap on any one item, aiming to finish `finish_buffer_days` before each
deadline. Anything that does not fit is reported as at risk instead of being
quietly dropped.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

from .db import Hub
from .timeutil import local_tz, parse_iso, to_iso, utcnow, week_monday

BASE_HOURS = [
    (re.compile(r"\bfinal (exam|examination)\b|\bfinal\b", re.I), 14.0),
    (re.compile(r"\b(midterm|mid-term|term test|test \d)\b", re.I), 8.0),
    (re.compile(r"\b(project|capstone)\b", re.I), 12.0),
    (re.compile(r"\b(essay|paper|report|proposal)\b", re.I), 8.0),
    (re.compile(r"\b(presentation|demo)\b", re.I), 5.0),
    (re.compile(r"\b(lab|tutorial)\b", re.I), 3.0),
    (re.compile(r"\b(quiz|check-?in|participation|reflection|discussion)\b", re.I), 1.5),
    (re.compile(r"\b(reading|response)\b", re.I), 1.5),
    (re.compile(r"\b(assignment|homework|problem set|a\d+)\b", re.I), 6.0),
]
KIND_HOURS = {"assignment": 5.0, "quiz": 1.5, "exam": 8.0, "manual": 2.0}
MIN_BLOCK_MINUTES = 30


def base_estimate(item: dict) -> float:
    for pattern, hours in BASE_HOURS:
        if pattern.search(item.get("title", "")):
            base = hours
            break
    else:
        base = KIND_HOURS.get(item.get("kind"), 2.0)
    weight = item.get("weight")
    if weight:
        # exam prep grows more slowly with weight than coursework does: a 40% final is not 4x a 10% midterm
        top = 1.5 if item.get("kind") == "exam" or base >= 8.0 and "final" in item.get("title", "").lower() else 2.5
        base *= min(top, max(0.5, weight / 10))
    return round(base, 1)


def calibration(hub: Hub) -> dict[int, float]:
    """Per course: median of logged hours / estimated hours over finished items (clamped 0.5-2.0)."""
    ratios: dict[int, list[float]] = {}
    rows = hub.query("""SELECT items.uid, items.course_id, items.est_hours, items.title, items.kind, items.weight,
                               SUM((julianday(time_sessions.end) - julianday(time_sessions.start)) * 24) AS hours
                        FROM items JOIN time_sessions ON time_sessions.item_uid = items.uid
                        WHERE items.status IN ('done', 'submitted') AND time_sessions.end IS NOT NULL
                        GROUP BY items.uid""")
    for row in rows:
        estimate = row["est_hours"] or base_estimate(row)
        if row["hours"] and estimate:
            ratios.setdefault(row["course_id"], []).append(row["hours"] / estimate)
    return {course: min(2.0, max(0.5, statistics.median(values))) for course, values in ratios.items()
            if len(values) >= 2}


def estimate(item: dict, factors: dict[int, float]) -> float:
    if item.get("est_manual") and item.get("est_hours"):
        return float(item["est_hours"])
    return round(base_estimate(item) * factors.get(item.get("course_id"), 1.0), 1)


def logged_hours(hub: Hub, uid: str) -> float:
    row = hub.one("""SELECT SUM((julianday(end) - julianday(start)) * 24) AS hours FROM time_sessions
                     WHERE item_uid=? AND end IS NOT NULL""", (uid,))
    return round(row["hours"] or 0.0, 2) if row else 0.0


def refresh_estimates(hub: Hub) -> None:
    factors = calibration(hub)
    for item in hub.items(kinds=("assignment", "quiz", "exam", "manual")):
        if not item.get("est_manual"):
            value = estimate(item, factors)
            if value != item.get("est_hours"):
                hub.update_item(item["uid"], est_hours=value)


# ---------------------------------------------------------------- scheduling

@dataclass
class Block:
    item_uid: str
    course_id: int | None
    title: str
    start: datetime
    end: datetime
    kind: str = "work"

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


@dataclass
class Plan:
    blocks: list[Block] = field(default_factory=list)
    at_risk: list[dict] = field(default_factory=list)
    scheduled_hours: dict[str, float] = field(default_factory=dict)


def _hm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def free_windows(day: date, availability: dict, busy: list[tuple[datetime, datetime]], tz,
                 not_before: datetime) -> list[tuple[datetime, datetime]]:
    windows = []
    for start_s, end_s in availability.get(str(day.weekday()), []):
        start = datetime.combine(day, _hm(start_s), tzinfo=tz)
        end = datetime.combine(day, _hm(end_s), tzinfo=tz)
        start = max(start, not_before)
        if end > start:
            windows.append((start, end))
    for busy_start, busy_end in sorted(busy):
        next_windows = []
        for start, end in windows:
            if busy_end <= start or busy_start >= end:
                next_windows.append((start, end))
                continue
            if busy_start > start:
                next_windows.append((start, busy_start))
            if busy_end < end:
                next_windows.append((busy_end, end))
        windows = next_windows
    return [(s, e) for s, e in windows if (e - s).total_seconds() >= MIN_BLOCK_MINUTES * 60]


def schedule(work: list[dict], availability: dict, busy: list[tuple[datetime, datetime]], now: datetime,
             daily_cap_hours: float = 4.0, block_minutes: int = 90, buffer_days: int = 1,
             horizon_days: int = 60, tz=None) -> Plan:
    """work: dicts with uid, course_id, title, due (UTC datetime), hours (remaining). Earliest deadline first."""
    tz = tz or local_tz()
    now_local = now.astimezone(tz)
    # round up to the next quarter hour so blocks start on clean times
    minute = (now_local.minute // 15 + 1) * 15
    start_from = now_local.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=minute)
    remaining = {w["uid"]: w["hours"] * 60 for w in work if w["hours"] > 0}
    queue = sorted([w for w in work if w["uid"] in remaining], key=lambda w: (w["due"], -w["hours"]))
    plan = Plan()
    day = start_from.date()
    last_day = day + timedelta(days=horizon_days)
    while day <= last_day and any(m >= MIN_BLOCK_MINUTES for m in remaining.values()):
        day_left = daily_cap_hours * 60
        per_item_today: dict[str, float] = {}
        for win_start, win_end in free_windows(day, availability, busy, tz, start_from):
            cursor = win_start
            while cursor < win_end and day_left >= MIN_BLOCK_MINUTES:
                target = None
                for w in queue:
                    if remaining.get(w["uid"], 0) < MIN_BLOCK_MINUTES:
                        continue      # under half an hour left: not worth a block of its own
                    deadline = w["due"].astimezone(tz) - timedelta(days=buffer_days)
                    hard_deadline = w["due"].astimezone(tz)
                    if cursor >= hard_deadline:
                        continue
                    if per_item_today.get(w["uid"], 0) >= daily_cap_hours * 60 / 2 and cursor < deadline:
                        continue
                    target = w
                    break
                if target is None:
                    break
                hard_deadline = target["due"].astimezone(tz)
                length = min(block_minutes, remaining[target["uid"]], day_left,
                             (win_end - cursor).total_seconds() / 60,
                             (hard_deadline - cursor).total_seconds() / 60)
                length = (length // 15) * 15 if length >= MIN_BLOCK_MINUTES else 0
                if length <= 0 or cursor + timedelta(minutes=length) > win_end:
                    break
                end = cursor + timedelta(minutes=length)
                plan.blocks.append(Block(target["uid"], target.get("course_id"), target["title"],
                                         cursor.astimezone(timezone.utc), end.astimezone(timezone.utc),
                                         target.get("kind", "work")))
                remaining[target["uid"]] -= length
                per_item_today[target["uid"]] = per_item_today.get(target["uid"], 0) + length
                day_left -= length
                cursor = end + timedelta(minutes=10 if length >= 60 else 0)   # a short break between blocks
        day += timedelta(days=1)
    for w in queue:
        left = remaining.get(w["uid"], 0)
        planned = w["hours"] * 60 - left
        plan.scheduled_hours[w["uid"]] = round(planned / 60, 2)
        if left >= MIN_BLOCK_MINUTES:
            plan.at_risk.append({"uid": w["uid"], "title": w["title"], "course_id": w.get("course_id"),
                                 "due": to_iso(w["due"]), "unscheduled_hours": round(left / 60, 1)})
    plan.blocks.sort(key=lambda b: b.start)
    return plan


def busy_times(hub: Hub, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    rows = hub.items(start=to_iso(start - timedelta(days=1)), end=to_iso(end), kinds=("class", "event", "exam"))
    out = []
    for row in rows:
        if row.get("all_day") or not row.get("end"):
            continue
        out.append((parse_iso(row["due"]), parse_iso(row["end"])))
    return out


def open_work(hub: Hub, now: datetime) -> list[dict]:
    """Unfinished items due in the future with the hours still to do (estimate minus time logged)."""
    refresh_estimates(hub)
    work = []
    for item in hub.items(start=to_iso(now), kinds=("assignment", "quiz", "exam", "manual")):
        if item["status"] != "open":
            continue
        est = item.get("est_hours") or base_estimate(item)
        done_blocks = hub.one("""SELECT SUM((julianday(end) - julianday(start)) * 24) AS h FROM plan_blocks
                                 WHERE item_uid=? AND done=1""", (item["uid"],))["h"] or 0.0
        left = max(0.0, est - max(logged_hours(hub, item["uid"]), done_blocks))
        title = (f"Prep: {item['course_code']} {item['title']}" if item["kind"] == "exam"
                 else f"{item['course_code'] or ''} {item['title']}".strip())
        work.append({"uid": item["uid"], "course_id": item["course_id"], "title": title,
                     "due": parse_iso(item["due"]), "hours": round(left, 2),
                     "kind": "exam-prep" if item["kind"] == "exam" else "work"})
    return work


def replan(hub: Hub, now: datetime | None = None, horizon_days: int = 60) -> Plan:
    """Rebuild future blocks (finished blocks and past blocks are kept) and save them."""
    now = now or utcnow()
    settings = hub.settings()
    work = open_work(hub, now)
    busy = busy_times(hub, now, now + timedelta(days=horizon_days))
    plan = schedule(work, settings["availability"], busy, now, float(settings["daily_cap_hours"]),
                    int(settings["block_minutes"]), int(settings["finish_buffer_days"]), horizon_days)
    with hub.tx() as conn:
        conn.execute("DELETE FROM plan_blocks WHERE done=0 AND start >= ?", (to_iso(now),))
        for block in plan.blocks:
            conn.execute("INSERT INTO plan_blocks (item_uid, course_id, title, start, end, kind) "
                         "VALUES (?, ?, ?, ?, ?, ?)",
                         (block.item_uid, block.course_id, block.title, to_iso(block.start), to_iso(block.end),
                          block.kind))
    return plan


def forecast(hub: Hub, now: datetime | None = None, weeks: int = 8) -> list[dict]:
    """Estimated hours of work due in each of the next weeks, against your weekly goal."""
    now = now or utcnow()
    tz = local_tz()
    goal = float(hub.get_setting("weekly_goal_hours"))
    monday = week_monday(now.astimezone(tz).date())
    out = []
    work = open_work(hub, now)
    for index in range(weeks):
        start = monday + timedelta(weeks=index)
        end = start + timedelta(days=7)
        due = [w for w in work if start <= w["due"].astimezone(tz).date() < end]
        hours = round(sum(w["hours"] for w in due), 1)
        out.append({"week_of": start.isoformat(), "hours": hours, "items": len(due), "goal": goal,
                    "crunch": hours > goal})
    return out


# ---------------------------------------------------------------- time tracking

def start_timer(hub: Hub, course_id: int | None, item_uid: str | None, now: datetime | None = None,
                note: str = "") -> int:
    now = now or utcnow()
    stop_timer(hub, now)
    with hub.tx() as conn:
        return conn.execute("INSERT INTO time_sessions (course_id, item_uid, start, note) VALUES (?, ?, ?, ?)",
                            (course_id, item_uid, to_iso(now), note[:200])).lastrowid


def stop_timer(hub: Hub, now: datetime | None = None) -> dict | None:
    now = now or utcnow()
    running = hub.one("SELECT * FROM time_sessions WHERE end IS NULL ORDER BY id DESC LIMIT 1")
    if not running:
        return None
    with hub.tx() as conn:
        conn.execute("UPDATE time_sessions SET end=? WHERE end IS NULL", (to_iso(now),))
    running["end"] = to_iso(now)
    return running


def running_timer(hub: Hub) -> dict | None:
    return hub.one("""SELECT time_sessions.*, courses.code AS course_code, items.title AS item_title
                      FROM time_sessions LEFT JOIN courses ON courses.id = time_sessions.course_id
                      LEFT JOIN items ON items.uid = time_sessions.item_uid
                      WHERE time_sessions.end IS NULL ORDER BY time_sessions.id DESC LIMIT 1""")


def log_time(hub: Hub, course_id: int | None, item_uid: str | None, start: datetime, minutes: int,
             note: str = "") -> None:
    if not 1 <= minutes <= 16 * 60:
        raise ValueError("minutes must be between 1 and 960")
    with hub.tx() as conn:
        conn.execute("INSERT INTO time_sessions (course_id, item_uid, start, end, kind, note) "
                     "VALUES (?, ?, ?, ?, 'manual', ?)",
                     (course_id, item_uid, to_iso(start), to_iso(start + timedelta(minutes=minutes)), note[:200]))


def week_report(hub: Hub, week_start: date, tz=None) -> dict:
    """Planned vs actual hours per course for the week starting on `week_start` (local Monday)."""
    tz = tz or local_tz()
    start = datetime.combine(week_start, time(0, 0), tzinfo=tz)
    end = start + timedelta(days=7)
    s, e = to_iso(start), to_iso(end)
    actual = hub.query("""SELECT course_id, SUM((julianday(COALESCE(end, ?)) - julianday(start)) * 24) AS hours
                          FROM time_sessions WHERE start >= ? AND start < ? GROUP BY course_id""",
                       (to_iso(utcnow()), s, e))
    planned = hub.query("""SELECT course_id, SUM((julianday(end) - julianday(start)) * 24) AS hours
                           FROM plan_blocks WHERE start >= ? AND start < ? GROUP BY course_id""", (s, e))
    by_day = hub.query("""SELECT start, end FROM time_sessions WHERE start >= ? AND start < ?""", (s, e))
    days = [0.0] * 7
    for row in by_day:
        begin = parse_iso(row["start"])
        finish = parse_iso(row["end"]) if row["end"] else utcnow()
        index = (begin.astimezone(tz).date() - week_start).days
        if 0 <= index < 7:
            days[index] += (finish - begin).total_seconds() / 3600
    courses = {c["id"]: c for c in hub.courses(include_hidden=True)}
    rows = {}
    for row in actual:
        rows.setdefault(row["course_id"], {"planned": 0.0, "actual": 0.0})["actual"] = round(row["hours"] or 0, 2)
    for row in planned:
        rows.setdefault(row["course_id"], {"planned": 0.0, "actual": 0.0})["planned"] = round(row["hours"] or 0, 2)
    out = []
    for course_id, values in rows.items():
        course = courses.get(course_id) or {}
        out.append({"course_id": course_id, "code": course.get("code", "Other"),
                    "color": course.get("color", "#64748b"), **values})
    out.sort(key=lambda r: -(r["actual"] + r["planned"]))
    return {"week_of": week_start.isoformat(), "courses": out, "by_day": [round(d, 2) for d in days],
            "total_actual": round(sum(r["actual"] for r in out), 2),
            "total_planned": round(sum(r["planned"] for r in out), 2),
            "goal": float(hub.get_setting("weekly_goal_hours"))}
