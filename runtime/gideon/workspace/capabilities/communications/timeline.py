from __future__ import annotations

import base64
import hashlib
import json
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .store import PeopleError, fields

KINDS = ("touchpoint", "message", "calendar", "social", "assignment")
SCAN_LIMIT = 10000


def read(store, data):
    fields(data, {"date", "timezone", "person_id", "kind", "limit", "cursor"})
    day = data.get("date")
    timezone_name = data.get("timezone", "UTC")
    try:
        if not isinstance(day, str) or len(day) != 10:
            raise ValueError()
        day_value = date.fromisoformat(day)
        tz = ZoneInfo(timezone_name)
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        raise PeopleError(
            "Timeline needs YYYY-MM-DD date and a valid timezone"
        ) from None
    person = data.get("person_id") or None
    kind = data.get("kind") or None
    if person:
        store.get(person)
    if kind is not None and kind not in KINDS:
        raise PeopleError("Unknown timeline kind")
    limit = data.get("limit", 50)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise PeopleError("Timeline page limit must be 1 to 100")
    try:
        lower = datetime.combine(day_value, datetime.min.time(), tz).astimezone(
            timezone.utc
        )
        upper = datetime.combine(
            day_value + timedelta(days=1), datetime.min.time(), tz
        ).astimezone(timezone.utc)
    except (OverflowError, ValueError):
        raise PeopleError("Timeline date is outside the supported range") from None
    fingerprint = hashlib.sha256(
        json.dumps([day, timezone_name, person, kind]).encode()
    ).hexdigest()
    after = None
    cursor = data.get("cursor")
    if cursor:
        try:
            if not isinstance(cursor, str) or len(cursor) > 2000:
                raise ValueError()
            decoded = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            if (
                decoded["query"] != fingerprint
                or not isinstance(decoded["at"], str)
                or not isinstance(decoded["id"], str)
            ):
                raise ValueError()
            after = (decoded["at"], decoded["id"])
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise PeopleError("Timeline cursor does not match this review") from None
    rows = []
    truncated = False
    available = []

    def append(
        identity,
        category,
        at,
        summary,
        person_id,
        source,
        params,
        qualification="recorded_local_event",
        calendar_end=None,
    ):
        if kind and kind != category or person and person != person_id:
            return
        instant = datetime.fromisoformat(at)
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=tz)
        instant = instant.astimezone(timezone.utc)
        if calendar_end:
            ending = datetime.fromisoformat(calendar_end)
            if ending.tzinfo is None:
                ending = ending.replace(tzinfo=tz)
            ending = ending.astimezone(timezone.utc)
            if (ending == instant and not lower <= instant < upper) or (
                ending != instant and (ending <= lower or instant >= upper)
            ):
                return
            instant = max(instant, lower)
        elif not lower <= instant < upper:
            return
        rows.append(
            {
                "id": identity,
                "kind": category,
                "occurred_at": instant.isoformat(),
                "summary": summary,
                "person_id": person_id,
                "source": source,
                "href": "#/capabilities/communications?" + urlencode(params),
                "qualification": qualification,
            }
        )

    with closing(store.connect()) as db:
        tables = {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }

        def scan(table, columns, order):
            nonlocal truncated
            if table not in tables:
                return []
            available.append(table)
            found = db.execute(
                f"SELECT {columns} FROM {table} ORDER BY {order} DESC LIMIT ?",
                (SCAN_LIMIT + 1,),
            ).fetchall()
            if len(found) > SCAN_LIMIT:
                truncated = True
            return found[:SCAN_LIMIT]

        for identity, body in scan(
            "touchpoints", "id,body", "json_extract(body,'$.occurred_at')"
        ):
            row = json.loads(body)
            append(
                "touchpoint:" + identity,
                "touchpoint",
                row["occurred_at"],
                row["summary"],
                row["person_id"],
                row["source"],
                {"person": row["person_id"]},
            )
        for source, account, identity, body in scan(
            "relationship_messages",
            "source,source_account_id,external_id,body",
            "json_extract(body,'$.occurred_at')",
        ):
            row = json.loads(body)
            event_id = hashlib.sha256(
                json.dumps([source, account, identity]).encode()
            ).hexdigest()
            append(
                "message:" + event_id,
                "message",
                row["occurred_at"],
                row["summary"],
                row["person_id"],
                source,
                {"person": row["person_id"]},
                "recorded_message_not_complete_inbox",
            )
        for source, identity, body in scan(
            "calendar_events", "source_id,id,body", "json_extract(body,'$.start')"
        ):
            row = json.loads(body)
            if row["status"] != "cancelled":
                append(
                    "calendar:" + source + ":" + identity,
                    "calendar",
                    row["start"],
                    row["title"],
                    None,
                    "calendar",
                    {"calendar_source": source},
                    "scheduled_not_attendance",
                    calendar_end=row["end"],
                )
        for identity, account_id, event, at, body in scan(
            "social_history", "id,account_id,event,at,body", "at"
        ):
            row = json.loads(body)
            append(
                "social:" + str(identity),
                "social",
                at,
                event + " " + row["platform"] + " @" + row["handle"],
                row.get("person_id"),
                "social_registry",
                {"social_account": account_id},
            )
        for identity, assignment_id, body in scan(
            "platform_assignment_history",
            "id,assignment_id,body",
            "json_extract(body,'$.at')",
        ):
            row = json.loads(body)
            append(
                "assignment:" + str(identity),
                "assignment",
                row["at"],
                row["agent_id"] + ": " + row["state"],
                None,
                "platform_assignment",
                {"platform_assignment": assignment_id},
            )
    rows.sort(key=lambda row: (row["occurred_at"], row["id"]), reverse=True)
    eligible = [
        row for row in rows if after is None or (row["occurred_at"], row["id"]) < after
    ]
    page = eligible[:limit]
    next_cursor = None
    if len(eligible) > limit:
        last = page[-1]
        next_cursor = base64.urlsafe_b64encode(
            json.dumps(
                {"query": fingerprint, "at": last["occurred_at"], "id": last["id"]}
            ).encode()
        ).decode()
    return {
        "date": day,
        "timezone": timezone_name,
        "events": page,
        "next_cursor": next_cursor,
        "matched_records": len(rows),
        "coverage": (
            "truncated_local_records" if truncated else "available_local_records"
        ),
        "available_sources": available,
        "limits": [
            "Calendar entries are scheduled events, not proof of attendance.",
            "This is recorded communications activity, not a complete record of human activity.",
            "Seek pagination reflects current records; it is not a frozen snapshot.",
        ],
    }
