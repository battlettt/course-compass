"""Time helpers. Everything is stored as UTC ISO 8601 ("2026-11-24T04:59:00Z") and shown in local time."""

from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TZ = "America/Toronto"


def local_tz(name: str | None = None) -> ZoneInfo:
    name = name or os.environ.get("COURSE_COMPASS_TZ", "").strip() or DEFAULT_TZ
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"Unknown time zone {name!r} (COURSE_COMPASS_TZ).") from None


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def to_iso(dt: datetime | None) -> str | None:
    """UTC ISO string with a Z suffix, seconds precision. Naive datetimes are taken as local time."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=local_tz())
    return dt.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str | None) -> datetime | None:
    """Parse D2L / ISO timestamps ("2026-10-30T03:59:00.000Z", "+00:00" offsets, or a bare date).

    A bare date becomes 23:59 local time that day, the usual meaning of "due on <date>".
    """
    if not value:
        return None
    value = value.strip()
    if len(value) == 10:
        day = date.fromisoformat(value)
        return datetime.combine(day, time(23, 59), tzinfo=local_tz()).astimezone(timezone.utc)
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0)


def local(dt: datetime, tz: ZoneInfo | None = None) -> datetime:
    return dt.astimezone(tz or local_tz())


def local_day(value: str | datetime, tz: ZoneInfo | None = None) -> date:
    dt = parse_iso(value) if isinstance(value, str) else value
    return local(dt, tz).date()


def day_start(day: date, tz: ZoneInfo | None = None) -> datetime:
    return datetime.combine(day, time(0, 0), tzinfo=tz or local_tz())


def countdown(due: datetime, now: datetime) -> str:
    """Short human countdown: "overdue 2d", "in 3h", "in 5d"."""
    delta = due - now
    seconds = delta.total_seconds()
    sign = "overdue " if seconds < 0 else "in "
    seconds = abs(seconds)
    if seconds < 3600:
        amount = f"{max(1, int(seconds // 60))}m"
    elif seconds < 2 * 86400:
        amount = f"{int(seconds // 3600)}h"
    else:
        amount = f"{int(seconds // 86400)}d"
    return sign + amount


def week_monday(day: date) -> date:
    return day - timedelta(days=day.weekday())
