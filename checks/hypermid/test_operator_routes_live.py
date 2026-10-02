from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from gideon.hypermid import HypermidAdapter, HypermidClient, HypermidLifecycle, Scope
from gideon.hypermid.client import HypermidRemoteError
from gideon.hypermid.config import (
    DaemonConfig,
    DaemonTransport,
    LocalAuthConfig,
    LocalAuthMethod,
)
from gideon.hypermid.foundation import Id
from gideon.hypermid.lifecycle import LocalEnrollment
from gideon.hypermid.operations import HypermidOperations


def _daemon_binary() -> str:
    configured = os.environ.get("HYPERMID_DAEMON_BINARY")
    if configured and Path(configured).is_file():
        return str(Path(configured).resolve())
    target = Path(os.environ.get("CARGO_TARGET_DIR", "target"))
    candidate = target / "debug" / "hypermid-daemon"
    if candidate.is_file():
        return str(candidate.resolve())
    raise RuntimeError("Build hypermid-daemon and set HYPERMID_DAEMON_BINARY")


@pytest.mark.asyncio
async def test_real_operator_diagnostics_and_integrity_receipt(tmp_path: Path) -> None:
    scope = Scope(Id("operator-owner"), Id("operator-project"))
    record = tmp_path / "connection.json"
    config = DaemonConfig(
        transport=DaemonTransport.UNIX_SOCKET,
        endpoint=str(tmp_path / "daemon.sock"),
        auth=LocalAuthConfig(LocalAuthMethod.PEER_AND_HMAC, str(record), True),
        executable=_daemon_binary(),
        connection_record=str(record),
        request_timeout_ms=10_000,
    )
    client = HypermidClient(record, scope=scope)
    lifecycle = HypermidLifecycle(
        HypermidAdapter(client, mode="shadow"),
        config,
        connection_record=record,
        enrollment=LocalEnrollment(
            scope=scope,
            credential_id=Id("operator-local-credential"),
            capability_id=Id("operator-local-capability"),
            operations=("read", "revise"),
            resources=(Id("memory-list"), Id("memory-maintenance")),
            expires_ms=int(time.time() * 1000) + 600_000,
        ),
    )
    try:
        started = await lifecycle.start()
        assert started.available and started.healthy, started.to_dict()
        operations = HypermidOperations(client)

        fresh = await operations.diagnostics(refresh=True)
        cached = await operations.diagnostics()
        assert not fresh.cached
        assert cached.cached
        assert cached.cursor == fresh.cursor
        assert all(check.cached for check in cached.checks)
        assert {check.id for check in fresh.checks} >= {
            "store-schema",
            "memory-indexes",
            "memory-vectors",
            "maintenance-queue",
            "maintenance-leases",
        }

        plan = await operations.plan_maintenance("integrity_check")
        with pytest.raises(HypermidRemoteError) as refused:
            await client.request(
                "maintenance.apply",
                {
                    "plan_id": plan.plan_id,
                    "plan_digest": "0" * 64,
                    "confirm_destructive": False,
                    "authority_digest": plan.authority_digest,
                    "blocker_digest": plan.blocker_digest,
                },
                effect_kind="durable",
            )
        assert refused.value.error.code == "PLAN_CHANGED"

        receipt = await operations.apply_maintenance(
            plan, reviewed_digest=plan.plan_digest
        )
        assert receipt.state == "committed"
        assert receipt.cursor is not None
        restored = await operations.maintenance_status(receipt.job_id)
        assert restored == receipt
        unchanged = await operations.cancel_maintenance(receipt.job_id)
        assert unchanged == receipt
    finally:
        await lifecycle.stop()
