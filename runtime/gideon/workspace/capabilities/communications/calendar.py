from __future__ import annotations

import asyncio
import hashlib
import json
import re
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from urllib.parse import quote, urlencode, urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiohttp import ClientError, ClientSession, ClientTimeout
from dateutil.rrule import rrulestr
from dateutil.tz import tzical

from gideon.core.config.credentials import get_credential

from .store import PeopleError, fields, instant, text

SOURCE_FIELDS = {"name", "kind", "calendar_id", "credential_ref", "timezone"}


def zone(name):
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise PeopleError("Unknown calendar timezone") from None


def schema(db):
    db.execute(
        "CREATE TABLE IF NOT EXISTS calendar_sources (id TEXT PRIMARY KEY,body TEXT NOT NULL,revision INTEGER NOT NULL)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS calendar_events (source_id TEXT NOT NULL,id TEXT NOT NULL,body TEXT NOT NULL,UNIQUE(source_id,id))"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS calendar_imports (source_id TEXT NOT NULL,digest TEXT NOT NULL,body TEXT NOT NULL,raw TEXT NOT NULL,UNIQUE(source_id,digest))"
    )


def sources(store):
    with closing(store.connect()) as db, db:
        schema(db)
        return [
            {**json.loads(row[0]), "revision": row[1]}
            for row in db.execute(
                "SELECT body,revision FROM calendar_sources ORDER BY rowid"
            )
        ]


def source(store, source_id):
    row = next((item for item in sources(store) if item["id"] == source_id), None)
    if not row:
        raise PeopleError("Calendar source not found", 404)
    return row


def save_source(store, data, source_id=None):
    fields(data, SOURCE_FIELDS | ({"revision"} if source_id else set()))
    body = {
        key: text(data.get(key, default), key, limit, required)
        for key, default, limit, required in [
            ("name", "", 200, True),
            ("calendar_id", "primary", 500, True),
            ("credential_ref", "", 120, False),
            ("timezone", "UTC", 100, True),
        ]
    }
    zone(body["timezone"])
    body["kind"] = data.get("kind")
    if body["kind"] not in ("ics", "google", "outlook"):
        raise PeopleError("Choose ICS, Google Calendar or Outlook")
    if body["kind"] != "ics" and not re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_]*", body["credential_ref"]
    ):
        raise PeopleError("Remote calendar requires an existing credential reference")
    revision = data.get("revision")
    if source_id and (type(revision) is not int or revision < 1):
        raise PeopleError("Calendar source revision is required")
    source_id = source_id or uuid4().hex
    with closing(store.connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        schema(db)
        old = db.execute(
            "SELECT body,revision FROM calendar_sources WHERE id=?", (source_id,)
        ).fetchone()
        if revision is not None and not old:
            raise PeopleError("Calendar source not found", 404)
        if old and old[1] != revision:
            raise PeopleError("Calendar source changed; reload", 409)
        if old and json.loads(old[0])["kind"] != body["kind"]:
            raise PeopleError("Calendar source kind is immutable", 409)
        body.update(id=source_id, sync={"state": "not_synced", "coverage": "unknown"})
        next_revision = old[1] + 1 if old else 1
        db.execute(
            "INSERT INTO calendar_sources VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body,revision=excluded.revision",
            (source_id, json.dumps(body), next_revision),
        )
        if old:
            db.execute("DELETE FROM calendar_events WHERE source_id=?", (source_id,))
    return {**body, "revision": next_revision}


def _local_time(value, params, default_zone, custom_zones=None):
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        return datetime.strptime(value, "%Y%m%d").date(), True
    if value.endswith("Z"):
        return (
            datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc),
            False,
        )
    observed = datetime.strptime(value, "%Y%m%dT%H%M%S")
    name = params.get("TZID", default_zone)
    tz = (custom_zones or {}).get(name) or zone(name)
    aware = observed.replace(tzinfo=tz)
    if (
        aware.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) != observed
        or aware.utcoffset() != observed.replace(tzinfo=tz, fold=1).utcoffset()
    ):
        raise PeopleError(
            "Ambiguous or nonexistent local calendar time; export UTC instants"
        )
    return aware, False


