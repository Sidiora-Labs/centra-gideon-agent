"""Tests for inline action button and extended element interactions."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from slack_desk_runtime.interactions import (
    _extract_selected_value,
    _mark_button_clicked,
)

# ---------------------------------------------------------------------------
# _mark_button_clicked
# ---------------------------------------------------------------------------


class TestMarkButtonClicked:
    def test_replaces_clicked_button_with_check(self) -> None:
        blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": "Pick one"}},
            {
                "type": "actions",
                "elements": [
                    {"action_id": "btn_1", "text": {"type": "plain_text", "text": "A"}},
                    {"action_id": "btn_2", "text": {"type": "plain_text", "text": "B"}},
                ],
            },
        ]
        result = _mark_button_clicked(blocks, "btn_1", "A")
        assert result[0] == blocks[0]  # section unchanged
        assert result[1]["type"] == "context"
        assert "✓ A" in result[1]["elements"][0]["text"]
        assert result[2]["type"] == "actions"
        assert len(result[2]["elements"]) == 1
        assert result[2]["elements"][0]["action_id"] == "btn_2"

    def test_button_not_found_returns_unchanged(self) -> None:
        blocks = [
            {
                "type": "actions",
                "elements": [{"action_id": "btn_1"}],
            },
        ]
        result = _mark_button_clicked(blocks, "nonexistent", "X")
        assert result == blocks

    def test_last_button_removed_drops_empty_actions(self) -> None:
        blocks = [
            {
                "type": "actions",
                "elements": [{"action_id": "btn_1"}],
            },
        ]
        result = _mark_button_clicked(blocks, "btn_1", "Done")
        assert len(result) == 1
        assert result[0]["type"] == "context"


# ---------------------------------------------------------------------------
# _extract_selected_value
# ---------------------------------------------------------------------------


class TestExtractSelectedValue:
    def test_selected_option(self) -> None:
        action = {
            "selected_option": {
                "value": "high",
                "text": {"type": "plain_text", "text": "High"},
            }
        }
        assert _extract_selected_value(action) == ("high", "High")

    def test_selected_date(self) -> None:
        action = {"selected_date": "2026-04-08"}
        assert _extract_selected_value(action) == ("2026-04-08", "2026-04-08")

    def test_selected_time(self) -> None:
        action = {"selected_time": "14:30"}
        assert _extract_selected_value(action) == ("14:30", "14:30")

    def test_selected_date_time(self) -> None:
        action = {"selected_date_time": 1712592000}
        assert _extract_selected_value(action) == ("1712592000", "1712592000")

    def test_no_selection_returns_empty(self) -> None:
        assert _extract_selected_value({}) == ("", "")

    def test_malformed_selected_option_no_crash(self) -> None:
        """Defensive .get() prevents KeyError on malformed payloads."""
        action = {"selected_option": {"unexpected": True}}
        raw, display = _extract_selected_value(action)
        assert raw == ""
        assert display == ""


# ---------------------------------------------------------------------------
# _handle_options — action button path
# ---------------------------------------------------------------------------


def _make_orch(post_ts: str = "9999.000") -> MagicMock:
    """Build a minimal mock orchestrator for _handle_options tests."""
    orch = MagicMock()
    orch.slack_desk = AsyncMock()
    orch.slack_desk.update_message = AsyncMock()
    orch.slack_desk.post_message = AsyncMock(return_value=post_ts)
    orch.slack_desk.delete_message = AsyncMock()
    orch.sessions = MagicMock()
    orch.ctx_builder = MagicMock()
    orch.cron_svc = MagicMock()
    orch.conv_log = MagicMock()
    orch.consolidator = MagicMock()
    orch.subagent_mgr = MagicMock()
    orch.task_runner = MagicMock()
    orch._handler_tasks = set()
    return orch


def _base_payload(
    value: str = "",
    action_id: str = "opt_0",
    thread_ts: str = "100.0",
    msg_ts: str = "200.0",
    blocks: list[dict] | None = None,
) -> tuple[dict, dict, str, str]:
    action = {"value": value, "action_id": action_id, "text": {"text": "Label"}}
    payload = {
        "user": {"id": "U123"},
        "team": {"id": "T1"},
        "message": {"ts": msg_ts, "thread_ts": thread_ts, "blocks": blocks or []},
    }
    return payload, action, "C1", msg_ts


@pytest.fixture
def orch_fixture(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Shared orchestrator mock with guaranteed cleanup via monkeypatch."""
    from slack_desk_runtime import interactions

    orch = _make_orch()
    monkeypatch.setattr(interactions, "_orch", orch)
    return orch


