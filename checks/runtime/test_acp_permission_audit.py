"""Actual permission wire answers and masked SEL records, via production ACP stdio."""

import json
import sys
from pathlib import Path

import pytest

from gideon.core.turn_streams import closing_stream
from gideon.integrations.acp.client import AcpClient
from gideon.integrations.acp.dialect import ZedAdapterDialect
from gideon.security.sel import sel


@pytest.mark.asyncio
async def test_unknown_allow_is_refused_and_sent_options_are_audited(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    secret = "ghp_" + "a" * 36
    script = tmp_path / "playback.json"
    options = [
        {"optionId": "allowOnce", "kind": "allow_once", "name": "Allow once"},
        {
            "optionId": "rejectContinue",
            "kind": "reject_once",
            "name": "Deny and continue " + secret,
        },
    ]
    script.write_text(
        json.dumps(
            {
                "version": 1,
                "turns": [
                    {
                        "stop_reason": "end_turn",
                        "tool_calls": [
                            {
                                "id": "call",
                                "name": "write",
                                "input": {"token": secret},
                                "requires_approval": True,
                                "options": options,
                            }
                        ],
                    }
                ],
            }
        )
    )
    program = "\n".join(
        [
            "import asyncio",
            "from gideon.integrations.acp.server import AcpStdioServer",
            "from gideon.integrations.llm.scripted import ScriptedProvider",
            "async def main():",
            "    provider=ScriptedProvider()",
            "    await provider.start()",
            "    server=AcpStdioServer()",
            "    server.sessions['local']=provider",
            "    await server.serve()",
            "asyncio.run(main())",
        ]
    )
    client = AcpClient(
        dialect=ZedAdapterDialect(),
        session_key="dashboard:audit",
        work_dir=tmp_path,
        command=[sys.executable, "-c", program],
        sandbox_mode="none",
        extra_env={
            "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "runtime"),
            "GIDEON_HOME": str(home),
            "GIDEON_SCRIPTED_MODEL_SCRIPT": str(script),
        },
    )
    request_id = None
    try:
        await client._open_connection()
        await client._connection.initialize({"protocolVersion": 1})
        client._session = client._connection._bind_session("local")
        client._session_id = "local"
        async with closing_stream(client.stream_events("Task", timeout=10)) as events:
            async for event in events:
                if event.kind == "permission_request":
                    request_id = event.request_id
                    await client._session.approve_tool(request_id, option_id="unknown")
                    await client.reject_tool(
                        request_id
                    )  # Settled request cannot send a second answer.
                if event.kind == "complete":
                    break
        answer = client.permission_answer(request_id)
        assert answer["sent"] == {
            "outcome": {"outcome": "selected", "optionId": "rejectContinue"}
        }
        assert [o["id"] for o in answer["offered"]] == ["allowOnce", "rejectContinue"]
        answer["offered"].clear()
        assert len(client.permission_answer(request_id)["offered"]) == 2
        rows = [
            r
            for r in sel().recent(100)
            if r.get("source") == "acp:permission"
            and r.get("caller_identity") == "dashboard:audit"
        ]
        assert len(rows) == 1
        assert rows[0]["outcome"] == "sent"
        assert rows[0]["metadata"]["remote_session_id"] == "local"
        assert rows[0]["metadata"]["sent"]["outcome"]["optionId"] == "rejectContinue"
        assert secret not in json.dumps(rows[0])
        assert "tool_input" not in json.dumps(rows[0])
    finally:
        await client.shutdown()
