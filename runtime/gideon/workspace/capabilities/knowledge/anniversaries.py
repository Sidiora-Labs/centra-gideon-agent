"""Calendar anniversary projection over the authoritative knowledge and memory stores."""

import json
from datetime import date as Date
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _local_date(value, zone):
    if not isinstance(value, str) or not value:
        raise ValueError("missing original date")
    if len(value) == 10:
        return Date.fromisoformat(value)
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    # Existing knowledge records can carry naive local wall-clock timestamps.
    return stamp.date() if stamp.tzinfo is None else stamp.astimezone(zone).date()


def _metadata(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


def _knowledge_rows(store):
    rows = store.db.execute(
        "SELECT id,item_type,title,content,created_at,file_metadata FROM items "
        "WHERE item_type IN ('note','journal','fleeting') "
        "AND status = 'active' AND COALESCE(is_archived,0) = 0"
    )
    for row in rows:
        row = dict(row)
        meta = _metadata(row["file_metadata"])
        original = meta.get(
            "original_at", meta.get("source_created_at", row["created_at"])
        )
        yield {
            "source_type": row["item_type"],
            "source_id": row["id"],
            "original_at": original,
            "title": row["title"],
            "content": row["content"],
            "source_link": f"#/knowledge/item/{quote(row['id'], safe='')}",
        }


def _memory_rows(service):
    if service is None:
        return
    offset = 0
    while True:
        records = service.episodic_list(limit=200, offset=offset)
        if not records:
            return
        for record in records:
            if record.get("is_deleted") or record.get("invalidated_at"):
                continue
            source = _metadata(record.get("source_ref"))
            original = source.get("original_at", record.get("created_at"))
            content = record.get("text", "")
            yield {
                "source_type": "memory",
                "source_id": record["id"],
                "original_at": original,
                "title": content.splitlines()[0][:100] if content else "Memory",
                "content": content,
                "source_link": f"#/capabilities/knowledge?memory={quote(record['id'], safe='')}",
            }
        offset += len(records)


def source_record(knowledge_store, memory_service, source_type, source_id):
    rows = (
        _memory_rows(memory_service)
        if source_type == "memory"
        else _knowledge_rows(knowledge_store)
    )
    return next(
        (
            row
            for row in rows
            if row["source_type"] == source_type and row["source_id"] == source_id
        ),
        None,
    )


def anniversaries(
    knowledge_store,
    memory_service=None,
    *,
    date=None,
    timezone="UTC",
    limit=20,
    offset=0,
):
    if (
        isinstance(limit, bool)
        or isinstance(offset, bool)
        or not isinstance(limit, int)
        or not isinstance(offset, int)
    ):
        raise ValueError("limit and offset must be integers")
    if not 1 <= limit <= 100 or not 0 <= offset <= 1_000_000:
        raise ValueError("limit must be 1..100 and offset 0..1000000")
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise ValueError("timezone must name an IANA timezone") from exc
    if date is not None and (not isinstance(date, str) or len(date) != 10):
        raise ValueError("date must be YYYY-MM-DD")
    day = Date.fromisoformat(date) if date is not None else datetime.now(zone).date()
    matches = []
    skipped = 0
    from itertools import chain

    for row in chain(_knowledge_rows(knowledge_store), _memory_rows(memory_service)):
        try:
            original = _local_date(row["original_at"], zone)
        except (ValueError, TypeError, OverflowError):
            skipped += 1
            continue
        if original.year >= day.year or (original.month, original.day) != (
            day.month,
            day.day,
        ):
            continue
        content = row.pop("content")
        row["excerpt"] = " ".join(content.split())[:240]
        row["years_ago"] = day.year - original.year
        matches.append(row)
    matches.sort(
        key=lambda row: (row["years_ago"], row["source_type"], row["source_id"])
    )
    total = len(matches)
    return {
        "date": day.isoformat(),
        "timezone": timezone,
        "leap_day_policy": "exact",
        "naive_timestamp_policy": "local_wall_clock",
        "items": matches[offset : offset + limit],
        "total": total,
        "offset": offset,
        "limit": limit,
        "next_offset": offset + limit if offset + limit < total else None,
        "skipped_invalid_dates": skipped,
        "sources": {
            "knowledge": "available",
            "memory": "available" if memory_service is not None else "unavailable",
        },
    }
