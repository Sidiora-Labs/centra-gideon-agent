"""Import retired automation documents as disabled, owner-reviewable triggers."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

from gideon.automation.triggers.models import Trigger, TriggerState

logger = logging.getLogger(__name__)


def _identity(source: str, raw_id: Any, row: dict[str, Any], index: int) -> str:
    candidate = str(raw_id or "").strip()
    candidate = re.sub(r"[^A-Za-z0-9_.-]+", "-", candidate).strip("-.")[:80]
    if not candidate:
        raw = json.dumps(row, sort_keys=True, ensure_ascii=False, default=str)
        candidate = hashlib.sha256(f"{index}:{raw}".encode()).hexdigest()[:20]
    return f"legacy-{source}-{candidate}"


def _disabled(record: dict[str, Any], *, source: str) -> Trigger:
    record = dict(record)
    record.update(
        enabled=False,
        author="legacy_import",
        origin_harness=f"legacy_import:{source}",
        capabilities={},
        state=TriggerState.PAUSED.value,
        next_fire_at="",
    )
    from gideon.automation.triggers.models import parse_trigger

    trigger, _issues = parse_trigger(record)
    trigger.enabled = False
    trigger.state = TriggerState.PAUSED.value
    trigger.capabilities = {}
    trigger.next_fire_at = ""
    trigger.author = "legacy_import"
    trigger.origin_harness = f"legacy_import:{source}"
    return trigger


def _cron_rows(root: Path) -> tuple[list[dict[str, Any]], bool]:
    path = root / "crons.json"
    if not path.exists():
        return [], True
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], False
    from gideon.automation.triggers.migrate import migrate_crons

    converted = migrate_crons(raw if isinstance(raw, dict) else {})
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(converted.converted):
        record = dict(item.trigger)
        record["id"] = _identity("cron", record.get("id"), record, index)
        rows.append(record)
    return rows, len(converted.refused) == 0 and converted.lossless


def _event_rows(root: Path) -> tuple[list[dict[str, Any]], bool]:
    path = root / "event_triggers.json"
    if not path.exists():
        return [], True
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], False
    if not isinstance(raw, list):
        return [], False
    from gideon.automation.event_triggers import PATTERN_SOURCE
    from gideon.automation.triggers.service import to_iso

    rows: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            return rows, False
        provider = str(item.get("action_provider") or "notify")
        pattern = str(item.get("pattern") or "MemoryUpdate")
        record = {
            "id": _event_identity(item.get("id"), item, index),
            "name": str(
                item.get("name") or item.get("id") or f"Imported event {index + 1}"
            ),
            "kind": "event",
            "enabled": False,
            "created_by": "legacy_import",
            "spec": {
                "source": str(
                    item.get("source") or PATTERN_SOURCE.get(pattern, "memory")
                ),
                "pattern": pattern,
                **{
                    key: item[key]
                    for key in (
                        "key_glob",
                        "content_re",
                        "sender_glob",
                        "address_glob",
                        "event_glob",
                        "max_fires",
                    )
                    if key in item
                },
            },
            "gates": {
                "debounce_secs": item.get("debounce_secs", 5),
                "max_fires": int(item.get("max_fires", 0) or 0),
            },
            "workflow": {
                "inline": {
                    "provider": provider,
                    "config": dict(item.get("action_config") or {}),
                }
            },
            "run_count": int(item.get("fire_count", 0) or 0),
            "last_fired_at": to_iso(float(item.get("last_fired_at", 0) or 0)),
        }
        rows.append(record)
    return rows, True


def _event_identity(raw_id: Any, row: dict[str, Any], index: int) -> str:
    candidate = str(raw_id or "").strip()
    candidate = re.sub(r"[^A-Za-z0-9_.-]+", "-", candidate).strip("-.")[:80]
    if not candidate:
        raw = json.dumps(row, sort_keys=True, ensure_ascii=False, default=str)
        candidate = hashlib.sha256(f"{index}:{raw}".encode()).hexdigest()[:20]
    return f"event:{candidate}"


def _nudge_rows(root: Path) -> tuple[list[dict[str, Any]], bool]:
    path = root / "autonudge.json"
    if not path.exists():
        return [], True
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], False
    raw_rows = document.get("loops") if isinstance(document, dict) else None
    if not isinstance(raw_rows, list):
        return [], False
    from gideon.automation.triggers.service import to_iso

    rows: list[dict[str, Any]] = []
    for index, item in enumerate(raw_rows):
        if not isinstance(item, dict):
            return rows, False
        session = str(item.get("session_name") or "").strip()
        if not session:
            return rows, False
        record = {
            "id": _identity("nudge", item.get("id"), item, index),
            "name": f"Imported nudge for {session}",
            "kind": "idle",
            "enabled": False,
            "created_by": "legacy_import",
            "spec": {
                "scope": session,
                "idle_secs": int(item.get("idle_secs", 60) or 60),
                "first_idle_secs": int(item.get("first_idle_secs", 0) or 0),
                "message": str(item.get("message") or ""),
                "max_cycles": int(item.get("max_cycles", 0) or 0),
                "stop_sentinel_path": str(item.get("stop_sentinel_path") or ""),
            },
            "run_count": int(item.get("cycle_count", 0) or 0),
            "last_fired_at": to_iso(float(item.get("last_fire_ts", 0) or 0)),
        }
        record["id"] = _identity("nudge", item.get("id"), item, index)
        rows.append(record)
    return rows, True


def _retire(source: Path) -> bool:
    destination = source.with_suffix(source.suffix + ".migrated")
    if destination.exists():
        return False
    try:
        source.rename(destination)
    except OSError:
        logger.warning("could not retire imported automation %s", source.name)
        return False
    return True


def import_legacy(root: Path | str, store: Any) -> dict[str, Any]:
    """Import each known legacy document once; malformed input stays available for review."""
    base = Path(root)
    sources = (
        ("crons", _cron_rows),
        ("event_triggers", _event_rows),
        ("autonudge", _nudge_rows),
    )
    imported: list[str] = []
    retired: list[str] = []
    pending: list[str] = []
    for name, convert in sources:
        source = base / (
            "event_triggers.json" if name == "event_triggers" else f"{name}.json"
        )
        if not source.exists():
            continue
        rows, complete = convert(base)
        if not complete:
            pending.append(name)
        try:
            for raw in rows:
                record = dict(raw)
                record.update(
                    enabled=False,
                    author="legacy_import",
                    origin_harness=f"legacy_import:{name}",
                    capabilities={},
                    state=TriggerState.PAUSED.value,
                    next_fire_at="",
                )
                trigger = _disabled(record, source=name)
                prior = store.get(trigger.id)
                if (
                    prior is not None
                    and prior.trigger.origin_harness != f"legacy_import:{name}"
                ):
                    pending.append(name)
                    continue
                if prior is None:
                    store.upsert(trigger)
                    imported.append(trigger.id)
        except Exception:
            logger.warning(
                "legacy automation import failed for %s", name, exc_info=True
            )
            pending.append(name)
            continue
        if complete and name not in pending and _retire(source):
            retired.append(name)
    return {"imported": imported, "retired": retired, "pending": sorted(set(pending))}
