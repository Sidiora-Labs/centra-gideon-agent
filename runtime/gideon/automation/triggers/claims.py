"""Persistent execution claims and resource ownership derived from live runs."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from gideon.automation.triggers.scheduling import CLAIM_MAX_DURATION_SECS, Claim

logger = logging.getLogger(__name__)
_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


class ClaimJournal:
    def __init__(self, base_dir: Path | str | None):
        from gideon.core.config.loader import config_dir

        self.directory = (
            Path(base_dir) if base_dir else config_dir()
        ) / "trigger-claims"

    def path(self, identity: str) -> Path:
        filename = _SAFE_RE.sub("-", identity) or "claim"
        return self.directory / (filename + ".json")

    @staticmethod
    def document(path: Path) -> dict | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def live(self, identity: str, observed_at: float) -> Claim | None:
        document = self.document(self.path(identity))
        if document is None:
            return None
        try:
            started = float(document.get("claimed_at") or 0.0)
            lifetime = float(
                document.get("max_duration_secs") or CLAIM_MAX_DURATION_SECS
            )
        except (TypeError, ValueError):
            return None
        if started <= 0 or observed_at - started >= lifetime:
            return None
        return Claim(
            str(document.get("trigger_id") or identity),
            str(document.get("holder") or ""),
            started,
            lifetime,
        )

    def publish(self, claim: Any) -> None:
        destination = self.path(claim.trigger_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "trigger_id": claim.trigger_id,
            "holder": getattr(claim, "holder", ""),
            "claimed_at": float(getattr(claim, "claimed_at", 0.0)),
            "max_duration_secs": float(
                getattr(claim, "max_duration_secs", CLAIM_MAX_DURATION_SECS)
                or CLAIM_MAX_DURATION_SECS
            ),
        }
        staged = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
        try:
            with staged.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(record))
                stream.flush()
                os.fsync(stream.fileno())
            staged.replace(destination)
        finally:
            staged.unlink(missing_ok=True)

    def remove(self, identity: str) -> bool:
        try:
            self.path(identity).unlink()
        except FileNotFoundError:
            return False
        except OSError:
            logger.debug("could not release claim for %s", identity, exc_info=True)
            return False
        return True

    def identities(self) -> Iterator[str]:
        if not self.directory.is_dir():
            return
        for entry in sorted(self.directory.iterdir()):
            if entry.suffix == ".json":
                document = self.document(entry)
                identity = str(document.get("trigger_id") or "") if document else ""
                if identity:
                    yield identity


def _claims_dir(base_dir: Path | str | None) -> Path:
    return ClaimJournal(base_dir).directory


def _claim_path(trigger_id: str, base_dir: Path | str | None) -> Path:
    return ClaimJournal(base_dir).path(trigger_id)


def read_claim(
    trigger_id: str, *, now: float = 0.0, base_dir: Path | str | None = None
) -> Claim | None:
    return ClaimJournal(base_dir).live(trigger_id, now or time.time())


def write_claim(claim: Any, *, base_dir: Path | str | None = None) -> None:
    trigger_id = getattr(claim, "trigger_id", None)
    if isinstance(trigger_id, str) and trigger_id:
        ClaimJournal(base_dir).publish(claim)


def release_claim(trigger_id: str, *, base_dir: Path | str | None = None) -> bool:
    return ClaimJournal(base_dir).remove(trigger_id)


def is_running(
    trigger_id: str, *, now: float = 0.0, base_dir: Path | str | None = None
) -> bool:
    return read_claim(trigger_id, now=now, base_dir=base_dir) is not None


def running_since(
    trigger_id: str, *, now: float = 0.0, base_dir: Path | str | None = None
) -> float | None:
    active = read_claim(trigger_id, now=now, base_dir=base_dir)
    return active.claimed_at if active is not None else None


def running_ids(*, now: float = 0.0, base_dir: Path | str | None = None) -> list[str]:
    return [
        identity
        for identity in ClaimJournal(base_dir).identities()
        if is_running(identity, now=now, base_dir=base_dir)
    ]


def _declared_slots(trigger: Any) -> list | tuple:
    declaration = getattr(trigger, "resource_slots", None)
    return declaration if isinstance(declaration, (list, tuple)) and declaration else ()


def _slot_names(slots: list | tuple) -> Iterator[str]:
    return (str(value or "").strip() for value in slots)


def slot_holders(
    store: Any, *, now: float = 0.0, base_dir: Path | str | None = None
) -> dict[str, str]:
    observed_at = now or time.time()
    ownership: dict[str, str] = {}
    for row in store.load():
        trigger = row.trigger
        if not getattr(row, "ok", True):
            continue
        slots = _declared_slots(trigger)
        if (
            slots
            and read_claim(trigger.id, now=observed_at, base_dir=base_dir) is not None
        ):
            for name in _slot_names(slots):
                if name:
                    ownership.setdefault(name, trigger.id)
    return ownership


def busy_slot(
    trigger: Any,
    *,
    holders: dict[str, str] | None = None,
    store: Any = None,
    now: float = 0.0,
    base_dir: Path | str | None = None,
) -> tuple[str, str]:
    slots = _declared_slots(trigger)
    if not slots or (holders is None and store is None):
        return "", ""
    occupied = (
        holders
        if holders is not None
        else slot_holders(store, now=now, base_dir=base_dir)
    )
    identity = str(getattr(trigger, "id", "") or "")
    for name in _slot_names(slots):
        owner = occupied.get(name, "")
        if name and owner and owner != identity:
            return name, owner
    return "", ""
