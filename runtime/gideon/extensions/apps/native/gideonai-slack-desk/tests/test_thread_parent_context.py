"""Tests for thread parent context injection.

When a user replies to a thread started by a cron (or any prior session),
the new interactive session fetches the parent message and injects it as
context so the LLM knows what started the thread.
"""

from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from slack_desk_helpers import MockSlackDeskClient

from gideon.context import ContextBuilder
from gideon.memory import MemoryStore
from gideon.llm.base import LLMEvent
from gideon.skills import SkillsLoader
from slack_desk_runtime.client import RealSlackDeskClient
from slack_desk_runtime.handler import handle_message, set_allowed_users, set_owner_id

if TYPE_CHECKING:
    from gideon.session import SessionManager

# ── Helpers ──


def _make_builder(tmp_path):
    return ContextBuilder(
        memory=MemoryStore(workspace=tmp_path / "ws"),
        skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
    )


class FakeProvider:
    def __init__(self):
        self._events = [LLMEvent(kind="text_chunk", text="ok")]
        self.last_message: str | None = None

    async def stream(self, message, timeout=120.0):
        self.last_message = message
        for e in self._events:
            yield e
        yield LLMEvent(kind="complete")

    async def approve_tool(self, rid, option_id="allow_once"):
        pass

    async def reject_tool(self, rid):
        pass

    async def start(self):
        pass

    async def shutdown(self):
        pass

    def context_usage_pct(self):
        return 0.0


class FakeSessionManager:
    def __init__(self):
        self._provider = FakeProvider()
        self._is_new = True

    async def get_or_create(self, key, agent=None, channel_id=None, approval_policy=None):
        was_new = self._is_new
        self._is_new = False
        return self._provider, was_new, False

    def check_context_usage(self, key, provider):
        return 0.0

    def record_success(self, key):
        pass

    async def record_failure(self, key):
        return False

    def release(self, key):
        pass

    async def set_channel(self, key, channel_id):
        pass

    def get_channel(self, key):
        return None

    def has_session(self, key):
        return False

    async def reset(self, key):
        pass

    def get_pid(self, key):
        return None

    def set_channel_link(self, key, thread_ts, channel_id):
        pass

    def get_session_for_thread(self, thread_ts):
        return None

    def enqueue(self, key, msg_ts, text, **kwargs):
        return False

    def is_cancelled(self, key, msg_ts):
        return False

    def dequeue(self, key):
        return None

    def clear_queue(self, key):
        pass


# ── Tests: client.py fetch_message ──


