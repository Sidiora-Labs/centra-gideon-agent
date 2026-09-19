"""Persist quiet-period timing and route idle work to wake or nudge delivery."""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from gideon.automation.triggers.provider import armable

logger = logging.getLogger(__name__)
DEFAULT_IDLE_SECS = 60.0
_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")
SKIP_NOT_IDLE = "not_idle_yet"
SKIP_AUTONUDGE = "autonudge_owns_session"
SKIP_SESSION_BUSY = "session_mid_turn"
SKIP_NUDGE_UNAVAILABLE = "nudge_service_unavailable"


@dataclass
class IdleState:
    armed_at: float = 0.0
    cycle_count: int = 0
    last_fire: float = 0.0
    error_count: int = 0
    created_ts: float = 0.0


@dataclass(frozen=True)
class IdleSettings:
    values: dict[str, Any]

    @classmethod
    def read(cls, trigger: Any) -> IdleSettings:
        return cls(
            trigger.spec if isinstance(getattr(trigger, "spec", None), dict) else {}
        )

    @property
    def session(self) -> str:
        text = str(self.values.get("scope") or "").strip()
        return text.partition(":")[2].strip() if text.startswith("session:") else ""

    @property
    def nudges(self) -> bool:
        return bool(str(self.values.get("message") or "").strip())

    def waiting_period(self, cycles: int) -> float:
        ordinary = _secs(self.values, "idle_secs", DEFAULT_IDLE_SECS)
        try:
            initial = float(self.values.get("first_idle_secs", 0) or 0)
        except (TypeError, ValueError):
            initial = 0.0
        return initial if initial > 0 and cycles == 0 else ordinary


@dataclass(frozen=True)
class IdleJournal:
    identity: str
    path: Path

    def read(self) -> IdleState:
        try:
            record = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return IdleState()
        except Exception:
            logger.warning(
                "idle state unreadable for %s; treating as fresh", self.identity
            )
            return IdleState()
        if not isinstance(record, dict):
            return IdleState()
        return IdleState(
            armed_at=float(record.get("armed_at") or 0.0),
            cycle_count=int(record.get("cycle_count") or 0),
            last_fire=float(record.get("last_fire") or 0.0),
            error_count=int(record.get("error_count") or 0),
            created_ts=float(record.get("created_ts") or 0.0),
        )

    def write(self, state: IdleState) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            from gideon.core.atomic_write import atomic_write

            atomic_write(self.path, json.dumps(asdict(state), indent=2))
        except Exception:
            logger.warning(
                "could not persist idle state for %s", self.identity, exc_info=True
            )


@dataclass
class IdleSelection:
    instant: float
    base_dir: Path | str | None
    owned_sessions: set[str]

    def reason(self, trigger: Any, state: IdleState) -> str:
        if state.armed_at <= 0:
            state.armed_at = self.instant
            save_state(trigger.id, state, base_dir=self.base_dir)
            return SKIP_NOT_IDLE
        if not _is_nudge(trigger) and scope_session(trigger) in self.owned_sessions:
            return SKIP_AUTONUDGE
        due, reason = is_idle(trigger, state, now=self.instant)
        return "" if due else reason


@dataclass
class IdleDeliveryBatch:
    instant: float
    base_dir: Path | str | None
    skipped: list[dict[str, str]]

    def skip(self, identity: str, reason: str) -> None:
        self.skipped.append(dict(trigger_id=identity, reason=reason))

    async def nudges(self, fires: list[Any]) -> int:
        if not fires:
            return 0
        from gideon.automation.triggers import nudge

        service = nudge.get_instance()
        delivered = 0
        for fire in fires:
            if service is None:
                self.skip(fire.trigger.id, SKIP_NUDGE_UNAVAILABLE)
                continue
            try:
                accepted, reason = await service.deliver(fire.trigger, now=self.instant)
            except Exception:
                logger.warning(
                    "nudge deliver failed for %s", fire.trigger.id, exc_info=True
                )
                accepted, reason = False, "deliver_error"
            if accepted:
                delivered += 1
            else:
                self.skip(fire.trigger.id, reason or SKIP_SESSION_BUSY)
        return delivered

    async def wakes(self, fires: list[Any], sessions: Any, runner: Any) -> int:
        from gideon.automation.triggers import executor, wakeup

        if not fires:
            return 0
        if sessions is None:
            for fire in fires:
                self.skip(fire.trigger.id, "no_session_manager")
            return 0
        receipts = wakeup.dispatch_fires(sessions, fires, now=self.instant)
        by_identity = {receipt.wakeup.trigger_id: receipt for receipt in receipts}
        delivered_count = 0
        for fire in fires:
            receipt = by_identity.get(fire.trigger.id)
            delivered = bool(receipt is not None and receipt.delivered)
            record_delivery(
                fire.trigger.id,
                delivered=delivered,
                now=self.instant,
                base_dir=self.base_dir,
            )
            if not delivered:
                reason = (
                    str(getattr(receipt, "disposition", "") or SKIP_SESSION_BUSY)
                    if receipt is not None
                    else SKIP_SESSION_BUSY
                )
                self.skip(fire.trigger.id, reason)
                continue
            delivered_count += 1
            key = wakeup.session_key_for(
                fire.trigger.id, session=str(getattr(fire.trigger, "session", ""))
            )
            try:
                await executor.drain(
                    sessions, key, runner, now=self.instant, base_dir=self.base_dir
                )
            except Exception:
                logger.warning(
                    "idle drain failed for %s", fire.trigger.id, exc_info=True
                )
        return delivered_count


