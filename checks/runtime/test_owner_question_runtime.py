"""Real authenticated owner answer reaches the native tool and subsequent SDK request."""
import asyncio
import json
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gideon.engine.agents.native.builtin_tools import NativeBuiltinToolProvider, PLATFORM_CATEGORIES
from gideon.engine.agents.native.runtime import NativeAgentRuntime
from gideon.engine.agents.provider import AgentRuntimeDefinition
from gideon.integrations.llm.events import EVENT_TOOL_CALL, EVENT_TOOL_RESULT, EVENT_COMPLETE
from gideon.interfaces.dashboard.chat_questions import api_question_answer, api_question_pending
from gideon.interfaces.dashboard.token_auth import token_auth_middleware, generate_token
from gideon.security.approval_answer import Principal, OWNER
from gideon.security.owner_questions import bind_call, reset_call, QuestionRefused
from gideon.security.session_credentials import begin_turn, end_turn
from test_dashboard_ingress_identity import state_at
from test_native_connection_recovery import _http_streams, _provider

QUESTIONS = {"questions": [{"header": "Plan", "question": "Which plan?", "options": [{"label": "Blue", "description": "Use blue"}, {"label": "Red", "description": "Use red"}], "multiSelect": False}]}


def app_for(state):
    app = web.Application(middlewares=[token_auth_middleware(port=8000)])
    app["state"] = state
    app.router.add_post("/api/chat/questions/answer", api_question_answer)
    app.router.add_get("/api/chat/questions", api_question_pending)
    return app


@pytest.fixture
def owned_state(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    state = state_at(home / "sessions")
    state.conversation_log.init()
    session = state.get_or_create_session("questions")
    session._initiator = {"kind": "owner", "name": "sir", "tenant": ""}
    return state, session


@pytest.mark.asyncio
async def test_actual_sdk_tool_wait_owner_http_answer_then_model_continues(owned_state, tmp_path):
    state, session = owned_state
    call = {"index": 0, "id": "question-call", "type": "function", "function": {"name": "ask_user", "arguments": json.dumps(QUESTIONS)}}
    responses = [([({"tool_calls": [call]}, "tool_calls")], "complete"), ([({"content": "The blue plan is selected."}, None), ({}, "stop")], "complete")]
    credential = begin_turn("dashboard:questions", Principal(OWNER, "sir"), turn_id="question-turn", memory_mode="persistent")
    async with _http_streams(responses) as (endpoint, requests, _, __):
        model = _provider(endpoint)
        runtime = NativeAgentRuntime(definition=AgentRuntimeDefinition(name="Owner question", provider="native", model="local-wire", tools=["ask_user"]), model_provider=model,
                                     tool_providers=[NativeBuiltinToolProvider(tmp_path, provider_name="gideon-filesystem", categories=PLATFORM_CATEGORIES)], cwd=tmp_path, session_key="dashboard:questions", max_turns=3)
        await runtime.start()
        events = []
        async def consume():
            async for event in runtime.stream("Ask which plan and finish"):
                events.append(event)
                if event.kind == EVENT_TOOL_CALL:
                    session.append("tool_call", "ask_user", meta={"tool_call_id": event.tool_call_id})
        task = asyncio.create_task(consume())
        session.task = task
        try:
            async with TestClient(TestServer(app_for(state))) as client:
                for _ in range(1500):
                    if state.owner_questions.pending:
                        break
                    if task.done():
                        await task
                        pytest.fail("Model tool finished without a waiting owner question")
                    await asyncio.sleep(0.01)
                assert state.owner_questions.pending, ([(event.kind, event.title, event.tool_output) for event in events], requests, [(tool.name, tool.requires_approval) for tool in runtime._tool_defs])
                headers = {"Authorization": "Bearer " + generate_token("sir")}
                pending = await (await client.get("/api/chat/questions?session=questions", headers=headers)).json()
                question_id = pending["questions"][0]["id"]
                assert session.to_dict()["waiting_for_input"]
                assert len(requests) == 1
                wrong = await client.post("/api/chat/questions/answer", headers={"Authorization": "Bearer " + generate_token("other")}, json={"id": question_id, "session": session.key, "answers": [{"selected": [0]}]})
                assert wrong.status == 400
                invalid = await client.post("/api/chat/questions/answer", headers=headers, json={"id": question_id, "session": session.key, "answers": [{"selected": [True]}]})
                assert invalid.status == 400 and state.owner_questions.pending
                answer = await client.post("/api/chat/questions/answer", headers=headers, json={"id": question_id, "session": session.key, "answers": [{"selected": [0], "other": ""}]})
                assert answer.status == 200, await answer.text()
                duplicate = await client.post("/api/chat/questions/answer", headers=headers, json={"id": question_id, "session": session.key, "skip": True})
                assert duplicate.status == 400
                await asyncio.wait_for(task, timeout=10)
            results = [event for event in events if event.kind == EVENT_TOOL_RESULT]
            assert len(results) == 1 and "Blue" in results[0].tool_output
            assert len(requests) == 2
            assert "Blue" in str(requests[1]["messages"])
            assert events[-1].kind == EVENT_COMPLETE
            rows = state.conversation_log._read_messages("dashboard:questions")
            records = [(row.get("meta") or {}).get("owner_question") for row in rows]
            assert any(record and record["outcome"] == "answered" for record in records)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await model.shutdown()
            end_turn(credential)


@pytest.mark.asyncio
async def test_actual_question_cancel_and_restart_orphan_expiration(owned_state):
    state, session = owned_state
    credential = begin_turn("dashboard:questions", Principal(OWNER, "sir"), turn_id="cancel-turn", memory_mode="persistent")
    token = bind_call("cancel-call")
    session.task = asyncio.create_task(asyncio.Event().wait())
    question_task = asyncio.create_task(state.owner_questions.ask("dashboard:questions", QUESTIONS))
    try:
        await asyncio.sleep(0)
        assert state.owner_questions.pending
        question_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await question_task
        assert not state.owner_questions.pending
        assert session.messages[-1]["meta"]["owner_question"]["outcome"] == "cancelled"
        session.append("tool_call", "orphan", meta={"tool_call_id": "old", "owner_question": {"id": "old-question", "outcome": "pending", "answerable": True}})
        state.owner_questions.expire_orphans(session)
        assert session.messages[-1]["meta"]["owner_question"]["outcome"] == "cancelled"
        assert not session.to_dict()["waiting_for_input"]
    finally:
        session.task.cancel()
        await asyncio.gather(session.task, return_exceptions=True)
        reset_call(token)
        end_turn(credential)


@pytest.mark.asyncio
async def test_unattended_and_model_supplied_call_identity_cannot_ask(owned_state):
    state, session = owned_state
    credential = begin_turn("subagent:child", Principal(OWNER, "sir"), turn_id="child", memory_mode="persistent")
    try:
        with pytest.raises(QuestionRefused):
            await state.owner_questions.ask("subagent:child", {**QUESTIONS, "tool_call_id": "forged"})
    finally:
        end_turn(credential)
    provider = NativeBuiltinToolProvider()
    definitions = await provider.list_tools()
    assert next(tool for tool in definitions if tool.name == "ask_user").interactive