class TestFetchMessage:
    @pytest.mark.asyncio
    async def test_returns_text(self):
        client = RealSlackDeskClient.__new__(RealSlackDeskClient)
        client._web = MagicMock()
        client._web.conversations_history = AsyncMock(
            return_value={"messages": [{"text": "standup summary here"}]}
        )
        result = await client.fetch_message("C123", "1234.5678")
        assert result == "standup summary here"

    @pytest.mark.asyncio
    async def test_extracts_text_from_blocks_after_ack(self):
        """After acknowledge, text field is '✅ Acknowledged' but blocks
        still contain the original content — fetch_message should extract it."""
        client = RealSlackDeskClient.__new__(RealSlackDeskClient)
        client._web = MagicMock()
        client._web.conversations_history = AsyncMock(
            return_value={
                "messages": [
                    {
                        "text": "✅ Acknowledged",
                        "blocks": [
                            {
                                "type": "section",
                                "text": {"type": "mrkdwn", "text": "standup summary here"},
                            },
                            {
                                "type": "context",
                                "elements": [{"type": "mrkdwn", "text": "✅ Acknowledged"}],
                            },
                        ],
                    }
                ]
            }
        )
        result = await client.fetch_message("C123", "1234.5678")
        assert result == "standup summary here"

    @pytest.mark.asyncio
    async def test_extracts_rich_text_blocks(self):
        """Messages using rich_text blocks should have their text extracted,
        including inline mentions, links, emoji, and list items."""
        client = RealSlackDeskClient.__new__(RealSlackDeskClient)
        client._web = MagicMock()
        client._web.conversations_history = AsyncMock(
            return_value={
                "messages": [
                    {
                        "text": "✅ Acknowledged",
                        "blocks": [
                            {
                                "type": "rich_text",
                                "elements": [
                                    {
                                        "type": "rich_text_section",
                                        "elements": [
                                            {"type": "text", "text": "Check "},
                                            {"type": "user", "user_id": "U123"},
                                            {"type": "text", "text": "'s PR at "},
                                            {"type": "link", "url": "https://example.com"},
                                            {"type": "text", "text": " "},
                                            {"type": "emoji", "name": "rocket"},
                                            {"type": "text", "text": " in "},
                                            {"type": "channel", "channel_id": "C456"},
                                        ],
                                    },
                                    {
                                        "type": "rich_text_list",
                                        "style": "bullet",
                                        "elements": [
                                            {
                                                "type": "rich_text_section",
                                                "elements": [
                                                    {"type": "text", "text": "item one"},
                                                ],
                                            },
                                            {
                                                "type": "rich_text_section",
                                                "elements": [
                                                    {"type": "text", "text": "item two"},
                                                ],
                                            },
                                        ],
                                    },
                                ],
                            }
                        ],
                    }
                ]
            }
        )
        result = await client.fetch_message("C123", "1234.5678")
        assert result == "Check <@U123>'s PR at https://example.com :rocket: in <#C456>\nitem one\nitem two"

    def test_extract_inline_texts_filters_empty_strings(self):
        """Degenerate elements with empty text values should be filtered out."""
        result = RealSlackDeskClient._extract_inline_texts([
            {"type": "text", "text": ""},
            {"type": "text", "text": "hello"},
            {"type": "link", "url": ""},
        ])
        assert result == ["hello"]

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self):
        from slack_sdk.errors import SlackApiError

        client = RealSlackDeskClient.__new__(RealSlackDeskClient)
        client._web = MagicMock()
        resp = MagicMock()
        resp.data = {"ok": False, "error": "channel_not_found"}
        client._web.conversations_history = AsyncMock(
            side_effect=SlackApiError("api down", response=resp)
        )
        result = await client.fetch_message("C123", "1234.5678")
        assert result is None


# ── Tests: context.py build_message thread_parent_text ──


class TestThreadParentTextInjection:
    def test_parent_text_injected(self, tmp_path):
        builder = _make_builder(tmp_path)
        msg, _ = builder.build_message(
            "tell me more",
            is_new_session=True,
            channel_id="C123",
            thread_ts="1234.5678",
            thread_parent_text="Here is the standup summary",
        )
        assert "prior session" in msg
        assert "Here is the standup summary" in msg
        assert "CHANNEL THREAD CONTEXT" in msg
        assert "channel_id: C123" in msg
        assert "thread_ts: 1234.5678" in msg

    def test_no_parent_falls_back_to_mcp_hint(self, tmp_path):
        """When fetch_message returns None (API failure or empty channel),
        the context block falls back to suggesting batch_get_thread_replies
        so the LLM can still retrieve thread history manually."""
        builder = _make_builder(tmp_path)
        msg, _ = builder.build_message(
            "tell me more",
            is_new_session=True,
            channel_id="C123",
            thread_ts="1234.5678",
            thread_parent_text=None,
        )
        assert "batch_get_thread_replies" in msg

    def test_parent_text_injected_alongside_channel_history(self, tmp_path):
        """Thread parent text is injected even when channel_history exists
        — they serve different purposes (recent messages vs original post)."""
        builder = _make_builder(tmp_path)
        from gideon.channel_history import ChannelHistory

        ch = ChannelHistory()
        ch.push("C123", "alice", "some context", thread_ts="1234.5678")
        builder.channel_history = ch
        msg, _ = builder.build_message(
            "hello",
            is_new_session=False,
            channel_id="C123",
            thread_ts="1234.5678",
            thread_parent_text="cron output here",
        )
        assert "CHANNEL THREAD CONTEXT" in msg
        assert "cron output here" in msg
        assert "some context" in msg

    def test_bare_thread_metadata_when_channel_history_exists(self, tmp_path):
        """When fetch_message returns None but channel history exists,
        bare thread metadata (channel_id/thread_ts) should still inject
        so the LLM knows it's in a thread."""
        builder = _make_builder(tmp_path)
        from gideon.channel_history import ChannelHistory

        ch = ChannelHistory()
        ch.push("C123", "alice", "some context", thread_ts="1234.5678")
        builder.channel_history = ch
        msg, _ = builder.build_message(
            "hello",
            is_new_session=False,
            channel_id="C123",
            thread_ts="1234.5678",
            thread_parent_text=None,
        )
        assert "CHANNEL THREAD CONTEXT" in msg
        assert "channel_id: C123" in msg
        assert "thread_ts: 1234.5678" in msg
        assert "batch_get_thread_replies" in msg
        assert "some context" in msg