@pytest.mark.asyncio
async def test_action_button_happy_path(orch_fixture: MagicMock) -> None:
    """Button with action:: prefix routes payload to session as context."""
    from slack_desk_runtime import interactions

    orch = orch_fixture

    payload_json = '{"task":"deploy"}'
    payload, action, channel, msg_ts = _base_payload(
        value=f"action::{payload_json}", action_id="btn_deploy"
    )

    with patch.object(interactions, "handle_message", new_callable=AsyncMock) as mock_hm:
        await interactions._handle_options(payload, action, channel, msg_ts)
        # Let the created task run
        await asyncio.sleep(0)
        for t in list(orch._handler_tasks):
            await t

        mock_hm.assert_called_once()
        call_kwargs = mock_hm.call_args
        assert call_kwargs.kwargs["action_context"] is not None
        assert "Action button clicked" in call_kwargs.kwargs["action_context"]
        assert payload_json in call_kwargs.kwargs["action_context"]


@pytest.mark.asyncio
async def test_extended_element_happy_path(orch_fixture: MagicMock) -> None:
    """Extended element with action:: in action_id merges selected_value."""
    from slack_desk_runtime import interactions

    orch = orch_fixture

    base = json.dumps({"snooze": True})
    payload, action, channel, msg_ts = _base_payload(
        value="", action_id=f"action::{base}"
    )
    action["selected_date"] = "2026-04-10"
    action["placeholder"] = {"text": "Pick date"}

    with patch.object(interactions, "handle_message", new_callable=AsyncMock) as mock_hm:
        await interactions._handle_options(payload, action, channel, msg_ts)
        await asyncio.sleep(0)
        for t in list(orch._handler_tasks):
            await t

        mock_hm.assert_called_once()
        ctx = mock_hm.call_args.kwargs["action_context"]
        assert "Action element selected" in ctx
        merged = json.loads(ctx.split(": ", 1)[1].split("]\n")[0])
        assert merged["selected_value"] == "2026-04-10"
        assert merged["snooze"] is True


@pytest.mark.asyncio
async def test_malformed_json_in_action_id_no_crash(orch_fixture: MagicMock) -> None:
    """Invalid JSON in action_id logs warning and returns gracefully."""
    from slack_desk_runtime import interactions

    payload, action, channel, msg_ts = _base_payload(
        value="", action_id="action::not{valid-json"
    )
    action["selected_date"] = "2026-04-10"

    with patch.object(interactions, "handle_message", new_callable=AsyncMock) as mock_hm:
        # Should not raise
        await interactions._handle_options(payload, action, channel, msg_ts)
        mock_hm.assert_not_called()


@pytest.mark.asyncio
async def test_non_dict_json_in_action_id_no_crash(orch_fixture: MagicMock) -> None:
    """Non-dict JSON (e.g. a list) in action_id logs warning and returns."""
    from slack_desk_runtime import interactions

    payload, action, channel, msg_ts = _base_payload(
        value="", action_id='action::["a","b"]'
    )
    action["selected_date"] = "2026-04-10"

    with patch.object(interactions, "handle_message", new_callable=AsyncMock) as mock_hm:
        await interactions._handle_options(payload, action, channel, msg_ts)
        mock_hm.assert_not_called()


