"""Real native app namespace isolation and activation revocation."""

import asyncio
import json
import os
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from gideon.hypermid.app_scopes import NativeAppScopes
from gideon.hypermid.client import HypermidClient, HypermidRemoteError
from gideon.hypermid.contracts import (
    AccessRequest,
    GrantOperation,
    MemoryContractError,
    MemoryOperation,
    MutationRequest,
    RecordDraft,
    RecordKind,
    RevisionPrecondition,
)
from gideon.hypermid.foundation import Id, Scope
from gideon.hypermid.memory import HypermidMemoryProvider, _trace
from gideon.hypermid.memory_client import MemoryClient
from gideon.security.approval_answer import YOU, app
from gideon.security.session_credentials import begin_turn, end_turn


@pytest.mark.asyncio
async def test_real_app_namespaces_isolate_and_revoke(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    for name in ["alpha-app", "beta-app"]:
        folder = tmp_path / "apps" / name
        folder.mkdir(parents=True)
        (folder / "app.json").write_text(
            json.dumps(
                {
                    "name": name,
                    "version": "1.0.0",
                    "permissions": {"agent": "tools", "memory": "app-scoped"},
                }
            )
        )
        (folder / "installed.json").write_text(
            json.dumps(
                {
                    "name": name,
                    "version": "1.0.0",
                    "enabled": True,
                    "origin": "local",
                    "tier": "community",
                }
            )
        )
    record = tmp_path / "state" / "connection.json"
    scope = Scope("app-owner", "app-project", "host-workspace")
    process = subprocess.Popen(
        [
            str(Path("target/debug/hypermid-daemon").resolve()),
            "--socket",
            str(tmp_path / "run" / "app.sock"),
            "--connection-record",
            str(record),
            "--local-credential-id",
            "app-owner-credential",
            "--local-owner-id",
            "app-owner",
            "--local-project-id",
            "app-project",
            "--local-workspace-id",
            "host-workspace",
            "--local-capability-id",
            "app-owner-capability",
            "--local-capability-operation",
            "administer",
            "--local-capability-resource",
            "memory-service",
            "--local-capability-expires-ms",
            str(int(time.time() * 1000) + 120000),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        env=os.environ.copy(),
    )
    client = HypermidClient(record, scope=scope)
    credential = None
    provider = None
    try:
        deadline = time.monotonic() + 10
        while not record.exists():
            if process.poll() is not None:
                raise AssertionError(process.stderr.read())
            assert time.monotonic() < deadline
            await asyncio.sleep(0.02)
        owner = MemoryClient(client, capability_id=Id("app-owner-capability"))
        native = NativeAppScopes(owner)
        credential = begin_turn(
            "alpha-origin",
            YOU,
            turn_id="alpha-turn",
            memory_mode="persistent",
            created_by_app="alpha-app",
            work_actor=app("alpha-app"),
        )
        alpha = await native.issue(trace=_trace())
        assert await native.resolve(alpha, trace=_trace()) == alpha
        assert await native.issue(trace=_trace()) == alpha
        alpha_memory = MemoryClient(client, capability_id=alpha.capability_id)
        request = MutationRequest(
            MemoryOperation.CREATE,
            scope,
            alpha.scope,
            RevisionPrecondition.must_not_exist(),
            _trace(),
            record_id=Id("alpha-fact"),
            category="app",
        )
        await alpha_memory.create(
            request,
            RecordDraft(
                Id("alpha-fact"),
                alpha.scope,
                RecordKind.FACT,
                "app",
                "alpha isolated fact",
                0.5,
                1.0,
            ),
            now_ms=int(time.time() * 1000),
            authority_resource=Id("memory-records"),
        )
        end_turn(credential)
        credential = begin_turn(
            "beta-origin",
            YOU,
            turn_id="beta-turn",
            memory_mode="persistent",
            created_by_app="beta-app",
            work_actor=app("beta-app"),
        )
        beta = await native.issue(trace=_trace())
        assert beta.scope != alpha.scope
        beta_memory = MemoryClient(client, capability_id=beta.capability_id)
        listed = await beta_memory.list(
            AccessRequest(
                GrantOperation.READ, scope, beta.scope, Id("memory-records"), _trace()
            )
        )
        assert not listed.records
        with pytest.raises(MemoryContractError):
            await native.resolve(alpha, trace=_trace())
        with pytest.raises(HypermidRemoteError):
            await beta_memory.list(
                AccessRequest(
                    GrantOperation.READ,
                    scope,
                    alpha.scope,
                    Id("memory-records"),
                    _trace(),
                )
            )
        end_turn(credential)
        credential = None
        from gideon.cognition import memory_service
        from gideon.extensions.apps import app_manager

        provider = HypermidMemoryProvider(
            record, scope=scope, capability_id=Id("app-owner-capability")
        )
        monkeypatch.setattr(
            memory_service, "_authoritative_service", SimpleNamespace(provider=provider)
        )
        assert app_manager.disable("alpha-app")

        with pytest.raises(HypermidRemoteError):
            await alpha_memory.list(
                AccessRequest(
                    GrantOperation.READ,
                    scope,
                    alpha.scope,
                    Id("memory-records"),
                    _trace(),
                )
            )
        assert app_manager.enable("alpha-app")
        credential = begin_turn(
            "alpha-origin2",
            YOU,
            turn_id="alpha-turn2",
            memory_mode="persistent",
            created_by_app="alpha-app",
            work_actor=app("alpha-app"),
        )
        fresh = await native.issue(trace=_trace())
        assert (
            fresh.scope == alpha.scope
            and fresh.epoch == alpha.epoch + 1
            and fresh.capability_id != alpha.capability_id
        )
        restored = await MemoryClient(client, capability_id=fresh.capability_id).list(
            AccessRequest(
                GrantOperation.READ, scope, fresh.scope, Id("memory-records"), _trace()
            )
        )
        assert [r.id for r in restored.records] == ["alpha-fact"]
        with pytest.raises(MemoryContractError):
            await native.resolve(replace(fresh, app_name="beta-app"), trace=_trace())
        end_turn(credential)
        credential = begin_turn(
            "temp-origin",
            YOU,
            turn_id="temp-turn",
            memory_mode="temporary",
            created_by_app="alpha-app",
            work_actor=app("alpha-app"),
        )
        with pytest.raises(MemoryContractError):
            await native.issue(trace=_trace())
        end_turn(credential)
        credential = begin_turn(
            "disabled-origin",
            YOU,
            turn_id="disabled-turn",
            memory_mode="persistent",
            created_by_app="alpha-app",
            work_actor=app("alpha-app"),
        )
        meta = tmp_path / "apps" / "alpha-app" / "installed.json"
        data = json.loads(meta.read_text())
        data["enabled"] = False
        meta.write_text(json.dumps(data))
        with pytest.raises(MemoryContractError):
            await native.resolve(fresh, trace=_trace())
    finally:
        if credential:
            end_turn(credential)
        if provider:
            provider.close()
        await client.close()
        process.terminate()
        process.wait(timeout=5)
