"""Published facade drives the real authenticated daemon, durable events and role modules."""

import asyncio
import hashlib
import os
import sys
import time
from pathlib import Path

import pytest

from gideon.cognition.context_engine import set_engine
from gideon.hypermid.adapter import HypermidAdapter
from gideon.hypermid.client import HypermidClient
from gideon.hypermid.config import (
    DaemonConfig,
    DaemonTransport,
    LocalAuthConfig,
    LocalAuthMethod,
)
from gideon.hypermid.foundation import Id, Scope
from gideon.hypermid.lifecycle import HypermidLifecycle, LocalEnrollment
from gideon.hypermid.modules import HypermidModuleSupervisor
from gideon.hypermid.subscriptions import SubscriptionClient
from gideon.sdk.hypermid import (
    HypermidRuntimeSDK,
    HypermidSDKError,
    RemoteHypermidClient,
    ResumeStore,
    ScopedEvent,
    ToolModuleSpec,
)


@pytest.mark.asyncio
async def test_real_sdk_authenticated_scope_subscription_resume_and_modules(tmp_path):
    binary = Path(os.environ["HYPERMID_DAEMON_BINARY"]).resolve()
    assert binary.is_file() and os.access(binary, os.X_OK)
    run = tmp_path / "daemon"
    run.mkdir()
    record = run / "connection.json"
    scope = Scope("sdk-owner", "sdk-project", "sdk-workspace")
    config = DaemonConfig(
        transport=DaemonTransport.UNIX_SOCKET,
        endpoint=str(run / "daemon.sock"),
        executable=str(binary),
        start_on_demand=True,
        auth=LocalAuthConfig(
            method=LocalAuthMethod.PEER_AND_HMAC,
            token_file=str(run / "auth.token"),
            require_peer_identity=True,
        ),
    ).with_connection_record(str(record))
    client = HypermidClient(record, scope=scope)
    supervisor = HypermidModuleSupervisor(tmp_path / "modules", scope=scope)
    lifecycle = HypermidLifecycle(
        HypermidAdapter(client, mode="pass_through"),
        config,
        connection_record=record,
        module_supervisor=supervisor,
        enrollment=LocalEnrollment(
            scope=scope,
            credential_id=Id("sdk-local-credential"),
            capability_id=Id("sdk-local-capability"),
            operations=("read",),
            resources=(Id("memory-list"),),
            expires_ms=time.time_ns() // 1_000_000 + 600_000,
        ),
    )
    stream = None
    try:
        status = await asyncio.wait_for(lifecycle.start(), 10)
        assert (
            status.available
            and status.scope_bound
            and status.protocol_version == "hypermid.v1"
        )
        sdk = HypermidRuntimeSDK(lifecycle)
        assert sdk.scope == scope and sdk.status().available
        remote: RemoteHypermidClient = client
        response = await asyncio.wait_for(
            sdk.query_remote(remote, "passthrough", {"sdk": "actual request"}), 5
        )
        assert response["payload"] == {"sdk": "actual request"}
        consumer = Id("sdk-consumer")
        store = ResumeStore(tmp_path / "subscriptions" / "resume.json")
        assert store.load() is None
        stream = sdk.subscribe(scope, consumer, "sdk.*", resume_store=store)
        waiting = asyncio.create_task(anext(stream))
        async with asyncio.timeout(5):
            while consumer not in sdk._subscriptions:
                await asyncio.sleep(0.01)
        publisher = SubscriptionClient(client)
        first = await publisher.publish(
            event_id=Id("sdk-event-one"),
            topic="sdk.events",
            scope=scope,
            at_ms=int(time.time() * 1000),
            schema_name="sdk.event",
            schema_version=1,
            payload={"number": 1},
        )
        delivered = await asyncio.wait_for(waiting, 5)
        assert (
            isinstance(delivered, ScopedEvent) and delivered.event_id == first.event_id
        )
        assert ScopedEvent.from_wire(delivered.to_wire()) == delivered
        await sdk.ack(consumer, delivered.event_id)
        assert store.load().cursor == delivered.cursor
        assert store.path.stat().st_mode & 0o777 == 0o600
        await stream.aclose()
        stream = None
        second = await publisher.publish(
            event_id=Id("sdk-event-two"),
            topic="sdk.events",
            scope=scope,
            at_ms=int(time.time() * 1000),
            schema_name="sdk.event",
            schema_version=1,
            payload={"number": 2},
        )
        stream = sdk.subscribe(scope, consumer, "sdk.*", resume_store=store)
        resumed = await asyncio.wait_for(anext(stream), 5)
        assert isinstance(resumed, ScopedEvent) and resumed.event_id == second.event_id
        assert resumed.cursor.sequence > delivered.cursor.sequence
        await sdk.ack(consumer, resumed.event_id)
        assert ResumeStore(store.path).load().cursor == resumed.cursor
        await stream.aclose()
        stream = None
        with pytest.raises(HypermidSDKError, match="not pending"):
            await sdk.ack(consumer, first.event_id)
        foreign = sdk.subscribe(
            Scope("other-owner", "sdk-project", "sdk-workspace"), consumer, "sdk.*"
        )
        with pytest.raises(HypermidSDKError, match="authenticated session"):
            await anext(foreign)
        spec = ToolModuleSpec(
            module_id=Id("sdk-digest-module"),
            command=(sys.executable, "-m", "gideon.hypermid.roles", "serve"),
            provider_name="sdk-digest",
            display_name="SDK digest",
            carrier_id=Id("sdk-carrier"),
        )
        provider = await sdk.open_tool_module("sdk-session", spec)
        source = tmp_path / "source.txt"
        source.write_bytes(b"actual SDK module payload")
        result = await provider.invoke("hypermid.file_digest", {"path": str(source)})
        assert (
            result.success
            and hashlib.sha256(source.read_bytes()).hexdigest() in result.output
        )
        await sdk.close_session("sdk-session")
        assert supervisor.providers_for_session("sdk-session") == ()
    finally:
        if stream is not None:
            await stream.aclose()
        await lifecycle.stop()
        set_engine(None)
