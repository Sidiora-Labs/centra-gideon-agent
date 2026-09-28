"""Durable owner decisions for missed and interrupted automation work."""

from __future__ import annotations

import fcntl
import json
import logging
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from gideon.core.atomic_write import atomic_write

logger = logging.getLogger(__name__)
FILENAME = "trigger-review.json"
_SCHEMA_VERSION = 1


class TriggerReviewStore:
    def __init__(self, base_dir: Path | str | None = None) -> None:
        if base_dir is None:
            from gideon.core.config.loader import config_dir

            base_dir = config_dir()
        self.base_dir = Path(base_dir)
        self.path = self.base_dir / FILENAME
        self.lock_path = self.base_dir / ".trigger-review.lock"

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _read(self) -> dict[str, Any]:
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"version": _SCHEMA_VERSION, "cards": {}}
        except (OSError, json.JSONDecodeError):
            logger.warning("trigger review store is unreadable: %s", self.path)
            return {"version": _SCHEMA_VERSION, "cards": {}}
        if not isinstance(document, dict) or not isinstance(document.get("cards"), dict):
            logger.warning("trigger review store has an invalid shape: %s", self.path)
            return {"version": _SCHEMA_VERSION, "cards": {}}
        return {"version": _SCHEMA_VERSION, "cards": document["cards"]}

    def _write(self, document: dict[str, Any]) -> None:
        atomic_write(self.path, json.dumps(document, indent=2, ensure_ascii=False) + "\n")

    def list(self, *, pending_only: bool = True) -> list[dict[str, Any]]:
        with self._locked():
            rows = list(self._read()["cards"].values())
        if pending_only:
            rows = [row for row in rows if row.get("status") == "pending"]
        return sorted(rows, key=lambda row: (float(row.get("created_at") or 0), str(row.get("id") or "")))

    def get(self, review_id: str) -> dict[str, Any] | None:
        with self._locked():
            row = self._read()["cards"].get(review_id)
            return dict(row) if isinstance(row, dict) else None

    def add_boot_observations(
        self,
        trigger_store: Any,
        report: dict[str, Any],
        interrupted: list[str],
        *,
        now: float | None = None,
    ) -> list[dict[str, Any]]:
        from gideon.automation.triggers.grants import action_revision
        from gideon.security.security import redact_values_for_display

        observed_at = time.time() if now is None else float(now)
        missed: dict[str, dict[str, Any]] = {}
        review = report.get("review") if isinstance(report, dict) else {}
        for entry in (review or {}).get("rows", []) or []:
            identity = str(entry.get("trigger_id") or "")
            if not identity:
                continue
            aggregate = missed.setdefault(
                identity, {"count": 0, "latest": 0.0}
            )
            aggregate["count"] += 1
            aggregate["latest"] = max(
                aggregate["latest"], float(entry.get("scheduled_for") or 0)
            )
        for entry in (review or {}).get("summaries", []) or []:
            identity = str(entry.get("trigger_id") or "")
            if not identity:
                continue
            aggregate = missed.setdefault(
                identity, {"count": 0, "latest": 0.0}
            )
            aggregate["count"] += int(entry.get("count") or 0)
            aggregate["latest"] = max(
                aggregate["latest"], float(entry.get("newest") or 0)
            )

        candidates: list[dict[str, Any]] = []
        for identity, aggregate in missed.items():
            row = trigger_store.get(identity)
            if row is None:
                continue
            trigger = row.trigger
            action = trigger.workflow if isinstance(trigger.workflow, dict) else {}
            action = action.get("inline") if isinstance(action.get("inline"), dict) else action
            revision = action_revision(trigger)
            candidates.append(
                {
                    "id": f"missed:{identity}",
                    "trigger_id": f"store:{identity}",
                    "trigger_name": str(trigger.name or identity),
                    "reason": "missed",
                    "missed_count": aggregate["count"],
                    "latest_missed_at": aggregate["latest"],
                    "run_id": "",
                    "action_revision": revision,
                    "frozen_action_fingerprint": revision,
                    "action": redact_values_for_display(action),
                    "created_at": observed_at,
                    "status": "pending",
                }
            )

        for identity in sorted(set(map(str, interrupted))):
            row = trigger_store.get(identity)
            if row is None:
                continue
            trigger = row.trigger
            action = trigger.workflow if isinstance(trigger.workflow, dict) else {}
            action = action.get("inline") if isinstance(action.get("inline"), dict) else action
            revision = action_revision(trigger)
            run_id = str(trigger.last_run_id or "")
            candidates.append(
                {
                    "id": f"interrupted:{identity}:{run_id}",
                    "trigger_id": f"store:{identity}",
                    "trigger_name": str(trigger.name or identity),
                    "reason": "interrupted",
                    "missed_count": 0,
                    "latest_missed_at": 0.0,
                    "run_id": run_id,
                    "action_revision": revision,
                    "frozen_action_fingerprint": revision,
                    "action": redact_values_for_display(action),
                    "created_at": observed_at,
                    "status": "pending",
                }
            )

        with self._locked():
            document = self._read()
            cards = document["cards"]
            for row in cards.values():
                if isinstance(row, dict) and row.get("status") == "running":
                    row["status"] = "pending"
                    row["last_error"] = "review decision was interrupted before completion"
            for candidate in candidates:
                previous = cards.get(candidate["id"])
                if isinstance(previous, dict):
                    if previous.get("status") == "pending":
                        if previous.get("action_revision") == candidate["action_revision"]:
                            previous["missed_count"] = max(
                                int(previous.get("missed_count") or 0),
                                int(candidate.get("missed_count") or 0),
                            )
                            previous["latest_missed_at"] = max(
                                float(previous.get("latest_missed_at") or 0),
                                float(candidate.get("latest_missed_at") or 0),
                            )
                        continue
                    if (
                        candidate["reason"] == "missed"
                        and float(candidate["latest_missed_at"] or 0)
                        <= float(previous.get("latest_missed_at") or 0)
                    ):
                        continue
                cards[candidate["id"]] = candidate
            self._write(document)
            return [dict(row) for row in cards.values() if row.get("status") == "pending"]

    def resolve(
        self,
        review_id: str,
        *,
        decision: str,
        outcome: str,
        allow_running: bool = False,
    ) -> bool:
        with self._locked():
            document = self._read()
            row = document["cards"].get(review_id)
            permitted = {"pending", "running"} if allow_running else {"pending"}
            if not isinstance(row, dict) or row.get("status") not in permitted:
                return False
            row["status"] = "resolved"
            row["decision"] = decision
            row["outcome"] = outcome
            row["resolved_at"] = time.time()
            self._write(document)
            return True

    def begin_run(self, review_id: str) -> dict[str, Any] | None:
        with self._locked():
            document = self._read()
            row = document["cards"].get(review_id)
            if not isinstance(row, dict) or row.get("status") != "pending":
                return None
            row["status"] = "running"
            self._write(document)
            return dict(row)

    def release_run(self, review_id: str, *, error: str) -> None:
        with self._locked():
            document = self._read()
            row = document["cards"].get(review_id)
            if not isinstance(row, dict) or row.get("status") != "running":
                return
            row["status"] = "pending"
            row["last_error"] = error[:300]
            self._write(document)


async def record_review_outcome(
    card: dict[str, Any],
    outcome: str,
    *,
    error: str = "",
    now: float | None = None,
    base_dir: Path | str | None = None,
) -> None:
    from gideon.automation.schedule_history import ExecutionJournal, ExecutionRecord
    from gideon.core.config.loader import config_dir

    finished = time.time() if now is None else float(now)
    identity = str(card.get("trigger_id") or "")
    if identity.startswith("store:"):
        identity = identity[len("store:") :]
    if not identity:
        return
    status = outcome
    if status == "dismissed":
        status = "interrupted_dismissed" if card.get("reason") == "interrupted" else "skipped_missed"
    record = ExecutionRecord(
        run_id=f"review-{int(finished * 1000)}",
        job_id=identity,
        trigger="review",
        started_at=finished,
        finished_at=finished,
        status=status,
        summary=f"{card.get('reason', 'missed')} review: {outcome}",
        error=error,
    )
    await ExecutionJournal(config_dir() if base_dir is None else Path(base_dir)).append(record)
