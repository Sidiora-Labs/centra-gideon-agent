"""Slack handler delegators over core session_restrictions — thread temporary
marking, the !temporary command, registry delegation (moved from core
tests/test_temporary_chat.py + tests/test_session_restrictions.py)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

import gideon.session_restrictions as sr


# ---------------------------------------------------------------------------
# Session temporary mode — state lives in core session_restrictions; the Slack
# handler's is_thread_temporary/_mark_temporary are thin delegators over it.
# ---------------------------------------------------------------------------

class TestSlackDeskThreadTemporary:
    def setup_method(self):
        from gideon import session_restrictions as sr

        sr._temporary.clear()

    def test_is_thread_temporary_false_by_default(self):
        from slack_desk_runtime.handler import is_thread_temporary

        assert is_thread_temporary("unknown-key") is False

    def test_mark_temporary(self):
        from slack_desk_runtime.handler import _mark_temporary, is_thread_temporary

        _mark_temporary("slack-key-1")
        assert is_thread_temporary("slack-key-1") is True

    def test_bounded_eviction(self):
        """Oldest entry is evicted when max size exceeded."""
        from gideon import session_restrictions as sr
        from slack_desk_runtime.handler import _mark_temporary, is_thread_temporary

        original_max = sr._MAX
        sr._MAX = 3
        try:
            _mark_temporary("a")
            _mark_temporary("b")
            _mark_temporary("c")
            _mark_temporary("d")
            assert is_thread_temporary("a") is False
            assert is_thread_temporary("d") is True
        finally:
            sr._MAX = original_max
            sr._temporary.clear()


# ---------------------------------------------------------------------------
# Slack: !temporary command handler
# ---------------------------------------------------------------------------

class TestTemporaryCommand:
    def setup_method(self):
        from gideon import session_restrictions as sr

        sr._temporary.clear()
        sr._incognito.clear()

    @pytest.mark.asyncio
    async def test_temporary_modifier_marks_thread(self):
        from slack_desk_runtime.handler import _apply_temporary_modifier, is_thread_temporary

        slack_desk = AsyncMock()
        sessions = MagicMock()

        await _apply_temporary_modifier("sk1", "U1", "C123", slack_desk, sessions, "ts1")

        assert is_thread_temporary("sk1") is True
        slack_desk.post_message.assert_called_once()
        assert "Temporary mode ON" in slack_desk.post_message.call_args[0][1]
        sessions.set_channel_link.assert_called_once_with("sk1", "sk1", "C123")

    @pytest.mark.asyncio
    async def test_temporary_modifier_idempotent(self):
        from slack_desk_runtime.handler import _apply_temporary_modifier, _mark_temporary

        _mark_temporary("sk2")

        slack_desk = AsyncMock()
        sessions = MagicMock()

        await _apply_temporary_modifier("sk2", "U1", "C123", slack_desk, sessions, "ts2")

        # Idempotent — no message posted on second call
        slack_desk.post_message.assert_not_called()


def test_slack_desk_delegators_read_core_registry():
    """slack.handler public checks delegate to the core registry."""
    from slack_desk_runtime.handler import is_thread_incognito, is_thread_temporary

    sr._temporary.clear()
    sr._incognito.clear()
    sr.mark_temporary("slack:C1:1.1")
    sr.mark_incognito("slack:C2:2.2")
    assert is_thread_temporary("slack:C1:1.1") is True
    assert is_thread_incognito("slack:C2:2.2") is True
