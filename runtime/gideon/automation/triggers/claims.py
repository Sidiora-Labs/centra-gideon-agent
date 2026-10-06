"""Persistent execution claims and resource ownership derived from live runs."""

from __future__ import annotations

import json
import fcntl
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4
from contextlib import contextmanager

from gideon.automation.triggers.scheduling import CLAIM_MAX_DURATION_SECS, Claim

logger = logging.getLogger(__name__)
_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def owner_state(pid: int, identity: str = "") -> bool | None:
    """Prove the same process, prove departure, or conservatively report unknown."""
    from gideon.engine.gateway_base import process_facts

    if pid <= 0:
        return False
    facts = process_facts(pid)
    if facts is not None:
        if facts.state == "Z" or (identity and facts.identity != identity):
            return False
        return True if identity else None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return None
    return None


def process_identity(pid: int) -> str:
    from gideon.engine.gateway_base import process_facts

    facts = process_facts(pid)
    return facts.identity if facts is not None else ""


class ClaimJournal:
    def __init__(self, base_dir: Path | str | None):
        from gideon.core.config.loader import config_dir

        self.directory = (
            Path(base_dir) if base_dir else config_dir()
        ) / "trigger-claims"

    def path(self, identity: str) -> Path:
        filename = _SAFE_RE.sub("-", identity) or "claim"
        return self.directory / (filename + ".json")

    @contextmanager
    def locked(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / ".claims.lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    @staticmethod
    def document(path: Path) -> dict | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def records(self, identity: str) -> list[dict]:
        document = self.document(self.path(identity))
        if document is None:
            return []
        holders = document.get("holders")
        return [row for row in holders if isinstance(row, dict)] if isinstance(holders, list) else [document]

    @staticmethod
    def claim(record: dict) -> Claim:
        return Claim(str(record["trigger_id"]), str(record["holder"]),
                     float(record["claimed_at"]), float(record.get("max_duration_secs") or CLAIM_MAX_DURATION_SECS),
                     int(record.get("owner_pid") or 0), str(record.get("owner_identity") or ""))

    def live(self, identity: str, observed_at: float) -> Claim | None:
        for record in self.records(identity):
            try:
                claim = self.claim(record)
                if claim.claimed_at > 0 and not claim.expired(observed_at):
                    return claim
            except (KeyError, TypeError, ValueError):
                continue
        return None

    @staticmethod
    def record(claim: Any) -> dict:
        return {"trigger_id": claim.trigger_id, "holder": claim.holder,
                "claimed_at": float(claim.claimed_at),
                "max_duration_secs": float(claim.max_duration_secs or CLAIM_MAX_DURATION_SECS),
                "owner_pid": int(getattr(claim, "owner_pid", 0) or 0),
                "owner_identity": str(getattr(claim, "owner_identity", "") or "")}

    def publish(self, claim: Any) -> None:
        acquire_claim(claim, base_dir=self.directory.parent)

    def _publish(self, claim: Any) -> None:
        records = self.records(claim.trigger_id)
        record = self.record(claim)
        records = [record if row.get("holder") == claim.holder else row for row in records]
        if not any(row.get("holder") == claim.holder for row in records):
            records.append(record)
        self._write_records(claim.trigger_id, records)

    def _write_records(self, identity: str, records: list[dict]) -> None:
        if not records:
            self.remove(identity)
            return
        destination = self.path(identity)
        document = {**records[0], "holders": records}
        staged = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
        try:
            with staged.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(document))
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


def read_claims(trigger_id: str, *, base_dir: Path | str | None = None) -> list[Claim]:
    journal = ClaimJournal(base_dir)
    with journal.locked():
        return [journal.claim(record) for record in journal.records(trigger_id)]


def acquire_claim(claim: Claim, *, owner_pid: int = 0, overlap: str = "skip", base_dir: Path | str | None = None) -> bool:
    journal = ClaimJournal(base_dir)
    with journal.locked():
        records = journal.records(claim.trigger_id)
        if journal.path(claim.trigger_id).exists() and not records:
            return False  # Unreadable ownership needs explicit recovery.
        if overlap != "parallel" and any(
            int(row.get("owner_pid") or 0) > 0 or not journal.claim(row).expired(time.time())
            for row in records
        ):
            return False
        if overlap != "parallel":
            journal._write_records(claim.trigger_id, [])
        claim.holder = f"{claim.holder}:{uuid4().hex}"
        claim.owner_pid = owner_pid
        claim.owner_identity = process_identity(owner_pid) if owner_pid else ""
        journal._publish(claim)
        return True


def bind_owner(trigger_id: str, *, owner_pid: int, base_dir: Path | str | None = None, expected_holder: str = "") -> str:
    journal = ClaimJournal(base_dir)
    with journal.locked():
        for record in journal.records(trigger_id):
            if int(record.get("owner_pid") or 0) > 0:
                continue
            if expected_holder and record.get("holder") != expected_holder:
                continue
            claim = journal.claim(record)
            if claim.expired(time.time()):
                continue
            claim.owner_pid, claim.owner_identity = owner_pid, process_identity(owner_pid)
            journal._publish(claim)
            return claim.holder
        return ""


def release_claim(trigger_id: str, *, base_dir: Path | str | None = None, holder: str = "", owner_pid: int = 0) -> bool:
    journal = ClaimJournal(base_dir)
    with journal.locked():
        records = journal.records(trigger_id)
        if not holder and len(records) > 1:
            return False
        for index, record in enumerate(records):
            if holder and record.get("holder") != holder:
                continue
            pid = int(record.get("owner_pid") or 0)
            identity = str(record.get("owner_identity") or "")
            if not holder and not pid:
                continue
            if pid and owner_state(pid, identity) is not False:
                if not holder or pid != owner_pid or identity != process_identity(owner_pid):
                    continue
            journal._write_records(trigger_id, records[:index] + records[index + 1:])
            return True
        return False


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
