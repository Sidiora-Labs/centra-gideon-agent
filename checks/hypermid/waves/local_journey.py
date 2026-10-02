"""Real local daemon and Gideon conversation acceptance helpers."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from gideon.cognition.history import ConversationLog
from gideon.hypermid import HypermidAdapter, HypermidClient, HypermidLifecycle, Scope
from gideon.hypermid.config import DaemonConfig, DaemonTransport, LocalAuthConfig, LocalAuthMethod
from gideon.hypermid.foundation import Id
from gideon.hypermid.lifecycle import LocalEnrollment


def daemon_binary() -> str:
    configured = os.environ.get("HYPERMID_DAEMON_BINARY")
    if configured and Path(configured).is_file():
        return str(Path(configured).resolve())
    target = Path(os.environ.get("CARGO_TARGET_DIR", "target"))
    candidate = target / "debug" / "hypermid-daemon"
    if candidate.is_file():
        return str(candidate.resolve())
    raise RuntimeError("Build hypermid-daemon and set HYPERMID_DAEMON_BINARY before acceptance")


def lifecycle(root: Path) -> HypermidLifecycle:
    record = root / "connection.json"
    config = DaemonConfig(
        transport=DaemonTransport.UNIX_SOCKET,
        endpoint=str(root / "daemon.sock"),
        auth=LocalAuthConfig(LocalAuthMethod.PEER_AND_HMAC, str(record), True),
        executable=daemon_binary(),
        connection_record=str(record),
        request_timeout_ms=10_000,
    )
    client = HypermidClient(record, scope=Scope("acceptance-owner", "acceptance-project"))
    return HypermidLifecycle(
        HypermidAdapter(client, mode="pass_through"), config, connection_record=record,
        enrollment=LocalEnrollment(
            scope=client.scope,
            credential_id=Id("acceptance-local-credential"),
            capability_id=Id("acceptance-local-capability"),
            operations=("read",),
            resources=(Id("acceptance-status"),),
            expires_ms=time.time_ns() // 1_000_000 + 600_000,
        ),
    )


async def authenticated_journey(root: Path) -> dict[str, object]:
    host = lifecycle(root)
    journal = ConversationLog(base_dir=root / "sessions")
    journal.append("hypermid-acceptance", "user", "Keep these original bytes: café\n第二行")
    before = journal._path("hypermid-acceptance").read_bytes()
    try:
        status = await host.start()
        assert status.available and status.healthy, status.to_dict()
        assert status.writer == "gideon" and not host.adapter.owns_compaction
        assert status.protocol_version and status.storage_version and status.build_version
        payload = journal.read_messages("hypermid-acceptance")
        returned = await host.adapter.passthrough(payload)
        assert returned == payload
        assert journal._path("hypermid-acceptance").read_bytes() == before
        assert host.adapter.status().writer == "gideon"
        instance = status.daemon_instance_id
        assert instance
        await host.stop()
        assert host.process is None and not host.adapter.status().available
        assert journal._path("hypermid-acceptance").read_bytes() == before
        restarted = await host.start()
        assert restarted.available and restarted.daemon_instance_id != instance
        assert await host.adapter.passthrough(payload) == payload
        return {"authenticated": True, "writer": restarted.writer, "journal_unchanged": True,
                "restart": True, "protocol": restarted.protocol_version}
    finally:
        await host.stop()


async def conversation_journey(root: Path) -> dict[str, object]:
    from gideon.engine.agents.native.runtime import NativeAgentRuntime
    from gideon.engine.agents.provider import AgentRuntimeDefinition
    from gideon.integrations.llm.credentials import Credential
    from gideon.integrations.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK
    from gideon.integrations.llm.openai import OpenAIProvider

    required = ("HYPERMID_TEST_MODEL", "HYPERMID_TEST_BASE_URL", "HYPERMID_TEST_API_KEY")
    if not all(os.environ.get(name) for name in required):
        raise RuntimeError("Live conversation acceptance requires an explicitly configured model and credential")
    host = lifecycle(root)
    journal = ConversationLog(base_dir=root / "sessions")
    prompt = "Reply with one short sentence confirming that this conversation is working."
    provider = OpenAIProvider(
        model=os.environ[required[0]], base_url=os.environ[required[1]],
        credential=Credential("hypermid-acceptance", "api_key", os.environ[required[2]], "env"),
        max_tokens=512,
    )
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="hypermid-acceptance", tools=[]),
        model_provider=provider, tool_providers=[], cwd=root, session_key="acceptance",
        max_turns=1, project_id="acceptance-project",
    )
    try:
        status = await host.start()
        assert status.available and status.writer == "gideon", status.to_dict()
        await runtime.start()
        journal.append("acceptance", "user", prompt)
        request = await host.adapter.passthrough({"text": prompt})
        assert request == {"text": prompt}
        events = await asyncio.wait_for(_collect(runtime.stream(request["text"])), timeout=90)
        terminal = [event for event in events if event.kind == EVENT_COMPLETE]
        assert len(terminal) == 1, "A real native turn must have exactly one terminal event"
        answer = "".join(event.text for event in events if event.kind == EVENT_TEXT_CHUNK)
        assert answer.strip(), "Live provider returned no user-visible answer"
        journal.append("acceptance", "assistant", answer)
        original = journal._path("acceptance").read_bytes()
        assert len(journal.read_messages("acceptance")) == 2
        await host.stop()
        missing = HypermidAdapter(HypermidClient(root / "absent.json", scope=Scope("acceptance-owner", "acceptance-project")), mode="pass_through")
        unavailable = await missing.start()
        assert not unavailable.available and unavailable.writer == "gideon"
        assert await missing.passthrough({"text": prompt}) == {"text": prompt}
        assert journal._path("acceptance").read_bytes() == original
        await missing.stop()
        return {"native_turn": True, "terminal_events": len(terminal), "transcript_messages": 2,
                "missing_daemon_preserves_history": True, "input_tokens": terminal[0].input_tokens,
                "output_tokens": terminal[0].output_tokens}
    finally:
        await runtime.shutdown()
        await host.stop()


async def _collect(stream):
    return [event async for event in stream]
