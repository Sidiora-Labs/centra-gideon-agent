#!/usr/bin/env python3
"""Wave 5 connected daemon MCP, native approval, model, and lifecycle journey."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from dotenv import find_dotenv, load_dotenv

from gideon.engine.agents.native.runtime import NativeAgentRuntime
from gideon.engine.agents.provider import AgentRuntimeDefinition
from gideon.hypermid import HypermidAdapter, HypermidClient, HypermidLifecycle
from gideon.hypermid.config import (
    DaemonConfig,
    DaemonTransport,
    LocalAuthConfig,
    LocalAuthMethod,
)
from gideon.hypermid.foundation import Id, Scope
from gideon.hypermid.lifecycle import LocalEnrollment
from gideon.hypermid.mcp_provider import DaemonMcpToolProvider
from gideon.hypermid.models import PROTOCOL
from gideon.hypermid.tool_provider import CancellationState
from gideon.integrations.llm.credentials import CredentialStore
from gideon.integrations.llm.events import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_RESULT,
)
from gideon.integrations.llm.openai import OpenAIProvider
from gideon.integrations.mcp_client import with_mcp_session_eviction
from gideon.operations.usage_ledger import UsageJournal, record_from_event


ROOT = Path(__file__).resolve().parents[2]
_SCOPE = Scope(Id("wave05-owner"), Id("wave05-project"), Id("wave05-workspace"))
_SESSION = "wave05-supervised-session"
_MODEL_CREDENTIAL = "wave05-centra"
_MAX_OUTPUT_TOKENS = 512


def _require_authorities() -> tuple[str, str, str]:
    required = ("GATEWAY_ROUTER", "GATEWAY_ROUTER_API_KEY", "HYPERMID_TEST_MODEL")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError(
            "Wave 5 requires the configured Centra model authority: "
            + ", ".join(missing)
        )
    return tuple(os.environ[name] for name in required)


def _daemon_binary() -> Path:
    configured = os.environ.get("HYPERMID_DAEMON_BINARY")
    if configured:
        candidate = Path(configured)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    target = Path(os.environ.get("CARGO_TARGET_DIR", ROOT / "target"))
    candidate = target / "debug" / "hypermid-daemon"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate.resolve()
    raise RuntimeError("Wave 5 requires the current executable hypermid-daemon binary")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_module_fixture(root: Path) -> tuple[Path, Path]:
    fixture = root / "mcp-fixture"
    modules = root / "modules"
    fixture.mkdir(mode=0o700)
    modules.mkdir(mode=0o700)
    server = fixture / "server.py"
    server.write_text(
        """#!/usr/bin/python3
import json
import pathlib
import sys
import time

def read():
    line = sys.stdin.readline()
    if not line:
        raise SystemExit(0)
    return json.loads(line)

