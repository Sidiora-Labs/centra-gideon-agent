"""Slack API client abstraction for Gideon.

Provides an async interface for Slack Web API operations.
Uses an ABC so tests can swap in a mock without touching Slack.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any

import aiohttp
from slack_sdk.errors import SlackClientError as SlackDeskClientError
from slack_sdk.web.async_client import AsyncWebClient

logger = logging.getLogger(__name__)


class SlackDeskClientOps(ABC):
    """Core Slack operations needed by Gideon."""

    @abstractmethod
    async def post_message(
        self,
        channel: str,
        text: str,
        thread_ts: str | None = None,
        unfurl_links: bool | None = None,
        unfurl_media: bool | None = None,
    ) -> str:
        """Post a message, return its ts."""

    @abstractmethod
    async def post_blocks(
        self,
        channel: str,
        blocks: list[dict],
        text: str,
        thread_ts: str | None = None,
        unfurl_links: bool | None = None,
        unfurl_media: bool | None = None,
    ) -> str:
        """Post a Block Kit message, return its ts."""

    @abstractmethod
    async def update_message(
        self, channel: str, ts: str, text: str = "", blocks: list[dict] | None = None
    ) -> None:
        """Edit an existing message."""

    @abstractmethod
    async def delete_message(self, channel: str, ts: str) -> None:
        """Delete a message."""

    @abstractmethod
    async def add_reaction(self, channel: str, ts: str, emoji: str) -> None:
        """Add a reaction to a message."""

    @abstractmethod
    async def remove_reaction(self, channel: str, ts: str, emoji: str) -> None:
        """Remove a reaction from a message."""

    @abstractmethod
    async def upload_file(
        self,
        channel: str,
        thread_ts: str,
        file: str,
        filename: str,
        title: str,
    ) -> None:
        """Upload a file to a Slack thread."""

    @abstractmethod
    async def open_dm(self, user_id: str) -> str:
        """Open a DM channel with a user, return channel ID."""

    @abstractmethod
    async def post_ephemeral(
        self, channel: str, user_id: str, text: str, blocks: list[dict] | None = None, thread_ts: str | None = None
    ) -> None:
        """Post an ephemeral message visible only to the specified user."""

    @abstractmethod
    async def views_publish(self, user_id: str, view: dict) -> None:
        """Publish a Home Tab view for a user."""

    async def is_dm(self, channel: str) -> bool:
        """Check if a channel is a 1:1 DM via conversations.info.

        Default implementation uses channel ID prefix heuristic.
        Subclasses should override with the real API call.
        """
        return channel.startswith("D")

    async def views_open(self, trigger_id: str, view: dict) -> None:
        """Open a modal view."""

    async def views_update(self, view_id: str, view: dict) -> None:
        """Update an existing modal view."""

    # ── Streaming API (chat.startStream / appendStream / stopStream) ──

    async def start_stream(
        self,
        channel: str,
        thread_ts: str,
        initial_text: str | None = None,
        team_id: str | None = None,
        user_id: str | None = None,
    ) -> str | None:
        """Start a streaming message. Returns ts or None if unsupported."""
        return None

    async def append_stream(self, channel: str, ts: str, text: str) -> bool:
        """Append text to a streaming message. Returns True on success."""
        return False

    async def stop_stream(self, channel: str, ts: str, final_text: str | None = None) -> bool:
        """Stop a streaming message. Returns True on success."""
        return False

    async def append_task(
        self,
        channel: str,
        ts: str,
        task_id: str,
        title: str,
        status: str,
        details: str = "",
        output: str = "",
    ) -> bool:
        """Append a task_update chunk to a streaming message. Returns True on success."""
        return False

    async def set_thread_status(self, channel: str, thread_ts: str, status: str) -> None:
        """Set assistant thread status via assistant.threads.setStatus.

        Pass an empty string to clear the status indicator.
        """

    async def set_thread_title(self, channel: str, thread_ts: str, title: str) -> None:
        """Set assistant thread title via assistant.threads.setTitle."""

    async def set_suggested_prompts(
        self, channel: str, thread_ts: str, prompts: list[dict[str, str]]
    ) -> None:
        """Set suggested prompts via assistant.threads.setSuggestedPrompts.

        Each prompt is a dict with 'title' (button label) and 'message'
        (text sent when clicked).
        """

    async def fetch_message(self, channel: str, ts: str) -> str | None:
        """Fetch a single message's text by channel and timestamp.

        Returns the message text, or None on failure.
        """
        return None

    async def fetch_thread_replies(self, channel: str, thread_ts: str, limit: int = 200) -> list[dict]:
        """Fetch thread replies. Returns list of message dicts with 'user'/'bot_id' and 'text'."""
        return []

    async def fetch_history(self, channel: str, oldest: str, limit: int = 200) -> list[dict]:
        """Fetch channel messages newer than ``oldest``, newest-first.

        ``oldest`` is EXCLUSIVE (see the concrete implementation) — a message whose
        ``ts`` equals ``oldest`` is not returned. Returns message dicts carrying
        'ts'/'user'/'bot_id'/'text'/'thread_ts'. Not abstract (mirrors
        ``fetch_message``/``fetch_thread_replies``) so existing mocks keep working.
        """
        return []

    async def download_file(self, url: str, dest: str) -> None:
        """Download a Slack-hosted file to a local path."""
        raise NotImplementedError


class RealSlackDeskClient(SlackDeskClientOps):
    """Slack Web API client backed by slack_sdk."""

    def __init__(self, bot_token: str):
        self._web = AsyncWebClient(token=bot_token)

    async def auth_test(self) -> dict[str, Any]:
        """Verify the bot token (Slack ``auth.test``); returns the raw payload."""
        resp = await self._web.auth_test()
        return dict(resp.data) if hasattr(resp, "data") else dict(resp)

    async def post_message(
        self,
        channel: str,
        text: str,
        thread_ts: str | None = None,
        unfurl_links: bool | None = None,
        unfurl_media: bool | None = None,
        reply_broadcast: bool | None = None,
    ) -> str:
        kwargs: dict[str, Any] = {"channel": channel, "text": text}
        if thread_ts is not None:
            kwargs["thread_ts"] = thread_ts
        if unfurl_links is not None:
            kwargs["unfurl_links"] = unfurl_links
        if unfurl_media is not None:
            kwargs["unfurl_media"] = unfurl_media
        if reply_broadcast and thread_ts is not None:
            kwargs["reply_broadcast"] = True
        resp = await self._web.chat_postMessage(**kwargs)
        return resp["ts"]

    async def post_blocks(
        self,
        channel: str,
        blocks: list[dict],
        text: str,
        thread_ts: str | None = None,
        unfurl_links: bool | None = None,
        unfurl_media: bool | None = None,
        reply_broadcast: bool | None = None,
    ) -> str:
        kwargs: dict[str, Any] = {"channel": channel, "blocks": blocks, "text": text}
        if thread_ts is not None:
            kwargs["thread_ts"] = thread_ts
        if unfurl_links is not None:
            kwargs["unfurl_links"] = unfurl_links
        if unfurl_media is not None:
            kwargs["unfurl_media"] = unfurl_media
        if reply_broadcast and thread_ts is not None:
            kwargs["reply_broadcast"] = True
        resp = await self._web.chat_postMessage(**kwargs)
        return resp["ts"]

    async def update_message(
        self, channel: str, ts: str, text: str = "", blocks: list[dict] | None = None
    ) -> None:
        kwargs: dict[str, Any] = {"channel": channel, "ts": ts, "text": text}
        if blocks:
            kwargs["blocks"] = blocks
        await self._web.chat_update(**kwargs)

    async def delete_message(self, channel: str, ts: str) -> None:
        await self._web.chat_delete(channel=channel, ts=ts)

    async def add_reaction(self, channel: str, ts: str, emoji: str) -> None:
        try:
            await self._web.reactions_add(channel=channel, name=emoji, timestamp=ts)
        except Exception:
            pass  # best-effort

    async def remove_reaction(self, channel: str, ts: str, emoji: str) -> None:
        try:
            await self._web.reactions_remove(channel=channel, name=emoji, timestamp=ts)
        except Exception:
            pass  # best-effort

    async def views_publish(self, user_id: str, view: dict) -> None:
        await self._web.views_publish(user_id=user_id, view=view)

    async def upload_file(
        self,
        channel: str,
        thread_ts: str,
        file: str,
        filename: str,
        title: str,
    ) -> None:
        await self._web.files_upload_v2(
            channel=channel,
            thread_ts=thread_ts,
            file=file,
            filename=filename,
            title=title,
        )

    async def open_dm(self, user_id: str) -> str:
        resp = await self._web.conversations_open(users=[user_id])
        return resp["channel"]["id"]

    async def post_ephemeral(
        self, channel: str, user_id: str, text: str, blocks: list[dict] | None = None, thread_ts: str | None = None
    ) -> None:
        kwargs: dict = {"channel": channel, "user": user_id, "text": text}
        if blocks:
            kwargs["blocks"] = blocks
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        await self._web.chat_postEphemeral(**kwargs)

    async def is_dm(self, channel: str) -> bool:
        """Check if channel is a 1:1 DM via conversations.info API."""
        try:
            resp = await self._web.conversations_info(channel=channel)
            ch = resp.data.get("channel", {}) if hasattr(resp, "data") else {}  # type: ignore[union-attr]
            return bool(ch.get("is_im", False))
        except Exception:
            # Fallback to heuristic if API call fails
            return channel.startswith("D")

    async def views_open(self, trigger_id: str, view: dict) -> None:
        await self._web.views_open(trigger_id=trigger_id, view=view)

    async def views_update(self, view_id: str, view: dict) -> None:
        await self._web.views_update(view_id=view_id, view=view)

    async def get_user_info(self, user_id: str) -> dict[str, str]:
        """Look up a user's profile via users.info API."""
        try:
            resp = await self._web.users_info(user=user_id)
            user: dict = resp.get("user", {})
            profile: dict = user.get("profile", {})
            return {
                "id": user_id,
                "name": user.get("name", user_id),
                "real_name": profile.get("real_name") or user.get("name") or user_id,
            }
        except Exception:
            return {"id": user_id, "name": user_id, "real_name": user_id}

    async def get_user_profile(self, user_id: str) -> dict:
        """Look up a user's full Slack profile via users.info API."""
        resp = await self._web.users_info(user=user_id)
        user: dict = resp.get("user", {})
        profile: dict = user.get("profile", {})
        info: dict = {
            "id": user_id,
            "name": user.get("name", user_id),
            "real_name": profile.get("real_name", ""),
            "display_name": profile.get("display_name", ""),
            "title": profile.get("title", ""),
            "status_text": profile.get("status_text", ""),
            "status_emoji": profile.get("status_emoji", ""),
            "timezone": user.get("tz", ""),
            "is_bot": bool(user.get("is_bot")),
            "is_admin": bool(user.get("is_admin")),
            "image_url": profile.get("image_192", ""),
        }
        return {k: v for k, v in info.items() if v is not None and v != ""}

    # ── Streaming API ──

    async def start_stream(
        self,
        channel: str,
        thread_ts: str,
        initial_text: str | None = None,
        team_id: str | None = None,
        user_id: str | None = None,
    ) -> str | None:
        """Start a streaming message via chat.startStream with task plan mode."""
        try:
            body: dict[str, Any] = {
                "channel": channel,
                "thread_ts": thread_ts,
                "task_display_mode": "plan",
            }
            if team_id:
                body["recipient_team_id"] = team_id
            if user_id:
                body["recipient_user_id"] = user_id
            chunks: list[dict[str, Any]] = []
            if initial_text:
                chunks.append({"type": "markdown_text", "text": initial_text})
            if chunks:
                body["chunks"] = chunks
            resp = await self._web.api_call("chat.startStream", json=body)
            return resp.get("ts")
        except Exception:
            logger.warning("chat.startStream failed", exc_info=True)
            return None

    async def append_stream(self, channel: str, ts: str, text: str) -> bool:
        """Append markdown text to a streaming message via chat.appendStream."""
        try:
            await self._web.api_call(
                "chat.appendStream",
                json={
                    "channel": channel,
                    "ts": ts,
                    "chunks": [{"type": "markdown_text", "text": text}],
                },
            )
            return True
        except Exception:
            logger.debug("chat.appendStream failed", exc_info=True)
            return False

    async def append_task(
        self,
        channel: str,
        ts: str,
        task_id: str,
        title: str,
        status: str,
        details: str = "",
        output: str = "",
    ) -> bool:
        """Append a task_update chunk to a streaming message."""
        try:
            task: dict[str, Any] = {
                "type": "task_update",
                "id": task_id,
                "title": title,
                "status": status,
            }
            if details:
                task["details"] = details
            if output:
                task["output"] = output
            await self._web.api_call(
                "chat.appendStream",
                json={"channel": channel, "ts": ts, "chunks": [task]},
            )
            return True
        except Exception:
            logger.debug("chat.appendStream task_update failed", exc_info=True)
            return False

    async def stop_stream(self, channel: str, ts: str, final_text: str | None = None) -> bool:
        """Stop a streaming message via chat.stopStream.

        We intentionally do NOT call chat.update after stopping — the streamed
        content already has rich formatting (syntax-highlighted code blocks, etc.)
        that chat.update would downgrade to plain mrkdwn.
        """
        try:
            await self._web.api_call("chat.stopStream", json={"channel": channel, "ts": ts})
            return True
        except Exception:
            logger.debug("chat.stopStream failed", exc_info=True)
            return False

    async def set_thread_status(self, channel: str, thread_ts: str, status: str) -> None:
        """Set assistant thread loading status via assistant.threads.setStatus."""
        try:
            await self._web.api_call(
                "assistant.threads.setStatus",
                params={"channel_id": channel, "thread_ts": thread_ts, "status": status},
            )
        except Exception:
            logger.debug("assistant.threads.setStatus failed", exc_info=True)

    async def set_thread_title(self, channel: str, thread_ts: str, title: str) -> None:
        """Set assistant thread title via assistant.threads.setTitle."""
        try:
            await self._web.api_call(
                "assistant.threads.setTitle",
                params={"channel_id": channel, "thread_ts": thread_ts, "title": title},
            )
        except Exception:
            logger.debug("assistant.threads.setTitle failed", exc_info=True)

    async def set_suggested_prompts(
        self, channel: str, thread_ts: str, prompts: list[dict[str, str]]
    ) -> None:
        """Set suggested prompts via assistant.threads.setSuggestedPrompts."""
        try:
            import json

            await self._web.api_call(
                "assistant.threads.setSuggestedPrompts",
                params={
                    "channel_id": channel,
                    "thread_ts": thread_ts,
                    "prompts": json.dumps(prompts),
                },
            )
        except Exception:
            logger.debug("assistant.threads.setSuggestedPrompts failed", exc_info=True)

    @staticmethod
    def _extract_inline_texts(elements: list[dict[str, Any]]) -> list[str]:
        """Extract text from inline rich_text elements (text, link, user, emoji, channel, usergroup)."""
        texts: list[str] = []
        for element in elements:
            inline_type = element.get("type")
            if inline_type == "text":
                texts.append(element.get("text", ""))
            elif inline_type == "link":
                texts.append(element.get("url", ""))
            elif inline_type == "user":
                texts.append(f'<@{element.get("user_id", "")}>')
            elif inline_type == "emoji":
                texts.append(f':{element.get("name", "")}:')
            elif inline_type == "channel":
                texts.append(f'<#{element.get("channel_id", "")}>')
            elif inline_type == "usergroup":
                texts.append(f'<!subteam^{element.get("usergroup_id", "")}>')
        return [t for t in texts if t]

    async def fetch_message(self, channel: str, ts: str) -> str | None:
        """Fetch a single message's text by channel and timestamp.

        Prefers content extracted from Block Kit ``blocks`` and falls back
        to the top-level ``text`` field when blocks yield nothing.
        """
        try:
            resp = await self._web.conversations_history(
                channel=channel, oldest=ts, latest=ts, inclusive=True, limit=1
            )
            messages: list[dict[str, Any]] = resp.get("messages", [])
            if messages:
                message = messages[0]
                text = message.get("text", "")
                parts: list[str] = []
                for block in message.get("blocks", []):
                    block_type = block.get("type")
                    if block_type == "section":
                        text_obj = block.get("text")
                        if text_obj:
                            section_text = text_obj.get("text", "")
                            if section_text:
                                parts.append(section_text)
                    elif block_type == "rich_text":
                        for rich_text_element in block.get("elements", []):
                            # rich_text_list has children that are each rich_text_section;
                            # rich_text_preformatted and rich_text_quote have inline
                            # elements directly, so the else branch handles them correctly.
                            leaves = (
                                rich_text_element.get("elements", [])
                                if rich_text_element.get("type") == "rich_text_list"
                                else [rich_text_element]
                            )
                            for leaf in leaves:
                                inline_texts = self._extract_inline_texts(
                                    leaf.get("elements", [])
                                )
                                if inline_texts:
                                    parts.append("".join(inline_texts))
                return "\n".join(parts) or text or None
        except (SlackDeskClientError, aiohttp.ClientError, asyncio.TimeoutError):
            logger.debug("fetch_message failed for %s/%s", channel, ts, exc_info=True)
        return None

    async def fetch_thread_replies(self, channel: str, thread_ts: str, limit: int = 200) -> list[dict]:
        """Fetch parent message + replies via conversations.replies API."""
        try:
            resp = await self._web.conversations_replies(
                channel=channel, ts=thread_ts, limit=limit,
            )
            data: dict = resp.data if hasattr(resp, "data") else dict(resp)  # type: ignore[assignment,call-overload]
            messages: list[dict] = data.get("messages", [])
            meta: dict = data.get("response_metadata", {})
            if meta.get("next_cursor"):
                logger.warning(
                    "Thread %s/%s has more messages than limit=%d; import is incomplete",
                    channel, thread_ts, limit,
                )
            return messages
        except (SlackDeskClientError, aiohttp.ClientError, asyncio.TimeoutError):
            logger.debug("fetch_thread_replies failed for %s/%s", channel, thread_ts, exc_info=True)
        return []

    async def fetch_history(self, channel: str, oldest: str, limit: int = 200) -> list[dict]:
        """Fetch channel messages newer than ``oldest`` via conversations.history.

        ``inclusive`` is deliberately NOT passed. Slack's ``conversations.history``
        treats ``oldest`` as exclusive unless ``inclusive=True`` is set (which
        ``fetch_message`` above DOES set, to fetch one exact ts). Leaving it off is
        what makes ``oldest=<last seen ts>`` a correct resume cursor: the message
        already delivered is not returned again. Raises on API/transport failure so
        the caller can distinguish "no new messages" from "the poll failed" — a
        swallowed error here would advance a cursor past unread messages.
        """
        resp = await self._web.conversations_history(channel=channel, oldest=oldest, limit=limit)
        data: dict = resp.data if hasattr(resp, "data") else dict(resp)  # type: ignore[assignment,call-overload]
        messages: list[dict] = data.get("messages", [])
        return messages if isinstance(messages, list) else []

    async def download_file(self, url: str, dest: str) -> None:
        """Download a Slack-hosted file using the bot token for auth."""
        headers = {"Authorization": f"Bearer {self._web.token}"}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                resp.raise_for_status()
                with open(dest, "wb") as f:
                    async for chunk in resp.content.iter_chunked(8192):
                        f.write(chunk)
