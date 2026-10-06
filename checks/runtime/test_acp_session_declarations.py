"""Declared ACP options through real protocol sessions and restart ownership."""

import asyncio
import json
import sys
from pathlib import Path

import pytest

from gideon.core.config import AppConfig
from gideon.engine.session import ConversationDirectory, _Session
from gideon.integrations.acp.client import AcpClient
from gideon.integrations.acp.options import compacts_itself, session_metadata
from gideon.integrations.llm.acp_agent import AcpAgentProvider
from gideon.integrations.llm.acp_provider_runtime import launch_arguments
from gideon.integrations.llm.registry import ProviderEntry


@pytest.mark.parametrize(
    "value", [[], "text", {"x": {1}}, {"x": float("nan")}, {"x": {1: "bad"}}]
)
def test_invalid_metadata_fails_closed(value):
    with pytest.raises(ValueError, match="session_meta"):
        session_metadata(value)


@pytest.mark.parametrize("value", [1, "true", None])
def test_compaction_declaration_is_boolean(value):
    with pytest.raises(ValueError, match="boolean"):
        compacts_itself(value)


@pytest.mark.asyncio
async def test_real_new_and_load_receive_copied_metadata(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    record = tmp_path / "requests.jsonl"
    program = "\n".join(
        [
            "import asyncio, json",
            "from gideon.integrations.acp.server import AcpStdioServer, AppConfig",
            "from gideon.integrations.llm.openai import OpenAIProvider",
            "from gideon.integrations.llm.credentials import Credential",
            "AppConfig.create_provider_factory=lambda self: lambda *a, **k: OpenAIProvider(model='local', credential=Credential(name='local',kind='api_key',secret='local-only'),base_url='http://127.0.0.1:1/v1')",
            "async def main():",
            "    server=AcpStdioServer()",
            "    dispatch=server.dispatch",
            "    async def recorded(frame):",
            f"        with open({str(record)!r}, 'a') as output: output.write(json.dumps(frame)+'\\n')",
            "        await dispatch(frame)",
            "    server.dispatch=recorded",
            "    await server.serve()",
            "asyncio.run(main())",
        ]
    )
    declared = {"isolation": {"sources": ["owner"]}, "hint": "app"}
    client = AcpClient(
        work_dir=tmp_path,
        command=[sys.executable, "-c", program],
        sandbox_mode="none",
        session_meta=declared,
        extra_env={
            "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "runtime"),
            "GIDEON_HOME": str(home),
            "GIDEON_CREDENTIAL_BACKEND": "dotenv",
        },
    )
    declared["isolation"]["sources"].append("mutated")
    try:
        await client._open_connection()
        connection = client._connection
        await connection.initialize({"protocolVersion": 1})
        session = await connection.new_session(
            {"cwd": str(tmp_path), "mcpServers": [], "_meta": {"hint": "caller"}}
        )
        assert session.session_id
        # This real server explicitly does not implement resume: the host retains fallback.
        assert (
            await connection.load_session(
                {"sessionId": session.session_id, "_meta": {"resume": "path"}},
                session_id=session.session_id,
            )
            is None
        )
        frames = [json.loads(line) for line in record.read_text().splitlines()]
        requests = [
            f for f in frames if f.get("method") in ("session/new", "session/load")
        ]
        assert len(requests) == 2
        assert requests[0]["params"]["_meta"] == {
            "isolation": {"sources": ["owner"]},
            "hint": "caller",
        }
        assert requests[1]["params"]["_meta"] == {
            "isolation": {"sources": ["owner"]},
            "hint": "app",
            "resume": "path",
        }
    finally:
        await client.shutdown()


@pytest.mark.asyncio
async def test_real_provider_restart_waits_for_turn_lease_and_reports_reason(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    provider = AcpAgentProvider(command=[sys.executable], compacts_itself=False)
    entry = _Session(provider=provider)
    manager = ConversationDirectory(AppConfig())
    manager._sessions["dashboard:local"] = entry
    notices = []

    async def notice(*args):
        notices.append(args)

    manager.set_restart_callback(notice)
    await entry.semaphore.acquire()
    task = asyncio.create_task(manager._compact_session("dashboard:local", 96))
    await asyncio.sleep(0)
    assert manager._sessions["dashboard:local"] is entry
    assert not notices
    entry.semaphore.release()
    await task
    assert "dashboard:local" not in manager._sessions
    assert notices == [
        ("dashboard:local", 96, "Gideon cannot compact this agent's context")
    ]
    automatic = AcpAgentProvider(command=[sys.executable], compacts_itself=True)
    assert automatic.compacts_automatically
    assert not automatic.compacts_in_process


def test_factory_retains_declaration():
    entry = ProviderEntry(
        name="acp:local",
        type="acp_agent",
        model="",
        options={
            "command": ["local"],
            "session_meta": {"flag": True},
            "compacts_itself": True,
        },
    )
    arguments = launch_arguments(entry, None, {})
    provider = AcpAgentProvider(**arguments)
    assert provider.compacts_automatically
    assert provider._client._session_meta == {"flag": True}
