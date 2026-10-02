#!/usr/bin/env python3
"""Wave 6 journey: durable bus, exact peer scope, sandbox and unknown effects."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

from gideon.integrations.sandbox_providers.base import SandboxSpec
from gideon.integrations.sandbox_providers.docker import build_docker_argv
from gideon.hypermid.federation import (
    EffectClass,
    FederatedCall,
    FederatedOperation,
    FederationSession,
    FederationViolation,
    PeerGrant,
    PeerHello,
)
from gideon.hypermid.foundation import Cursor, Digest, EffectState, Id, Scope, Trace
from gideon.hypermid.models import PROTOCOL, Principal
from gideon.hypermid.remote import (
    RemoteAccessService,
    RemoteEffectLedger,
    RemoteModuleExecutor,
    RemoteModuleLaunch,
    RemoteOutcomeUnknown,
    RemoteViolation,
)
from gideon.hypermid.subscriptions import ResumePoint, ResumeStore, ScopedEvent
from gideon.security.sandbox import ResourceCeilings

ROOT = Path(__file__).resolve().parents[2]


def _cargo(*arguments: str) -> None:
    environment = os.environ.copy()
    environment.setdefault("CARGO_BUILD_JOBS", "2")
    subprocess.run(
        ["cargo", *arguments],
        cwd=ROOT,
        env=environment,
        check=True,
        stdin=subprocess.DEVNULL,
    )


def _scope(workspace: str | None = "workspace-main") -> Scope:
    return Scope(Id("owner-main"), Id("project-main"), Id(workspace) if workspace else None)


def _principal() -> Principal:
    return Principal(
        id=Id("principal-phone"),
        kind="device",
        scopes=("context.read", "remote.compute"),
    )


def _session(scope: Scope, *, now_ms: int) -> FederationSession:
    return FederationSession(
        hello=PeerHello(
            peer_id=Id("peer-phone"),
            owner_id=scope.owner_id,
            project_id=scope.project_id,
            protocol=PROTOCOL,
            capabilities=("federation", "remote_effects"),
            catalog_digest=Digest.sha256(b"catalog"),
        ),
        grant=PeerGrant(
            peer_id=Id("peer-phone"),
            principal_id=Id("principal-phone"),
            scope=scope,
            operations=frozenset({"memory.query", "memory.effect"}),
            scopes=frozenset({"context.read", "remote.compute"}),
            expires_ms=now_ms + 60_000,
        ),
        expected_scope=scope,
        local_capabilities=frozenset({"federation", "remote_effects"}),
        local_scopes=frozenset({"context.read", "remote.compute"}),
        catalog={
            "memory.query": FederatedOperation(
                "memory.query", EffectClass.QUERY, frozenset({"context.read"})
            ),
            "memory.effect": FederatedOperation(
                "memory.effect", EffectClass.DURABLE, frozenset({"remote.compute"})
            ),
        },
        now_ms=now_ms,
    )


def _call(
    scope: Scope,
    effect: EffectClass,
    *,
    now_ms: int,
    suffix: str,
    payload: object,
) -> FederatedCall:
    durable = effect is EffectClass.DURABLE
    return FederatedCall(
        message_id=Id(f"message-{suffix}"),
        operation="memory.effect" if durable else "memory.query",
        principal=_principal(),
        scope=scope,
        trace=Trace(Id(f"trace-{suffix}"), Id(f"request-{suffix}")),
        deadline_ms=now_ms + 20_000,
        effect=effect,
        effect_id=Id(f"effect-{suffix}") if durable else None,
        input_digest=Digest.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        )
        if durable
        else None,
        payload=payload,
    )


def _verify_subscription_state(root: Path, scope: Scope) -> None:
    payload = {"answer": 42, "nested": {"stable": True}}
    digest = Digest.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    )
    event = ScopedEvent.from_wire(
        {
            "event_id": "event-1",
            "topic": "context.changed",
            "producer": _principal().to_wire(),
            "scope": scope.to_wire(),
            "at_ms": 1,
            "schema_name": "context.changed",
            "schema_version": 1,
            "payload_digest": str(digest),
            "trace": Trace(Id("trace-event"), Id("request-event")).to_wire(),
            "cursor": Cursor(7, 12).to_wire(),
            "payload": payload,
            "delivery_count": 1,
        }
    )
    assert event.payload_digest == digest and event.cursor == Cursor(7, 12)
    store = ResumeStore(root / "subscriptions" / "phone.json")
    point = ResumePoint(Id("consumer-phone"), scope, "context.*", event.cursor)
    store.save(point)
    assert store.load() == point
    assert (store.path.stat().st_mode & 0o777) == 0o600


def _verify_remote_access(root: Path, scope: Scope, now_ms: int) -> None:
    state_path = root / "remote" / "state.json"
    service = RemoteAccessService(state_path, scope)
    plan = service.invoke(
        "remote.enable.plan",
        {
            "endpoint": "tcp://127.0.0.1:7443",
            "server_name": "hypermid.local",
            "device_name": "phone",
            "expires_at": now_ms + 60_000,
            "capabilities": ["context.read", "remote.compute"],
        },
    )
    status = service.invoke("remote.enable", {"plan_digest": plan["plan_digest"]})
    assert status["state"] == "remote" and status["tls"]["configured"] is True
    restored = RemoteAccessService(state_path, scope).status()
    assert restored["enabled"] is True and restored["devices"][0]["name"] == "phone"
    try:
        service.plan_enable(
            endpoint="tcp://127.0.0.1:7443",
            server_name="hypermid.local",
            device_name="attacker",
            expires_at=now_ms + 60_000,
            capabilities=["writer.acquire"],
        )
    except RemoteViolation:
        pass
    else:
        raise AssertionError("writer authority was admitted into a remote plan")
    try:
        RemoteAccessService(
            state_path, Scope(Id("owner-other"), scope.project_id, scope.workspace_id)
        )
    except RemoteViolation:
        pass
    else:
        raise AssertionError("remote configuration widened into another owner")


async def _verify_remote_execution(root: Path, scope: Scope, now_ms: int) -> None:
    module = root / "remote_module.py"
    counter = root / "counter.txt"
    module.write_text(
        """import json, pathlib, sys, time
