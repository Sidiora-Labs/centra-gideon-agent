"""Manage conversation nudges through trigger records and idle state."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from gideon import shutdown_event
from gideon.automation.triggers import idle_poll
from gideon.automation.triggers.models import Trigger
from gideon.automation.triggers.store import TriggerStore

logger = logging.getLogger(__name__)
_MIN_IDLE_SECS = 15
_MAX_IDLE_SECS = 86400
_MAX_CONSECUTIVE_ERRORS = 3
STOP_SENTINEL = "STOP"
_ID_PREFIX = "nudge:"
_LEGACY_FILE = "autonudge.json"
_INSTANCE: AutoNudgeService | None = None


@dataclass
class NudgeLoop:
    id: str
    session_name: str
    message: str
    idle_secs: int = 60
    max_cycles: int = 0
    cycle_count: int = 0
    active: bool = True
    last_fire_ts: float = 0.0
    created_ts: float = 0.0
    stop_sentinel_path: str = ""
    error_count: int = 0
    first_idle_secs: int = 0


@dataclass(frozen=True)
class NudgeRecord:
    trigger: Trigger
    state: idle_poll.IdleState

    def project(self) -> NudgeLoop:
        spec = self.trigger.spec if isinstance(self.trigger.spec, dict) else {}

        def integer(key: str, default: int = 0) -> int:
            try:
                return int(spec.get(key, default) or default)
            except (TypeError, ValueError):
                return default

        values: dict = dict(
            id=self.trigger.id,
            session_name=idle_poll.scope_session(self.trigger),
            message=str(spec.get("message") or ""),
            idle_secs=integer("idle_secs", 60),
            max_cycles=integer("max_cycles"),
            active=bool(self.trigger.enabled),
            stop_sentinel_path=str(spec.get("stop_sentinel_path") or ""),
            first_idle_secs=(
                0 if self.state.cycle_count > 0 else integer("first_idle_secs")
            ),
        )
        values.update(
            cycle_count=self.state.cycle_count,
            last_fire_ts=self.state.last_fire,
            created_ts=self.state.created_ts,
            error_count=self.state.error_count,
        )
        return NudgeLoop(**values)


class LegacyNudges:
    def __init__(self, service: AutoNudgeService):
        self.service = service
        self.path = service._base_dir / _LEGACY_FILE

    def import_row(self, raw: dict) -> bool:
        identity = str(raw.get("id") or "") or f"{_ID_PREFIX}{uuid.uuid4().hex[:8]}"
        session = str(raw.get("session_name") or "")
        if not session:
            return False
        row = self.service._build_row(
            loop_id=identity,
            session_name=session,
            message=str(raw.get("message") or ""),
            idle_secs=int(raw.get("idle_secs", 60) or 60),
            max_cycles=int(raw.get("max_cycles", 0) or 0),
            stop_sentinel_path=str(raw.get("stop_sentinel_path") or ""),
            first_idle_secs=int(raw.get("first_idle_secs", 0) or 0),
            enabled=bool(raw.get("active", True)),
        )
        self.service._store.upsert(row)
        state = idle_poll.load_state(identity, base_dir=self.service._base_dir)
        for attribute, key, convert in (
            ("cycle_count", "cycle_count", int),
            ("last_fire", "last_fire_ts", float),
            ("error_count", "error_count", int),
            ("created_ts", "created_ts", float),
        ):
            setattr(state, attribute, convert(raw.get(key, 0) or 0))
        state.armed_at = time.time()
        idle_poll.save_state(identity, state, base_dir=self.service._base_dir)
        return True

    def run(self) -> None:
        if not self.path.exists():
            return
        try:
            record = json.loads(self.path.read_text(encoding="utf-8"))
            rows = record.get("loops", []) if isinstance(record, dict) else []
        except Exception:
            logger.warning(
                "AutoNudge: legacy %s unreadable — leaving in place", self.path
            )
            return
        imported = 0
        for raw in rows:
            if isinstance(raw, dict):
                try:
                    imported += int(self.import_row(raw))
                except Exception:
                    logger.warning(
                        "AutoNudge: skipping malformed legacy loop: %r",
                        raw,
                        exc_info=True,
                    )
        try:
            self.path.rename(self.path.with_suffix(".json.migrated"))
        except Exception:
            logger.warning(
                "AutoNudge: could not rename migrated %s", self.path, exc_info=True
            )
        logger.info(
            "AutoNudge: migrated %d legacy loops into the trigger store", imported
        )


@dataclass
class NudgeAttempt:
    service: AutoNudgeService
    loop: NudgeLoop
    instant: float

    async def run(self) -> tuple[bool, str]:
        loop, service = self.loop, self.service
        if shutdown_event.is_set():
            return False, "shutting_down"
        if loop.stop_sentinel_path and Path(loop.stop_sentinel_path).exists():
            logger.info(
                "AutoNudge: stop sentinel found for %s — removing loop", loop.id
            )
            await service.remove(loop.id)
            return False, "stop_sentinel"
        if loop.max_cycles and loop.cycle_count >= loop.max_cycles:
            logger.info("AutoNudge: loop %s reached max_cycles — deactivating", loop.id)
            await service.update(loop.id, active=False)
            return False, "max_cycles"
        from gideon.workspace.capabilities.identity.lifecycle import nudge_allowed

        if not nudge_allowed(loop.session_name):
            return False, "identity_policy_blocked"
        if service._on_fire is None:
            return False, "no_deliverer"
        try:
            delivered = await service._on_fire(loop)
        except Exception:
            logger.exception("AutoNudge fire callback failed for %s", loop.id)
            delivered = False
        if not delivered:
            return False, idle_poll.SKIP_SESSION_BUSY
        idle_poll.record_delivery(
            loop.id, delivered=True, now=self.instant, base_dir=service._base_dir
        )
        loop.cycle_count += 1
        loop.last_fire_ts, loop.first_idle_secs = self.instant, 0
        service._emit("fired", loop)
        return True, ""


def enabled() -> bool:
    return os.environ.get("GIDEON_AUTONUDGE", "1").lower() not in {"0", "false", "no"}


def get_instance() -> AutoNudgeService | None:
    return _INSTANCE


def is_nudge(trigger: Any) -> bool:
    return idle_poll.IdleSettings.read(trigger).nudges


def _idle_period(value: Any) -> int:
    return max(_MIN_IDLE_SECS, min(_MAX_IDLE_SECS, int(value)))


class AutoNudgeService:
    def __init__(
        self,
        base_dir: Path | None = None,
        on_fire: Callable[[NudgeLoop], Awaitable[bool]] | None = None,
    ) -> None:
        from gideon.core.config.loader import config_dir

        self._base_dir = base_dir or config_dir()
        self._store = TriggerStore(base_dir=self._base_dir)
        self._on_fire = on_fire
        self._observers: list[Callable[[str, NudgeLoop | None], None]] = []
        self._lock = asyncio.Lock()

    def _rows(self) -> list[Trigger]:
        try:
            rows = self._store.list_triggers(kind="idle")
        except Exception:
            logger.warning("nudge: trigger store unreadable", exc_info=True)
            return []
        return list(filter(is_nudge, rows))

    def _to_loop(self, trigger: Trigger) -> NudgeLoop:
        state = idle_poll.load_state(trigger.id, base_dir=self._base_dir)
        return NudgeRecord(trigger, state).project()

    def _get_row(self, loop_id: str) -> Trigger | None:
        row = self._store.get(loop_id)
        return row.trigger if row is not None and is_nudge(row.trigger) else None

    def subscribe(self, cb: Callable[[str, NudgeLoop | None], None]) -> None:
        self._observers.append(cb)

    def _emit(self, event: str, loop: NudgeLoop | None) -> None:
        for listener in self._observers:
            try:
                listener(event, loop)
            except Exception:
                logger.warning("AutoNudge observer failed", exc_info=True)

    def _rearm(self, identity: str, instant: float) -> None:
        state = idle_poll.load_state(identity, base_dir=self._base_dir)
        state.armed_at = instant
        idle_poll.save_state(identity, state, base_dir=self._base_dir)

    async def start(self) -> None:
        if not enabled():
            logger.info("AutoNudge disabled (GIDEON_AUTONUDGE=0)")
            return
        self._migrate_legacy()
        instant = time.time()
        count = 0
        for trigger in self._rows():
            if trigger.enabled:
                self._rearm(trigger.id, instant)
                count += 1
        global _INSTANCE
        _INSTANCE = self
        logger.info("AutoNudge started (%d loops re-armed)", count)

    def stop(self) -> None:
        global _INSTANCE
        if self is _INSTANCE:
            _INSTANCE = None

    def _migrate_legacy(self) -> None:
        LegacyNudges(self).run()

    def _build_row(
        self,
        *,
        loop_id: str,
        session_name: str,
        message: str,
        idle_secs: int,
        max_cycles: int,
        stop_sentinel_path: str,
        first_idle_secs: int,
        enabled: bool = True,
    ) -> Trigger:
        spec = dict(
            scope=f"session:{session_name}",
            idle_secs=idle_secs,
            first_idle_secs=first_idle_secs,
            message=message,
            max_cycles=max_cycles,
            stop_sentinel_path=stop_sentinel_path,
        )
        return Trigger(
            id=loop_id,
            name=f"Auto-nudge — {session_name}",
            kind="idle",
            enabled=enabled,
            created_by="system",
            spec=spec,
            session=f"conversation:{session_name}",
            overlap="skip",
            delivery="none",
        )

    async def add(
        self,
        session_name: str,
        message: str,
        idle_secs: int = 60,
        max_cycles: int = 0,
        stop_sentinel_path: str = "",
        first_idle_secs: int = 0,
    ) -> NudgeLoop:
        period = _idle_period(idle_secs)
        initial = (
            min(period, max(_MIN_IDLE_SECS, int(first_idle_secs)))
            if first_idle_secs
            else 0
        )
        async with self._lock:
            previous = self._find_by_session(session_name)
            if previous:
                self.remove_sync(previous.id)
            trigger = self._build_row(
                loop_id=f"{_ID_PREFIX}{uuid.uuid4().hex[:8]}",
                session_name=session_name,
                message=message,
                idle_secs=period,
                max_cycles=max(0, int(max_cycles)),
                stop_sentinel_path=stop_sentinel_path,
                first_idle_secs=initial,
            )
            self._store.upsert(trigger)
            instant = time.time()
            idle_poll.save_state(
                trigger.id,
                idle_poll.IdleState(armed_at=instant, created_ts=instant),
                base_dir=self._base_dir,
            )
            loop = self._to_loop(trigger)
        self._emit("added", loop)
        logger.info(
            "AutoNudge: added loop %s on session %s (idle=%ds)",
            loop.id,
            session_name,
            period,
        )
        return loop

    async def update(
        self,
        loop_id: str,
        *,
        message: str | None = None,
        idle_secs: int | None = None,
        max_cycles: int | None = None,
        active: bool | None = None,
    ) -> NudgeLoop | None:
        async with self._lock:
            trigger = self._get_row(loop_id)
            if trigger is None:
                return None
            values = dict(trigger.spec or {})
            updates = (
                ("message", message, lambda value: value),
                ("idle_secs", idle_secs, _idle_period),
                ("max_cycles", max_cycles, lambda value: max(0, int(value))),
            )
            for key, value, convert in updates:
                if value is not None:
                    values[key] = convert(value)
            trigger.spec = values
            if active is not None:
                trigger.enabled = bool(active)
            self._store.upsert(trigger)
            if trigger.enabled:
                self._rearm(loop_id, time.time())
            loop = self._to_loop(trigger)
        self._emit("updated", loop)
        return loop

    def remove_sync(self, loop_id: str) -> None:
        row = self._get_row(loop_id)
        if row is not None:
            view = self._to_loop(row)
            self._store.delete(loop_id)
            idle_poll.clear_state(loop_id, base_dir=self._base_dir)
            self._emit("removed", view)

    async def remove(self, loop_id: str) -> None:
        async with self._lock:
            self.remove_sync(loop_id)

    def get_by_session(self, session_name: str) -> NudgeLoop | None:
        return self._find_by_session(session_name)

    def list_all(self) -> list[NudgeLoop]:
        return list(map(self._to_loop, self._rows()))

    def _find_by_session(self, session_name: str) -> NudgeLoop | None:
        row = next(
            (
                trigger
                for trigger in self._rows()
                if idle_poll.scope_session(trigger) == session_name
            ),
            None,
        )
        return self._to_loop(row) if row is not None else None

    def notify_turn_complete(self, session_name: str, *, errored: bool = False) -> None:
        loop = self._find_by_session(session_name)
        if not loop or not loop.active:
            return
        state = idle_poll.load_state(loop.id, base_dir=self._base_dir)
        state.error_count = state.error_count + 1 if errored else 0
        if errored and state.error_count >= _MAX_CONSECUTIVE_ERRORS:
            logger.warning(
                "AutoNudge: loop %s hit %d consecutive errors — deactivating",
                loop.id,
                state.error_count,
            )
            idle_poll.save_state(loop.id, state, base_dir=self._base_dir)
            trigger = self._get_row(loop.id)
            if trigger is not None:
                trigger.enabled = False
                self._store.upsert(trigger)
            loop.active, loop.error_count = False, state.error_count
            self._emit("errored_out", loop)
        else:
            state.armed_at = time.time()
            idle_poll.save_state(loop.id, state, base_dir=self._base_dir)

    def notify_user_input(self, session_name: str) -> None:
        loop = self._find_by_session(session_name)
        if loop:
            self._rearm(loop.id, time.time())

    async def deliver(self, trigger: Any, *, now: float = 0.0) -> tuple[bool, str]:
        instant = now or time.time()
        return await NudgeAttempt(self, self._to_loop(trigger), instant).run()
