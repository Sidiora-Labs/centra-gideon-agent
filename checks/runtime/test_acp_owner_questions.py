"""Actual production ACP subprocess carries authenticated owner question outcomes."""

import asyncio
import json
import sys
from pathlib import Path

import pytest
import test_owner_question_runtime as question_fixtures
from aiohttp.test_utils import TestClient, TestServer
from test_owner_question_runtime import app_for

from gideon.integrations.acp.client import AcpClient
from gideon.integrations.acp.dialect import ClaudeCodeDialect, CodexDialect
from gideon.interfaces.dashboard.token_auth import generate_token
from gideon.security.approval_answer import OWNER, Principal
from gideon.security.session_credentials import begin_turn, end_turn

owned_state = question_fixtures.owned_state

FORM = {
    "mode": "form",
    "message": "Choose a plan",
    "requestedSchema": {
        "type": "object",
        "properties": {
            "question_0": {
                "type": "string",
                "title": "Plan",
                "oneOf": [
                    {"const": "original-a", "title": "Same label"},
                    {"const": "original-b", "title": "Same label"},
                ],
            },
            "question_0_custom": {"type": "string"},
        },
        "required": ["question_0"],
    },
}


@pytest.mark.asyncio
async def test_actual_owner_http_answer_skip_stop_and_late_request(
    owned_state, tmp_path
):
    state, session = owned_state
    home = tmp_path / "home"
    script = tmp_path / "script.json"
    script.write_text(
        json.dumps(
            {
                "version": 1,
                "on_exhausted": "repeat_last",
                "turns": [{"text": "finished"}],
            }
        )
    )
    record = tmp_path / "responses.jsonl"
    settlement_path = tmp_path / "settled.sock"
    settled_receipts = asyncio.Queue()

    async def settled(reader, writer):
        try:
            settled_receipts.put_nowait(json.loads(await reader.readline()))
        finally:
            writer.close()
            await writer.wait_closed()

    settlement_server = await asyncio.start_unix_server(
        settled, path=str(settlement_path)
    )
    program = "\n".join(
        [
            "import asyncio,json",
            "from gideon.integrations.acp.server import AcpStdioServer",
            "from gideon.integrations.llm.scripted import ScriptedProvider",
            "async def main():",
            "    provider=ScriptedProvider(); await provider.start()",
            "    server=AcpStdioServer(); server.sessions['local']=provider",
            "    dispatch=server.dispatch; prompt=server._prompt",
            "    async def request(method,params):",
            "        rid=server._next_request_id; server._next_request_id-=1",
            "        future=asyncio.get_running_loop().create_future(); server.pending[rid]=future",
            "        try:",
            "            await server.send({'jsonrpc':'2.0','id':rid,'method':method,'params':params})",
            "            return await future",
            "        finally: server.pending.pop(rid,None)",
            "    async def question(sid,rid,params):",
            "        text=str(params.get('prompt')); form=" + repr(FORM),
            "        try:",
            "            if 'unsupported' in text:",
            "                await request('unrecognized/method',{'sessionId':sid})",
            "                form={'requestedSchema':{'type':'object','properties':{'unrelated':{'type':'string'}}}}",
            "            await request('elicitation/create',dict(form,sessionId=sid))",
            "            await prompt(sid,rid,params)",
            "            if 'idle' in text: await request('elicitation/create',dict(form,sessionId=sid))",
            "        except asyncio.CancelledError: await server.reply(rid,{'stopReason':'cancelled'})",
            "        finally:",
            "            server.turns.pop(sid,None)",
            f"            reader,writer=await asyncio.open_unix_connection({str(settlement_path)!r})",
            "            writer.write((json.dumps({'session':sid,'request':rid})+chr(10)).encode()); await writer.drain()",
            "            writer.close(); await writer.wait_closed()",
            "    async def recorded(frame):",
            f"        with open({str(record)!r},'a') as output: output.write(json.dumps(frame)+chr(10))",
            "        if frame.get('method')=='session/new': await server.reply(frame['id'],{'sessionId':'local','configOptions':[{'id':'mode','category':'mode','type':'select','currentValue':'default','options':[{'value':'default','name':'Default'}]}]}); return",
            "        if frame.get('method')=='session/set_config_option' and frame.get('params')=={'sessionId':'local','configId':'mode','value':'default'}: await server.reply(frame['id'],{}); return",
            "        await dispatch(frame)",
            "    server._prompt=question; server.dispatch=recorded; await server.serve()",
            "asyncio.run(main())",
        ]
    )
    credential = begin_turn(
        "dashboard:questions",
        Principal(OWNER, "sir"),
        turn_id="owner-acp",
        memory_mode="persistent",
    )
    client = AcpClient(
        dialect=ClaudeCodeDialect(),
        work_dir=tmp_path,
        session_key="dashboard:questions",
        command=[sys.executable, "-c", program],
        sandbox_mode="none",
        extra_env={
            "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "runtime"),
            "GIDEON_HOME": str(home),
            "GIDEON_SCRIPTED_MODEL_SCRIPT": str(script),
            "GIDEON_CREDENTIAL_BACKEND": "dotenv",
        },
    )

    async def consume(text):
        events = [event async for event in client.stream_events(text, timeout=10)]
        receipt = await asyncio.wait_for(settled_receipts.get(), 5)
        frames = [json.loads(line) for line in record.read_text().splitlines()]
        prompt = next(
            frame
            for frame in reversed(frames)
            if frame.get("method") == "session/prompt"
        )
        assert receipt == {"session": "local", "request": prompt["id"]}
        return events

    async def wait_question(task):
        for _ in range(500):
            if state.owner_questions.pending:
                return next(iter(state.owner_questions.pending))
            if task.done():
                await task
                pytest.fail("No owner question was emitted")
            await asyncio.sleep(0.01)
        pytest.fail("Owner question timed out")

    try:
        async with TestClient(TestServer(app_for(state))) as http:
            headers = {"Authorization": "Bearer " + generate_token("sir")}
            for text, skip in [("answer", False), ("skip", True)]:
                task = asyncio.create_task(consume(text))
                session.task = task
                identity = await wait_question(task)
                wrong = await http.post(
                    "/api/chat/questions/answer",
                    headers={"Authorization": "Bearer " + generate_token("other")},
                    json={
                        "id": identity,
                        "session": session.key,
                        "answers": [{"selected": [0]}],
                    },
                )
                assert wrong.status in {400, 403}
                response = await http.post(
                    "/api/chat/questions/answer",
                    headers=headers,
                    json={
                        "id": identity,
                        "session": session.key,
                        "skip": skip,
                        "answers": [{"selected": [1], "other": ""}],
                    },
                )
                assert response.status == 200, await response.text()
                events = await asyncio.wait_for(task, 10)
                assert sum(event.kind == "complete" for event in events) == 1
            task = asyncio.create_task(consume("stop"))
            session.task = task
            identity = await wait_question(task)
            await client.cancel_session()
            events = await asyncio.wait_for(task, 10)
            assert sum(event.kind == "complete" for event in events) == 1
            late = await http.post(
                "/api/chat/questions/answer",
                headers=headers,
                json={"id": identity, "session": session.key, "skip": True},
            )
            assert late.status == 400
            task = asyncio.create_task(consume("unsupported idle"))
            session.task = task
            events = await asyncio.wait_for(task, 10)
            assert not state.owner_questions.pending
        frames = [json.loads(line) for line in record.read_text().splitlines()]
        initialize = next(
            frame for frame in frames if frame.get("method") == "initialize"
        )
        assert initialize["params"]["clientCapabilities"] == {
            "elicitation": {"form": {}}
        }
        responses = [
            frame["result"]
            for frame in frames
            if isinstance(frame.get("result"), dict) and "action" in frame["result"]
        ]
        assert responses[0] == {
            "action": "accept",
            "content": {"question_0": "original-b"},
        }
        assert responses[1] == {"action": "decline"}
        assert responses[2:] == [
            {"action": "cancel"},
            {"action": "cancel"},
            {"action": "cancel"},
        ]
        assert any(frame.get("error", {}).get("code") == -32601 for frame in frames)
        assert CodexDialect().client_capabilities(attended=True) == {}
    finally:
        await client._teardown()
        settlement_server.close()
        await settlement_server.wait_closed()
        end_turn(credential)
