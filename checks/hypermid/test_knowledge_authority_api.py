from __future__ import annotations

import asyncio
import json
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import pytest

from checks.hypermid.test_auth_protocol import (
    LiveDaemon,
    _daemon_binary,
    _issue_client,
    _record_variant,
)
from gideon.hypermid.client import HypermidClient, HypermidRemoteError
from gideon.hypermid.models import Scope, Trace


@dataclass(frozen=True)
class OperatorIdentity:
    credential_id: str
    owner_id: str
    project_id: str
    capability_id: str
    operations: tuple[str, ...]
    resources: tuple[str, ...]


def _start_authorized_daemon(
    root: Path,
    name: str,
    identity: OperatorIdentity,
    workspace_id: str,
    expires_ms: int,
    *,
    tls_directory: Path | None = None,
) -> LiveDaemon:
    socket = root / f"{name}.sock"
    record = root / f"{name}.json"
    arguments = [
        str(_daemon_binary()),
        "--socket",
        str(socket),
        "--connection-record",
        str(record),
        "--local-credential-id",
        identity.credential_id,
        "--local-owner-id",
        identity.owner_id,
        "--local-project-id",
        identity.project_id,
        "--local-workspace-id",
        workspace_id,
        "--local-capability-id",
        identity.capability_id,
        "--local-capability-expires-ms",
        str(expires_ms),
    ]
    for operation in identity.operations:
        arguments.extend(("--local-capability-operation", operation))
    for resource in identity.resources:
        arguments.extend(("--local-capability-resource", resource))
    if tls_directory is not None:
        arguments.extend(("--tls-directory", str(tls_directory)))
    process = subprocess.Popen(
        arguments,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 10
    while not record.is_file():
        if process.poll() is not None:
            stderr = process.stderr.read() if process.stderr is not None else ""
            raise AssertionError(
                f"hypermid-daemon exited before readiness ({process.returncode}): {stderr}"
            )
        if time.monotonic() >= deadline:
            process.terminate()
            process.wait(timeout=5)
            raise AssertionError("authorized hypermid-daemon connection record timed out")
        time.sleep(0.01)
    return LiveDaemon(process, socket, record)


def _operator_grant(
    database: Path,
    *,
    grantee_principal_id: str,
    claimed_scope: Scope,
    target_scope: Scope,
    capability_id: str,
    operation: str,
    resource: str,
    expires_ms: int,
) -> dict[str, Any]:
    command = [
        str(_daemon_binary()),
        "grant",
        "install",
        "--database",
        str(database),
        "--operator-owner-id",
        str(target_scope.owner_id),
        "--grantee-principal-id",
        grantee_principal_id,
        "--claimed-owner-id",
        str(claimed_scope.owner_id),
        "--claimed-project-id",
        str(claimed_scope.project_id),
        "--target-owner-id",
        str(target_scope.owner_id),
        "--target-project-id",
        str(target_scope.project_id),
        "--capability-id",
        capability_id,
        "--operation",
        operation,
        "--resource",
        resource,
        "--expires-ms",
        str(expires_ms),
    ]
    if claimed_scope.workspace_id is not None:
        command.extend(("--claimed-workspace-id", str(claimed_scope.workspace_id)))
    if target_scope.workspace_id is not None:
        command.extend(("--target-workspace-id", str(target_scope.workspace_id)))
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    receipt = json.loads(result.stdout)
    assert receipt["status"] == "installed"
    assert receipt["principal_id"] == grantee_principal_id
    assert receipt["capability_id"] == capability_id
    return receipt


def _operator_revoke(database: Path, owner_id: str, capability_id: str) -> None:
    result = subprocess.run(
        [
            str(_daemon_binary()),
            "grant",
            "revoke",
            "--database",
            str(database),
            "--operator-owner-id",
            owner_id,
            "--capability-id",
            capability_id,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    receipt = json.loads(result.stdout)
    assert receipt == {
        "capability_id": capability_id,
        "revoked": True,
        "status": "revoked",
    }


def _trace(name: str) -> Trace:
    return Trace(f"trace-{name}", f"request-{name}")


def _record_draft(record_id: str, scope: Scope, content: str) -> dict[str, Any]:
    return {
        "id": record_id,
        "scope": scope.to_wire(),
        "kind": "fact",
        "category": "facts",
        "content": content,
        "metadata": {},
        "importance": 1,
        "confidence": 1,
        "expires_at_ms": None,
        "retention_until_ms": None,
        "provenance": [],
        "lineage": [],
        "summary": None,
    }


def _mutation_request(
    operation: str,
    actor_scope: Scope,
    target_scope: Scope,
    record_id: str,
    revision: dict[str, Any],
    trace: Trace,
) -> dict[str, Any]:
    return {
        "operation": operation,
        "actor_scope": actor_scope.to_wire(),
        "target_scope": target_scope.to_wire(),
        "record_id": record_id,
        "category": "facts",
        "revision": revision,
        "trace": trace.to_wire(),
    }


def _access_request(
    actor_scope: Scope, target_scope: Scope, record_id: str, trace: Trace
) -> dict[str, Any]:
    return {
        "operation": "read",
        "actor_scope": actor_scope.to_wire(),
        "target_scope": target_scope.to_wire(),
        "resource_id": record_id,
        "category": "facts",
        "trace": trace.to_wire(),
    }


async def _create_record(
    client: HypermidClient,
    capability_id: str,
    actor_scope: Scope,
    target_scope: Scope,
    record_id: str,
    content: str,
    name: str,
) -> dict[str, Any]:
    trace = _trace(name)
    response = await client.request(
        "memory.record.create",
        {
            "capability_id": capability_id,
            "request": _mutation_request(
                "create",
                actor_scope,
                target_scope,
                record_id,
                {"kind": "must_not_exist"},
                trace,
            ),
            "draft": _record_draft(record_id, target_scope, content),
            "sources": [],
            "now_ms": int(time.time() * 1000),
        },
        trace=trace,
        effect_kind="durable",
    )
    assert isinstance(response, dict)
    return response["result"]["record"]


async def _read_record(
    client: HypermidClient,
    capability_id: str,
    actor_scope: Scope,
    target_scope: Scope,
    record_id: str,
    name: str,
) -> dict[str, Any] | None:
    trace = _trace(name)
    response = await client.request(
        "memory.record.get",
        {
            "capability_id": capability_id,
            "request": _access_request(actor_scope, target_scope, record_id, trace),
        },
        trace=trace,
    )
    assert isinstance(response, dict)
    return response["result"]["record"]


async def _update_record(
    client: HypermidClient,
    capability_id: str,
    actor_scope: Scope,
    target_scope: Scope,
    record_id: str,
    digest: str,
    content: str,
    name: str,
) -> dict[str, Any]:
    trace = _trace(name)
    response = await client.request(
        "memory.record.update",
        {
            "capability_id": capability_id,
            "request": _mutation_request(
                "update",
                actor_scope,
                target_scope,
                record_id,
                {"kind": "match", "digest": digest},
                trace,
            ),
            "draft": _record_draft(record_id, target_scope, content),
            "sources": [],
            "now_ms": int(time.time() * 1000),
        },
        trace=trace,
        effect_kind="durable",
    )
    assert isinstance(response, dict)
    return response["result"]["record"]


async def _denial(awaitable: Any) -> tuple[str, str, str | None]:
    with pytest.raises(HypermidRemoteError) as caught:
        await awaitable
    error = caught.value.error
    return (
        error.code,
        error.message,
        error.effect_state.value if error.effect_state is not None else None,
    )


@pytest.mark.asyncio
async def test_real_tls_knowledge_authority_matrix(tmp_path: Path) -> None:
    shared_workspace = "workspace-shared"
    bob_scope = Scope("bob", "project-b", shared_workspace)
    alice_scope = Scope("alice", "project-a", shared_workspace)
    bob = OperatorIdentity(
        credential_id="credential-bob",
        owner_id="bob",
        project_id="project-b",
        capability_id="capability-bob",
        operations=("append", "read", "revise", "administer"),
        resources=(
            "record-bob",
            "share-read-bob",
            "share-revise-bob",
            "memory-share-grants",
        ),
    )
    alice = OperatorIdentity(
        credential_id="credential-alice",
        owner_id="alice",
        project_id="project-a",
        capability_id="capability-alice",
        operations=("append", "read", "revise"),
        resources=("record-alice",),
    )
    expires_ms = int((time.time() + 600) * 1000)
    active: LiveDaemon | None = None
    bob_certificate: tuple[Path, Path, Any] | None = None
    alice_certificate: tuple[Path, Path, Any] | None = None
    try:
        active = _start_authorized_daemon(
            tmp_path, "bob-initial", bob, shared_workspace, expires_ms
        )
        tls_directory = active.tls_directory
        now = datetime.now(timezone.utc)
        bob_certificate = _issue_client(
            tmp_path,
            tls_directory,
            "bob-authority-client",
            not_before=now - timedelta(minutes=1),
            not_after=now + timedelta(days=1),
        )
        alice_certificate = _issue_client(
            tmp_path,
            tls_directory,
            "alice-authority-client",
            not_before=now - timedelta(minutes=1),
            not_after=now + timedelta(days=1),
        )
        bob_record = _record_variant(
            active.record,
            tmp_path / "bob-client.json",
            client_certificate=str(bob_certificate[0]),
            client_private_key=str(bob_certificate[1]),
        )
        async with HypermidClient(bob_record, scope=bob_scope) as bob_client:
            assert bob_client.session is not None
            assert bob_client.session.principal.id == bob.credential_id
            original = await _create_record(
                bob_client,
                bob.capability_id,
                bob_scope,
                bob_scope,
                "record-bob",
                "bob private durable record",
                "bob-create",
            )
            for grant_id, operation in (
                ("share-read-bob", "read"),
                ("share-revise-bob", "update"),
            ):
                trace = _trace(f"grant-{operation}")
                response = await bob_client.request(
                    "memory.grant.create",
                    {
                        "capability_id": bob.capability_id,
                        "draft": {
                            "id": grant_id,
                            "owner_scope": bob_scope.to_wire(),
                            "grantee_scope": alice_scope.to_wire(),
                            "operations": [operation],
                            "categories": ["facts"],
                            "expires_at_ms": expires_ms,
                            "expected_revision": 0,
                        },
                    },
                    trace=trace,
                    effect_kind="durable",
                )
                assert response["result"]["revision"] == 1
            listed = await bob_client.request(
                "memory.grant.list",
                {"capability_id": bob.capability_id, "owner_scope": bob_scope.to_wire()},
                trace=_trace("grant-list"),
            )
            assert [grant["id"] for grant in listed["result"]["grants"]] == [
                "share-read-bob",
                "share-revise-bob",
            ]
        active.stop()
        active = None

        database = tmp_path / "state" / "memory.sqlite3"
        _operator_grant(
            database,
            grantee_principal_id=alice.credential_id,
            claimed_scope=alice_scope,
            target_scope=bob_scope,
            capability_id="share-read-bob",
            operation="read",
            resource="record-bob",
            expires_ms=expires_ms,
        )
        _operator_grant(
            database,
            grantee_principal_id=alice.credential_id,
            claimed_scope=alice_scope,
            target_scope=bob_scope,
            capability_id="share-revise-bob",
            operation="revise",
            resource="record-bob",
            expires_ms=expires_ms,
        )
        _operator_revoke(database, bob.owner_id, "share-revise-bob")

        active = _start_authorized_daemon(
            tmp_path,
            "alice",
            alice,
            shared_workspace,
            expires_ms,
            tls_directory=tls_directory,
        )
        alice_record_json = json.loads(active.record.read_text(encoding="utf-8"))
        alice_record = _record_variant(
            active.record,
            tmp_path / "alice-client.json",
            client_certificate=str(alice_certificate[0]),
            client_private_key=str(alice_certificate[1]),
        )
        bob_record_json = json.loads(bob_record.read_text(encoding="utf-8"))
        assert alice_record_json["credential_id"] == alice.credential_id
        assert bob_record_json["credential_id"] == bob.credential_id
        assert alice_record_json["secret_b64"] != bob_record_json["secret_b64"]
        assert alice_certificate[0] != bob_certificate[0]

        async with HypermidClient(alice_record, scope=alice_scope) as alice_client:
            assert alice_client.session is not None
            assert alice_client.session.principal.id == alice.credential_id
            await _create_record(
                alice_client,
                alice.capability_id,
                alice_scope,
                alice_scope,
                "record-alice",
                "alice private durable record",
                "alice-create",
            )
            shared = await _read_record(
                alice_client,
                "share-read-bob",
                alice_scope,
                bob_scope,
                "record-bob",
                "shared-read",
            )
            assert shared is not None
            assert shared["current"]["digest"] == original["current"]["digest"]
            assert shared["current"]["content"] == "bob private durable record"

            tenant_denial = await _denial(
                _read_record(
                    alice_client,
                    alice.capability_id,
                    alice_scope,
                    bob_scope,
                    "record-bob",
                    "tenant-denied",
                )
            )
            guessed_denial = await _denial(
                _read_record(
                    alice_client,
                    "share-read-bob",
                    alice_scope,
                    bob_scope,
                    "record-bob-guessed",
                    "guessed-denied",
                )
            )
            assert tenant_denial == guessed_denial
            assert tenant_denial == (
                "AUTHORIZATION_DENIED",
                "the authenticated principal lacks an exact live capability and memory grant",
                "not_started",
            )

            read_only_write = await _denial(
                _update_record(
                    alice_client,
                    "share-read-bob",
                    alice_scope,
                    bob_scope,
                    "record-bob",
                    original["current"]["digest"],
                    "unauthorized shared write",
                    "shared-write-denied",
                )
            )
            revoked_write = await _denial(
                _update_record(
                    alice_client,
                    "share-revise-bob",
                    alice_scope,
                    bob_scope,
                    "record-bob",
                    original["current"]["digest"],
                    "revoked write",
                    "revoked-write-denied",
                )
            )
            assert read_only_write[0] == "AUTHORIZATION_DENIED"
            assert revoked_write[0] == "AUTHORIZATION_DENIED"
        active.stop()
        active = None

        active = _start_authorized_daemon(
            tmp_path,
            "bob-final",
            bob,
            shared_workspace,
            expires_ms,
            tls_directory=tls_directory,
        )
        bob_final_record = _record_variant(
            active.record,
            tmp_path / "bob-final-client.json",
            client_certificate=str(bob_certificate[0]),
            client_private_key=str(bob_certificate[1]),
        )
        async with HypermidClient(bob_final_record, scope=bob_scope) as bob_client:
            after = await _read_record(
                bob_client,
                bob.capability_id,
                bob_scope,
                bob_scope,
                "record-bob",
                "bob-after-denials",
            )
            assert after is not None
            assert after["current"]["digest"] == original["current"]["digest"]
            assert after["current"]["content"] == original["current"]["content"]
            assert after["current"]["number"] == original["current"]["number"]
    except BaseException as failure:
        if active is not None:
            active.stop()
            stderr = active.process.stderr.read() if active.process.stderr is not None else ""
            active = None
            if stderr:
                raise AssertionError(f"hypermid-daemon stderr:\n{stderr}") from failure
        raise
    finally:
        if active is not None:
            active.stop()
