"""Real console futures exercise the gateway approval policy and cleanup."""

import asyncio
import time

import pytest

from gideon.core.config import AppConfig
from gideon.engine.gateway import RuntimeCoordinator
from gideon.engine.session import ConversationDirectory
from gideon.integrations.llm.base import LLMEvent
from gideon.interfaces.dashboard.state import ConsoleState, _ChatSession


def coordinator():
    config = AppConfig()
    runtime = RuntimeCoordinator(config)
    runtime._channel_delivery = None
    runtime.sessions = ConversationDirectory(config)
    runtime.dashboard_state = ConsoleState(runtime.sessions, time.time())
    session = _ChatSession("owned")
    session.task = asyncio.current_task()
    runtime.dashboard_state._sessions["owned"] = session
    return runtime, session


@pytest.mark.parametrize("decision", [True, False])
def test_console_decision_settles_the_real_pending_future(decision):
    async def exercise():
        runtime, _ = coordinator()
        event = LLMEvent(
            kind="permission_request",
            request_id="real-prompt",
            title="write report",
            tool_input="report.txt",
        )
        pending = asyncio.create_task(runtime._interactive_approval("subagent")(event))
        try:
            async with asyncio.timeout(5):
                while (
                    event.request_id not in runtime.dashboard_state._pending_approvals
                ):
                    await asyncio.sleep(0.01)
                card = runtime.dashboard_state._pending_approvals[event.request_id]
                assert card["session"] == "owned" and card["tool_input"] == "report.txt"
                assert runtime.dashboard_state.resolve_approval(
                    event.request_id, decision
                )
                assert await pending is decision
            assert not runtime.dashboard_state._approval_futures
            assert not runtime.dashboard_state._pending_approvals
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)

    asyncio.run(exercise())


def test_missing_explicit_parent_does_not_inherit_running_session_trust():
    async def exercise():
        runtime, session = coordinator()
        session._trust = True
        event = LLMEvent(
            kind="permission_request", request_id="unresolved", title="write report"
        )
        callback = runtime._interactive_approval("subagent", session_resolver={}.get)
        pending = asyncio.create_task(callback(event))
        try:
            async with asyncio.timeout(5):
                while (
                    event.request_id not in runtime.dashboard_state._pending_approvals
                ):
                    await asyncio.sleep(0.01)
                assert not pending.done()
                assert runtime.dashboard_state.resolve_approval(event.request_id, False)
                assert await pending is False
            assert not runtime.dashboard_state._approval_futures
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)

    asyncio.run(exercise())
