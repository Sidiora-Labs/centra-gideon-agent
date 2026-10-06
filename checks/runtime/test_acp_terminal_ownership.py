"""ACP terminal ownership through a real stdio server and local SDK requests."""
import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from gideon.core.turn_streams import closing_stream
from gideon.integrations.acp.client import AcpClient
from gideon.integrations.acp.dialect import DefaultDialect, ZedAdapterDialect
from gideon.integrations.acp.errors import AcpMethodNotFound, AcpProcessDied
from gideon.integrations.acp.types import EVENT_COMPLETE, EVENT_TEXT_CHUNK


@pytest.mark.parametrize("dialect", [DefaultDialect(), ZedAdapterDialect()])
def test_deny_selects_continuing_refusal_before_turn_end(dialect):
    offered = [
        {"id": "rejectStop", "kind": "reject_once", "label": "No, stop"},
        {"id": "rejectContinue", "kind": "reject_once", "label": "No, continue"},
        {"id": "allowOnce", "kind": "allow_once", "label": "Allow"},
    ]
    assert dialect.select_reject_option_id(offered) == "rejectContinue"
    assert dialect.deny_ends_turn(offered) is False
    assert dialect.deny_ends_turn(offered[:1]) is True
    assert dialect.select_reject_option_id([{"id": "denyAndAllow", "label": "Deny or allow"}]) == ""


async def _read_terminal(stream):
    events = []
    async for event in stream:
        events.append(event)
        if event.kind == EVENT_COMPLETE:
            break
    return events


@pytest.mark.asyncio
async def test_terminal_error_and_partial_close_release_before_next_prompt(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    requests = []

    async def completion(request):
        body = await request.json()
        text = body["messages"][-1]["content"]
        requests.append(text)
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        def frame(delta, finish=None):
            return ("data: " + json.dumps({"id": "local", "object": "chat.completion.chunk", "created": 1, "model": "local-contract", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\n").encode()
        try:
            await response.write(frame({"content": "[Tool call interrupted]" if text == "slow" else "Local answer."}))
            await asyncio.sleep(0.2 if text == "slow" else 0.01)
            await response.write(frame({}, "stop"))
            await response.write(b"data: [DONE]\n\n")
        except ConnectionResetError:
            pass
        return response

    app = web.Application()
    app.router.add_post("/v1/chat/completions", completion)
    async with TestServer(app) as http:
        program = "\n".join([
            "import asyncio",
            "from gideon.integrations.acp.server import AcpStdioServer",
            "from gideon.integrations.llm.openai import OpenAIProvider",
            "from gideon.integrations.llm.credentials import Credential",
            "async def main():",
            f"    provider = OpenAIProvider(model='local-contract', credential=Credential(name='local', kind='api_key', secret='local-only'), base_url={str(http.make_url('/v1'))!r})",
            "    await provider.start()",
            "    server = AcpStdioServer()",
            "    server.sessions['local'] = provider",
            "    await server.serve()",
            "asyncio.run(main())",
        ])
        client = AcpClient(work_dir=tmp_path, command=[sys.executable, "-c", program], sandbox_mode="none", extra_env={"PYTHONPATH": str(Path(__file__).resolve().parents[2] / "runtime"), "GIDEON_HOME": str(home), "GIDEON_CREDENTIAL_BACKEND": "dotenv"})
        retained = []
        try:
            await client._open_connection()
            await client._connection.initialize({"protocolVersion": 1})
            client._session = client._connection._bind_session("local")
            client._session_id = "local"
            first = client.stream_events("first", timeout=5)
            retained.append(first)
            assert (await asyncio.wait_for(_read_terminal(first), 5))[-1].kind == EVENT_COMPLETE
            assert not client._session._turn_lock.locked()
            second = client.stream_events("second", timeout=5)
            retained.append(second)
            assert (await asyncio.wait_for(_read_terminal(second), 5))[-1].kind == EVENT_COMPLETE
            failed = client._session.stream_command("/unsupported", timeout=5)
            retained.append(failed)
            with pytest.raises(AcpMethodNotFound):
                await asyncio.wait_for(_read_terminal(failed), 5)
            assert not client._session._turn_lock.locked()
            partial = client.stream_events("slow", timeout=5)
            retained.append(partial)
            event = await asyncio.wait_for(anext(partial), 5)
            assert event.kind == EVENT_TEXT_CHUNK
            assert not client._session._turn_done.is_set()
            await asyncio.wait_for(partial.aclose(), 5)
            assert not client._session._turn_lock.locked()
            assert client._session._owed_answer is None
            third = client.stream_events("third", timeout=5)
            retained.append(third)
            assert (await asyncio.wait_for(_read_terminal(third), 5))[-1].kind == EVENT_COMPLETE
            assert requests == ["first", "second", "slow", "third"]
        finally:
            for stream in retained:
                await stream.aclose()
            await client.shutdown()
