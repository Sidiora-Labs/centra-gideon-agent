"""Owner-issued, expiring approval leases for one persisted loop run."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from gideon.automation.loop import store
from gideon.automation.loop.loop import ACTIVE_STATUSES, PRELAUNCH_STATUSES
from gideon.core.config.loader import AppConfig


@dataclass
class _RunGrant:
    marker: tuple
    deadline: float
    receipt: str
    workers: dict[str, tuple[Any, bool, bool]] = field(default_factory=dict)
    timer: Any = None


def register_worker(state, session, loop):
    """Associate the actual worker object armed by the host loop lifecycle."""
    owners = state.__dict__.setdefault("_loop_approval_workers", {})
    owners[session.key] = (session, loop.id)


def owner_loop(state, session):
    binding = state.__dict__.get("_loop_approval_workers", {}).get(session.key)
    if (
        binding is None
        or binding[0] is not session
        or getattr(session, "created_by_app", False)
    ):
        return None
    try:
        return store.get(binding[1])
    except Exception:
        return None


def _marker(loop):
    return (loop.created_at, loop.started_at)


def offer(state, session):
    loop = owner_loop(state, session)
    if (
        loop is None
        or not loop.attended
        or loop.status not in ACTIVE_STATUSES | PRELAUNCH_STATUSES
    ):
        return None
    return {
        "loop_id": loop.id,
        "run_started_at": loop.started_at,
        "duration_seconds": max(0, AppConfig.load().loops.trust_ttl_secs),
        "name": loop.name,
    }


def _registry(state):
    return state.__dict__.setdefault("_loop_run_grants", {})


def revoke(state, loop_id):
    grant = _registry(state).pop(loop_id, None)
    if grant is None:
        return
    if grant.timer is not None:
        grant.timer.cancel()
    from gideon.interfaces.dashboard.chat_utils import _history_key_for

    for key, (session, floor_trust, floor_seeded) in grant.workers.items():
        session._trust = floor_trust
        session._agent_floor_seeded = floor_seeded
        try:
            state.sessions.set_approval_policy(_history_key_for(key), "")
        except Exception:
            import logging

            logging.getLogger(__name__).warning(
                "Failed to revoke loop worker policy %s", key, exc_info=True
            )


def refresh(state, session):
    loop = owner_loop(state, session)
    if loop is None:
        binding = state.__dict__.get("_loop_approval_workers", {}).get(session.key)
        if binding is not None and binding[0] is session:
            revoke(state, binding[1])
        return False
    grant = _registry(state).get(loop.id)
    if grant is None:
        return False
    if (
        not loop.attended
        or loop.status not in ACTIVE_STATUSES | PRELAUNCH_STATUSES
        or _marker(loop) != grant.marker
        or time.monotonic() >= grant.deadline
    ):
        revoke(state, loop.id)
        return False
    from gideon.security.approval_grants import TRUST, stands

    if not stands(TRUST, caller=session.key, subject=f"loop={loop.id}", audit=False):
        revoke(state, loop.id)
        return False
    grant.workers.setdefault(
        session.key,
        (
            session,
            bool(session._trust and session._agent_floor_seeded),
            bool(session._agent_floor_seeded),
        ),
    )
    session._agent_floor_seeded = False
    session._trust = True
    return True


def issue(state, session, receipt, offered):
    current = offer(state, session)
    if current is None or current != offered or current["duration_seconds"] <= 0:
        return False
    loop = owner_loop(state, session)
    revoke(state, loop.id)
    grant = _RunGrant(
        _marker(loop), time.monotonic() + current["duration_seconds"], receipt
    )
    _registry(state)[loop.id] = grant
    grant.timer = asyncio.get_running_loop().call_later(
        current["duration_seconds"], revoke, state, loop.id
    )
    from gideon.interfaces.dashboard.chat_utils import _history_key_for

    for worker in state._sessions.values():
        if refresh(state, worker):
            state.sessions.set_approval_policy(_history_key_for(worker.key), "")
    return True
