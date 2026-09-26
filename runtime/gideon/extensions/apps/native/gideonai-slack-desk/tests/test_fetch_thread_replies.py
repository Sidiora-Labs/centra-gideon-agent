"""Tests for SlackDeskClientOps.fetch_thread_replies and RealSlackDeskClient pagination."""

from unittest.mock import AsyncMock, patch

import pytest


class TestSlackDeskClientOpsBase:
    """Base class fetch_thread_replies returns empty list."""

    @pytest.mark.asyncio
    async def test_base_returns_empty(self):
        """Verify the default implementation returns [] (tested via MockSlackDeskClient
        which inherits from SlackDeskClientOps without overriding fetch_thread_replies)."""
        from slack_desk_helpers import MockSlackDeskClient

        client = MockSlackDeskClient()
        result = await client.fetch_thread_replies("C1", "100.0")
        assert result == []


class TestRealSlackDeskClientFetchThreadReplies:
    """RealSlackDeskClient.fetch_thread_replies with mocked web client."""

    @pytest.mark.asyncio
    async def test_returns_messages(self):
        from slack_desk_runtime.client import RealSlackDeskClient

        web = AsyncMock()
        web.conversations_replies = AsyncMock(
            return_value={
                "messages": [{"user": "U1", "text": "hi"}],
                "response_metadata": {},
            }
        )
        client = RealSlackDeskClient.__new__(RealSlackDeskClient)
        client._web = web

        result = await client.fetch_thread_replies("C1", "100.0")
        assert len(result) == 1
        assert result[0]["text"] == "hi"
        web.conversations_replies.assert_called_once_with(
            channel="C1", ts="100.0", limit=200,
        )

    @pytest.mark.asyncio
    async def test_logs_warning_on_pagination(self):
        from slack_desk_runtime.client import RealSlackDeskClient

        web = AsyncMock()
        web.conversations_replies = AsyncMock(
            return_value={
                "messages": [{"user": "U1", "text": "hi"}],
                "response_metadata": {"next_cursor": "abc123"},
            }
        )
        client = RealSlackDeskClient.__new__(RealSlackDeskClient)
        client._web = web

        with patch("slack_desk_runtime.client.logger") as mock_logger:
            result = await client.fetch_thread_replies("C1", "100.0")
            assert len(result) == 1
            mock_logger.warning.assert_called_once()
            assert "incomplete" in mock_logger.warning.call_args[0][0]

    @pytest.mark.asyncio
    async def test_returns_empty_on_error(self):
        import aiohttp

        from slack_desk_runtime.client import RealSlackDeskClient

        web = AsyncMock()
        web.conversations_replies = AsyncMock(
            side_effect=aiohttp.ClientError("timeout")
        )
        client = RealSlackDeskClient.__new__(RealSlackDeskClient)
        client._web = web

        result = await client.fetch_thread_replies("C1", "100.0")
        assert result == []

    @pytest.mark.asyncio
    async def test_no_pagination_warning_without_cursor(self):
        from slack_desk_runtime.client import RealSlackDeskClient

        web = AsyncMock()
        web.conversations_replies = AsyncMock(
            return_value={
                "messages": [{"user": "U1", "text": "hi"}],
                "response_metadata": {},
            }
        )
        client = RealSlackDeskClient.__new__(RealSlackDeskClient)
        client._web = web

        with patch("slack_desk_runtime.client.logger") as mock_logger:
            await client.fetch_thread_replies("C1", "100.0")
            mock_logger.warning.assert_not_called()