@pytest.mark.asyncio
async def test_post_message_failure_aborts(orch_fixture: MagicMock) -> None:
    """If post_message returns None, handle_message is never called."""
    from slack_desk_runtime import interactions

    orch = orch_fixture
    orch.slack_desk.post_message = AsyncMock(return_value=None)

    payload, action, channel, msg_ts = _base_payload(
        value='action::{"k":"v"}', action_id="btn_x"
    )

    with patch.object(interactions, "handle_message", new_callable=AsyncMock) as mock_hm:
        await interactions._handle_options(payload, action, channel, msg_ts)
        await asyncio.sleep(0)
        mock_hm.assert_not_called()


@pytest.mark.asyncio
async def test_standard_options_post_message_failure_aborts(orch_fixture: MagicMock) -> None:
    """If update_message AND fallback post_blocks both fail, handle_message is never called."""
    from slack_desk_runtime import interactions

    orch = orch_fixture
    orch.slack_desk.update_message = AsyncMock(side_effect=Exception("API error"))
    orch.slack_desk.post_blocks = AsyncMock(return_value=None)

    payload, action, channel, msg_ts = _base_payload(
        value="some choice", action_id="opt_0"
    )

    with patch.object(interactions, "handle_message", new_callable=AsyncMock) as mock_hm:
        await interactions._handle_options(payload, action, channel, msg_ts)
        mock_hm.assert_not_called()


@pytest.mark.asyncio
async def test_redaction_applied_to_payload(orch_fixture: MagicMock) -> None:
    """Exfiltration URLs in action payload are redacted before context."""
    from slack_desk_runtime import interactions

    orch = orch_fixture

    # base64-like blob (40+ chars) triggers _EXFIL_PATTERNS
    blob = "A" * 50
    evil_url = f"https://evil.com/exfil?data={blob}"
    payload, action, channel, msg_ts = _base_payload(
        value=f"action::{evil_url}", action_id="btn_evil"
    )

    with patch.object(interactions, "handle_message", new_callable=AsyncMock) as mock_hm:
        await interactions._handle_options(payload, action, channel, msg_ts)
        await asyncio.sleep(0)
        for t in list(orch._handler_tasks):
            await t

        ctx = mock_hm.call_args.kwargs["action_context"]
        # Full URL should be replaced with [REDACTED: ...]
        assert blob not in ctx
        assert "REDACTED" in ctx


@pytest.mark.asyncio
async def test_sel_audit_logged_for_action(orch_fixture: MagicMock) -> None:
    """SEL audit event is logged for action button interactions."""
    from slack_desk_runtime import interactions

    orch = orch_fixture

    payload, action, channel, msg_ts = _base_payload(
        value='action::{"k":"v"}', action_id="btn_audit"
    )

    with (
        patch.object(interactions, "handle_message", new_callable=AsyncMock),
        patch.object(interactions, "sel") as mock_sel,
    ):
        await interactions._handle_options(payload, action, channel, msg_ts)
        await asyncio.sleep(0)
        for t in list(orch._handler_tasks):
            await t

        mock_sel().log_api_access.assert_called_once()
        call_kwargs = mock_sel().log_api_access.call_args.kwargs
        assert call_kwargs["caller"] == "U123"
        assert call_kwargs["source"] == "slack"
        assert call_kwargs["outcome"] == "allowed"


