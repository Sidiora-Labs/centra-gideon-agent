from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from gideon.core.config import AppConfig
from gideon.engine.gateway import ApprovalExchange, ApprovalFlow, RuntimeCoordinator
from gideon.engine.session import ConversationDirectory
from gideon.integrations.channel_trust import allow_sender
from gideon.integrations.llm.events import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TOOL_RESULT,
    AgentEvent,
)
from gideon.interfaces.dashboard.chat_runner import (
    _channel_task_terminal_status,
    _unfinished_channel_task_status,
)
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.security.approval_answer import YOU
from gideon.integrations.acp.types import STOP_REASON_END_TURN

_DISCORD_APP = (
    Path(__file__).resolve().parents[2]
    / "runtime/gideon/extensions/apps/native/gideonai-discord-desk"
)
sys.path.insert(0, str(_DISCORD_APP))
from discord_desk.delivery import (  # noqa: E402
    _PendingApproval,
    _approval_ending as discord_approval_ending,
    _apply_verified_interaction,
    _render_stream_tasks,
)

from gideon.integrations.telegram.delivery import _approval_ending as telegram_approval_ending


@pytest.mark.asyncio
async def test_task_and_approval_prompts_report_actual_terminal_outcomes(
    tmp_path, monkeypatch
):
    home = tmp_path / "gideon"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("GIDEON_OWNER_ID", "owner-1")
    allow_sender("discord", "owner-1")

    task_vectors = (
        (AgentEvent(kind=EVENT_TOOL_RESULT, tool_meta={"ok": True}), False, "complete"),
        (AgentEvent(kind=EVENT_TOOL_RESULT, tool_meta={"ok": False}), False, "failed"),
        (
            AgentEvent(
                kind=EVENT_TOOL_RESULT,
                tool_meta={"ok": True, "unasked_outcome": "denied"},
            ),
            False,
            "rejected",
        ),
        (AgentEvent(kind=EVENT_TOOL_RESULT, tool_meta={"ok": True}), True, "failed"),
    )
    for event, agent_error, expected in task_vectors:
        assert _channel_task_terminal_status(event, agent_error=agent_error) == expected

    assert (
        _unfinished_channel_task_status(
            AgentEvent(kind=EVENT_COMPLETE, stop_reason=STOP_REASON_END_TURN)
        )
        == "failed"
    )
    assert (
        _unfinished_channel_task_status(
            AgentEvent(kind=EVENT_COMPLETE, stop_reason="cancelled")
        )
        == "cancelled"
    )

    rendered = _render_stream_tasks(
        "Working",
        {
            "ok": ("Read status", "complete"),
            "bad": ("Write config", "failed"),
            "denied": ("Delete data", "rejected"),
            "expired": ("Deploy", "expired"),
            "cancelled": ("Restart", "cancelled"),
        },
    )
    assert rendered == (
        "Working\n✅ Read status\n❌ Write config\n🚫 Delete data"
        "\n⌛ Deploy\n↩️ Restart"
    )

    assert [
        discord_approval_ending(status)
        for status in ("approved", "rejected", "expired", "cancelled")
    ] == ["✅ Approved", "🚫 Rejected", "⌛ Expired", "↩️ Cancelled"]
    assert [
        telegram_approval_ending(status)
        for status in ("approved", "rejected", "expired", "cancelled")
    ] == ["✅ Approved", "🚫 Rejected", "⌛ Expired", "↩️ Cancelled"]

    cfg = AppConfig.load()
    cfg.session.pool_size = 0
    directory = ConversationDirectory(cfg)
    state = ConsoleState(directory, start_time=0.0, owner_id="owner-1")
    runtime = RuntimeCoordinator(cfg)
    runtime.sessions = directory
    runtime.dashboard_state = state
    flow = ApprovalFlow(runtime, "channel-terminal", lambda request_id: "channel-session")

    async def wait_for_dashboard_prompt(request_id: str, exchange: ApprovalExchange):
        assert exchange.dashboard_future is not None
        async with asyncio.timeout(5):
            while request_id not in state._pending_approvals:
                await asyncio.sleep(0)

    try:
        # A provider-shaped interaction contains only fields Discord supplies to
        # INTERACTION_CREATE. No interaction ack identifiers are present, so this
        # exercises the actual local verifier without performing network I/O.
        event = AgentEvent(
            kind=EVENT_PERMISSION_REQUEST,
            request_id="channel-approved",
            title="restart service",
        )
        exchange = ApprovalExchange(flow, event, "channel-session")
        pending = _PendingApproval(event.request_id, "channel-7", "prompt-7")
        assert exchange.on_prompted(pending) is True
        await wait_for_dashboard_prompt(event.request_id, exchange)

        outsider = {
            "type": 3,
            "data": {"custom_id": "approve:channel-approved"},
            "channel_id": "channel-7",
            "message": {"id": "prompt-7"},
            "member": {"user": {"id": "other-user", "bot": False}},
        }
        assert not _apply_verified_interaction(outsider, pending, "owner-1")
        assert not pending.future.done()
        assert pending.answerer is None

        owner = {
            "type": 3,
            "data": {"custom_id": "approve:channel-approved"},
            "channel_id": "channel-7",
            "message": {"id": "prompt-7"},
            "member": {"user": {"id": "owner-1", "bot": False}},
        }
        assert _apply_verified_interaction(owner, pending, "owner-1")
        assert pending.answerer is not None and pending.answerer.kind == "channel"
        assert exchange.finish_channel_decision(await pending.future) is True
        assert await exchange.dashboard_future is True
        assert state.ended_as(event.request_id) == "approved"

        dashboard_cases = (
            ("dashboard-rejected", "rejected"),
            ("dashboard-expired", "expired"),
            ("dashboard-cancelled", "cancelled"),
        )
        for request_id, outcome in dashboard_cases:
            exchange = ApprovalExchange(
                flow,
                AgentEvent(
                    kind=EVENT_PERMISSION_REQUEST,
                    request_id=request_id,
                    title="write report",
                ),
                "channel-session",
            )
            pending = _PendingApproval(request_id, "channel-8", f"prompt-{request_id}")
            assert exchange.on_prompted(pending) is True
            await wait_for_dashboard_prompt(request_id, exchange)
            if outcome in {"expired", "cancelled"}:
                state.end_approval(request_id, outcome=outcome)
            else:
                assert state.resolve_approval(request_id, False, by=YOU)
            assert await exchange.dashboard_future is False
            assert await pending.future == outcome
            assert exchange.finish_channel_decision(await pending.future) is False
            assert state.ended_as(request_id) == outcome
    finally:
        await directory.close_all()
