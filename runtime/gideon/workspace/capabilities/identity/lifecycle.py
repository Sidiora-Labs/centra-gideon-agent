"""Explicit identity authority over one canonical autonomous loop."""

import hashlib
import json
import re
import sqlite3
import time
import uuid
from dataclasses import replace
from pathlib import Path

from gideon.automation.loop import manager
from gideon.automation.loop import store as loops
from gideon.automation.loop.loop import ACTION_SOURCE_STATES, LoopStatus
from gideon.core.config import config_dir
from gideon.workspace.capabilities.identity.lifecycle_route import (
    require_route,
    route_fingerprint,
)
from gideon.workspace.capabilities.identity.store import ConflictError

PRESETS = {
    "reflect": "Reflect on current identity anchors and the current loop objective. Report uncertainty; do not change human-authored anchors or expand permissions.",
    "review": "Review recorded progress toward the current loop objective. Distinguish evidence from inference and propose one bounded next step within current permissions.",
}


class LifecycleStore:
    def __init__(self, home: Path):
        self.home = Path(home).resolve()
        self.path = self.home / "capabilities/identity/lifecycle.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.executescript(
                "CREATE TABLE IF NOT EXISTS policy(id INTEGER PRIMARY KEY,body TEXT NOT NULL);CREATE TABLE IF NOT EXISTS operations(request_id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,body TEXT NOT NULL);CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,request_id TEXT UNIQUE,body TEXT NOT NULL);"
            )

    def _home(self):
        if self.home != config_dir().resolve():
            raise ValueError("Lifecycle engine belongs to a different active home")

    def policy(self):
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT body FROM policy WHERE id=1").fetchone()
        return (
            json.loads(row[0])
            if row
            else {
                "revision": 0,
                "enabled": False,
                "loop_id": None,
                "route_fingerprint": None,
            }
        )

    def _loop(self):
        self._home()
        row = loops.get(self.policy()["loop_id"]) if self.policy()["loop_id"] else None
        if row is None:
            raise ValueError("Bind an existing autonomous loop first")
        return row

    def status(self):
        self._home()
        policy = self.policy()
        row = loops.get(policy["loop_id"]) if policy["loop_id"] else None
        with sqlite3.connect(self.path) as db:
            requests = [
                json.loads(r[0])
                for r in db.execute("SELECT body FROM requests ORDER BY rowid")
            ]
            journal = [
                json.loads(r[0])
                for r in db.execute("SELECT body FROM operations ORDER BY rowid")
            ]
        return {
            "policy": policy,
            "loop": (
                {"id": row.id, "name": row.name, "status": LoopStatus(row.status).value}
                if row
                else None
            ),
            "requests": requests,
            "journal": journal,
            "provider_readiness": "unknown",
            "scope": "One bound autonomous loop; pause and stop prevent future cycles. Cancel current turn is separate.",
        }

    def _claim(self, request_id, fields):
        if not isinstance(request_id, str) or not 1 <= len(request_id.strip()) <= 128:
            raise ValueError("request_id is required")
        fingerprint = hashlib.sha256(
            json.dumps(fields, sort_keys=True).encode()
        ).hexdigest()
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT fingerprint,body FROM operations WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if row:
                if row[0] != fingerprint:
                    raise ConflictError(
                        "Request identifier names a different operation"
                    )
                return json.loads(row[1])
            revision = self.policy()["revision"]
            if (
                type(fields["expected_revision"]) is not int
                or fields["expected_revision"] != revision
            ):
                raise ConflictError("Lifecycle policy changed; reload")
            body = {
                "request_id": request_id,
                "operation": fields,
                "status": "applying",
                "created_at": time.time(),
            }
            db.execute(
                "INSERT INTO operations VALUES(?,?,?)",
                (request_id, fingerprint, json.dumps(body)),
            )
        return None

    def _finish(self, request_id, status, **fields):
        with sqlite3.connect(self.path) as db:
            body = json.loads(
                db.execute(
                    "SELECT body FROM operations WHERE request_id=?", (request_id,)
                ).fetchone()[0]
            )
            body.update(status=status, **fields)
            db.execute(
                "UPDATE operations SET body=? WHERE request_id=?",
                (json.dumps(body), request_id),
            )
        return body

    async def configure(
        self, *, loop_id, enabled, expected_revision, request_id, state, service
    ):
        self._home()
        if (
            type(enabled) is not bool
            or not isinstance(loop_id, str)
            or not re.fullmatch(r"[0-9a-f]{8}", loop_id)
        ):
            raise ValueError("Expected canonical loop ID and enabled boolean")
        if state is None or service is None:
            raise ValueError("Loop engine unavailable")
        async with manager.dashboard_boundary_lock(state, "identity-policy"):
            previous = self.policy()
            if previous["loop_id"] and previous["loop_id"] != loop_id:
                raise ValueError("This identity is already bound to another loop")
            async with manager.dashboard_boundary_lock(state, loop_id):
                replay = self._claim(
                    request_id,
                    dict(
                        loop_id=loop_id,
                        enabled=enabled,
                        expected_revision=expected_revision,
                    ),
                )
                if replay:
                    return replay
                try:
                    previous = self.policy()
                    loop = loops.get(loop_id)
                    if loop is None:
                        raise ValueError("Loop not found")
                    if enabled:
                        require_route(loop)
                    elif loop.status == LoopStatus.RUNNING:
                        await manager.pause(state, service, loop_id)
                    policy = {
                        "revision": previous["revision"] + 1,
                        "enabled": enabled,
                        "loop_id": loop_id,
                        "route_fingerprint": (
                            route_fingerprint(loop)
                            if enabled
                            else previous["route_fingerprint"]
                        ),
                    }
                    with sqlite3.connect(self.path) as db:
                        db.execute(
                            "INSERT OR REPLACE INTO policy VALUES(1,?)",
                            (json.dumps(policy),),
                        )
                    return self._finish(request_id, "applied", policy=policy)
                except Exception as error:
                    self._finish(request_id, "failed", error=str(error))
                    raise

    async def action(self, *, action, expected_revision, request_id, state, service):
        loop = self._loop()
        if action not in (*ACTION_SOURCE_STATES, "cancel_turn") or action not in (
            "start",
            "resume",
            "pause",
            "stop",
            "cancel_turn",
        ):
            raise ValueError("Unsupported lifecycle action")
        if state is None or service is None:
            raise ValueError("Loop engine unavailable")
        async with manager.dashboard_boundary_lock(state, loop.id):
            replay = self._claim(
                request_id, dict(action=action, expected_revision=expected_revision)
            )
            if replay:
                return replay
            try:
                loop = self._loop()
                if action == "cancel_turn":
                    from gideon.interfaces.dashboard.chat_utils import _history_key_for

                    outcome = await state.sessions.stop_turn(
                        _history_key_for(manager.session_key(loop.id)),
                        preserve_queue=True,
                    )
                    return self._finish(request_id, "applied", cancellation=outcome)
                if loop.status not in ACTION_SOURCE_STATES[action]:
                    raise ValueError("Action is not available in current loop state")
                if action in ("start", "resume"):
                    require_loop_allowed(loop)
                result = await getattr(
                    manager, "start" if action == "resume" else action
                )(state, service, loop.id)
                return self._finish(
                    request_id, "applied", loop_status=LoopStatus(result.status).value
                )
            except Exception as error:
                self._finish(request_id, "failed", error=str(error))
                raise

    def request_thinking(self, *, request_id, preset):
        loop = self._loop()
        require_loop_allowed(loop)
        if (
            preset not in PRESETS
            or not isinstance(request_id, str)
            or not 1 <= len(request_id) <= 128
        ):
            raise ValueError("Choose a bounded thinking preset and request identifier")
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            rows = [json.loads(r[0]) for r in db.execute("SELECT body FROM requests")]
            prior = next((r for r in rows if r["request_id"] == request_id), None)
            if prior:
                if prior["preset"] != preset:
                    raise ConflictError("Request identifier names a different preset")
                return prior
            now = time.time()
            if (
                any(r["status"] in ("pending", "running") for r in rows)
                or sum(r["created_at"] // 86400 == now // 86400 for r in rows) >= 3
                or any(now - r["created_at"] < 1800 for r in rows)
            ):
                raise ConflictError(
                    "Thinking request quota reached: one pending, three per UTC day, thirty minutes apart"
                )
            row = {
                "id": uuid.uuid4().hex,
                "request_id": request_id,
                "preset": preset,
                "status": "pending",
                "created_at": now,
                "route_fingerprint": self.policy()["route_fingerprint"],
                "policy_revision": self.policy()["revision"],
            }
            db.execute(
                "INSERT INTO requests VALUES(?,?,?)",
                (row["id"], request_id, json.dumps(row)),
            )
        return row

    async def dispatch(self, *, id, state, service):
        loop = self._loop()
        if state is None or service is None:
            raise ValueError("Loop engine unavailable")
        async with manager.dashboard_boundary_lock(state, loop.id):
            with sqlite3.connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                found = db.execute(
                    "SELECT body FROM requests WHERE id=?", (id,)
                ).fetchone()
                if found is None:
                    raise KeyError(id)
                row = json.loads(found[0])
                if row["status"] != "pending":
                    return row
                row["status"] = "running"
                db.execute(
                    "UPDATE requests SET body=? WHERE id=?", (json.dumps(row), id)
                )
            try:
                require_loop_allowed(loop)
                if row["policy_revision"] != self.policy()["revision"] or row[
                    "route_fingerprint"
                ] != route_fingerprint(loop):
                    raise ValueError("Thinking request approval changed")
                trigger = service.get_by_session(manager.session_key(loop.id))
                if (
                    loop.status != LoopStatus.RUNNING
                    or trigger is None
                    or not trigger.active
                ):
                    raise ValueError("Bound loop must be running with an active worker")
                from gideon.automation.triggers.nudge import NudgeAttempt

                delivered, reason = await NudgeAttempt(
                    service,
                    replace(trigger, message=PRESETS[row["preset"]]),
                    time.time(),
                ).run()
                row.update(
                    status="delivered" if delivered else "blocked",
                    reason=reason,
                    meaning="Delivery receipt only; model completion and provider readiness remain unknown",
                )
            except Exception as error:
                row.update(status="blocked", reason=str(error))
            with sqlite3.connect(self.path) as db:
                db.execute(
                    "UPDATE requests SET body=? WHERE id=?", (json.dumps(row), id)
                )
            return row


def require_loop_allowed(loop):
    path = config_dir() / "capabilities/identity/lifecycle.sqlite3"
    if not path.exists():
        return
    policy = LifecycleStore(config_dir()).policy()
    if policy["loop_id"] != loop.id:
        return
    if not policy["enabled"]:
        raise ValueError("Bound identity is disabled")
    require_route(loop)
    if policy["route_fingerprint"] != route_fingerprint(loop):
        raise ValueError("Bound identity route changed; human approval required")


def nudge_allowed(session_name):
    path = config_dir() / "capabilities/identity/lifecycle.sqlite3"
    if not path.exists():
        return True
    policy = LifecycleStore(config_dir()).policy()
    identifier = policy["loop_id"]
    if not identifier or not (
        session_name == manager.session_key(identifier)
        or session_name.startswith(manager.session_key(identifier) + "-")
    ):
        return True
    try:
        loop = loops.get(identifier)
        if loop is None or loop.status != LoopStatus.RUNNING:
            return False
        require_loop_allowed(loop)
        return True
    except (ValueError, KeyError, PermissionError):
        return False
