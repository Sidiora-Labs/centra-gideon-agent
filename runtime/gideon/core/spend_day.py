"""Spend dates and boundaries in the configured scheduler timezone.

Ledger stamps remain UTC instants. A day spans successive local midnights,
including 23- and 25-hour daylight-saving days.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone, tzinfo
from gideon.core.timezones import resolve_zone
DAY_FORMAT = "%Y-%m-%d"

def zone() -> tzinfo:
    return resolve_zone()

def today(tz: tzinfo | None = None) -> str:
    return datetime.now(zone() if tz is None else tz).strftime(DAY_FORMAT)

def next_day_starts(tz: tzinfo | None = None) -> float:
    tz = zone() if tz is None else tz
    tomorrow = datetime.now(tz).date() + timedelta(days=1)
    return datetime.combine(tomorrow, datetime.min.time(), tzinfo=tz).timestamp()

def day_of(ts: object, tz: tzinfo | None = None) -> str:
    try:
        stamp = datetime.fromisoformat(str(ts))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(zone() if tz is None else tz).strftime(DAY_FORMAT)
    except (TypeError, ValueError, OverflowError):
        return ""

def day_of_epoch(ts: object, tz: tzinfo | None = None) -> str:
    try:
        return datetime.fromtimestamp(float(ts), zone() if tz is None else tz).strftime(DAY_FORMAT)
    except (TypeError, ValueError, OSError, OverflowError):
        return ""

def days_ending(day: str, count: int) -> list[str]:
    end = datetime.strptime(day, DAY_FORMAT)
    return [(end - timedelta(days=n)).strftime(DAY_FORMAT) for n in range(count - 1, -1, -1)]

def start_of(day: str, tz: tzinfo | None = None) -> str:
    date = datetime.strptime(day, DAY_FORMAT).date()
    midnight = datetime.combine(date, datetime.min.time(), tzinfo=zone() if tz is None else tz)
    return midnight.astimezone(timezone.utc).isoformat()