def _duration(value, all_day=False):
    match = re.fullmatch(
        r"P(?:(?P<weeks>\d+)W|(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?)",
        value,
    )
    if (
        not match
        or not any(part is not None for part in match.groupdict().values())
        or "T" in value
        and not any(
            match.group(name) is not None for name in ("hours", "minutes", "seconds")
        )
    ):
        raise PeopleError("Invalid RFC calendar duration")
    if all_day and any(
        match.group(name) is not None for name in ("hours", "minutes", "seconds")
    ):
        raise PeopleError("All-day duration must use days or weeks")
    duration = timedelta(
        **{name: int(part or 0) for name, part in match.groupdict().items()}
    )
    if duration <= timedelta(0):
        raise PeopleError("Calendar duration must be positive")
    return duration


def _local_event_times(raw, default_zone, custom_zones=None):
    start, all_day = _local_time(*raw["DTSTART"], default_zone, custom_zones)
    if "DTEND" in raw and "DURATION" in raw:
        raise PeopleError("Event cannot contain both DTEND and DURATION")
    if "DTEND" in raw:
        end, end_all_day = _local_time(*raw["DTEND"], default_zone, custom_zones)
        if end_all_day != all_day:
            raise PeopleError("Event start and end value types differ")
    elif "DURATION" in raw:
        end = start + _duration(raw["DURATION"][0], all_day)
    else:
        end = start + timedelta(days=1) if all_day else start
    if end < start:
        raise PeopleError("Calendar event ends before it starts")
    return start, end, all_day


def _event(raw, default_zone, identity=None, custom_zones=None):
    uid = text(raw.get("UID", ("", {}))[0], "UID", 500, True)
    if "DTSTART" not in raw:
        raise PeopleError("Event DTSTART is required")
    local_start, local_end, all_day = _local_event_times(
        raw, default_zone, custom_zones
    )
    start = (
        local_start.isoformat()
        if all_day
        else local_start.astimezone(timezone.utc).isoformat()
    )
    end = (
        local_end.isoformat()
        if all_day
        else local_end.astimezone(timezone.utc).isoformat()
    )

    def unescape(value):
        return (
            value.replace("\\n", "\n")
            .replace("\\N", "\n")
            .replace("\\,", ",")
            .replace("\\;", ";")
            .replace("\\\\", "\\")
        )

    return {
        "id": identity or uid,
        "uid": uid,
        "title": unescape(raw.get("SUMMARY", ("", {}))[0])[:1000],
        "location": unescape(raw.get("LOCATION", ("", {}))[0])[:2000],
        "start": start,
        "end": end,
        "all_day": all_day,
        "status": raw.get("STATUS", ("CONFIRMED", {}))[0].lower(),
        "recurrence_unexpanded": False,
    }


def _window(start, end, master_start):
    if start is None and end is None:
        beginning = master_start
        ending = beginning + timedelta(days=366)
    else:
        try:
            beginning = datetime.fromisoformat(start)
            ending = datetime.fromisoformat(end)
        except (TypeError, ValueError):
            raise PeopleError("Recurrence window requires ISO start and end") from None
        if beginning.tzinfo is None:
            beginning = beginning.replace(tzinfo=timezone.utc)
        if ending.tzinfo is None:
            ending = ending.replace(tzinfo=timezone.utc)
    if not beginning < ending or ending - beginning > timedelta(days=366):
        raise PeopleError("Recurrence window must be positive and at most 366 days")
    return beginning.astimezone(timezone.utc), ending.astimezone(timezone.utc)


