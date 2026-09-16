"""Event identity, delivery decisions and persistent pending-event storage."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

DEDUP_WINDOW_SECS = 300.0
MAX_TRANSIENT_RETRIES = 5
COALESCE_WINDOW_SECS = 0.25


class Handling(str, Enum):
    DELIVERED = "delivered"
    TRANSIENT = "transient"
    PERMANENT = "permanent"


class DeliveryStatus(str, Enum):
    PENDING = "pending"
    DELIVERED = "delivered"
    GIVEN_UP = "given_up"


class WakeKind(str, Enum):
    WAKE = "wake"
    RESUME = "resume"


class DrainAction(str, Enum):
    CONSUME = "consume"
    HOLD = "hold"
    GIVE_UP = "give_up"
    SKIP_DUPLICATE = "skip_duplicate"


def droppable(kind: str) -> bool:
    return WakeKind.RESUME.value != kind


def payload_hash(source: str, kind: str, payload: dict[str, Any]) -> str:
    identity = dict(source=source, kind=kind, payload=payload or {})
    encoded = json.dumps(
        identity, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass
class Envelope:
    seq: int
    source: str
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    emitted_at: float = 0.0
    spawned_by: str = ""

    @property
    def payload_hash(self) -> str:
        return payload_hash(self.source, self.kind, self.payload)

    @property
    def event_id(self) -> str:
        return "evt-" + self.payload_hash[:16]

    def to_dict(self) -> dict[str, Any]:
        record = {
            name: getattr(self, name)
            for name in ("seq", "source", "kind", "emitted_at", "spawned_by")
        }
        record["payload"] = dict(self.payload)
        record["event_id"] = self.event_id
        return record

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Envelope:
        record = d or {}
        data = record.get("payload")
        strings = {
            key: str(record.get(key, "") or "")
            for key in ("source", "kind", "spawned_by")
        }
        return cls(
            seq=int(record.get("seq", 0) or 0),
            payload=dict(data) if isinstance(data, dict) else {},
            emitted_at=float(record.get("emitted_at", 0.0) or 0.0),
            **strings,
        )


@dataclass
class Cursor:
    trigger_id: str
    stream: str
    seq: int = 0
    held_retries: int = 0

    def advance(self, to_seq: int) -> bool:
        moved = to_seq > self.seq
        if moved:
            self.seq, self.held_retries = to_seq, 0
        return moved

    def to_dict(self) -> dict[str, Any]:
        return dict(
            trigger_id=self.trigger_id,
            stream=self.stream,
            seq=self.seq,
            held_retries=self.held_retries,
        )


@dataclass(frozen=True)
class RetryBudget:
    spent: int
    maximum: int

    def decision(self) -> tuple[str, str]:
        attempt = self.spent + 1
        if attempt >= self.maximum:
            return DrainAction.GIVE_UP.value, (
                f"transient failure persisted for {self.maximum} attempts; advancing loudly rather "
                "than stalling every other automation"
            )
        return DrainAction.HOLD.value, (
            f"transient failure, attempt {attempt} of {self.maximum}; the event is not lost"
        )


def is_duplicate(
    envelope: Envelope,
    seen: dict[str, float],
    now: float,
    window: float = DEDUP_WINDOW_SECS,
) -> bool:
    stamp = seen.get(envelope.payload_hash)
    return False if stamp is None else now - stamp < window


def drain_decision(
    *, handling: str, held_retries: int, max_retries: int = MAX_TRANSIENT_RETRIES
) -> tuple[str, str]:
    if handling in (Handling.DELIVERED.value, Handling.PERMANENT.value):
        reason = (
            ""
            if handling == Handling.DELIVERED.value
            else (
                "permanent failure: the payload cannot be handled, so holding would stall the stream"
            )
        )
        return DrainAction.CONSUME.value, reason
    if handling == Handling.TRANSIENT.value:
        return RetryBudget(held_retries, max_retries).decision()
    raise AssertionError(
        f"no branch for handling {handling!r} — a new Handling member must declare its own cursor "
        "rule here rather than inherit another member's"
    )


def classify_handler_outcome(
    exception: BaseException | None, reported: str = ""
) -> str:
    for known in Handling:
        if reported == known.value:
            return reported
    return Handling.DELIVERED.value if exception is None else Handling.TRANSIENT.value


def cycle_guard(envelope: Envelope, trigger_id: str) -> tuple[bool, str]:
    repeated = bool(envelope.spawned_by) and envelope.spawned_by == trigger_id
    return (
        (False, f"{trigger_id} would fire on an event its own run emitted")
        if repeated
        else (True, "")
    )


def coalesce_family(
    envelopes: list[Envelope], now: float, window: float = COALESCE_WINDOW_SECS
) -> list[Envelope]:
    families: dict[tuple[str, str], Envelope] = {}
    for candidate in envelopes:
        identity = candidate.source, candidate.kind
        previous = families.get(identity)
        if previous is None or candidate.seq > previous.seq:
            families[identity] = candidate
    return sorted(families.values(), key=lambda candidate: candidate.seq)


@dataclass(frozen=True)
class EventSpool:
    path: Path

    def append(self, envelope: Envelope) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(envelope.to_dict(), separators=(",", ":")) + "\n"
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(line)
        except Exception:
            return False
        return True

    def lines(self) -> list[str] | None:
        try:
            return self.path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return None

    def peek(self, limit: int) -> tuple[list[Envelope], int]:
        source = self.lines()
        if source is None:
            return [], 0
        records, damaged = [], 0
        for line in source[:limit]:
            if not line.strip():
                continue
            try:
                decoded = Envelope.from_dict(json.loads(line.strip()))
            except Exception:
                damaged += 1
            else:
                records.append(decoded)
        return records, damaged

    def acknowledge(self, count: int) -> None:
        source = self.lines()
        if source is None:
            return
        remainder = source[count:]
        replacement = "".join(line + "\n" for line in remainder)
        try:
            from gideon.automation.workflows.store import atomic_write

            atomic_write(self.path, replacement)
        except Exception:
            pass


@dataclass(frozen=True)
class HeldEvent:
    identity: str = ""
    retries: int = 0

    @classmethod
    def read(cls, path: Path) -> HeldEvent:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(record, dict):
                return cls(
                    str(record.get("event_id", "") or ""),
                    int(record.get("held_retries", 0) or 0),
                )
        except Exception:
            pass
        return cls()

    def write(self, path: Path) -> bool:
        try:
            from gideon.automation.workflows.store import atomic_write

            path.parent.mkdir(parents=True, exist_ok=True)
            record = dict(event_id=self.identity, held_retries=int(self.retries))
            atomic_write(path, json.dumps(record, separators=(",", ":")))
        except Exception:
            return False
        return True


def _home_file(filename: str) -> Path:
    from gideon.core.config.loader import config_dir

    return Path(config_dir()) / filename


def spool_path() -> Path:
    return _home_file("trigger-spool.jsonl")


def spool_hold_path() -> Path:
    return _home_file("trigger-spool-hold.json")


def spool_fire(envelope: Envelope, *, path: Path | None = None) -> bool:
    return EventSpool(path or spool_path()).append(envelope)


def drain_spool(
    *, path: Path | None = None, limit: int = 500
) -> tuple[list[Envelope], int]:
    return EventSpool(path or spool_path()).peek(limit)


def clear_spool(*, handled: int, path: Path | None = None) -> None:
    EventSpool(path or spool_path()).acknowledge(handled)


def read_spool_hold(*, path: Path | None = None) -> tuple[str, int]:
    state = HeldEvent.read(path or spool_hold_path())
    return state.identity, state.retries


def write_spool_hold(
    *, event_id: str, held_retries: int, path: Path | None = None
) -> bool:
    return HeldEvent(event_id, held_retries).write(path or spool_hold_path())


def clear_spool_hold(*, path: Path | None = None) -> None:
    try:
        target = path or spool_hold_path()
        target.unlink(missing_ok=True)
    except Exception:
        pass


@dataclass
class Dispatch:
    id: str
    trigger_id: str
    event_id: str
    kind: str = WakeKind.WAKE.value
    targets: dict[str, str] = field(default_factory=dict)
    attempts: dict[str, int] = field(default_factory=dict)
    created_at: float = 0.0

    def mark(self, target: str, status: str) -> None:
        if status != DeliveryStatus.DELIVERED.value:
            self.attempts[target] = 1 + self.attempts.get(target, 0)
        self.targets[target] = status

    @property
    def fully_delivered(self) -> bool:
        return bool(self.targets) and not any(
            status != DeliveryStatus.DELIVERED.value for status in self.targets.values()
        )

    @property
    def given_up(self) -> list[str]:
        return sorted(
            target
            for target in self.targets
            if self.targets[target] == DeliveryStatus.GIVEN_UP.value
        )

    def to_dict(self) -> dict[str, Any]:
        result = {
            name: getattr(self, name)
            for name in (
                "id",
                "trigger_id",
                "event_id",
                "kind",
                "created_at",
                "fully_delivered",
            )
        }
        result.update(targets=dict(self.targets), attempts=dict(self.attempts))
        return result
