"""Durable, owner-scoped notices for tasks approaching their due date."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import quote

from gideon.core.atomic_write import atomic_write
from gideon.core.config import loader as config_loader

logger = logging.getLogger(__name__)
LEDGER_NAME = "_due_notices.json"
LEAD_HOUR = 9
STALE_DAYS = 1
SWEEP_INTERVAL = 300
START_DELAY = 1


def _ledger_path() -> Path:
    return config_loader.config_dir() / "tasks" / LEDGER_NAME


def _read_ledger(path: Path) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        logger.warning("task due notice ledger could not be read")
        return {}
    notified = payload.get("notified") if isinstance(payload, dict) else None
    if not isinstance(notified, dict):
        logger.warning("task due notice ledger has an invalid shape")
        return {}
    return {
        key: value
        for key, value in notified.items()
        if isinstance(key, str) and isinstance(value, str)
    }


def _write_ledger(path: Path, notified: dict[str, str]) -> None:
    atomic_write(path, json.dumps({"notified": notified}, sort_keys=True) + "\n")


def _due_day(value: object) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


async def sweep(state, *, now: datetime | None = None) -> int:
    """Notify eligible owner tasks and return the number considered notified."""
    if state is None:
        return 0
    from gideon.cognition.identity import current_username
    from gideon.engine.tasks import registry
    from gideon.engine.tasks.models import TERMINAL_STATUSES, TaskStatus
    from gideon.extensions.providers.entity_routes import notification_posture
    from gideon.workspace import notification_kinds

    current = now.astimezone() if now is not None and now.tzinfo else datetime.now().astimezone()
    owner = current_username()
    all_tasks = []
    offset = 0
    while True:
        page, total = await registry.list_all_tasks(limit=200, offset=offset)
        all_tasks.extend(page)
        offset += len(page)
        if not page or offset >= total:
            break

    path = _ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    notified = _read_ledger(path)
    changed = False
    count = 0
    today = current.date()
    terminal = set(TERMINAL_STATUSES) | {TaskStatus.SKIPPED}
    for task in all_tasks:
        if not getattr(task, "due_reminder", True) or task.status in terminal:
            continue
        if not task.belongs_to(owner):
            continue
        due = _due_day(getattr(task, "due", ""))
        if due is None or today > due + timedelta(days=STALE_DAYS):
            continue
        due_at = datetime.combine(due - timedelta(days=1), time(hour=LEAD_HOUR), tzinfo=current.tzinfo)
        if current < due_at or notified.get(task.id) == due.isoformat():
            continue
        posture = notification_posture(notification_kinds.TASK_DUE, now=current)
        if posture == "quiet":
            continue
        title = str(getattr(task, "title", "") or "Untitled task")
        state.notify(
            notification_kinds.TASK_DUE,
            f"Task due: {title}",
            f"{title} is due {due.isoformat()}.",
            meta={
                "task_id": task.id,
                "due": due.isoformat(),
                "statusUrl": "#/tasks?open=" + quote(task.id, safe=""),
            },
        )
        notified[task.id] = due.isoformat()
        changed = True
        count += 1
    if changed:
        _write_ledger(path, notified)
    return count


async def run(state) -> None:
    await asyncio.sleep(START_DELAY)
    while True:
        try:
            await sweep(state)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("task due notice sweep failed", exc_info=True)
        await asyncio.sleep(SWEEP_INTERVAL)