def send(value):
    sys.stdout.write(json.dumps(value, separators=(\",\", \":\")) + \"\\n\")
    sys.stdout.flush()

request = read()
send({\"jsonrpc\":\"2.0\",\"id\":request[\"id\"],\"result\":{\"protocolVersion\":\"2025-06-18\",\"capabilities\":{\"tools\":{}},\"serverInfo\":{\"name\":\"wave05-mcp\",\"version\":\"1\"}}})
read()
while True:
    request = read()
    if request.get(\"method\") == \"tools/list\":
        send({\"jsonrpc\":\"2.0\",\"id\":request[\"id\"],\"result\":{\"tools\":[
            {\"name\":\"echo\",\"description\":\"Canonical mcp/wave05/echo tool. Echo one value.\",\"inputSchema\":{\"type\":\"object\",\"properties\":{\"value\":{\"type\":\"string\"}},\"required\":[\"value\"],\"additionalProperties\":False}},
            {\"name\":\"slow_effect\",\"description\":\"Canonical mcp/wave05/slow_effect tool. Begin a durable effect and wait for cancellation.\",\"inputSchema\":{\"type\":\"object\",\"properties\":{\"value\":{\"type\":\"string\"}},\"required\":[\"value\"],\"additionalProperties\":False}}
        ]}})
    elif request.get(\"method\") == \"tools/call\":
        name = request[\"params\"][\"name\"]
        if name == \"echo\":
            send({\"jsonrpc\":\"2.0\",\"id\":request[\"id\"],\"result\":{\"content\":[{\"type\":\"text\",\"text\":request[\"params\"][\"arguments\"][\"value\"]}]}})
        else:
            pathlib.Path(\"effect-started\").write_text(\"started\\n\")
            while True:
                time.sleep(1)
""",
        encoding="utf-8",
    )
    server.chmod(0o700)

    module_host = modules / "module-host"
    shutil.copy2("/bin/sleep", module_host)
    module_host.chmod(0o755)
    manifest = {
        "schema_version": 1,
        "module_id": "wave05-mcp-module",
        "version": "1.0.0",
        "executable": "module-host",
        "arguments": ["600"],
        "artifact_digest": _sha256(module_host),
        "protocol": PROTOCOL,
        "roles": ["operation_provider"],
        "operations": [
            {
                "name": "mcp.wave05.invoke",
                "effect": "durable",
                "remote": False,
                "required_scopes": ["mcp.invoke"],
            }
        ],
        "requires": [],
        "concurrency": "bounded",
        "max_concurrency": 2,
        "overlap": "exclusive",
        "restart": {
            "mode": "on_failure",
            "max_restarts": 1,
            "window_ms": 1_000,
            "base_backoff_ms": 10,
            "max_backoff_ms": 20,
            "drain_timeout_ms": 100,
        },
        "health": {
            "cadence_ms": 100,
            "deadline_ms": 50,
            "failure_threshold": 1,
            "action": "restart",
        },
    }
    (modules / "wave05-mcp-module.json").write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    python = Path("/usr/bin/python3")
    config = root / "mcp-config.json"
    config.write_text(
        json.dumps(
            {
                "manifest_roots": [str(modules)],
                "modules": [
                    {
                        "module_id": "wave05-mcp-module",
                        "server_name": "wave05",
                        "route_operation": "mcp.wave05.invoke",
                        "sandbox": {
                            "executable": str(python),
                            "executable_sha256": _sha256(python),
                            "arguments": ["/run/hypermid/grant-0/server.py"],
                            "working_directory": str(fixture),
                            "filesystem": [
                                {"host_path": str(fixture), "access": "read_write"}
                            ],
                            "maximum_lifetime_ms": 60_000,
                        },
                        "budgets": {
                            "initialization_ms": 2_000,
                            "request_ms": 30_000,
                            "frame_bytes": 16_384,
                            "idle_ms": 30_000,
                            "shutdown_ms": 500,
                            "stderr_bytes": 4_096,
                        },
                    }
                ],
                "flow": {
                    "request_credits": 8,
                    "byte_credits": 8_388_608,
                    "max_queued_requests": 32,
                    "max_queued_bytes": 8_388_608,
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    config.chmod(0o600)
    return config, fixture


def _lifecycle(root: Path, mcp_config: Path) -> HypermidLifecycle:
    state = root / "daemon"
    record = state / "connection.json"
    config = DaemonConfig(
        transport=DaemonTransport.UNIX_SOCKET,
        endpoint=str(state / "hypermid.sock"),
        auth=LocalAuthConfig(LocalAuthMethod.PEER_AND_HMAC, str(record), True),
        executable=str(_daemon_binary()),
        connection_record=str(record),
        request_timeout_ms=10_000,
        mcp_config=str(mcp_config),
    )
    client = HypermidClient(record, scope=_SCOPE)
    return HypermidLifecycle(
        HypermidAdapter(client, mode="pass_through"),
        config,
        connection_record=record,
        enrollment=LocalEnrollment(
            scope=_SCOPE,
            credential_id=Id("wave05-local-credential"),
            capability_id=Id("wave05-local-capability"),
            operations=("read",),
            resources=(Id("wave05-status"),),
            expires_ms=int(time.time() * 1000) + 600_000,
        ),
    )


def _credential(root: Path, secret: str):
    home = root / "credentials"
    home.mkdir(mode=0o700)
    descriptor = home / CredentialStore.CREDENTIALS_FILE
    descriptor.write_text(
        json.dumps(
            {
                _MODEL_CREDENTIAL: {
                    "type": "api_key",
                    "value_env": "GATEWAY_ROUTER_API_KEY",
                }
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    descriptor.chmod(0o600)
    credential = CredentialStore(home).resolve(_MODEL_CREDENTIAL)
    if credential.secret != secret or credential.kind != "api_key":
        raise RuntimeError("Centra credential authority did not resolve the configured handle")
    return credential


async def _collect_turn(runtime: NativeAgentRuntime, prompt: str) -> list[object]:
    events = []
    async for event in runtime.stream(prompt):
        events.append(event)
        if event.kind == EVENT_PERMISSION_REQUEST:
            await runtime.approve_tool(event.request_id)
    return events


async def _wait_for_slow_dispatch(
    provider: DaemonMcpToolProvider, marker: Path, task: asyncio.Task[list[object]]
) -> str:
    deadline = asyncio.get_running_loop().time() + 20
    while asyncio.get_running_loop().time() < deadline:
        keys = provider.active_call_keys
        if marker.is_file() and len(keys) == 1:
            return keys[0]
        if task.done():
            await task
            raise AssertionError("slow MCP effect completed before cancellation")
        await asyncio.sleep(0.01)
    raise TimeoutError("slow MCP effect did not reach dispatched state")


def _spawned_pid(journal: Path) -> int:
    records = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    spawned = [
        record["event"]["identity"]["pid"]
        for record in records
        if record.get("event", {}).get("kind") == "spawned"
        and record["event"].get("module_id") == "wave05-mcp-module"
    ]
    assert len(spawned) == 1
    return int(spawned[0])


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def _connected_journey(root: Path) -> dict[str, object]:
    endpoint, secret, model = _require_authorities()
    home = root / "gideon-home"
    os.environ["GIDEON_HOME"] = str(home)
    os.environ["GIDEON_CREDENTIAL_BACKEND"] = "dotenv"
    credential = _credential(root, secret)
    mcp_config, fixture = _write_module_fixture(root)
    lifecycle = _lifecycle(root, mcp_config)
    provider: DaemonMcpToolProvider | None = None
    runtime: NativeAgentRuntime | None = None
    child_pid: int | None = None
    model_provider = OpenAIProvider(
        model=model,
        credential=credential,
        base_url=endpoint,
        max_tokens=_MAX_OUTPUT_TOKENS,
        extra_options={"temperature": 0},
    )
    model_provider.served_model_ref = f"centra:{model}"
    model_requests: list[dict[str, Any]] = []
    daemon_requests: list[dict[str, Any]] = []
    original_model_request = model_provider._request

    def capture_model_request(
        messages: list[dict],
        *,
        model: str,
        tools: list[dict] | None = None,
        reasoning_effort: str = "",
    ) -> dict[str, Any]:
        request = original_model_request(
            messages,
            model=model,
            tools=tools,
            reasoning_effort=reasoning_effort,
        )
        model_requests.append(request)
        return request

    model_provider._request = capture_model_request
    try:
        status = await lifecycle.start()
        assert status.available and status.healthy, status.to_dict()
        client = lifecycle.adapter.client
        assert client is not None and client.connected
        original_daemon_request = client.request

        async def capture_daemon_request(
            operation: str, payload: object, **kwargs: object
        ) -> object:
            daemon_requests.append({"operation": operation, "payload": payload})
            return await original_daemon_request(operation, payload, **kwargs)

        client.request = capture_daemon_request
        provider = DaemonMcpToolProvider(client, session_key=_SESSION)
        definitions = await provider.list_tools()
        names = {definition.name for definition in definitions}
        assert names == {"mcp/wave05/echo", "mcp/wave05/slow_effect"}
        assert all(definition.requires_approval for definition in definitions)
        health = await provider.health()
        assert len(health) == 1
        assert health[0].connected_sessions == 1
        assert health[0].active_calls == 0
        assert health[0].process_ready is True

        runtime = NativeAgentRuntime(
            definition=AgentRuntimeDefinition(
                name="wave05-native",
                provider="native",
                model=model,
                tools=sorted(names),
            ),
            model_provider=model_provider,
            tool_providers=[provider],
            cwd=root,
            session_key=_SESSION,
            max_turns=3,
            project_id=str(_SCOPE.project_id),
        )
        await runtime.start()
        assert all(runtime._tool_index[name] is provider for name in names)
        assert set(runtime._tool_wire_to_canonical.values()) == names
        assert all(
            wire != canonical
            for wire, canonical in runtime._tool_wire_to_canonical.items()
        )

        echo_events = await _collect_turn(
            runtime,
            "Call mcp/wave05/echo exactly once with value wave05-connected. "
            "Wait for approval, then report the returned value in one sentence.",
        )
        echo_approvals = [
            event for event in echo_events if event.kind == EVENT_PERMISSION_REQUEST
        ]
        echo_results = [event for event in echo_events if event.kind == EVENT_TOOL_RESULT]
        echo_terminals = [event for event in echo_events if event.kind == EVENT_COMPLETE]
        echo_text = "".join(
            event.text for event in echo_events if event.kind == EVENT_TEXT_CHUNK
        )
        assert len(echo_approvals) == 1
        assert len(echo_results) == 1 and echo_results[0].tool_meta.get("ok") is True
        assert "wave05-connected" in str(echo_results[0].tool_output)
        assert echo_results[0].tool_meta.get("effect_state") == "committed"
        assert len(echo_terminals) == 1 and echo_terminals[0].stop_reason == "end_turn"
        assert echo_terminals[0].served_model_ref == f"centra:{model}"
        assert echo_terminals[0].tool_call_count == 1
        assert echo_terminals[0].tool_meta.get("model_calls", 0) >= 2
        assert echo_text.strip()

        marker = fixture / "effect-started"
        slow_task = asyncio.create_task(
            _collect_turn(
                runtime,
                "Call mcp/wave05/slow_effect exactly once with value cancel-me. "
                "Wait for approval. If its outcome becomes unknown, state that plainly.",
            )
        )
        call_key = await _wait_for_slow_dispatch(provider, marker, slow_task)
        cancellation = await provider.cancel(call_key)
        assert cancellation.state is CancellationState.UNKNOWN
        assert cancellation.effect_state.value == "unknown"
        slow_events = await asyncio.wait_for(slow_task, timeout=30)
        slow_approvals = [
            event for event in slow_events if event.kind == EVENT_PERMISSION_REQUEST
        ]
        slow_results = [event for event in slow_events if event.kind == EVENT_TOOL_RESULT]
        slow_terminals = [event for event in slow_events if event.kind == EVENT_COMPLETE]
        assert len(slow_approvals) == 1
        assert len(slow_results) == 1 and slow_results[0].tool_meta.get("ok") is False
        assert slow_results[0].tool_meta.get("effect_state") == "unknown"
        assert len(slow_terminals) == 1 and slow_terminals[0].stop_reason == "end_turn"
        assert slow_terminals[0].served_model_ref == f"centra:{model}"
        assert slow_terminals[0].tool_call_count == 1

        for terminal in (echo_terminals[0], slow_terminals[0]):
            assert terminal.input_tokens >= 0
            assert terminal.output_tokens >= 0
            assert terminal.cache_creation_tokens >= 0
            assert terminal.cache_read_tokens >= 0
            record_from_event(
                terminal,
                source="chat",
                session_key=_SESSION,
                agent="wave05-native",
                provider="centra",
                model=model,
                estimate_if_missing=False,
            )
        usage_rows = UsageJournal(home / "usage" / "turns.jsonl").rows()
        assert len(usage_rows) == 2
        assert all(row["provider"] == "centra" for row in usage_rows)
        assert all(row["model"] == model for row in usage_rows)
        assert all(row["model_calls"] >= 2 for row in usage_rows)

        child_pid = _spawned_pid(root / "daemon" / "state" / "mcp-children.jsonl")
        assert _process_alive(child_pid)
        await with_mcp_session_eviction(None)(_SESSION)
        assert provider.connected is False
        evicted_health = await client.request("mcp.health", {})
        modules = evicted_health["modules"]
        assert len(modules) == 1
        assert modules[0]["connected_sessions"] == 0
        assert modules[0]["active_calls"] == 0

        daemon_serialized = json.dumps(
            daemon_requests,
            sort_keys=True,
            default=str,
        )
        model_serialized = json.dumps(model_requests, sort_keys=True, default=str)
        for forbidden in (secret, endpoint, _MODEL_CREDENTIAL):
            assert forbidden not in daemon_serialized
            assert forbidden not in model_serialized
        for forbidden_key in ("credential", "authorization", "api_key", "oauth"):
            assert forbidden_key not in daemon_serialized.lower()
        return {
            "daemon_mcp_bridge": True,
            "native_tool_provider": True,
            "approval_count": len(echo_approvals) + len(slow_approvals),
            "terminal_count": len(echo_terminals) + len(slow_terminals),
            "served_model": echo_terminals[0].served_model_ref,
            "usage_rows": len(usage_rows),
            "cancel_state": cancellation.state.value,
            "session_evicted": True,
            "daemon_sha256": _sha256(_daemon_binary()),
        }
    finally:
        if runtime is not None:
            await runtime.shutdown()
        await model_provider.shutdown()
        await lifecycle.stop()
        assert lifecycle.process is None
        if child_pid is not None:
            deadline = asyncio.get_running_loop().time() + 5
            while _process_alive(child_pid) and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.02)
            assert not _process_alive(child_pid), "daemon left a supervised module process live"


def run(root: Path) -> dict[str, object]:
    _require_authorities()
    return asyncio.run(_connected_journey(root))


if __name__ == "__main__":
    environment = find_dotenv(usecwd=True)
    if environment:
        load_dotenv(environment, override=False)
    os.environ.setdefault("HYPERMID_TEST_MODEL", "xai/grok-4.6(high)")
    with tempfile.TemporaryDirectory(prefix="hypermid-wave-05-") as temporary:
        print(json.dumps(run(Path(temporary)), sort_keys=True))
