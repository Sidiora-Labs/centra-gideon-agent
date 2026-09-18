import asyncio
import json
import re
import time

from gideon.automation.workflows.ownership import owned_key
from gideon.cognition.context import PromptAssembler
from gideon.core.config import AppConfig
from gideon.engine import gateway
from gideon.engine.delegation_host import DelegationHost
from gideon.engine.session import ConversationDirectory
from gideon.engine.subagent import SubagentInfo
from gideon.integrations.tool_providers.result_store import get_result
from gideon.interfaces.dashboard.state import ConsoleState, _load_notifications


def coordinator():
    runtime = gateway.RuntimeCoordinator(AppConfig())
    runtime.sessions = ConversationDirectory(runtime.config)
    runtime.ctx_builder = PromptAssembler()
    runtime.dashboard_state = ConsoleState(runtime.sessions, time.time())
    runtime._init_subagents()
    return runtime


def test_actual_supervisor_delivers_batch_and_honors_silent_only_batches(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))

    async def exercise():
        runtime = coordinator()
        manager = runtime.subagent_mgr
        try:
            assert manager._reaper_task and not manager._reaper_task.done()
            await manager._on_done([])
            assert not _load_notifications()
            good = SubagentInfo(
                id="good",
                task="First task",
                result="Done",
                parent_session_key="subagent:parent",
            )
            failed = SubagentInfo(
                id="bad",
                task="Second task",
                error="Unavailable",
                parent_session_key="subagent:parent",
                silent=True,
            )
            await manager._on_done([good, failed])
            notes = _load_notifications()
            assert len(notes) == 1 and notes[0]["title"] == "2 subagents with failures"
            assert "Agent `good` completed" in notes[0]["body"]
            assert "Agent `bad` failed" in notes[0]["body"]
            good.silent = True
            await manager._on_done([good, failed])
            assert _load_notifications() == notes
        finally:
            await manager.cancel_all()

    asyncio.run(exercise())


def test_busy_parent_keeps_real_task_and_queues_single_batch(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setattr(gateway, "INJECTION_TIMEOUT", 0.01)

    async def exercise():
        runtime = coordinator()
        session = runtime.dashboard_state.get_or_create_session("busy")
        wait = asyncio.create_task(asyncio.Event().wait())
        session.task = wait
        try:
            await runtime.subagent_mgr._on_done(
                [
                    SubagentInfo(
                        id="one",
                        task="Work",
                        result="Result",
                        parent_session_key="dashboard:busy",
                    )
                ]
            )
            assert session.task is wait and session.running
            assert session.queue_depth == 1
            assert "Agent `one` completed" in session.queue_pop()["content"]
            assert len(_load_notifications()) == 1
        finally:
            wait.cancel()
            await asyncio.gather(wait, return_exceptions=True)
            await runtime.subagent_mgr.cancel_all()

    asyncio.run(exercise())


def test_failure_event_updates_actual_session_and_recovery_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))

    async def exercise():
        runtime = coordinator()
        session = runtime.dashboard_state.get_or_create_session("failure")
        info = SubagentInfo(
            id="one", task="Work", parent_session_key="dashboard:failure"
        )
        try:
            await runtime.subagent_mgr._on_event(
                "subagent_injection_failed",
                info,
                {"error": "delivery unavailable", "failure_msg": "Retry the result"},
            )
            assert session._pending_subagent_failures == ["Retry the result"]
            assert "delivery unavailable" in session.messages[-1]["content"]
            session._recovery_retrigger_count = 3
            DelegationHost(runtime, gateway).recover(session, info.parent_session_key)
            assert session._pending_subagent_failures == []
            assert session.task is None
        finally:
            await runtime.subagent_mgr.cancel_all()

    asyncio.run(exercise())


def test_long_completion_keeps_actual_retrievable_result(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))

    async def exercise():
        runtime = coordinator()
        parent = "subagent:parent"
        raw = json.dumps(
            [{"line": number, "text": "original data" * 20} for number in range(200)]
        )
        try:
            await runtime.subagent_mgr._on_done(
                [
                    SubagentInfo(
                        id="large",
                        task="Produce report",
                        result=raw,
                        parent_session_key=parent,
                    )
                ]
            )
            body = _load_notifications()[0]["body"]
            result_id = re.search(r'result_id="(r_[a-f0-9]+)"', body)
            assert result_id and len(body) < len(raw)
            stored = get_result(parent, result_id.group(1))
            assert stored["raw"] == raw
        finally:
            await runtime.subagent_mgr.cancel_all()

    asyncio.run(exercise())


def test_workflow_owned_completion_never_routes_to_a_channel(tmp_path, monkeypatch):
    """A stage subagent's parent is the run (`workflow:<run>:<node>`), not a chat.

    `CompletionDelivery.send` routed every parent that was not `dashboard:`, `cron:` or
    `subagent:` down the CHANNEL path — which opens an ACP session on that key, injects
    the completion, and posts the result into the owner's DM. A workflow stage's result
    belongs to its run: the controller reconciles it off the supervisor and threads it
    into the next node. Delivering it twice, once as a channel message, is the leak.
    """
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))

    async def exercise():
        runtime = coordinator()
        manager = runtime.subagent_mgr
        key = owned_key("r-42", "stage")
        asked: list[str] = []
        original = runtime.sessions.get_or_create

        async def watched(session_key, *args, **kwargs):
            asked.append(session_key)
            return await original(session_key, *args, **kwargs)

        monkeypatch.setattr(runtime.sessions, "get_or_create", watched)
        try:
            await manager._on_done(
                [
                    SubagentInfo(
                        id="stage-1",
                        task="Run the stage",
                        result="stage output",
                        parent_session_key=key,
                        silent=True,
                    )
                ]
            )
            assert asked == [], f"a workflow-owned completion opened a session: {asked}"
            assert not runtime.sessions.has_session(key)
            assert _load_notifications() == []
        finally:
            await manager.cancel_all()

    asyncio.run(exercise())