# ---------------------------------------------------------------------------
# Phase 6 — stop_kill_now action and _handle_stop_confirm via stop_turn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slack_desk_kill_now_action_force_stops(orch_fixture: MagicMock) -> None:
    """stop_kill_now action calls sessions.stop_turn with force=True."""
    from slack_desk_runtime import interactions
    from slack_desk_runtime.handler import set_allowed_users, set_owner_id

    set_owner_id("U123")
    set_allowed_users({"U123"})

    orch = orch_fixture
    orch.sessions.stop_turn = AsyncMock(return_value="hard")

    payload = {
        "type": "block_actions",
        "user": {"id": "U123"},
        "team": {"id": "T1"},
        "channel": {"id": "C1"},
        "response_url": "https://hooks.slack.com/actions/T1/fake",
        "message": {"ts": "200.0", "thread_ts": "100.0", "blocks": []},
        "actions": [
            {
                "action_id": "stop_kill_now",
                "value": "session_key_123",
                "text": {"type": "plain_text", "text": "Kill Now"},
            }
        ],
    }

    with patch.object(interactions, "sel") as mock_sel:
        mock_sel.return_value = MagicMock()
        await interactions.dispatch(payload)

    orch.sessions.stop_turn.assert_called_once()
    call_kwargs = orch.sessions.stop_turn.call_args
    assert call_kwargs.args[0] == "session_key_123"
    assert call_kwargs.kwargs["force"] is True


@pytest.mark.asyncio
async def test_slack_desk_kill_now_posts_to_thread_not_session_key(
    orch_fixture: MagicMock,
) -> None:
    """stop_kill_now posts to the ephemeral's thread_ts, not to session_key.

    Regression test: for linked dashboard sessions, session_key is not a
    valid Slack thread (e.g. ``dashboard:chat-xxx``) and would fail to post.
    """
    from slack_desk_runtime import interactions
    from slack_desk_runtime.handler import set_allowed_users, set_owner_id

    set_owner_id("U123")
    set_allowed_users({"U123"})

    orch = orch_fixture
    # stop_turn must invoke on_hard for the post_message branch to run

    async def _fake_stop_turn(key, *, force=False, on_soft=None, on_hard=None):
        if on_hard:
            await on_hard()
        return "hard"

    orch.sessions.stop_turn = AsyncMock(side_effect=_fake_stop_turn)
    orch.slack_desk = MagicMock()
    orch.slack_desk.post_message = AsyncMock(return_value="300.0")

    payload = {
        "type": "block_actions",
        "user": {"id": "U123"},
        "team": {"id": "T1"},
        "channel": {"id": "C1"},
        "response_url": "https://hooks.slack.com/actions/T1/fake",
        "message": {"ts": "200.0", "thread_ts": "100.0", "blocks": []},
        "actions": [
            {
                "action_id": "stop_kill_now",
                "value": "dashboard:chat-xyz",  # linked-session key ≠ thread_ts
                "text": {"type": "plain_text", "text": "Kill Now"},
            }
        ],
    }

    with patch.object(interactions, "sel") as mock_sel:
        mock_sel.return_value = MagicMock()
        await interactions.dispatch(payload)

    orch.slack_desk.post_message.assert_called_once()
    args = orch.slack_desk.post_message.call_args.args
    # (channel, text, thread_ts) — thread_ts must be 100.0, not dashboard:chat-xyz
    assert args[2] == "100.0"
    assert args[2] != "dashboard:chat-xyz"


@pytest.mark.asyncio
async def test_slack_desk_kill_now_rejects_unauthorized(orch_fixture: MagicMock) -> None:
    """stop_kill_now enforces is_allowed_user() — deny-by-default."""
    from slack_desk_runtime import interactions

    orch = orch_fixture
    orch.sessions.stop_turn = AsyncMock(return_value="hard")

    payload = {
        "type": "block_actions",
        "user": {"id": "U_RANDOM"},  # Not allowlisted
        "team": {"id": "T1"},
        "channel": {"id": "C1"},
        "response_url": "https://hooks.slack.com/actions/T1/fake",
        "message": {"ts": "200.0", "thread_ts": "100.0", "blocks": []},
        "actions": [
            {
                "action_id": "stop_kill_now",
                "value": "session_key_123",
                "text": {"type": "plain_text", "text": "Kill Now"},
            }
        ],
    }

    with patch.object(interactions, "sel") as mock_sel:
        mock_sel.return_value = MagicMock()
        await interactions.dispatch(payload)

    # Unauthorized user — stop_turn must NOT be called
    orch.sessions.stop_turn.assert_not_called()


