"""Review reminders from the SM-2 queue: a due-today digest and an RFC 5545 (.ics) export.

Local data only, no LLM. The calendar has one all-day event per topic per due
date (a topic batch), because ten separate "review card" events on one day are
noise in a calendar; each event's description lists the questions in it.
Cards that are already overdue are put on `today`, since that is when they
can still be reviewed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from .review import Card, due_queue

PRODID = "-//Course Compass//Spaced review reminders//EN"
MAX_LINE_OCTETS = 75
DEFAULT_HORIZON_DAYS = 14


@dataclass
class DueCard:
    item_id: str
    topic: str
    question: str
    card: Card
    days_overdue: int

    @property
    def difficulty(self) -> str:
        """Plain words for the SM-2 state: new, hard (ease < 1.7), shaky (< 2.5) or steady."""
        if self.card.repetitions == 0 and self.card.interval == 0:
            return "new"
        if self.card.ease < 1.7:
            return "hard"
        if self.card.ease < 2.5:
            return "shaky"
        return "steady"


@dataclass
class TopicDue:
    topic: str
    cards: list[DueCard]

    @property
    def most_overdue(self) -> int:
        return max(card.days_overdue for card in self.cards)

    @property
    def hardest_ease(self) -> float:
        return min(card.card.ease for card in self.cards)


def live_cards(db: dict) -> dict[str, Card]:
    return {item_id: Card.from_dict(raw) for item_id, raw in db["cards"].items() if item_id in db["quiz_items"]}


def due_digest(db: dict, today: date) -> list[TopicDue]:
    """Cards due today or earlier grouped by topic.

    Topics are ordered most overdue first, then hardest (lowest ease), then
    name; cards inside a topic keep the `due_queue` order (overdue, then hard).
    """
    cards = live_cards(db)
    groups: dict[str, list[DueCard]] = {}
    for item_id in due_queue(cards, today):
        raw = db["quiz_items"][item_id]
        card = cards[item_id]
        overdue = (today - date.fromisoformat(card.due)).days
        groups.setdefault(raw["topic"], []).append(DueCard(item_id, raw["topic"], raw["question"], card, overdue))
    topics = [TopicDue(topic, items) for topic, items in groups.items()]
    return sorted(topics, key=lambda t: (-t.most_overdue, t.hardest_ease, t.topic))


def upcoming_count(db: dict, today: date, days: int) -> int:
    end = (today + timedelta(days=days)).isoformat()
    return sum(1 for card in live_cards(db).values() if today.isoformat() < card.due <= end)


def overdue_words(days: int) -> str:
    if days <= 0:
        return "due today"
    return f"{days} day{'s' if days != 1 else ''} overdue"


# ---------------------------------------------------------------- iCalendar

def escape_text(value: str) -> str:
    """RFC 5545 section 3.3.11 TEXT escaping."""
    return (value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n"))


def unescape_text(value: str) -> str:
    out, i = [], 0
    while i < len(value):
        if value[i] == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append("\n" if nxt in "nN" else nxt)
            i += 2
        else:
            out.append(value[i])
            i += 1
    return "".join(out)


def fold_line(line: str) -> str:
    """Fold to at most 75 octets per line (RFC 5545 section 3.1), never splitting a UTF-8 character."""
    parts, current, size = [], "", 0
    limit = MAX_LINE_OCTETS
    for char in line:
        width = len(char.encode("utf-8"))
        if size + width > limit:
            parts.append(current)
            current, size = "", 0
            limit = MAX_LINE_OCTETS - 1          # continuation lines start with one space
        current += char
        size += width
    parts.append(current)
    return "\r\n ".join(parts)


@dataclass
class ReviewEvent:
    day: date
    topic: str
    questions: list[str]
    overdue: int = 0

    @property
    def summary(self) -> str:
        count = len(self.questions)
        return f"Review {self.topic}: {count} card{'s' if count != 1 else ''}"

    @property
    def uid(self) -> str:
        digest = hashlib.sha1(f"{self.topic}\x00{self.day.isoformat()}".encode("utf-8")).hexdigest()[:16]
        return f"{digest}@course-compass"


def review_events(db: dict, today: date, days: int = DEFAULT_HORIZON_DAYS) -> list[ReviewEvent]:
    """One event per (topic, due date) from today to today + days; overdue cards land on today."""
    if days < 0:
        raise ValueError("--days must be 0 or more.")
    end = today + timedelta(days=days)
    batches: dict[tuple[date, str], ReviewEvent] = {}
    for item_id, card in sorted(live_cards(db).items(), key=lambda kv: (kv[1].due, kv[1].ease, kv[0])):
        due = date.fromisoformat(card.due)
        if due > end:
            continue
        raw = db["quiz_items"][item_id]
        day = max(due, today)
        event = batches.setdefault((day, raw["topic"]), ReviewEvent(day, raw["topic"], []))
        event.questions.append(raw["question"])
        event.overdue += int(due < today)
    return sorted(batches.values(), key=lambda e: (e.day, e.topic))


def to_ics(events: list[ReviewEvent], now: datetime | None = None) -> str:
    """Serialize events as an RFC 5545 VCALENDAR with CRLF line endings and folded lines."""
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN", "METHOD:PUBLISH"]
    for event in events:
        description = [f"{len(event.questions)} spaced-review card(s) from Course Compass (local data, no LLM)."]
        if event.overdue:
            description.append(f"{event.overdue} of them overdue.")
        description += [f"- {question}" for question in event.questions]
        description.append("Run: python3 -m course_compass review")
        lines += [
            "BEGIN:VEVENT",
            f"UID:{event.uid}",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{event.day.strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{(event.day + timedelta(days=1)).strftime('%Y%m%d')}",
            f"SUMMARY:{escape_text(event.summary)}",
            f"DESCRIPTION:{escape_text(chr(10).join(description))}",
            "CATEGORIES:Study",
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "".join(fold_line(line) + "\r\n" for line in lines)


def parse_ics(text: str) -> list[dict]:
    """Minimal reader for our own output: unfold, split VEVENTs, unescape TEXT values.

    Returns one dict per event mapping property name (parameters stripped) to value.
    Used by the tests to round-trip the export.
    """
    if "\r\n" not in text:
        raise ValueError("iCalendar lines must end with CRLF")
    unfolded = text.replace("\r\n ", "").replace("\r\n\t", "")
    events, current = [], None
    for line in unfolded.split("\r\n"):
        if not line:
            continue
        name_part, _, value = line.partition(":")
        name = name_part.split(";")[0].upper()
        if name == "BEGIN" and value == "VEVENT":
            current = {}
        elif name == "END" and value == "VEVENT":
            events.append(current)
            current = None
        elif current is not None:
            current[name] = unescape_text(value) if name in ("SUMMARY", "DESCRIPTION") else value
    return events
