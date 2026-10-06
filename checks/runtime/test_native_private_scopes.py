"""Real native private execution issuance and retirement boundaries."""

import asyncio
import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest

from gideon.hypermid.client import HypermidClient, HypermidRemoteError
from gideon.hypermid.contracts import MemoryContractError, PrivateScopeReceipt
from gideon.hypermid.foundation import Id, Scope
from gideon.hypermid.memory import _trace
from gideon.hypermid.memory_client import MemoryClient
from gideon.hypermid.private_scopes import NativePrivateScopes


@pytest.mark.asyncio
async def test_real_native_private_scope_lifetime(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    record = tmp_path / "state" / "connection.json"
    scope = Scope("private-owner", "private-project", "private-host-workspace")
    process = subprocess.Popen(
        [
            str(Path("target/debug/hypermid-daemon").resolve()),
            "--socket",
            str(tmp_path / "run" / "private.sock"),
            "--connection-record",
            str(record),
            "--local-credential-id",
            "private-credential",
            "--local-owner-id",
            str(scope.owner_id),
            "--local-project-id",
            str(scope.project_id),
            "--local-workspace-id",
            str(scope.workspace_id),
            "--local-capability-id",
            "private-owner-capability",
            "--local-capability-operation",
            "administer",
            "--local-capability-operation",
            "read",
            "--local-capability-resource",
            "memory-service",
            "--local-capability-resource",
            "memory-records",
            "--local-capability-expires-ms",
            str(int(time.time() * 1000) + 120000),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        env=os.environ.copy(),
    )
    client = HypermidClient(record, scope=scope)
    try:
        deadline = time.monotonic() + 10
        while not record.exists():
            if process.poll() is not None:
                raise AssertionError(process.stderr.read())
            assert time.monotonic() < deadline, "native daemon did not become ready"
            await asyncio.sleep(0.02)
        memory = MemoryClient(client, capability_id=Id("private-owner-capability"))
        lifecycle = NativePrivateScopes(memory)
        receipt = await lifecycle.issue(
            origin_session_key="private-origin",
            original_actor="owner-actor",
            memory_mode="temporary",
            ttl_ms=60000,
            trace=_trace(),
        )
        assert receipt.actor_scope == scope
        assert (
            receipt.scope.owner_id == scope.owner_id
            and receipt.scope.project_id == scope.project_id
        )
        assert receipt.scope.workspace_id != scope.workspace_id
        assert receipt.memory_mode == "temporary"
        assert await lifecycle.resolve(receipt, trace=_trace()) == receipt
        assert (
            await lifecycle.issue(
                origin_session_key="private-origin",
                original_actor="owner-actor",
                memory_mode="temporary",
                ttl_ms=60000,
                trace=_trace(),
            )
            == receipt
        )
        with pytest.raises(HypermidRemoteError):
            await lifecycle.issue(
                origin_session_key="private-origin",
                original_actor="changed-actor",
                memory_mode="temporary",
                ttl_ms=60000,
                trace=_trace(),
            )
        with pytest.raises(HypermidRemoteError):
            await memory._call(
                "memory.private-scope.resolve",
                {
                    "scope_id": str(receipt.scope_id),
                    "target_scope": Scope(
                        "other-owner", "private-project", receipt.scope_id
                    ).to_wire(),
                    "capability_id": str(receipt.capability_id),
                },
                trace=_trace(),
                capability=False,
            )
        # Private execution grants cannot authorize durable memory access.
        access_trace = _trace()
        with pytest.raises(HypermidRemoteError):
            await memory._call(
                "memory.diagnostics",
                {
                    "capability_id": str(receipt.capability_id),
                    "request": {
                        "operation": "read",
                        "actor_scope": scope.to_wire(),
                        "target_scope": receipt.scope.to_wire(),
                        "resource_id": "memory-records",
                        "category": None,
                        "trace": access_trace.to_wire(),
                    },
                },
                trace=access_trace,
                capability=False,
            )
        retired = await lifecycle.retire("private-origin", trace=_trace())
        assert retired.retired
        assert (await lifecycle.retire("private-origin", trace=_trace())).retired
        with pytest.raises(HypermidRemoteError):
            await lifecycle.resolve(receipt, trace=_trace())
        with pytest.raises(HypermidRemoteError):
            await lifecycle.issue(
                origin_session_key="private-origin",
                original_actor="owner-actor",
                memory_mode="temporary",
                ttl_ms=60000,
                trace=_trace(),
            )
        assert await lifecycle.retire("unknown-origin", trace=_trace()) is None
        short = await lifecycle.issue(
            origin_session_key="short-origin",
            original_actor="owner-actor",
            memory_mode="incognito",
            ttl_ms=50,
            trace=_trace(),
        )
        await asyncio.sleep(0.08)
        with pytest.raises(HypermidRemoteError):
            await lifecycle.resolve(short, trace=_trace())
        damaged = dict(
            scope_id=str(receipt.scope_id),
            actor_scope=scope.to_wire(),
            scope=Scope("wrong-owner", "private-project", receipt.scope_id).to_wire(),
            capability_id=str(receipt.capability_id),
            origin_session_key="private-origin",
            original_actor="owner-actor",
            memory_mode="temporary",
            expires_at_ms=receipt.expires_at_ms,
            retired=False,
        )
        with pytest.raises(MemoryContractError):
            PrivateScopeReceipt.from_wire(damaged)
        corrupt = await lifecycle.issue(
            origin_session_key="damaged-origin",
            original_actor="owner-actor",
            memory_mode="temporary",
            ttl_ms=60000,
            trace=_trace(),
        )
        databases = list(tmp_path.rglob("memory.sqlite3"))
        assert len(databases) == 1
        with sqlite3.connect(databases[0]) as db:
            raw = db.execute(
                "SELECT receipt_json FROM memory_private_scopes WHERE scope_id=?",
                (str(corrupt.scope_id),),
            ).fetchone()[0]
            edited = json.loads(raw)
            edited["original_actor"] = "changed-actor"
            db.execute(
                "UPDATE memory_private_scopes SET receipt_json=? WHERE scope_id=?",
                (json.dumps(edited), str(corrupt.scope_id)),
            )
        with pytest.raises(HypermidRemoteError):
            await lifecycle.resolve(corrupt, trace=_trace())
        with pytest.raises(HypermidRemoteError):
            await lifecycle.retire("damaged-origin", trace=_trace())
        assert not list(tmp_path.rglob("memory_index.db"))
    finally:
        await client.close()
        process.terminate()
        process.wait(timeout=5)
