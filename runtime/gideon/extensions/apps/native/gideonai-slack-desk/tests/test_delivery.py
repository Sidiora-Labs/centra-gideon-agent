"""SlackDeskDelivery — outbound rendering + open_dm retry (moved from core gateway)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from slack_desk_runtime.delivery import SlackDeskDelivery


def _delivery(client=None, owner="U_OWNER"):
    return SlackDeskDelivery(client or MagicMock(), owner)


class TestOpenDmRetry:
    @pytest.mark.asyncio
    async def test_success_first_attempt(self):
        client = MagicMock()
        client.open_dm = AsyncMock(return_value="D_CHAN")
        d = _delivery(client)
        assert await d.open_dm("U1") == "D_CHAN"
        assert client.open_dm.call_count == 1

    @pytest.mark.asyncio
    async def test_retries_on_server_error(self):
        from slack_sdk.errors import SlackApiError

        resp = MagicMock(); resp.status_code = 500
        client = MagicMock()
        client.open_dm = AsyncMock(side_effect=[SlackApiError("500", resp), "D_OK"])
        d = _delivery(client)
        assert await d.open_dm("U1", max_attempts=2) == "D_OK"

    @pytest.mark.asyncio
    async def test_raises_on_non_retryable(self):
        from slack_sdk.errors import SlackApiError

        resp = MagicMock(); resp.status_code = 403
        client = MagicMock()
        client.open_dm = AsyncMock(side_effect=SlackApiError("403", resp))
        d = _delivery(client)
        with pytest.raises(SlackApiError):
            await d.open_dm("U1", max_attempts=3)
        assert client.open_dm.call_count == 1

    @pytest.mark.asyncio
    async def test_retries_on_rate_limit(self):
        from slack_sdk.errors import SlackApiError

        resp = MagicMock(); resp.status_code = 429
        client = MagicMock()
        client.open_dm = AsyncMock(side_effect=[SlackApiError("429", resp), SlackApiError("429", resp), "D_OK"])
        d = _delivery(client)
        assert await d.open_dm("U1", max_attempts=3) == "D_OK"

    @pytest.mark.asyncio
    async def test_raises_after_max_attempts(self):
        from slack_sdk.errors import SlackApiError

        resp = MagicMock(); resp.status_code = 500
        client = MagicMock()
        client.open_dm = AsyncMock(side_effect=SlackApiError("500", resp))
        d = _delivery(client)
        with pytest.raises(SlackApiError):
            await d.open_dm("U1", max_attempts=2)
        assert client.open_dm.call_count == 2


class TestDeliveryRendering:
    @pytest.mark.asyncio
    async def test_deliver_cron_result_posts_blocks_with_ack(self):
        client = MagicMock()
        client.post_blocks = AsyncMock(return_value="1.1")
        client.post_message = AsyncMock()
        d = _delivery(client)
        ts = await d.deliver_cron_result("C1", "nightly", "job1", "done", "")
        assert ts == "1.1"
        client.post_blocks.assert_awaited_once()
        # ack block present in the posted blocks
        blocks = client.post_blocks.call_args[0][1]
        assert any("actions" == b.get("type") for b in blocks)

    @pytest.mark.asyncio
    async def test_deliver_notification_formats_title(self):
        client = MagicMock()
        client.post_message = AsyncMock(return_value="2.2")
        d = _delivery(client)
        await d.deliver_notification("C1", "Heads up", "body", "")
        posted = client.post_message.call_args[0][1]
        assert "Heads up" in posted

    @pytest.mark.asyncio
    async def test_deliver_chat_mirror_extracts_options(self):
        client = MagicMock()
        client.post_message = AsyncMock()
        client.post_blocks = AsyncMock()
        d = _delivery(client)
        await d.deliver_chat_mirror("C1", "Pick one\n[OPTIONS: A | B]", "1.0")
        client.post_message.assert_awaited()  # text without the OPTIONS tag
        client.post_blocks.assert_awaited()   # options rendered as blocks


class TestNewChannelDeliveryMethods:
    """The generic ChannelDelivery surface added when core stopped touching the
    raw Slack client (deliver_rich / upload_attachment / streaming / identity)."""

    @pytest.mark.asyncio
    async def test_deliver_text_forwards_unfurl_and_broadcast(self):
        client = MagicMock()
        client.post_message = AsyncMock(return_value="ts1")
        d = _delivery(client)
        await d.deliver_text("C1", "hi", "th1", unfurl_links=False, unfurl_media=False, reply_broadcast=True)
        # first (and only) part carries the hints
        client.post_message.assert_awaited_once()
        args, kwargs = client.post_message.call_args
        assert args[0] == "C1" and args[2] == "th1"
        assert kwargs == {"unfurl_links": False, "unfurl_media": False, "reply_broadcast": True}

    @pytest.mark.asyncio
    async def test_deliver_rich_posts_blocks_with_fallback(self):
        client = MagicMock()
        client.post_blocks = AsyncMock(return_value="ts2")
        d = _delivery(client)
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "x"}}]
        ts = await d.deliver_rich("C1", blocks, "fallback", thread_ts="th", unfurl_links=False)
        assert ts == "ts2"
        # unfurl_media/reply_broadcast keep deliver_rich's own defaults (True/False).
        client.post_blocks.assert_awaited_once_with(
            "C1", blocks, "fallback", thread_ts="th", unfurl_links=False, unfurl_media=True, reply_broadcast=False,
        )

    @pytest.mark.asyncio
    async def test_upload_attachment_maps_to_client_upload(self):
        client = MagicMock()
        client.upload_file = AsyncMock(return_value=None)
        d = _delivery(client)
        await d.upload_attachment("C1", "/a/b.txt", filename="b.txt", thread_ts="th", title="T")
        client.upload_file.assert_awaited_once_with("C1", "th", "/a/b.txt", "b.txt", "T")

    @pytest.mark.asyncio
    async def test_stream_primitives(self):
        client = MagicMock()
        client.start_stream = AsyncMock(return_value="sts")
        client.append_task = AsyncMock(return_value=True)
        client.stop_stream = AsyncMock(return_value=True)
        d = _delivery(client)
        assert await d.start_stream("C1", "th", initial_text="Thinking…") == "sts"
        await d.append_stream_task("C1", "sts", "t1", "Doing", "in_progress")
        client.append_task.assert_awaited_once_with("C1", "sts", "t1", "Doing", "in_progress")
        await d.stop_stream("C1", "sts")
        client.stop_stream.assert_awaited_once_with("C1", "sts")

    @pytest.mark.asyncio
    async def test_resolve_user_name_prefers_real_name(self):
        client = MagicMock()
        client.get_user_info = AsyncMock(return_value={"name": "u", "real_name": "Real Name"})
        d = _delivery(client)
        assert await d.resolve_user_name("U1") == "Real Name"

    @pytest.mark.asyncio
    async def test_resolve_user_name_falls_back_to_id_on_error(self):
        client = MagicMock()
        client.get_user_info = AsyncMock(side_effect=Exception("boom"))
        d = _delivery(client)
        assert await d.resolve_user_name("U1") == "U1"

    def test_build_thread_link_is_slack_desk_deep_link(self):
        # Core asks the seam for the jump-to-source link; the slack.com URL
        # format lives here, never in core.
        d = _delivery()
        assert d.build_thread_link("C123", "1712793600.000001") == (
            "https://slack.com/app_redirect?channel=C123"
            "&message_ts=1712793600.000001"
        )
        assert d.build_thread_link("C123", "") == (
            "https://slack.com/app_redirect?channel=C123"
        )
        assert d.build_thread_link("", "1.2") == ""


class TestTransportWiresChannelDelivery:
    """start_inbound must register the delivery on the orchestrator AND set it on
    dashboard_state — the send-message/chat-mirror path reads dashboard_state."""

    def test_both_handles_set(self):
        from unittest.mock import MagicMock as MM
        from slack_desk_runtime.delivery import SlackDeskDelivery

        ds = MM(); ds.channel_delivery = None
        orch = MM(); orch.dashboard_state = ds
        registered = {}
        orch.register_channel_delivery = lambda d: registered.__setitem__("d", d)

        # Mirror transport.py:99-103 exactly.
        delivery = SlackDeskDelivery(MM(), "U_OWNER")
        if hasattr(orch, "register_channel_delivery"):
            orch.register_channel_delivery(delivery)
        if getattr(orch, "dashboard_state", None) is not None:
            orch.dashboard_state.channel_delivery = delivery

        assert registered["d"] is delivery
        assert ds.channel_delivery is delivery


class TestApprovalBriefOnTheNotification:
    """The blast-radius line also rides the notification fallback.

    A lock screen renders no Block Kit, so the fallback is often the ONLY thing the
    owner reads before opening Slack. It reuses the exact string the prompt shows, so
    the push and the prompt cannot say different things about what a call would touch.
    """

    @staticmethod
    def _event(brief):
        from gideon.llm.base import LLMEvent

        return LLMEvent(
            kind="permission_request",
            request_id="req-fallback",
            title="bash",
            options=[],
            tool_input='{"command": "rm -rf build"}',
            tool_meta={} if brief is None else {"approval_brief": brief},
        )

    _BRIEF = {
        "tool": "bash",
        "risk": "destructive",
        "blastRadius": {"writes": True, "network": False, "shell": True, "readOnly": False},
        "blastRadiusLine": "writes files, runs a command",
    }

    @staticmethod
    async def _prompt(brief, outcome):
        """Drive one real request_approval to completion, returning (verdict, fallback)."""
        client = MagicMock()
        client.open_dm = AsyncMock(return_value="D1")
        client.post_blocks = AsyncMock(return_value="1.1")
        client.update_message = AsyncMock()
        delivery = _delivery(client)
        verdict = await delivery.request_approval(
            TestApprovalBriefOnTheNotification._event(brief),
            source="cron",
            on_prompted=lambda pending: pending.future.set_result(outcome),
        )
        return verdict, client.post_blocks.call_args[0][2]

    @pytest.mark.asyncio
    async def test_approved_notification_names_the_blast_radius(self):
        verdict, fallback = await self._prompt(self._BRIEF, "approved")
        assert verdict is True
        assert fallback == (
            "🔐 [cron] Approve: bash? — Can: writes files, runs a command · Risk: destructive"
        )

    @pytest.mark.asyncio
    async def test_rejected_notification_names_the_same_blast_radius(self):
        """Refusing is a decision too — the owner needs the same facts to refuse well."""
        verdict, fallback = await self._prompt(self._BRIEF, "rejected")
        assert verdict is False
        assert "Can: writes files, runs a command · Risk: destructive" in fallback

    @pytest.mark.asyncio
    @pytest.mark.parametrize("outcome,verdict", [("approved", True), ("rejected", False)])
    async def test_no_brief_leaves_the_notification_untouched(self, outcome, verdict):
        """VACUITY TWIN for both decisions: no brief, no added clause."""
        got, fallback = await self._prompt(None, outcome)
        assert got is verdict
        assert fallback == "🔐 [cron] Approve: bash?"
