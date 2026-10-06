"""Local supported SDK/native question transport for the console consumer gate."""

import asyncio
import json
import os
from pathlib import Path

from aiohttp import web
from test_dashboard_ingress_identity import state_at
from test_native_connection_recovery import _http_streams, _provider
from test_owner_question_runtime import app_for

from gideon.engine.agents.native.builtin_tools import (
    PLATFORM_CATEGORIES,
    NativeBuiltinToolProvider,
)
from gideon.engine.agents.native.runtime import NativeAgentRuntime
from gideon.engine.agents.provider import AgentRuntimeDefinition
from gideon.integrations.llm.events import EVENT_TOOL_CALL
from gideon.interfaces.dashboard.token_auth import generate_token
from gideon.security.approval_answer import OWNER, Principal
from gideon.security.session_credentials import begin_turn, end_turn


async def main():
    home = Path(os.environ["GIDEON_HOME"])
    state = state_at(home / "sessions")
    state.conversation_log.init()
    session = state.get_or_create_session("question-ui")
    session._initiator = {"kind": "owner", "name": "sir", "tenant": ""}
    state.owner_questions.window = float(os.environ.get("QUESTION_WINDOW", "600"))
    arguments = {
        "questions": [
            {
                "question": "Which plan?",
                "header": "Plan",
                "multiSelect": False,
                "free_text": False,
                "options": [
                    {"label": "Blue", "description": "Use blue"},
                    {"label": "Red", "description": "Use red"},
                ],
            },
            {
                "question": "Which checks?",
                "header": "Checks",
                "multiSelect": True,
                "options": [
                    {"label": "Compile", "description": "Compile source"},
                    {"label": "Runtime", "description": "Run the runtime"},
                ],
            },
        ]
    }
    call = {
        "index": 0,
        "id": "ui-question-call",
        "type": "function",
        "function": {"name": "ask_user", "arguments": json.dumps(arguments)},
    }
    responses = [
        ([({"tool_calls": [call]}, "tool_calls")], "complete"),
        ([({"content": "Owner answer received."}, None), ({}, "stop")], "complete"),
    ]
    credential = begin_turn(
        "dashboard:question-ui",
        Principal(OWNER, "sir"),
        turn_id="ui-question-turn",
        memory_mode="persistent",
    )
    async with _http_streams(responses) as (endpoint, requests, _, __):
        provider = _provider(endpoint)
        runtime = NativeAgentRuntime(
            definition=AgentRuntimeDefinition(
                name="Owner UI question",
                provider="native",
                model="local-wire",
                tools=["ask_user"],
            ),
            model_provider=provider,
            tool_providers=[
                NativeBuiltinToolProvider(
                    home,
                    provider_name="gideon-filesystem",
                    categories=PLATFORM_CATEGORIES,
                )
            ],
            cwd=home,
            session_key="dashboard:question-ui",
            max_turns=3,
        )
        await runtime.start()
        events = []

        async def consume():
            async for event in runtime.stream("Ask the owner for a plan and finish"):
                events.append(event)
                if event.kind == EVENT_TOOL_CALL:
                    session.append(
                        "tool", "ask_user", meta={"tool_call_id": event.tool_call_id}
                    )

        task = asyncio.create_task(consume())
        session.task = task
        app = app_for(state)

        async def proof(request):
            return web.json_response(
                {
                    "requests": requests,
                    "done": task.done(),
                    "events": [event.kind for event in events],
                    "messages": state.conversation_log._read_messages(
                        "dashboard:question-ui"
                    ),
                }
            )

        app.router.add_get("/proof", proof)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        for _ in range(2000):
            if state.owner_questions.pending:
                break
            if task.done():
                await task
                raise RuntimeError("The real native model did not ask the owner")
            await asyncio.sleep(0.01)
        question = next(iter(state.owner_questions.pending.values()))
        print(
            "QUESTION_READY:"
            + json.dumps(
                {
                    "url": f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}",
                    "token": generate_token("sir"),
                    "question": state.owner_questions.card(question),
                }
            ),
            flush=True,
        )
        try:
            await asyncio.Event().wait()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runner.cleanup()
            await provider.shutdown()
            end_turn(credential)


asyncio.run(main())