# ── Tests: handler.py fetch integration ──


class TestHandlerFetchesThreadParent:
    @pytest.mark.asyncio
    async def test_fetches_parent_on_new_session(self, tmp_path):
        set_owner_id("U001")
        set_allowed_users([{"slack_id": "U001"}])
        slack_desk = MockSlackDeskClient()
        slack_desk._fetch_message_result = "cron output here"
        sessions = cast("SessionManager", FakeSessionManager())
        builder = _make_builder(tmp_path)

        await handle_message(
            slack_desk,
            sessions,
            "C123",
            "implement step 3",
            thread_ts="9999.0001",
            msg_ts="9999.0002",
            user_id="U001",
            context_builder=builder,
        )
        assert ("fetch_message", {"channel": "C123", "ts": "9999.0001"}) in slack_desk.actions

    @pytest.mark.asyncio
    async def test_skips_fetch_when_parent_in_compressed_history(self, tmp_path):
        """When compressed history exists, fetch_message is skipped —
        the parent is already in context."""
        set_owner_id("U001")
        set_allowed_users([{"slack_id": "U001"}])
        slack_desk = MockSlackDeskClient()
        slack_desk._fetch_message_result = "cron output here"
        sessions = cast("SessionManager", FakeSessionManager())
        builder = _make_builder(tmp_path)
        from gideon.history import ConversationLog

        log = ConversationLog(base_dir=tmp_path / "conv")
        log.append("9999.0001", "user", "hello")
        log.append("9999.0001", "assistant", "hi there")
        builder.conversation_log = log

        await handle_message(
            slack_desk,
            sessions,
            "C123",
            "follow up",
            thread_ts="9999.0001",
            msg_ts="9999.0002",
            user_id="U001",
            context_builder=builder,
        )
        assert ("fetch_message", {"channel": "C123", "ts": "9999.0001"}) not in slack_desk.actions

    @pytest.mark.asyncio
    async def test_truncates_long_parent_text(self, tmp_path):
        """Parent messages over 3000 chars are truncated to prevent
        consuming too much of the LLM context window."""
        set_owner_id("U001")
        set_allowed_users([{"slack_id": "U001"}])
        slack_desk = MockSlackDeskClient()
        slack_desk._fetch_message_result = "x" * 5000
        sm = FakeSessionManager()
        sessions = cast("SessionManager", sm)
        builder = _make_builder(tmp_path)

        await handle_message(
            slack_desk,
            sessions,
            "C123",
            "hello",
            thread_ts="9999.0001",
            msg_ts="9999.0002",
            user_id="U001",
            context_builder=builder,
        )
        full_message = sm._provider.last_message
        assert full_message is not None
        assert "x" * 5000 not in full_message
        assert "x" * 3000 in full_message
        assert "truncated" in full_message
