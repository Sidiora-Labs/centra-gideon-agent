"""SlackDeskDelivery — the app-side ChannelDelivery the gateway delivers through.

All Slack rendering (mrkdwn conversion, Block Kit ack buttons, message splitting,
timing footers, the interactive approval prompt + owner-response wait) lives HERE,
so core delivers with plain text + structured intent and never imports Slack code.
The Slack transport registers an instance onto the orchestrator at start_inbound.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from gideon.sdk.channel import redact_credentials, redact_exfiltration_urls

from slack_desk_runtime.client import RealSlackDeskClient
from slack_desk_runtime.format import (
    SLACK_BLOCK_SECTION_LIMIT,
    build_cron_ack_block,
    split_message,
    to_slack_desk_mrkdwn,
)

logger = logging.getLogger(__name__)

# deliver_cron_result puts parts[0] into a Block Kit `section.text`, so every part must
# respect the 3000-char section bound — not the 3900 plain-message bound. This was 3800,
# which let a cron result between 3001 and 3800 chars build a section Slack rejects
# (invalid_blocks); the overflow parts go out as plain messages, where 3000 is also fine.
_CRON_MSG_LIMIT = SLACK_BLOCK_SECTION_LIMIT


class SlackDeskDelivery:
    """Renders + delivers gateway results to Slack. Implements ChannelDelivery."""

    def __init__(self, client: RealSlackDeskClient, owner_id: str) -> None:
        self._client = client
        self._owner_id = owner_id

    # ── raw client passthrough (used by the approval flow + session routing) ──
    @property
    def client(self) -> RealSlackDeskClient:
        return self._client

    async def open_dm(self, user_id: str, max_attempts: int = 3) -> str:
        """Resolve a DM channel, retrying transient Slack API errors."""
        from slack_sdk.errors import SlackApiError

        for attempt in range(1, max_attempts + 1):
            try:
                return await self._client.open_dm(user_id) or ""
            except (SlackApiError, ConnectionError, TimeoutError) as exc:
                retryable = (
                    not isinstance(exc, SlackApiError)
                    or exc.response.status_code == 429
                    or exc.response.status_code >= 500
                )
                if not retryable or attempt >= max_attempts:
                    raise
                logger.warning(
                    "open_dm attempt %d/%d failed, retrying in %ds", attempt, max_attempts, attempt,
                    exc_info=True,
                )
                await asyncio.sleep(attempt)
        return ""

    async def deliver_text(
        self, channel: str, text: str, thread_ts: str = "", *,
        unfurl_links: bool | None = None, unfurl_media: bool | None = None,
        reply_broadcast: bool | None = None,
    ) -> str:
        body = to_slack_desk_mrkdwn(text)
        body, _ = redact_exfiltration_urls(body)
        body, _ = redact_credentials(body)
        parts = split_message(body)
        last = ""
        for i, part in enumerate(parts):
            # Link/broadcast hints apply to the first message only; continuation
            # parts thread under it plainly.
            if i == 0:
                last = await self._client.post_message(
                    channel, part, thread_ts or None,
                    unfurl_links=unfurl_links, unfurl_media=unfurl_media,
                    reply_broadcast=reply_broadcast,
                ) or last
            else:
                last = await self._client.post_message(channel, part, thread_ts or None) or last
        return last

    async def deliver_rich(
        self, channel: str, payload: Any, fallback_text: str, *,
        thread_ts: str = "", unfurl_links: bool = True, unfurl_media: bool = True,
        reply_broadcast: bool = False,
    ) -> str:
        # payload is Slack Block Kit (already sanitized by the caller); post as blocks.
        return await self._client.post_blocks(
            channel, payload, fallback_text,
            thread_ts=thread_ts or None,
            unfurl_links=unfurl_links, unfurl_media=unfurl_media,
            reply_broadcast=reply_broadcast,
        ) or ""

    async def deliver_cron_result(
        self, channel: str, job_name: str, job_id: str, text: str, thread_ts: str = ""
    ) -> str:
        redacted, _ = redact_exfiltration_urls(text)
        redacted, _ = redact_credentials(redacted)
        post_text = f"⏰ *Cron: {job_name}*\n\n{to_slack_desk_mrkdwn(redacted)}"
        parts = split_message(post_text, limit=_CRON_MSG_LIMIT)
        blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": parts[0]}},
        ] + build_cron_ack_block(job_id)
        parent_ts = await self._client.post_blocks(channel, blocks, parts[0], thread_ts or None)
        thread_root = thread_ts or parent_ts
        for part in parts[1:]:
            await self._client.post_message(channel, part, thread_root)
        return parent_ts or ""

    async def deliver_notification(
        self, channel: str, title: str, text: str, thread_ts: str = ""
    ) -> str:
        post = f"💓 *{title}*\n\n{to_slack_desk_mrkdwn(text)}"
        return await self._client.post_message(channel, post, thread_ts or None) or ""

    async def deliver_chat_mirror(
        self, channel: str, text: str, thread_ts: str = ""
    ) -> None:
        from slack_desk_runtime.format import build_options_blocks
        from gideon.sdk.channel import extract_options

        body = to_slack_desk_mrkdwn(text)
        body, _ = redact_exfiltration_urls(body)
        body, _ = redact_credentials(body)
        body, options = extract_options(body)
        for part in split_message(body):
            await self._client.post_message(channel, part, thread_ts or None)
        if options:
            await self._client.post_blocks(
                channel, build_options_blocks(options), "Options", thread_ts or None
            )

    async def deliver_subagent_reply(
        self, channel: str, text: str, thread_ts: str = "", elapsed_secs: float = 0.0
    ) -> None:
        reply_text, _ = redact_exfiltration_urls(to_slack_desk_mrkdwn(text))
        reply_text, _ = redact_credentials(reply_text)
        for part in split_message(reply_text):
            await self._client.post_message(channel, part, thread_ts or None)
        try:
            from slack_desk_runtime.handler import build_timing_footer

            footer_blocks, footer_text = build_timing_footer(elapsed_secs, self._client)
            await self._client.post_blocks(channel, footer_blocks, footer_text, thread_ts or None)
        except Exception:
            logger.debug("Failed to post subagent timing footer", exc_info=True)

    # ── Identity resolution ──
    async def resolve_user_name(self, user_id: str) -> str:
        try:
            info = await self._client.get_user_info(user_id) or {}
            return info.get("real_name") or info.get("name") or user_id
        except Exception:
            logger.debug("resolve_user_name failed for %s", user_id, exc_info=True)
            return user_id

    async def resolve_user_profile(self, user_id: str) -> dict:
        try:
            return await self._client.get_user_profile(user_id) or {}
        except Exception:
            logger.debug("resolve_user_profile failed for %s", user_id, exc_info=True)
            return {}

    async def channel_info(self, channel_id: str) -> dict:
        try:
            resp = await self._client._web.conversations_info(channel=channel_id)
            ch = resp.get("channel", {}) if isinstance(resp, dict) else {}
            return {"name": ch.get("name", ""), "is_im": bool(ch.get("is_im"))}
        except Exception:
            logger.debug("channel_info failed for %s", channel_id, exc_info=True)
            return {}

    def list_reply_channels(self) -> list[dict]:
        """Channels the bot can reply in — DM + tracked + active per-channel configs,
        from the app's OWN SlackDeskSettings (core holds no Slack config)."""
        from slack_desk_runtime.settings import get_settings

        s = get_settings()
        channels: list[dict] = [{"id": "dm", "name": "Direct Message"}]
        seen: set[str] = set()
        for tc in s.tracking_channels:
            cid = tc.get("channel_id", "")
            if cid and cid not in seen:
                channels.append({"id": cid, "name": tc.get("name", cid)})
                seen.add(cid)
        for cid, cc in s.channels.items():
            if cid not in seen and cc.activation in ("always", "mention", "observe"):
                channels.append({"id": cid, "name": cid})
                seen.add(cid)
        return channels

    def is_tracked_channel(self, channel_id: str) -> bool:
        from slack_desk_runtime.settings import get_settings

        return channel_id in {
            c.get("channel_id") for c in get_settings().tracking_channels if c.get("channel_id")
        }

    def build_thread_link(self, channel: str, ts: str) -> str:
        """Slack deep link to a message (jump-to-source for notifications).

        The slack.com URL format is a Slack vendor concern, so it lives here —
        core asks the ChannelDelivery seam for the link and stays provider-blind.
        """
        if not channel:
            return ""
        if ts:
            return f"https://slack.com/app_redirect?channel={channel}&message_ts={ts}"
        return f"https://slack.com/app_redirect?channel={channel}"

    # ── Attachment + streaming primitives ──
    async def upload_attachment(
        self, channel: str, file_path: str, *, filename: str = "", thread_ts: str = "",
        title: str = "", initial_comment: str = "",
    ) -> str:
        # RealSlackDeskClient.upload_file(channel, thread_ts, file, filename, title) → None.
        await self._client.upload_file(channel, thread_ts, file_path, filename or "", title or filename or "")
        return ""

    async def start_stream(self, channel: str, thread_ts: str = "", initial_text: str = "") -> str:
        return await self._client.start_stream(channel, thread_ts, initial_text=initial_text) or ""

    async def append_stream_task(
        self, channel: str, stream_ts: str, task_id: str, title: str, status: str,
    ) -> None:
        await self._client.append_task(channel, stream_ts, task_id, title, status)

    async def stop_stream(self, channel: str, stream_ts: str) -> None:
        await self._client.stop_stream(channel, stream_ts)

    async def request_approval(
        self, event: Any, *, source: str, parent_session_key: str = "",
        sessions: Any = None, on_prompted: Any = None,
    ) -> bool | None:
        """Post the Slack approval prompt and wait for the owner's response.

        Returns approved/rejected, or None when Slack can't prompt (caller falls
        back to the dashboard). ``on_prompted(channel, ts, pending)`` lets the
        caller race a dashboard prompt against the Slack one."""
        import re

        from slack_desk_runtime.handler import (
            _approval_brief_line,
            _build_approval_blocks,
            _pending_approvals,
            _PendingApproval,
        )

        if not self._owner_id:
            return None
        request_id = str(event.request_id)
        thread_ts: str | None = None
        channel: str | None = None
        if parent_session_key and sessions:
            channel = sessions.get_channel(parent_session_key)
            thread_ts = sessions.get_thread(parent_session_key)
            if not thread_ts and channel and re.fullmatch(r"\d+\.\d+", parent_session_key):
                thread_ts = parent_session_key
        is_dm = not channel
        if not channel:
            channel = await self._client.open_dm(self._owner_id)
            thread_ts = None
        if not channel:
            return None

        blocks = _build_approval_blocks(event, is_dm=is_dm, source=source)
        title_safe, _ = redact_exfiltration_urls(event.title)
        title_safe, _ = redact_credentials(title_safe)
        fallback = f"🔐 [{source}] Approve: {title_safe}?"
        # The notification preview is the FIRST thing the owner reads, and often the only
        # thing (a lock screen shows no blocks). Same composed line as the block, so the
        # push and the prompt can never say different things about the blast radius.
        brief_line = _approval_brief_line(event)
        if brief_line:
            fallback += f" — {brief_line}"
        approval_ts = await self._client.post_blocks(channel, blocks, fallback, thread_ts)

        pending = _PendingApproval(
            provider=None, request_id=request_id, session_key=parent_session_key,  # type: ignore[arg-type]
        )
        key = f"{channel}:{approval_ts}"
        _pending_approvals[key] = pending
        if on_prompted:
            on_prompted(pending)

        try:
            outcome = await asyncio.wait_for(pending.future, timeout=7200)
        except asyncio.TimeoutError:
            outcome = "rejected"
        finally:
            _pending_approvals.pop(key, None)

        status = "✅ Approved" if outcome == "approved" else "🚫 Rejected"
        try:
            await self._client.update_message(
                channel, approval_ts, text=f"🔐 *{title_safe}* — {status}"
            )
        except Exception:
            pass
        return outcome == "approved"