request = json.loads(sys.stdin.readline())
payload = request[\"payload\"]
if payload.get(\"sleep\"):
    time.sleep(payload[\"sleep\"])
path = pathlib.Path(payload[\"counter\"])
count = int(path.read_text()) + 1 if path.exists() else 1
path.write_text(str(count))
print(json.dumps({\"result\": {\"count\": count, \"echo\": payload.get(\"value\")}}), flush=True)
""",
        encoding="utf-8",
    )
    session = _session(scope, now_ms=now_ms)
    ledger_path = root / "effects.jsonl"
    ledger = RemoteEffectLedger(ledger_path)
    executor = RemoteModuleExecutor(ledger)
    launch = RemoteModuleLaunch(
        argv=("python3", os.fspath(module)),
        working_directory=root,
        executable_path=module,
        executable_digest=Digest.sha256(module.read_bytes()),
        egress_tier="off",
        environment=(("PYTHONIOENCODING", "utf-8"),),
    )
    docker_argv = build_docker_argv(
        list(launch.argv),
        workspace_dir=os.fspath(root),
        ceilings=ResourceCeilings(),
        spec=SandboxSpec(
            mode="strict",
            profile="tool",
            workspace_dir=os.fspath(root),
            egress_tier="off",
            env=dict(launch.environment),
            safety_profile="remote_module",
        ),
        container_name="gideon-sbx-remote-verification",
    )
    assert "--interactive" in docker_argv
    assert docker_argv[docker_argv.index("--network") + 1] == "none"
    payload = {"counter": os.fspath(counter), "value": "durable"}
    effect = _call(scope, EffectClass.DURABLE, now_ms=now_ms, suffix="commit", payload=payload)
    result = await executor.execute(session, effect, launch)
    assert result.state == EffectState.COMMITTED.value and result.result == {
        "count": 1,
        "echo": "durable",
    }
    repeated = await executor.execute(session, effect, launch)
    assert repeated == result and counter.read_text() == "1"

    cancellation = asyncio.Event()
    slow = _call(
        scope,
        EffectClass.DURABLE,
        now_ms=int(time.time() * 1000),
        suffix="cancel",
        payload={"counter": os.fspath(counter), "sleep": 5},
    )
    task = asyncio.create_task(executor.execute(session, slow, launch, cancellation=cancellation))
    await asyncio.sleep(0.15)
    cancellation.set()
    try:
        await task
    except RemoteOutcomeUnknown:
        pass
    else:
        raise AssertionError("post-dispatch cancellation was not reported unknown")
    recovered = RemoteEffectLedger(ledger_path).status(Id("effect-cancel"))
    assert recovered is not None and recovered.state == EffectState.UNKNOWN.value

    denied_launch = RemoteModuleLaunch(
        argv=("python3", os.fspath(module)),
        working_directory=root,
        executable_path=module,
        executable_digest=Digest.sha256(module.read_bytes()),
        egress_url="http://127.0.0.1/private",
        egress_tier="all",
    )
    query = _call(
        scope,
        EffectClass.QUERY,
        now_ms=int(time.time() * 1000),
        suffix="egress",
        payload={"counter": os.fspath(counter)},
    )
    try:
        await executor.execute(session, query, denied_launch)
    except RemoteViolation as exc:
        assert "egress denied" in str(exc)
    else:
        raise AssertionError("loopback remote egress was admitted")

    foreign_call = _call(
        Scope(Id("owner-other"), scope.project_id, scope.workspace_id),
        EffectClass.QUERY,
        now_ms=int(time.time() * 1000),
        suffix="foreign",
        payload={},
    )
    try:
        session.admit(foreign_call)
    except FederationViolation:
        pass
    else:
        raise AssertionError("federation widened into another owner")


def main() -> None:
    _cargo("test", "-p", "hypermid-daemon", "--test", "bus_rpc")
    _cargo("test", "-p", "hypermid-daemon", "--test", "federation")
    now_ms = int(time.time() * 1000)
    scope = _scope()
    with tempfile.TemporaryDirectory(prefix="hypermid-wave06-") as temporary:
        root = Path(temporary)
        _verify_subscription_state(root, scope)
        _verify_remote_access(root, scope, now_ms)
        asyncio.run(_verify_remote_execution(root, scope, now_ms))
    print(
        json.dumps(
            {
                "wave": 6,
                "status": "passed",
                "durable_bus": "authenticated daemon restart journey passed",
                "federation": "rust and Python exact-scope journeys passed",
                "remote": "sandbox, egress, cancellation, and unknown effect journey passed",
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
