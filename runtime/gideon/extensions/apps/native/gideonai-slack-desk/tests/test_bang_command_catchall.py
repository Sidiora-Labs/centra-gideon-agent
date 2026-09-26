"""Tests for unrecognized bang command catch-all in handler.py."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from slack_desk_runtime import handler


def _make_slack_desk():
    slack_desk = MagicMock()
    slack_desk.post_message = AsyncMock()
    slack_desk.post_blocks = AsyncMock()
    return slack_desk


@pytest.mark.asyncio
async def test_unrecognized_bang_returns_error():
    """Given message text '!foo', when _handle_slash_command is called,
    then it posts error message and returns '' (not None)."""
    slack_desk = _make_slack_desk()
    with patch.object(handler, "is_allowed_user", return_value=True):
        result = await handler._handle_slash_command(
            "!foo", slack_desk, MagicMock(), "C1", "t1", "msg1", "t1", "U1",
        )
    assert result == ""
    slack_desk.post_message.assert_called_once_with(
        "C1",
        "❌ Unknown command `!foo`. Type `/gideon help` for available commands.",
        "t1",
    )


@pytest.mark.asyncio
async def test_recognized_bang_still_works():
    """Given message text '!yolo on', when _handle_slash_command is called,
    then it processes normally (does not hit the catch-all)."""
    slack_desk = _make_slack_desk()
    with (
        patch.object(handler, "is_allowed_user", return_value=True),
        patch("gideon.trust_mode.is_yolo_active", return_value=False),
        patch("gideon.trust_mode.yolo_from_config", return_value=False),
        patch("gideon.trust_mode.enable_yolo"),
        patch.object(handler, "sel") as mock_sel,
        patch.object(handler, "deprecation_warning_block", return_value={}),
    ):
        mock_sel.return_value.log_api_access = MagicMock()
        result = await handler._handle_slash_command(
            "!yolo on", slack_desk, MagicMock(), "C1", "t1", "msg1", "t1", "U1",
        )
    assert result == ""
    # The error message should NOT have been posted
    for call in slack_desk.post_message.call_args_list:
        assert "Unknown command" not in str(call)


@pytest.mark.asyncio
async def test_bare_exclamation_returns_error():
    """Given message text '!', when _handle_slash_command is called,
    then it posts an error message and returns '' (not None)."""
    slack_desk = _make_slack_desk()
    with patch.object(handler, "is_allowed_user", return_value=True):
        result = await handler._handle_slash_command(
            "!", slack_desk, MagicMock(), "C1", "t1", "msg1", "t1", "U1",
        )
    assert result == ""
    slack_desk.post_message.assert_called_once_with(
        "C1",
        "❌ Unknown command `!`. Type `/gideon help` for available commands.",
        "t1",
    )
