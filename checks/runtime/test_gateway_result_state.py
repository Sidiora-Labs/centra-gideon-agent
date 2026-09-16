import asyncio
import time

from gideon.core.config import AppConfig
from gideon.engine.gateway import RuntimeCoordinator
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.handlers import MAX_PROMPT_BYTES
from gideon.interfaces.dashboard.state import (
    ConsoleState,
    _ChatSession,
    _load_notifications,
)


def coordinator():
    config = AppConfig()
    runtime = RuntimeCoordinator(config)
    runtime._channel_delivery = None
    runtime.sessions = ConversationDirectory(config)
    runtime.dashboard_state = ConsoleState(runtime.sessions, time.time())
    session = _ChatSession("chat-1-123")
    runtime.dashboard_state._sessions[session.key] = session
    return runtime, session


def test_real_session_receives_scrubbed_result_and_durable_notification():
    async def exercise():
        runtime, session = coordinator()
        secret = "AKIAIOSFODNN7EXAMPLE"
        await runtime._deliver_result(
            f"Report {secret}", f"Task {secret}", f"Ready {secret}", "dashboard:chat-1"
        )
        message = session.messages[-1]
        assert message["role"] == "assistant"
        assert "Report" in message["content"] and "Ready" in message["content"]
        assert secret not in message["content"]
        note = _load_notifications()[-1]
        assert note["session"] == session.key
        assert note["kind"] == "heartbeat" and "Task" in note["body"]
        assert secret not in str(note)
        count = len(_load_notifications())
        await runtime._deliver_result("Missing", "task", "result", "dashboard:absent")
        assert len(_load_notifications()) == count
        assert "absent" not in runtime.dashboard_state._sessions

    asyncio.run(exercise())


def test_busy_session_queues_bounded_utf8_without_notification():
    async def exercise():
        runtime, session = coordinator()
        session.task = asyncio.current_task()
        before = _load_notifications()
        await runtime._deliver_result(
            "Report", "task", "你" * MAX_PROMPT_BYTES, "prompt:dashboard:chat-1"
        )
        assert session.queue_depth == 1
        prompt = session._queue[0]["content"]
        assert prompt.startswith("Report\n\n")
        assert len(prompt.encode("utf-8")) <= MAX_PROMPT_BYTES
        assert "\ufffd" not in prompt
        assert not session.messages and _load_notifications() == before

    asyncio.run(exercise())


def test_replacing_inbox_service_retires_old_task_and_keeps_operator_settings():
    async def exercise():
        runtime, _ = coordinator()
        runtime._cfg.inbox.enabled = False
        runtime._cfg.dashboard.user_name = "  Operator  "
        runtime._cfg.inbox.style_rules = ["Concise", "Friendly"]
        await runtime._init_inbox()
        first = runtime.inbox_svc
        first_task = first._task
        try:
            await runtime._init_inbox()
            await asyncio.sleep(0)
            second = runtime.inbox_svc
            assert second is not first and first_task.cancelled()
            assert second._provider is None
            assert (
                second._user_name == "Operator"
                and second._style_rules == "Concise\nFriendly"
            )
            assert second._task is not None and not second._task.done()
        finally:
            task = runtime.inbox_svc._task
            runtime.inbox_svc.stop()
            await asyncio.gather(first_task, task, return_exceptions=True)

    asyncio.run(exercise())
