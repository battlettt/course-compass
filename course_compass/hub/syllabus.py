"""Course outline (syllabus) extractor: grading weights, exam and due dates, contacts, office hours, late policy.

Local and rule-based, so it works with no LLM and can be checked: every value
keeps the exact line it came from (`quote`, `line`), and the UI shows that
quote next to the value. Dates written without a year get the year that puts
them inside the term.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_MONTH = (r"(?P<month>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
          r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?")
_WEEKDAY = r"(?:(?:mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)[a-z]*\.?,?\s+)?"
DATE_RES = [
    re.compile(_WEEKDAY + _MONTH + r"\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(?P<year>20\d\d))?\b", re.I),
    re.compile(_WEEKDAY + r"(?P<day>\d{1,2})(?:st|nd|rd|th)?\s+" + _MONTH + r"(?:,?\s+(?P<year>20\d\d))?\b", re.I),
    re.compile(r"\b(?P<year>20\d\d)-(?P<mnum>\d{2})-(?P<day>\d{2})\b"),
]
TIME_RE = re.compile(
    r"\b(?P<h1>\d{1,2})(?::(?P<m1>\d{2}))?\s*(?P<ap1>[ap]\.?m\.?)?\s*(?:-|–|to)\s*"
    r"(?P<h2>\d{1,2})(?::(?P<m2>\d{2}))?\s*(?P<ap2>[ap]\.?m\.?)|"
    r"\b(?P<h>\d{1,2}):(?P<m>\d{2})\s*(?P<ap>[ap]\.?m\.?)?|\b(?P<hs>\d{1,2})\s*(?P<aps>[ap]\.?m\.?)", re.I)
PERCENT_RE = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d+)?)\s?%")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
NOT_A_COMPONENT = re.compile(
    r"\b(late|penalt|deduct|per day|per hour|must (?:achieve|pass|obtain|earn)|minimum|at least|pass(?:ing)?\b|"
    r"curve|bonus|extra credit|reweigh|shift(?:ed)?|transfer(?:red)?|instead|replac|of the class|students)", re.I)
EXAM_WORDS = re.compile(r"\b(midterm|mid-term|final exam|final examination|exam|test\s*\d*|quiz)\b", re.I)
WORK_WORDS = re.compile(r"\b(assignment|a\d+\b|project|report|lab|essay|paper|presentation|proposal|"
                        r"deliverable|homework|problem set|milestone)", re.I)
OFFICE_RE = re.compile(r"office hours?", re.I)
LATE_RE = re.compile(r"\blate\b.*\b(submission|penalt|policy|deduct|accepted|assignments?|work|per (?:day|hour)|lose|loses)\b|"
                     r"\b(submission|assignments?)\b.*\blate\b", re.I)
TEXTBOOK_RE = re.compile(r"\b(textbook|required text|course text|readings?:)", re.I)
INSTRUCTOR_RE = re.compile(r"\b(instructor|professor|prof\.|lecturer|teaching assistant|\bta\b|tas\b)", re.I)


@dataclass
class Component:
    name: str
    weight: float
    quote: str
    line: int
    date: str | None = None


@dataclass
class KeyDate:
    label: str
    date: str               # YYYY-MM-DD
    kind: str               # exam, work, other
    quote: str
    line: int
    start_time: str | None = None   # HH:MM local
    end_time: str | None = None
    weight: float | None = None


@dataclass
class Syllabus:
    components: list[Component] = field(default_factory=list)
    dates: list[KeyDate] = field(default_factory=list)
    contacts: list[dict] = field(default_factory=list)
    office_hours: list[dict] = field(default_factory=list)
    late_policy: list[dict] = field(default_factory=list)
    textbooks: list[dict] = field(default_factory=list)

    @property
    def total_weight(self) -> float:
        return round(sum(c.weight for c in self.components), 2)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["total_weight"] = self.total_weight
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Syllabus":
        return cls(components=[Component(**c) for c in data.get("components", [])],
                   dates=[KeyDate(**d) for d in data.get("dates", [])],
                   contacts=data.get("contacts", []), office_hours=data.get("office_hours", []),
                   late_policy=data.get("late_policy", []), textbooks=data.get("textbooks", []))


def _clean(line: str) -> str:
    return re.sub(r"\s+", " ", line.replace("|", " ").replace("\t", " ")).strip(" -:•*·,;")


def _infer_year(month: int, day: int, term_start: date | None, term_end: date | None, today: date) -> int:
    anchor_start = term_start or today - timedelta(days=150)
    anchor_end = term_end or today + timedelta(days=200)
    for year in sorted({anchor_start.year, anchor_end.year, today.year}):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if anchor_start - timedelta(days=45) <= candidate <= anchor_end + timedelta(days=45):
            return year
    return anchor_start.year


def find_dates(line: str, term_start: date | None = None, term_end: date | None = None,
               today: date | None = None) -> list[tuple[date, tuple[int, int]]]:
    today = today or date.today()
    found, taken = [], []
    for regex in DATE_RES:
        for match in regex.finditer(line):
            span = match.span()
            if any(s < span[1] and span[0] < e for s, e in taken):
                continue
            groups = match.groupdict()
            month = int(groups["mnum"]) if groups.get("mnum") else MONTHS[groups["month"][:3].lower()]
            day = int(groups["day"])
            year = int(groups["year"]) if groups.get("year") else _infer_year(month, day, term_start, term_end, today)
            try:
                found.append((date(year, month, day), span))
                taken.append(span)
            except ValueError:
                continue
    return sorted(found, key=lambda pair: pair[1][0])


def _hhmm(hour: str, minute: str | None, ampm: str | None, fallback_ampm: str | None = None) -> str | None:
    h, m = int(hour), int(minute or 0)
    marker = (ampm or fallback_ampm or "").lower().replace(".", "")
    if marker == "pm" and h < 12:
        h += 12
    elif marker == "am" and h == 12:
        h = 0
    elif not marker and 1 <= h <= 7:
        h += 12      # "3:30" in an exam line with no am/pm is an afternoon/evening time
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return f"{h:02d}:{m:02d}"


def find_times(text: str) -> tuple[str | None, str | None]:
    match = TIME_RE.search(text)
    if not match:
        return None, None
    g = match.groupdict()
    if g["h1"]:
        end = _hhmm(g["h2"], g["m2"], g["ap2"])
        start = _hhmm(g["h1"], g["m1"], g["ap1"], g["ap2"])
        return start, end
    if g["h"]:
        return _hhmm(g["h"], g["m"], g["ap"]), None
    return _hhmm(g["hs"], None, g["aps"]), None


def _label(line: str, spans: list[tuple[int, int]], keep_parens: bool = False) -> str:
    text = line
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + " " + text[end:]
    text = TIME_RE.sub(" ", text)
    text = PERCENT_RE.sub(" ", text)
    text = re.sub(r"\((?:\s|,|-)*\)", " ", text)
    text = re.sub(r"(?:\s*[,;:–-]\s*){2,}", " ", text)          # separators left behind by removed dates
    head, colon, _ = text.partition(":")
    if colon and len(re.findall(r"[A-Za-z]", head)) >= 3:
        text = head                                              # "Midterm Exam: <date>, <time>" -> "Midterm Exam"
    if not keep_parens:
        text = re.sub(r"\s*\([^)]*\)\s*$", "", text)              # trailing "(time set by the Registrar)"
    return _clean(re.sub(r"\b(due|on|at|date|by|weight(?:ing)?)\b\s*$", "", _clean(text), flags=re.I))[:120]


def parse_syllabus(text: str, term_start: date | None = None, term_end: date | None = None,
                   today: date | None = None) -> Syllabus:
    today = today or date.today()
    out = Syllabus()
    lines = text.splitlines()
    seen_components: set[str] = set()
    seen_dates: set[tuple[str, str]] = set()
    for number, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or len(line) > 400:
            continue
        quote = _clean(line)
        dates = find_dates(line, term_start, term_end, today)
        percents = PERCENT_RE.findall(line)

        # grading components: "Midterm exam ..... 25%"
        if percents and not NOT_A_COMPONENT.search(line):
            weight = float(percents[-1])
            name = _label(line, [span for _, span in dates], keep_parens=True)   # keep "(5)" item counts
            name = re.sub(r"^(\d+[.)]\s*)", "", name)
            key = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
            if 0 < weight <= 100 and re.search(r"[A-Za-z]{3}", name) and key not in seen_components \
                    and not re.search(r"\b(total|grade breakdown|weight)\b$", key):
                seen_components.add(key)
                out.components.append(Component(name=name, weight=weight, quote=quote, line=number,
                                                 date=dates[0][0].isoformat() if dates else None))

        # dated events: exams, deliverables
        for when, span in dates:
            label = _label(line, [s for _, s in dates]) or quote[:80]
            kind = "exam" if EXAM_WORDS.search(line) else "work" if WORK_WORDS.search(line) else "other"
            if kind == "other" and not re.search(r"\b(due|deadline|submit|reading week|holiday|last day|"
                                                 r"drop|break|no class|lecture)\b", line, re.I):
                continue
            key = (label.lower(), when.isoformat())
            if key in seen_dates:
                continue
            seen_dates.add(key)
            start_time, end_time = find_times(line[span[1]:]) if kind != "other" else (None, None)
            weight = float(percents[-1]) if percents and not NOT_A_COMPONENT.search(line) else None
            out.dates.append(KeyDate(label=label, date=when.isoformat(), kind=kind, quote=quote, line=number,
                                     start_time=start_time, end_time=end_time, weight=weight))

        for email in EMAIL_RE.findall(line):
            if not any(c["email"] == email for c in out.contacts):
                role = "TA" if re.search(r"\b(ta|tas|teaching assistant)\b", line, re.I) else \
                       "Instructor" if INSTRUCTOR_RE.search(line) or not out.contacts else "Contact"
                name = _clean(EMAIL_RE.sub("", line))
                name = re.sub(r"^(instructor|professor|prof\.?|lecturer|ta|teaching assistant|email|e-mail)s?\b[:\s]*",
                              "", name, flags=re.I)
                out.contacts.append({"email": email, "role": role, "name": name[:80], "line": number})
        if OFFICE_RE.search(line):
            following = " ".join(l.strip() for l in lines[number:number + 1] if l.strip() and len(l) < 200)
            detail = quote if re.search(r"\d", quote) or not following else f"{quote} {following}"
            out.office_hours.append({"quote": detail[:240], "line": number})
        if LATE_RE.search(line) and not line.startswith("#") and len(out.late_policy) < 4:
            out.late_policy.append({"quote": quote[:400], "line": number})
        if TEXTBOOK_RE.search(line) and len(out.textbooks) < 4:
            out.textbooks.append({"quote": quote[:240], "line": number})
    out.dates.sort(key=lambda d: (d.date, d.start_time or ""))
    return out


# ---------------------------------------------------------------- matching against LEARN

def _tokens(text: str) -> set[str]:
    words = set(re.findall(r"[a-z]+|\d+", text.lower()))
    return words - {"the", "a", "an", "of", "and", "due", "on", "in", "for", "submission", "folder"}


def similar(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def match_component(name: str, components: list[Component]) -> Component | None:
    """Map a LEARN grade item or work item name onto a syllabus component ("Assignment 3" -> "Assignments")."""
    lowered = name.lower()
    best, best_score = None, 0.0
    for component in components:
        comp = component.name.lower()
        stem = re.sub(r"s\b", "", re.sub(r"\(.*?\)", "", comp)).strip()
        score = similar(name, component.name)
        if stem and stem.split()[0] in lowered:
            score += 0.5
        if score > best_score:
            best, best_score = component, score
    return best if best_score >= 0.3 else None


def conflicts(parsed: Syllabus, items: list[dict], tz) -> list[dict]:
    """Syllabus dates that disagree with a similarly named LEARN item by more than a day."""
    found = []
    for key_date in parsed.dates:
        if key_date.kind == "other":
            continue
        syllabus_day = date.fromisoformat(key_date.date)
        for item in items:
            if item.get("source") != "d2l" or not item.get("due"):
                continue
            if similar(key_date.label, item["title"]) < 0.5:
                continue
            due = datetime.fromisoformat(item["due"].replace("Z", "+00:00")).astimezone(tz).date()
            if abs((due - syllabus_day).days) > 1:
                found.append({"label": key_date.label, "syllabus_date": key_date.date, "learn_date": due.isoformat(),
                              "item_uid": item["uid"], "quote": key_date.quote})
    return found
