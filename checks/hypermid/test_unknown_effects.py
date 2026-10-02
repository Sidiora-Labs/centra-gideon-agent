from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from gideon.hypermid import HypermidAdapter, HypermidClient, HypermidLifecycle, Scope
from gideon.hypermid.config import (
    DaemonConfig,
    DaemonTransport,
    LocalAuthConfig,
    LocalAuthMethod,
)
from gideon.hypermid.effects import EffectLifecycleState, EffectNextAction, EffectService
from gideon.hypermid.foundation import Id
from gideon.hypermid.lifecycle import LocalEnrollment


ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "checks/hypermid/fixtures/effect-seed/Cargo.toml"


def _daemon_binary() -> str:
    configured = os.environ.get("HYPERMID_DAEMON_BINARY")
    if configured and Path(configured).is_file():
        return str(Path(configured).resolve())
    target = Path(os.environ.get("CARGO_TARGET_DIR", "target"))
    candidate = target / "debug" / "hypermid-daemon"
    if candidate.is_file():
        return str(candidate.resolve())
    raise RuntimeError("Build hypermid-daemon and set HYPERMID_DAEMON_BINARY")


def _seed(state_root: Path) -> None:
    environment = os.environ.copy()
    environment["CARGO_BUILD_JOBS"] = "2"
    subprocess.run(
        [
            str(Path.home() / ".cargo/bin/cargo"),
            "run",
            "--quiet",
            "--manifest-path",
            str(SEED),
            "--",
            str(state_root),
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=300,
        check=True,
    )


def _runtime(root: Path, scope: Scope) -> tuple[HypermidLifecycle, EffectService]:
    record = root / "connection.json"
    client = HypermidClient(record, scope=scope)
    lifecycle = HypermidLifecycle(
        HypermidAdapter(client, mode="shadow"),
        DaemonConfig(
            transport=DaemonTransport.UNIX_SOCKET,
            endpoint=str(root / "daemon.sock"),
            auth=LocalAuthConfig(LocalAuthMethod.PEER_AND_HMAC, str(record), True),
            executable=_daemon_binary(),
            connection_record=str(record),
            request_timeout_ms=10_000,
        ),
        connection_record=record,
        enrollment=LocalEnrollment(
            scope=scope,
            credential_id=Id("effect-local-credential"),
            capability_id=Id("effect-local-capability"),
            operations=("read", "revise"),
            resources=(Id("effect-state"),),
            expires_ms=int(time.time() * 1000) + 600_000,
        ),
    )
    return lifecycle, EffectService(client)


@pytest.mark.asyncio
async def test_persisted_unknown_is_reviewed_then_authoritatively_reconciled(
    tmp_path: Path,
) -> None:
    scope = Scope(Id("effect-owner"), Id("effect-project"))
    effect_id = Id("effect-persisted-unknown")
    _seed(tmp_path / "state")

    lifecycle, effects = _runtime(tmp_path, scope)
    try:
        started = await lifecycle.start()
        assert started.available and started.healthy, started.to_dict()
        unresolved = await effects.list_unresolved(scope)
        assert unresolved.scope == scope
        assert [item.effect_id for item in unresolved.effects] == [effect_id]
        unknown = unresolved.effects[0]
        assert unknown.state is EffectLifecycleState.UNKNOWN
        assert unknown.reviewable
        assert unknown.next_action is EffectNextAction.CHECK_AUTHORITATIVE_STATUS

        reviewed = await effects.mark_reviewed(
            scope, effect_id, review_id=Id("effect-review-persisted")
        )
        assert reviewed.effect_id == effect_id
        assert reviewed.idempotency_key == effect_id
        assert reviewed.proposed_state is EffectLifecycleState.COMMITTED
    finally:
        await lifecycle.stop()

    lifecycle, effects = _runtime(tmp_path, scope)
    try:
        restarted = await lifecycle.start()
        assert restarted.available and restarted.healthy, restarted.to_dict()
        resumed = await effects.status(scope, effect_id)
        assert resumed.state is EffectLifecycleState.UNKNOWN
        assert resumed.review_plan == reviewed
        assert not resumed.reviewable

        resolved = await effects.reconcile(scope, resumed.review_plan)
        assert resolved.state is EffectLifecycleState.COMMITTED
        assert resolved.next_action is EffectNextAction.RESOLVED
        assert resolved.result_digest == reviewed.result_digest
        assert await effects.status(scope, effect_id) == resolved
    finally:
        await lifecycle.stop()
