"""iCalendar in both directions.

Reading: LEARN's own calendar feed (Calendar > Subscribe in LEARN gives a
private .ics URL) and any timetable .ics, including weekly repeating classes
(RRULE FREQ=WEEKLY/DAILY with UNTIL or COUNT, BYDAY, EXDATE).

Writing: the subscription feed for Apple Calendar. Apple re-fetches a
subscribed calendar on its own schedule, so a due date that moves on LEARN
moves on the phone too. Events carry the course code, a link back, and
VALARM reminders.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..reminders import escape_text, fold_line, unescape_text
from .timeutil import local_tz, parse_iso, to_iso

PRODID = "-//Course Compass//School hub//EN"
MAX_ICS_BYTES = 5_000_000
MAX_OCCURRENCES = 400
WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
WINDOWS_TZ = {"Eastern Standard Time": "America/Toronto", "Pacific Standard Time": "America/Vancouver",
              "Central Standard Time": "America/Chicago", "Mountain Standard Time": "America/Edmonton"}


class ICSError(Exception):
    pass


@dataclass
class Prop:
    name: str
    params: dict[str, str]
    value: str


@dataclass
class Event:
    uid: str
    summary: str
    start: datetime
    end: datetime | None
    all_day: bool
    description: str = ""
    location: str = ""
    url: str = ""
    categories: list[str] = field(default_factory=list)


def unfold(text: str) -> list[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n[ \t]", "", text).split("\n")


def parse_line(line: str) -> Prop | None:
    # NAME;PARAM=VALUE;PARAM="quoted:value":VALUE  (a colon inside a quoted param is not the separator)
    match = re.match(r'^([A-Za-z0-9-]+)((?:;[A-Za-z0-9-]+=(?:"[^"]*"|[^;:]*))*):(.*)$', line)
    if not match:
        return None
    params = {}
    for raw in re.findall(r';([A-Za-z0-9-]+)=("[^"]*"|[^;:]*)', match.group(2)):
        params[raw[0].upper()] = raw[1].strip('"')
    return Prop(match.group(1).upper(), params, match.group(3))


def _zone(tzid: str | None) -> ZoneInfo | timezone:
    if not tzid:
        return local_tz()
    tzid = WINDOWS_TZ.get(tzid, tzid)
    try:
        return ZoneInfo(tzid)
    except (ZoneInfoNotFoundError, ValueError):
        return local_tz()


def parse_dt(prop: Prop) -> tuple[datetime, bool]:
    """(UTC datetime, all_day). All-day dates become local midnight."""
    value = prop.value.strip()
    if prop.params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        day = datetime.strptime(value[:8], "%Y%m%d").date()
        return datetime.combine(day, time(0, 0), tzinfo=local_tz()).astimezone(timezone.utc), True
    match = re.fullmatch(r"(\d{8})T(\d{6})(Z?)", value)
    if not match:
        raise ICSError(f"bad date-time {value!r}")
    naive = datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S")
    zone = timezone.utc if match.group(3) else _zone(prop.params.get("TZID"))
    return naive.replace(tzinfo=zone).astimezone(timezone.utc), False


def _rrule_dates(start: datetime, rule: str, exdates: set[datetime], tz) -> list[datetime]:
    parts = dict(part.split("=", 1) for part in rule.split(";") if "=" in part)
    freq = parts.get("FREQ", "").upper()
    if freq not in ("WEEKLY", "DAILY"):
        return [start]
    interval = max(1, int(parts.get("INTERVAL", "1") or 1))
    count = int(parts["COUNT"]) if parts.get("COUNT", "").isdigit() else None
    until = None
    if parts.get("UNTIL"):
        until = parse_dt(Prop("UNTIL", {}, parts["UNTIL"]))[0]
        if len(parts["UNTIL"]) == 8:
            until += timedelta(days=1)
    local_start = start.astimezone(tz)
    byday = [WEEKDAYS[d[-2:]] for d in parts.get("BYDAY", "").split(",") if d[-2:] in WEEKDAYS]
    out: list[datetime] = []
    if freq == "DAILY":
        step = timedelta(days=interval)
        current = local_start
        while len(out) < MAX_OCCURRENCES:
            candidate = current.astimezone(timezone.utc)
            if until and candidate > until or count is not None and len(out) >= count:
                break
            if candidate not in exdates:
                out.append(candidate)
            current = (current.replace(tzinfo=None) + step).replace(tzinfo=tz)
        return out
    days = byday or [local_start.weekday()]
    week = local_start.date() - timedelta(days=local_start.weekday())
    produced = 0
    for _ in range(MAX_OCCURRENCES):
        for weekday in sorted(days):
            day = week + timedelta(days=weekday)
            if day < local_start.date():
                continue
            candidate = datetime.combine(day, local_start.time().replace(tzinfo=None), tzinfo=tz)
            candidate = candidate.astimezone(timezone.utc)
            if until and candidate > until or count is not None and produced >= count:
                return out
            produced += 1
            if candidate not in exdates:
                out.append(candidate)
        week += timedelta(weeks=interval)
        if len(out) >= MAX_OCCURRENCES:
            break
    return out


def parse_calendar(text: str) -> list[Event]:
    if len(text) > MAX_ICS_BYTES:
        raise ICSError("calendar file is larger than 5 MB")
    if "BEGIN:VCALENDAR" not in text[:2000].upper():
        raise ICSError("not an iCalendar file (no BEGIN:VCALENDAR)")
    events: list[Event] = []
    current: list[Prop] | None = None
    depth = 0
    for line in unfold(text):
        if not line.strip():
            continue
        prop = parse_line(line)
        if prop is None:
            continue
        if prop.name == "BEGIN" and prop.value.upper() == "VEVENT":
            current, depth = [], 0
        elif current is not None and prop.name == "BEGIN":
            depth += 1                       # nested VALARM: ignore its properties
        elif current is not None and prop.name == "END" and prop.value.upper() != "VEVENT":
            depth -= 1
        elif current is not None and prop.name == "END":
            events.extend(_build_events(current))
            current = None
        elif current is not None and depth == 0:
            current.append(prop)
    return events


def _build_events(props: list[Prop]) -> list[Event]:
    by_name: dict[str, Prop] = {}
    exdates: set[datetime] = set()
    for prop in props:
        if prop.name == "EXDATE":
            for value in prop.value.split(","):
                try:
                    exdates.add(parse_dt(Prop("EXDATE", prop.params, value))[0])
                except ICSError:
                    pass
        else:
            by_name.setdefault(prop.name, prop)
    if "DTSTART" not in by_name:
        return []
    if by_name.get("STATUS") and by_name["STATUS"].value.upper() == "CANCELLED":
        return []
    start, all_day = parse_dt(by_name["DTSTART"])
    end = parse_dt(by_name["DTEND"])[0] if "DTEND" in by_name else None
    if end is None and "DURATION" in by_name:
        end = start + parse_duration(by_name["DURATION"].value)
    uid = by_name["UID"].value if "UID" in by_name else hashlib.sha1(
        (by_name.get("SUMMARY", Prop("", {}, "")).value + start.isoformat()).encode()).hexdigest()
    base = dict(
        summary=unescape_text(by_name["SUMMARY"].value) if "SUMMARY" in by_name else "(no title)",
        description=unescape_text(by_name["DESCRIPTION"].value) if "DESCRIPTION" in by_name else "",
        location=unescape_text(by_name["LOCATION"].value) if "LOCATION" in by_name else "",
        url=by_name["URL"].value if "URL" in by_name else "",
        categories=[c.strip() for c in unescape_text(by_name["CATEGORIES"].value).split(",")]
        if "CATEGORIES" in by_name else [],
    )
    if "RRULE" not in by_name:
        return [Event(uid=uid, start=start, end=end, all_day=all_day, **base)]
    tz = _zone(by_name["DTSTART"].params.get("TZID"))
    length = (end - start) if end else timedelta(0)
    return [Event(uid=f"{uid}/{occ.strftime('%Y%m%dT%H%M%SZ')}", start=occ, end=occ + length,
                  all_day=all_day, **base)
            for occ in _rrule_dates(start, by_name["RRULE"].value, exdates, tz)]


def parse_duration(value: str) -> timedelta:
    match = re.fullmatch(r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", value.strip())
    if not match:
        return timedelta(0)
    sign = -1 if match.group(1) == "-" else 1
    weeks, days, hours, minutes, seconds = (int(g or 0) for g in match.groups()[1:])
    return sign * timedelta(weeks=weeks, days=days, hours=hours, minutes=minutes, seconds=seconds)


# ---------------------------------------------------------------- writing the feed

def _stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def feed_uid(uid: str) -> str:
    return hashlib.sha1(uid.encode("utf-8")).hexdigest()[:20] + "@course-compass"


def _event_lines(item: dict, now: datetime, alarms: list[int]) -> list[str]:
    start = parse_iso(item["due"])
    lines = ["BEGIN:VEVENT", f"UID:{feed_uid(item['uid'])}", f"DTSTAMP:{_stamp(now)}"]
    kind = item["kind"]
    code = item.get("course_code") or ""
    if item.get("all_day"):
        day = start.astimezone(local_tz()).date()
        lines += [f"DTSTART;VALUE=DATE:{day.strftime('%Y%m%d')}",
                  f"DTEND;VALUE=DATE:{(day + timedelta(days=1)).strftime('%Y%m%d')}"]
    elif kind in ("assignment", "quiz", "manual") or not item.get("end"):
        # a deadline: a 15-minute block that ends at the due time, so it shows at the right spot
        lines += [f"DTSTART:{_stamp(start - timedelta(minutes=15))}", f"DTEND:{_stamp(start)}"]
    else:
        lines += [f"DTSTART:{_stamp(start)}", f"DTEND:{_stamp(parse_iso(item['end']))}"]
    if kind in ("assignment", "quiz", "manual"):
        summary = f"{code} · {item['title']} due".strip(" ·")
    elif kind == "study":
        summary = item["title"]
    else:
        summary = f"{code} · {item['title']}".strip(" ·")
    if item.get("status") in ("submitted", "done"):
        summary = "✓ " + summary
    lines.append(f"SUMMARY:{escape_text(summary)}")
    details = []
    if item.get("weight"):
        details.append(f"Worth {item['weight']:g}% of the final grade.")
    if item.get("details"):
        details.append(item["details"][:1500])
    if item.get("url"):
        details.append(item["url"])
    if details:
        lines.append(f"DESCRIPTION:{escape_text(chr(10).join(details))}")
    if item.get("location"):
        lines.append(f"LOCATION:{escape_text(item['location'])}")
    if item.get("url", "").startswith("https://"):
        lines.append(f"URL:{item['url']}")
    if code:
        lines.append(f"CATEGORIES:{escape_text(code)}")
    if item.get("course_color"):
        lines.append(f"COLOR:{item['course_color']}")
    lines.append("TRANSP:TRANSPARENT" if kind in ("assignment", "quiz", "manual") else "TRANSP:OPAQUE")
    if item.get("status") not in ("submitted", "done") and kind != "class":
        for minutes in alarms:
            lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{escape_text(summary)}",
                      f"TRIGGER:-PT{int(minutes)}M", "END:VALARM"]
    lines.append("END:VEVENT")
    return lines


def build_feed(items: list[dict], name: str, now: datetime | None = None,
               alarms: list[int] | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             f"X-WR-CALNAME:{escape_text(name)}", f"NAME:{escape_text(name)}",
             "REFRESH-INTERVAL;VALUE=DURATION:PT1H", "X-PUBLISHED-TTL:PT1H"]
    for item in items:
        if item.get("due"):
            lines += _event_lines(item, now, alarms if alarms is not None else [2880, 180])
    lines.append("END:VCALENDAR")
    return "".join(fold_line(line) + "\r\n" for line in lines)


def event_to_item(event: Event, source: str, course_id: int | None, kind: str) -> dict:
    return {"uid": f"{source}:{event.uid}", "course_id": course_id, "kind": kind, "title": event.summary[:300],
            "due": to_iso(event.start), "end": to_iso(event.end) if event.end else None,
            "all_day": event.all_day, "source": source, "url": event.url if event.url.startswith("https://") else "",
            "details": event.description[:4000], "location": event.location[:300]}
