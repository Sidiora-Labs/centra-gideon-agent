"""Real daemon app memory provider scope and privacy integration."""

import json
import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from gideon.hypermid.contracts import (
    MemoryOperation,
    MutationRequest,
    RecordDraft,
    RecordKind,
    RevisionPrecondition,
)
from gideon.hypermid.foundation import Id, Scope
from gideon.hypermid.memory import HypermidMemoryProvider, _trace
from gideon.security.approval_answer import YOU, app
from gideon.security.session_credentials import begin_turn, end_turn


def test_real_app_memory_consumer(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    for name in ("alpha-app", "beta-app"):
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
    scope = Scope("consumer-owner", "consumer-project", "host-workspace")
    record = tmp_path / "state" / "connection.json"
    command = [
        str(Path("target/debug/hypermid-daemon").resolve()),
        "--socket",
        str(tmp_path / "run" / "consumer.sock"),
        "--connection-record",
        str(record),
        "--local-credential-id",
        "consumer-credential",
        "--local-owner-id",
        scope.owner_id,
        "--local-project-id",
        scope.project_id,
        "--local-workspace-id",
        scope.workspace_id,
        "--local-capability-id",
        "consumer-capability",
        "--local-capability-expires-ms",
        str(int(time.time() * 1000) + 120000),
    ]
    for operation in ("administer", "read", "append"):
        command += ["--local-capability-operation", operation]
    for resource in ("memory-service", "memory-records", "memory-list"):
        command += ["--local-capability-resource", resource]
    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        env=os.environ.copy(),
    )
    provider = None
    credential = None
    try:
        deadline = time.monotonic() + 10
        while not record.exists():
            if process.poll() is not None:
                raise AssertionError(process.stderr.read())
            assert time.monotonic() < deadline
            time.sleep(0.02)
        provider = HypermidMemoryProvider(
            record, scope=scope, capability_id=Id("consumer-capability")
        )
        from gideon.cognition import memory_service
        from gideon.cognition.context import PromptAssembler

        monkeypatch.setattr(
            memory_service, "_authoritative_service", SimpleNamespace(provider=provider)
        )
        scopes = []
        for name in ("alpha-app", "beta-app"):
            credential = begin_turn(
                name + "-origin",
                YOU,
                turn_id=name + "-turn",
                memory_mode="persistent",
                created_by_app=name,
                work_actor=app(name),
            )
            receipt = provider._app_receipt()
            scopes.append(receipt.scope)
            native_id = provider._native_record_id("same-key")
            request = MutationRequest(
                MemoryOperation.CREATE,
                scope,
                receipt.scope,
                RevisionPrecondition.must_not_exist(),
                _trace(),
                record_id=native_id,
                category="app",
            )
            draft = RecordDraft(
                native_id,
                receipt.scope,
                RecordKind.FACT,
                "app",
                name + " constellation fact",
                0.5,
                1.0,
                metadata={"gideon_id": "same-key"},
            )
            provider._call(
                lambda client: client.create(
                    request,
                    draft,
                    now_ms=int(time.time() * 1000),
                    authority_resource=Id("memory-records"),
                )
            )
            assert provider.get("same-key").text == name + " constellation fact"
            assert [item.text for item in provider.query()] == [
                name + " constellation fact"
            ]
            recalled = PromptAssembler.resolve_scoped_recall(
                SimpleNamespace(memory=provider), "constellation", scope=scope
            )
            assert recalled.state == "daemon"
            assert name in str(recalled.component)
            assert (
                not provider._writes_allowed()
            )  # no foreground writer lease was issued
            end_turn(credential)
            credential = None
        assert scopes[0] != scopes[1]
        credential = begin_turn(
            "private-origin",
            YOU,
            turn_id="private-turn",
            memory_mode="incognito",
            created_by_app="alpha-app",
            work_actor=app("alpha-app"),
        )
        assert provider.get("same-key").text.startswith("alpha-app")
        assert not provider._writes_allowed()
        end_turn(credential)
        credential = begin_turn(
            "temporary-origin",
            YOU,
            turn_id="temporary-turn",
            memory_mode="temporary",
            created_by_app="alpha-app",
            work_actor=app("alpha-app"),
        )
        assert provider.get("same-key") is None
        end_turn(credential)
        credential = None
        from gideon.extensions.apps import app_manager

        assert app_manager.disable("alpha-app")
        credential = begin_turn(
            "revoked-origin",
            YOU,
            turn_id="revoked-turn",
            memory_mode="persistent",
            created_by_app="alpha-app",
            work_actor=app("alpha-app"),
        )
        assert provider.get("same-key") is None
    finally:
        if credential:
            end_turn(credential)
        if provider:
            provider.close()
        process.terminate()
        process.wait(timeout=5)