def _rule(value, anchor):
    components = {}
    allowed = {
        "FREQ",
        "UNTIL",
        "COUNT",
        "INTERVAL",
        "BYSECOND",
        "BYMINUTE",
        "BYHOUR",
        "BYDAY",
        "BYMONTHDAY",
        "BYYEARDAY",
        "BYWEEKNO",
        "BYMONTH",
        "BYSETPOS",
        "WKST",
    }
    for item in value.split(";"):
        if "=" not in item:
            raise PeopleError("Invalid RRULE property")
        key, raw = item.split("=", 1)
        if key not in allowed or key in components:
            raise PeopleError("Unsupported or duplicate RRULE component: " + key)
        components[key] = raw
    if components.get("FREQ") not in {
        "SECONDLY",
        "MINUTELY",
        "HOURLY",
        "DAILY",
        "WEEKLY",
        "MONTHLY",
        "YEARLY",
    }:
        raise PeopleError("RRULE has an invalid FREQ")
    try:
        interval = int(components.get("INTERVAL", "1"))
        count = int(components["COUNT"]) if "COUNT" in components else None
    except (TypeError, ValueError):
        raise PeopleError(
            "RRULE interval and count must be positive integers"
        ) from None
    if interval < 1 or count is not None and count < 1:
        raise PeopleError("RRULE interval or count is outside supported bounds")
    try:
        return rrulestr(value, dtstart=anchor, forceset=False)
    except (TypeError, ValueError, OverflowError):
        raise PeopleError("Invalid RRULE property") from None


def _expand_rule(value, anchor, all_day, lower, upper):
    rule = _rule(value, anchor)
    threshold = lower.astimezone(anchor.tzinfo)
    candidates = []
    for occurrence in rule.xafter(threshold, count=5001, inc=True):
        occurrence_utc = occurrence.astimezone(timezone.utc)
        if occurrence_utc >= upper:
            break
        candidates.append(occurrence.date() if all_day else occurrence)
        if len(candidates) > 5000:
            raise PeopleError("RRULE expansion exceeds 5000 occurrences")
    return candidates


