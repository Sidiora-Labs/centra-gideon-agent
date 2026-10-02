from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from gideon.hypermid.adapter import HypermidAdapter
from gideon.hypermid.client import HypermidClient
from gideon.hypermid.config import (
    DaemonConfig,
    DaemonTransport,
    LocalAuthConfig,
    LocalAuthMethod,
)
from gideon.hypermid.contracts import (
    AccessRequest,
    GrantOperation,
    MemoryOperation,
    MutationRequest,
    RecordDraft,
    RecordKind,
    RevisionPrecondition,
    SearchMode,
    SearchRequest,
)
from gideon.hypermid.foundation import Id, Trace
from gideon.hypermid.handlers import HypermidHandlerError, service_for
from gideon.hypermid.lifecycle import HypermidLifecycle, LocalEnrollment
from gideon.hypermid.memory_client import MemoryClient
from gideon.hypermid.models import Scope


def _daemon_binary() -> Path:
    configured = os.environ.get("HYPERMID_DAEMON_BINARY")
    if configured:
        return Path(configured)
    target = Path(os.environ.get("CARGO_TARGET_DIR", "target"))
    candidate = target / "debug" / "hypermid-daemon"
    if candidate.is_file():
        return candidate
    raise RuntimeError("focused local install journey requires the built daemon binary")


@pytest.mark.asyncio
async def test_zero_state_reviewed_install_launches_authenticated_daemon(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    binary = _daemon_binary()
    runtime_root = tmp_path / "hypermid" / "runtime"
    record = runtime_root / "connection.json"
    scope = Scope("owner-1", "project-1", "workspace-1")
    client = HypermidClient(record, scope=scope)
    lifecycle = HypermidLifecycle(
        HypermidAdapter(client, mode="pass_through"),
        DaemonConfig(
            transport=DaemonTransport.UNIX_SOCKET,
            endpoint=str(runtime_root / "hypermid.sock"),
            executable=str(binary),
            start_on_demand=True,
            auth=LocalAuthConfig(
                method=LocalAuthMethod.PEER_AND_HMAC,
                token_file=str(runtime_root / "auth.token"),
                require_peer_identity=True,
            ),
        ).with_connection_record(str(record)),
        connection_record=record,
        enrollment=None,
    )
    handlers = service_for(lifecycle)
    enrollment_path = tmp_path / "hypermid" / "enrollment.json"
    assert not record.exists() and not enrollment_path.exists()
    with pytest.raises(
        HypermidHandlerError, match="authored by the local server"
    ):
        await handlers.plan(
            "install",
            {
                "target_version": "1.0.0",
                "params": {
                    "local_enrollment": {
                        "operations": ["administer"],
                        "resources": ["memory-service"],
                        "expires_ms": 9_007_199_254_740_991,
                    }
                },
            },
        )
    assert not record.exists() and not enrollment_path.exists()
    plan = await handlers.plan(
        "install", {"target_version": "1.0.0", "params": {}}
    )
    assert not record.exists() and not enrollment_path.exists()
    assert plan["binary_digest"]
    assert plan["scope"] == scope.to_wire()
    assert plan["operator_identity"]["uid"] == os.geteuid()
    assert plan["params"]["local_enrollment"]["operations"] == [
        "read",
        "append",
        "revise",
        "delete",
        "restore",
    ]
    assert plan["params"]["local_enrollment"]["resources"] == [
        "memory-records",
        "memory-list",
    ]
    assert "credential_id" not in str(plan)
    assert "capability_id" not in str(plan)

    try:
        receipt = await handlers.apply(
            "install",
            {"plan_id": plan["plan_id"], "plan_digest": plan["plan_digest"]},
        )
        assert receipt["state"] == "committed"
        assert record.is_file() and enrollment_path.is_file()
        enrollment = LocalEnrollment.load(enrollment_path, scope=scope)
        assert str(enrollment.capability_id).startswith("local-capability-")
        assert lifecycle.adapter.status().available
        payload = [{"role": "user", "content": "zero-state local install"}]
        assert await lifecycle.adapter.passthrough(payload) == payload
        memory = MemoryClient(client, capability_id=enrollment.capability_id)
        record_id = Id("zero-state-record")
        collection = Id("memory-records")
        create_trace = Trace(Id("trace-zero-create"), Id("request-zero-create"))
        created = await memory.create(
            MutationRequest(
                operation=MemoryOperation.CREATE,
                actor_scope=scope,
                target_scope=scope,
                revision=RevisionPrecondition.must_not_exist(),
                trace=create_trace,
                record_id=record_id,
                category="test",
            ),
            RecordDraft(
                id=record_id,
                scope=scope,
                kind=RecordKind.NOTE,
                category="test",
                content="server-authored zero-state grant",
                importance=0.5,
                confidence=1.0,
            ),
            now_ms=time.time_ns() // 1_000_000,
            authority_resource=collection,
        )
        assert created.record is not None
        fetched, _ = await memory.get(
            AccessRequest(
                operation=GrantOperation.READ,
                actor_scope=scope,
                target_scope=scope,
                resource_id=record_id,
                trace=Trace(Id("trace-zero-get"), Id("request-zero-get")),
            ),
            authority_resource=collection,
        )
        assert fetched == created.record
        search_trace = Trace(Id("trace-zero-search"), Id("request-zero-search"))
        searched = await memory.search(
            AccessRequest(
                operation=GrantOperation.SEARCH,
                actor_scope=scope,
                target_scope=scope,
                resource_id=collection,
                trace=search_trace,
            ),
            SearchRequest(
                query="server-authored zero-state",
                mode=SearchMode.LEXICAL,
                limit=10,
                trace=search_trace,
                now_ms=time.time_ns() // 1_000_000,
            ),
        )
        assert [hit.id for hit in searched.hits] == [record_id]
        assert searched.hits[0].scores.lexical > 0.0
    finally:
        await lifecycle.stop()