@pytest.mark.asyncio
async def test_handle_stop_kill_now_defense_in_depth(orch_fixture: MagicMock) -> None:
    """_handle_stop_kill_now re-checks is_allowed_user() even if dispatch() is bypassed.

    Defense-in-depth: if the dispatch() gate ever regresses (as happened
    with _handle_interactive), the handler must still deny unauthorized
    callers. Direct handler invocation simulates that bypass.
    """
    from slack_desk_runtime import interactions
    from slack_desk_runtime.handler import set_allowed_users, set_owner_id

    set_owner_id("U123")
    set_allowed_users({"U123"})

    orch = orch_fixture
    orch.sessions.stop_turn = AsyncMock(return_value="hard")

    payload = {"response_url": "", "message": {"ts": "200.0", "thread_ts": "100.0"}}
    action = {"action_id": "stop_kill_now", "value": "session_key_123"}

    with patch.object(interactions, "sel") as mock_sel:
        mock_sel.return_value = MagicMock()
        await interactions._handle_stop_kill_now(
            payload, action, channel="C1", msg_ts="200.0", user_id="U_RANDOM"
        )

    orch.sessions.stop_turn.assert_not_called()


@pytest.mark.asyncio
async def test_handle_stop_confirm_uses_stop_turn(orch_fixture: MagicMock) -> None:
    """/gideon stop confirm button routes through stop_turn, not bare reset."""
    from slack_desk_runtime import interactions
    from slack_desk_runtime.handler import set_allowed_users, set_owner_id

    set_owner_id("U123")
    set_allowed_users({"U123"})

    orch = orch_fixture
    orch.sessions.has_session = MagicMock(return_value=True)
    orch.sessions.stop_turn = AsyncMock(return_value="soft")
    orch._session_tasks = {}

    payload = {
        "type": "block_actions",
        "user": {"id": "U123"},
        "team": {"id": "T1"},
        "channel": {"id": "C1"},
        "response_url": "https://hooks.slack.com/actions/T1/fake",
        "message": {"ts": "200.0", "thread_ts": "100.0", "blocks": []},
        "actions": [
            {
                "action_id": "pc_stop_confirm",
                "value": "",
                "text": {"type": "plain_text", "text": "Confirm"},
            }
        ],
    }

    with patch.object(interactions, "sel") as mock_sel:
        mock_sel.return_value = MagicMock()
        await interactions.dispatch(payload)

    orch.sessions.stop_turn.assert_called_once()
    # Verify reset was NOT called directly
    orch.sessions.reset.assert_not_called()


@pytest.mark.asyncio
async def test_handle_stop_confirm_rejects_unauthorized(orch_fixture: MagicMock) -> None:
    """_handle_stop_confirm enforces is_allowed_user() — deny-by-default.

    stop_turn() can escalate to a hard kill, so the handler must re-check
    authorization even though dispatch() also enforces it.
    """
    from slack_desk_runtime import interactions
    from slack_desk_runtime.handler import set_allowed_users, set_owner_id

    set_owner_id("U123")
    set_allowed_users({"U123"})  # U_RANDOM not allowlisted

    orch = orch_fixture
    orch.sessions.has_session = MagicMock(return_value=True)
    orch.sessions.stop_turn = AsyncMock(return_value="soft")
    orch._session_tasks = {}

    payload = {
        "type": "block_actions",
        "user": {"id": "U_RANDOM"},
        "team": {"id": "T1"},
        "channel": {"id": "C1"},
        "response_url": "https://hooks.slack.com/actions/T1/fake",
        "message": {"ts": "200.0", "thread_ts": "100.0", "blocks": []},
        "actions": [
            {
                "action_id": "pc_stop_confirm",
                "value": "",
                "text": {"type": "plain_text", "text": "Confirm"},
            }
        ],
    }

    with patch.object(interactions, "sel") as mock_sel:
        mock_sel.return_value = MagicMock()
        await interactions.dispatch(payload)

    orch.sessions.stop_turn.assert_not_called()