def _recurrence_order(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    return datetime.combine(value, datetime.min.time(), timezone.utc)


def _starts_in_window(item, lower, upper):
    value = (
        datetime.combine(
            date.fromisoformat(item["start"]), datetime.min.time(), timezone.utc
        )
        if item["all_day"]
        else datetime.fromisoformat(item["start"]).astimezone(timezone.utc)
    )
    return lower <= value < upper


def _occurrence(
    master,
    raw,
    start_local,
    original_key,
    default_zone,
    period_end=None,
    custom_zones=None,
    duration=None,
):
    base_start, base_end, all_day = _local_event_times(raw, default_zone, custom_zones)
    duration = duration if duration is not None else base_end - base_start
    if all_day:
        start = start_local.isoformat()
        end = (start_local + duration).isoformat()
    else:
        start = start_local.astimezone(timezone.utc).isoformat()
        end_local = period_end if period_end is not None else start_local + duration
        end = end_local.astimezone(timezone.utc).isoformat()
    return {
        **master,
        "id": master["uid"] + "#" + original_key,
        "start": start,
        "end": end,
        "recurring": True,
        "recurrence_id": original_key,
    }


def parse_ics(content, default_zone, window_start=None, window_end=None):
    content = text(content, "ICS content", 1048576, True)
    if len(content.encode()) > 1048576:
        raise PeopleError("Calendar export exceeds 1 MiB")
    lines = re.sub(r"\r?\n[ \t]", "", content).splitlines()
    if not lines or lines[0] != "BEGIN:VCALENDAR" or lines[-1] != "END:VCALENDAR":
        raise PeopleError("A complete VCALENDAR export is required")
    if "VERSION:2.0" not in lines:
        raise PeopleError("Calendar VERSION:2.0 is required")
    try:
        calendar_zones = tzical(StringIO("\n".join(lines)))
        custom_zones = {
            name: calendar_zones.get(name) for name in calendar_zones.keys()
        }
    except (TypeError, ValueError, OverflowError):
        raise PeopleError("Invalid embedded VTIMEZONE definition") from None
    rows, current, nested = [], None, 0
    for line in lines[1:-1]:
        if line == "BEGIN:VEVENT":
            if current is not None:
                raise PeopleError("Nested VEVENT is invalid")
            current, nested = {}, 0
        elif line == "END:VEVENT":
            if current is None or nested:
                raise PeopleError("Malformed VEVENT structure")
            rows.append(current)
            current = None
        elif current is not None:
            if line.startswith("BEGIN:"):
                nested += 1
                continue
            if line.startswith("END:"):
                nested -= 1
                continue
            if nested:
                continue
            if ":" not in line:
                raise PeopleError("Malformed calendar property")
            key, value = line.split(":", 1)
            tokens = key.split(";")
            name = tokens[0].upper()
            params = dict(token.split("=", 1) for token in tokens[1:] if "=" in token)
            if name in current and name in (
                "UID",
                "DTSTART",
                "DTEND",
                "DURATION",
                "SUMMARY",
                "RECURRENCE-ID",
                "RRULE",
            ):
                raise PeopleError("Duplicate calendar identity or time property")
            if name in ("RDATE", "EXDATE"):
                current.setdefault(name, []).append((value, params))
            else:
                current[name] = (value, params)
    if current is not None or len(rows) > 1000:
        raise PeopleError("Incomplete calendar event or more than 1000 events")
    events, seen, warnings = [], set(), []
    try:
        grouped = {}
        for raw in rows:
            uid = text(raw.get("UID", ("", {}))[0], "UID", 500, True)
            grouped.setdefault(uid, []).append(raw)
        for uid, group in grouped.items():
            masters = [raw for raw in group if "RECURRENCE-ID" not in raw]
            if len(masters) != 1:
                raise PeopleError(
                    "Each recurring UID requires exactly one master event"
                )
            raw = masters[0]
            master = _event(raw, default_zone, custom_zones=custom_zones)
            if not any(key in raw for key in ("RRULE", "RDATE", "EXDATE")):
                if master["id"] in seen:
                    raise PeopleError("Duplicate calendar event identity")
                seen.add(master["id"])
                events.append(master)
                continue
            base_local, all_day = _local_time(
                *raw["DTSTART"], default_zone, custom_zones
            )
            anchor = (
                datetime.combine(base_local, datetime.min.time(), timezone.utc)
                if all_day
                else base_local
            )
            lower, upper = _window(
                window_start, window_end, anchor.astimezone(timezone.utc)
            )
            exclusions = set()
            additions = []
            period_ends = {}
            for name, target in (("EXDATE", exclusions), ("RDATE", additions)):
                for value, params in raw.get(name, []):
                    for part in value.split(","):
                        if name == "RDATE" and (
                            params.get("VALUE") == "PERIOD" or "/" in part
                        ):
                            if "/" not in part:
                                raise PeopleError(
                                    "RDATE period requires a start and end or duration"
                                )
                            start_value, end_value = part.split("/", 1)
                            local_value, value_all_day = _local_time(
                                start_value, params, default_zone, custom_zones
                            )
                            if value_all_day or all_day:
                                raise PeopleError(
                                    "RDATE period requires a date-time start"
                                )
                            if end_value.startswith("P"):
                                period_end = local_value + _duration(end_value)
                            else:
                                period_end, end_all_day = _local_time(
                                    end_value, params, default_zone, custom_zones
                                )
                                if end_all_day:
                                    raise PeopleError(
                                        "RDATE period requires a date-time end"
                                    )
                            if period_end <= local_value:
                                raise PeopleError(
                                    "RDATE period end must follow its start"
                                )
                            period_ends[local_value.isoformat()] = period_end
                            additions.append(local_value)
                            continue
                        local_value, value_all_day = _local_time(
                            part, params, default_zone, custom_zones
                        )
                        if value_all_day != all_day:
                            raise PeopleError(name + " value type differs from DTSTART")
                        (
                            target.add(local_value.isoformat())
                            if name == "EXDATE"
                            else target.append(local_value)
                        )
            overrides, ranges, override_keys = {}, [], set()
            for override in (item for item in group if "RECURRENCE-ID" in item):
                recurrence_value, recurrence_all_day = _local_time(
                    *override["RECURRENCE-ID"], default_zone, custom_zones
                )
                if recurrence_all_day != all_day:
                    raise PeopleError("RECURRENCE-ID value type differs from DTSTART")
                key = recurrence_value.isoformat()
                if key in override_keys:
                    raise PeopleError("Duplicate recurrence override identity")
                override_keys.add(key)
                range_value = override["RECURRENCE-ID"][1].get("RANGE")
                if range_value and range_value != "THISANDFUTURE":
                    raise PeopleError("Unsupported RECURRENCE-ID RANGE value")
                if not range_value:
                    overrides[key] = override
                    continue
                cancelled = override.get("STATUS", ("", {}))[0].upper() == "CANCELLED"
                if cancelled:
                    shift, range_duration, range_master = timedelta(0), None, master
                else:
                    range_start, range_end, range_all_day = _local_event_times(
                        override, default_zone, custom_zones
                    )
                    if range_all_day != all_day:
                        raise PeopleError(
                            "RANGE override value type differs from DTSTART"
                        )
                    shift = range_start - recurrence_value
                    range_duration = range_end - range_start
                    range_master = _event(
                        override, default_zone, custom_zones=custom_zones
                    )
                ranges.append(
                    (
                        _recurrence_order(recurrence_value),
                        key,
                        shift,
                        range_duration,
                        range_master,
                        cancelled,
                    )
                )
            ranges.sort(key=lambda item: item[0])
            candidates = []
            if "RRULE" in raw:
                forward = max((entry[2] for entry in ranges), default=timedelta(0))
                backward = min((entry[2] for entry in ranges), default=timedelta(0))
                candidates.extend(
                    _expand_rule(
                        raw["RRULE"][0],
                        anchor,
                        all_day,
                        lower - max(forward, timedelta(0)),
                        upper - min(backward, timedelta(0)),
                    )
                )
            else:
                candidates.append(base_local)
            candidates.extend(additions)
            unique = {candidate.isoformat(): candidate for candidate in candidates}
            matched_ranges = set()
            for original_key, occurrence in sorted(unique.items()):
                if original_key in exclusions:
                    continue
                override = overrides.pop(original_key, None)
                if override:
                    if override.get("STATUS", ("", {}))[0].upper() == "CANCELLED":
                        continue
                    item = _event(
                        override, default_zone, uid + "#" + original_key, custom_zones
                    )
                    item.update(
                        recurring=True, recurrence_id=original_key, overridden=True
                    )
                else:
                    applicable = [
                        entry
                        for entry in ranges
                        if entry[0] <= _recurrence_order(occurrence)
                    ]
                    if applicable:
                        _, range_key, shift, range_duration, range_master, cancelled = (
                            applicable[-1]
                        )
                        matched_ranges.add(range_key)
                        if cancelled:
                            continue
                        occurrence = occurrence + shift
                        item = _occurrence(
                            range_master,
                            raw,
                            occurrence,
                            original_key,
                            default_zone,
                            custom_zones=custom_zones,
                            duration=range_duration,
                        )
                        item.update(range_applied=range_key, overridden=True)
                    else:
                        item = _occurrence(
                            master,
                            raw,
                            occurrence,
                            original_key,
                            default_zone,
                            period_ends.get(original_key),
                            custom_zones,
                        )
                if not _starts_in_window(item, lower, upper):
                    continue
                if item["id"] in seen:
                    raise PeopleError("Duplicate calendar occurrence identity")
                seen.add(item["id"])
                events.append(item)
            unmatched = sorted(
                list(overrides)
                + [entry[1] for entry in ranges if entry[1] not in matched_ranges]
            )
            if unmatched:
                warnings.append({"uid": uid, "unmatched_overrides": unmatched})
    except PeopleError:
        raise
    except (ValueError, TypeError, OverflowError):
        raise PeopleError("Invalid calendar date or property") from None
    return events, warnings


def persist(store, source_row, events, sync, raw="", digest_material=None):
    if len(events) > 5000:
        raise PeopleError("Calendar sync exceeds 5000 events")
    if len({event["id"] for event in events}) != len(events):
        raise PeopleError("Duplicate provider calendar event identity")
    with closing(store.connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        schema(db)
        current = db.execute(
            "SELECT revision FROM calendar_sources WHERE id=?", (source_row["id"],)
        ).fetchone()
        if not current or current[0] != source_row["revision"]:
            raise PeopleError("Calendar source changed during sync", 409)
        if sync["coverage"] != "partial":
            db.execute(
                "DELETE FROM calendar_events WHERE source_id=?", (source_row["id"],)
            )
        for event in events:
            db.execute(
                "INSERT INTO calendar_events VALUES (?,?,?) ON CONFLICT(source_id,id) DO UPDATE SET body=excluded.body",
                (source_row["id"], event["id"], json.dumps(event)),
            )
        body = {k: v for k, v in source_row.items() if k != "revision"}
        body["sync"] = sync
        db.execute(
            "UPDATE calendar_sources SET body=? WHERE id=?",
            (json.dumps(body), source_row["id"]),
        )
        if raw:
            digest = hashlib.sha256(
                (str(source_row["revision"]) + ":" + (digest_material or raw)).encode()
            ).hexdigest()
            db.execute(
                "INSERT INTO calendar_imports VALUES (?,?,?,?) ON CONFLICT(source_id,digest) DO NOTHING",
                (source_row["id"], digest, json.dumps(sync), raw),
            )
    return sync


def upload(store, source_id, data):
    fields(data, {"content", "revision", "window_start", "window_end"})
    row = source(store, source_id)
    if row["kind"] != "ics" or data.get("revision") != row["revision"]:
        raise PeopleError("ICS import requires the current ICS source revision", 409)
    content = data.get("content")
    text(content, "ICS content", 1048576, True)
    digest_material = json.dumps(
        [data.get("window_start"), data.get("window_end"), content],
        separators=(",", ":"),
    )
    digest = hashlib.sha256(
        (str(row["revision"]) + ":" + digest_material).encode()
    ).hexdigest()
    with closing(store.connect()) as db:
        previous = db.execute(
            "SELECT body FROM calendar_imports WHERE source_id=? AND digest=?",
            (source_id, digest),
        ).fetchone()
        if previous:
            return json.loads(previous[0])
    events, warnings = parse_ics(
        content, row["timezone"], data.get("window_start"), data.get("window_end")
    )
    sync = {
        "state": "synced",
        "coverage": "partial" if warnings else "available_snapshot",
        "scope": "uploaded_ics",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "warnings": warnings,
        "events": len(events),
        "source_digest": hashlib.sha256(content.encode()).hexdigest(),
        "source_revision": row["revision"],
        "start": data.get("window_start"),
        "end": data.get("window_end"),
    }
    return persist(store, row, events, sync, content, digest_material)


def provider_event(raw, kind):
    if not isinstance(raw, dict):
        raise PeopleError("Invalid calendar provider event")
    identity = text(raw.get("id"), "event identity", 500, True)
    google = kind == "google"
    start, end = raw.get("start"), raw.get("end")
    if not isinstance(start, dict) or not isinstance(end, dict):
        raise PeopleError("Calendar provider event has no start/end")
    all_day = bool(start.get("date")) if google else raw.get("isAllDay") is True

    def value(field):
        if all_day:
            return date.fromisoformat(
                (field.get("date") or field.get("dateTime", "")[:10])
            ).isoformat()
        raw_time = field.get("dateTime")
        if (
            not google
            and field.get("timeZone") == "UTC"
            and isinstance(raw_time, str)
            and not re.search(r"(Z|[+-]\d\d:\d\d)$", raw_time)
        ):
            raw_time += "Z"
        return instant(raw_time)

    try:
        beginning, ending = value(start), value(end)
    except (ValueError, TypeError):
        raise PeopleError("Invalid provider calendar time") from None
    if ending < beginning:
        raise PeopleError("Calendar event ends before it starts")
    location = raw.get("location", "")
    if not google:
        if location and not isinstance(location, dict):
            raise PeopleError("Invalid Outlook location")
        location = (location or {}).get("displayName", "")
    return {
        "id": identity,
        "uid": str(raw.get("iCalUID") or raw.get("iCalUId") or identity),
        "title": text(raw.get("summary" if google else "subject", ""), "title", 1000),
        "location": text(location, "location", 2000),
        "start": beginning,
        "end": ending,
        "all_day": all_day,
        "status": (
            "cancelled"
            if raw.get("isCancelled") or raw.get("status") == "cancelled"
            else "confirmed"
        ),
        "recurrence_unexpanded": False,
    }


async def sync_remote(store, source_id, data):
    row = source(store, source_id)
    try:
        return await _sync_remote(store, source_id, data)
    except PeopleError as exc:
        with closing(store.connect()) as db, db:
            body = {k: v for k, v in row.items() if k != "revision"}
            body["sync"] = {
                **row["sync"],
                "state": "failed",
                "coverage": "unknown",
                "error": str(exc),
            }
            db.execute(
                "UPDATE calendar_sources SET body=? WHERE id=? AND revision=?",
                (json.dumps(body), source_id, row["revision"]),
            )
        raise


async def _sync_remote(store, source_id, data):
    fields(data, {"start", "end"})
    row = source(store, source_id)
    beginning, ending = instant(data.get("start")), instant(data.get("end"))
    if not beginning < ending or (
        datetime.fromisoformat(ending) - datetime.fromisoformat(beginning)
    ) > timedelta(days=366):
        raise PeopleError("Calendar window must be positive and at most 366 days")
    if row["kind"] not in ("google", "outlook"):
        raise PeopleError("Upload an ICS export for this source")
    token = get_credential(row["credential_ref"])
    if not token:
        raise PeopleError("Calendar credential is unavailable", 503)
    google = row["kind"] == "google"
    base = (
        "https://www.googleapis.com/calendar/v3/calendars/"
        + quote(row["calendar_id"], safe="")
        + "/events"
        if google
        else "https://graph.microsoft.com/v1.0/me/"
        + (
            "calendar/calendarView"
            if row["calendar_id"] == "primary"
            else "calendars/" + quote(row["calendar_id"], safe="") + "/calendarView"
        )
    )
    params = (
        {
            "timeMin": beginning,
            "timeMax": ending,
            "singleEvents": "true",
            "maxResults": "500",
        }
        if google
        else {"startDateTime": beginning, "endDateTime": ending, "$top": "500"}
    )
    url = base + "?" + urlencode(params)
    events, visited = [], set()
    try:
        async with ClientSession(
            timeout=ClientTimeout(total=20),
            headers={
                "Authorization": "Bearer " + token,
                "Prefer": 'outlook.timezone="UTC"',
            },
        ) as client:
            while url:
                if url in visited or len(visited) >= 10:
                    raise PeopleError("Calendar pagination exceeded its bound", 502)
                if (
                    urlsplit(url).scheme != "https"
                    or urlsplit(url).netloc != urlsplit(base).netloc
                ):
                    raise PeopleError("Calendar pagination escaped its provider", 502)
                visited.add(url)
                async with client.get(url, allow_redirects=False) as response:
                    if response.status != 200:
                        raise PeopleError("Calendar provider refused the request", 502)
                    chunks, size = [], 0
                    async for chunk in response.content.iter_chunked(65536):
                        size += len(chunk)
                        if size > 2 * 1024 * 1024:
                            raise PeopleError(
                                "Calendar provider page is too large", 502
                            )
                        chunks.append(chunk)
                    page = json.loads(b"".join(chunks))
                if not isinstance(page, dict):
                    raise PeopleError("Invalid calendar provider page", 502)
                items = page.get("items" if google else "value")
                if not isinstance(items, list):
                    raise PeopleError("Calendar provider returned no event list", 502)
                events.extend(provider_event(event, row["kind"]) for event in items)
                if len(events) > 5000:
                    raise PeopleError("Calendar sync exceeds 5000 events")
                cursor = (
                    page.get("nextPageToken") if google else page.get("@odata.nextLink")
                )
                if cursor is not None and (
                    not isinstance(cursor, str) or len(cursor) > 8000
                ):
                    raise PeopleError("Invalid calendar pagination cursor", 502)
                url = (
                    base + "?" + urlencode({**params, "pageToken": cursor})
                    if google and cursor
                    else cursor
                )
        state = {
            "state": "synced",
            "coverage": "available_snapshot",
            "scope": "provider_window",
            "start": beginning,
            "end": ending,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "events": len(events),
            "warnings": [],
        }
        return persist(store, row, events, state)
    except (ClientError, asyncio.TimeoutError, ValueError, TypeError):
        raise PeopleError(
            "Calendar connection failed or returned invalid data", 503
        ) from None


def daily(store, day, timezone_name="UTC"):
    tz = zone(timezone_name)
    try:
        if not isinstance(day, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            raise ValueError()
        day_value = date.fromisoformat(day)
    except (ValueError, TypeError):
        raise PeopleError("Calendar review date must be YYYY-MM-DD") from None
    lower = (
        datetime.combine(day_value, datetime.min.time(), tz)
        .astimezone(timezone.utc)
        .isoformat()
    )
    upper = (
        datetime.combine(day_value + timedelta(days=1), datetime.min.time(), tz)
        .astimezone(timezone.utc)
        .isoformat()
    )
    source_rows = sources(store)
    by_id = {row["id"]: row for row in source_rows}
    events = []
    with closing(store.connect()) as db:
        for source_id, body in db.execute("SELECT source_id,body FROM calendar_events"):
            row = json.loads(body)
            start, end = row["start"], row["end"]
            present = (
                (start <= day < end)
                if row["all_day"]
                else (start < upper and end > lower)
                or (start == end and lower <= start < upper)
            )
            if present and row["status"] != "cancelled":
                events.append(
                    {
                        **row,
                        "source_id": source_id,
                        "source_kind": by_id[source_id]["kind"],
                    }
                )
    now = datetime.now(timezone.utc)
    for row in source_rows:
        sync = row["sync"]
        captured = sync.get("captured_at")
        row["review_coverage"] = sync["coverage"]
        if not captured or now - datetime.fromisoformat(captured) > timedelta(hours=24):
            row["review_coverage"] = "unknown"
        elif (
            sync.get("scope") in ("provider_window", "uploaded_ics")
            and sync.get("start")
            and not (sync["start"] <= lower and upper <= sync["end"])
        ):
            row["review_coverage"] = "unknown"
    coverage = (
        "unknown"
        if not source_rows
        else (
            "available_snapshot"
            if all(
                row["review_coverage"] == "available_snapshot" for row in source_rows
            )
            else "partial"
        )
    )
    return {
        "date": day,
        "timezone": timezone_name,
        "events": sorted(events, key=lambda row: (row["start"], row["id"])),
        "sources": source_rows,
        "coverage": coverage,
    }