def _state_dir(base_dir: Path | str | None) -> Path:
    from gideon.core.config.loader import config_dir

    directory = Path(base_dir) if base_dir else config_dir()
    return directory / "trigger-idle"


def _state_path(trigger_id: str, base_dir: Path | str | None) -> Path:
    filename = (_SAFE_RE.sub("-", trigger_id) or "idle") + ".json"
    return _state_dir(base_dir) / filename


def load_state(trigger_id: str, *, base_dir: Path | str | None = None) -> IdleState:
    return IdleJournal(trigger_id, _state_path(trigger_id, base_dir)).read()


def save_state(
    trigger_id: str, state: IdleState, *, base_dir: Path | str | None = None
) -> None:
    IdleJournal(trigger_id, _state_path(trigger_id, base_dir)).write(state)


def clear_state(trigger_id: str, *, base_dir: Path | str | None = None) -> None:
    try:
        path = _state_path(trigger_id, base_dir)
        path.unlink(missing_ok=True)
    except Exception:
        logger.debug("could not clear idle state for %s", trigger_id, exc_info=True)


def idle_triggers(store: Any) -> list[Any]:
    return [
        trigger
        for trigger in armable(store)
        if trigger.kind == "idle" and trigger.fires_automatically
    ]


def scope_session(trigger: Any) -> str:
    return IdleSettings.read(trigger).session


def _secs(spec: dict[str, Any], key: str, default: float) -> float:
    try:
        parsed = float(spec.get(key, default))
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def wait_secs(trigger: Any, state: IdleState) -> float:
    return IdleSettings.read(trigger).waiting_period(state.cycle_count)


def is_idle(trigger: Any, state: IdleState, *, now: float) -> tuple[bool, str]:
    if state.armed_at <= 0 or now - state.armed_at < wait_secs(trigger, state):
        return False, SKIP_NOT_IDLE
    return True, ""


def notify_activity(
    trigger_ids: list[str] | None = None,
    *,
    session_key: str = "",
    store: Any = None,
    now: float = 0.0,
    base_dir: Path | str | None = None,
) -> list[str]:
    instant = now or time.time()
    selected = list(trigger_ids or [])
    if not selected and store is not None and session_key:
        selected = [
            trigger.id
            for trigger in idle_triggers(store)
            if scope_session(trigger) == session_key
        ]
    for identity in selected:
        state = load_state(identity, base_dir=base_dir)
        state.armed_at = instant
        save_state(identity, state, base_dir=base_dir)
    return selected


def _is_nudge(trigger: Any) -> bool:
    return IdleSettings.read(trigger).nudges


def _nudge_owned_sessions(triggers: list[Any]) -> set[str]:
    owners = set(map(scope_session, filter(_is_nudge, triggers)))
    owners.discard("")
    return owners


def due_fires(
    store: Any, *, now: float = 0.0, base_dir: Path | str | None = None
) -> tuple[list[Any], list[dict[str, str]]]:
    from gideon.automation.triggers.service import DueFire

    instant = now or time.time()
    candidates = idle_triggers(store)
    selection = IdleSelection(instant, base_dir, _nudge_owned_sessions(candidates))
    ready, skipped = [], []
    for trigger in candidates:
        try:
            state = load_state(trigger.id, base_dir=base_dir)
            reason = selection.reason(trigger, state)
            if reason:
                skipped.append(dict(trigger_id=trigger.id, reason=reason))
            else:
                ready.append(
                    DueFire(
                        trigger=trigger, scheduled_for=state.armed_at, reason="idle"
                    )
                )
        except Exception:
            logger.warning(
                "idle poll failed for %s", getattr(trigger, "id", "?"), exc_info=True
            )
    return ready, skipped


def record_delivery(
    trigger_id: str,
    *,
    delivered: bool,
    now: float = 0.0,
    base_dir: Path | str | None = None,
) -> IdleState:
    instant = now or time.time()
    state = load_state(trigger_id, base_dir=base_dir)
    if delivered:
        state.cycle_count += 1
        state.last_fire = state.armed_at = instant
        save_state(trigger_id, state, base_dir=base_dir)
    return state


async def poll(
    store: Any,
    sessions: Any,
    runner: Any,
    *,
    now: float = 0.0,
    base_dir: Path | str | None = None,
) -> tuple[int, list[dict[str, str]]]:
    instant = now or time.time()
    fires, skipped = due_fires(store, now=instant, base_dir=base_dir)
    if not fires:
        return 0, skipped
    nudge_fires = [fire for fire in fires if _is_nudge(fire.trigger)]
    wake_fires = [fire for fire in fires if not _is_nudge(fire.trigger)]
    batch = IdleDeliveryBatch(instant, base_dir, skipped)
    delivered = await batch.nudges(nudge_fires)
    delivered += await batch.wakes(wake_fires, sessions, runner)
    return delivered, skipped
