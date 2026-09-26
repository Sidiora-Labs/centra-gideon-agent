"""Slack inbox source — the poll-based ``MessageSourceProvider`` half of this app.

CHANNEL-EXPANSION T7.4 (atom CE-8). The app already registers a ``channel``
provider (``transport.create_provider``) for the live Socket-Mode conversation.
This module adds the *second* provider: an ``inbox`` one, so Slack messages also
reach the generic Inbox through core's vendor-neutral message-source seam instead
of a Slack-shaped path in core. Core only ever sees ``MessageSourceProvider`` /
``IncomingMessage``, imported from ``gideon.sdk`` — the only import surface
an app is allowed to use.

It is a thin ADAPTER, not a second Slack client: every call delegates to the
existing ``slack_desk_runtime.client.RealSlackDeskClient`` through the ``SlackDeskClientOps``
ABC, so a test can substitute the bundle's ``MockSlackDeskClient`` unchanged.

Checkpointing: core hands us the per-channel cursor dict it persisted last time
and expects the updated one back. The cursor is a Slack message ``ts``, passed
straight to ``conversations.history``'s ``oldest``, which is EXCLUSIVE when
``inclusive`` is not set (see ``client.fetch_history``) — so a poll never
re-delivers what the previous poll already returned, and the ``ts == cursor``
equality skip the earlier draft needed is unnecessary. Advancing the cursor is
deliberately decoupled from FILTERING: a bot/own message still moves the cursor
(it was seen and judged), while a channel whose fetch RAISED keeps its old cursor
so the next poll retries the same window rather than silently skipping messages.
"""

from __future__ import annotations

import logging
import sys as _sys
from pathlib import Path as _Path
from typing import Any

# The app loader only keeps this app's dir on sys.path while it execs THIS module,
# but the sibling ``slack_desk_runtime.*`` imports below must keep resolving for the life
# of the process (the inbox service polls long after boot). Pin it, exactly as
# transport.py does for the channel half.
_APP_DIR = str(_Path(__file__).resolve().parents[1])
if _APP_DIR not in _sys.path:
    _sys.path.insert(0, _APP_DIR)

from gideon.sdk.inbox import IncomingMessage, MessageSourceProvider

from slack_desk_runtime.client import RealSlackDeskClient, SlackDeskClientOps

logger = logging.getLogger(__name__)

#: Slack returns newest-first; cap a single poll so a long-quiet channel cannot
#: flood the Inbox on the first run after enable.
_POLL_LIMIT = 50


class SlackDeskInboxSource(MessageSourceProvider):
    """Expose Slack as a generic inbox message source."""

    #: Shown by /api/inbox/providers alongside ``source_name``.
    display_name = "Slack"

    def __init__(
        self, config: dict[str, Any] | None = None, client: SlackDeskClientOps | None = None
    ) -> None:
        cfg = config or {}
        import os

        # Per-instance config wins; else the shared credential store the gateway
        # propagates into the environment — the same resolution order as
        # SlackDeskTransport, so the two providers never disagree about which
        # workspace this app is bound to.
        token = cfg.get("bot_token", "") or os.environ.get("SLACK_BOT_TOKEN", "")
        # ``client`` is the test seam (MockSlackDeskClient); production passes none.
        self._client: SlackDeskClientOps = client or RealSlackDeskClient(str(token))
        # Resolved lazily and cached: a display name per Slack user id. Bounded by
        # the number of distinct senders in watched channels.
        self._names: dict[str, str] = {}

    @property
    def source_name(self) -> str:
        return "slack"

    async def poll(
        self, watched_channels: list[str], checkpoints: dict[str, str], user_id: str
    ) -> tuple[list[IncomingMessage], dict[str, str]]:
        """Fetch messages newer than each channel's checkpoint.

        Returns ``(messages, updated_checkpoints)``. A channel whose fetch fails
        keeps its old checkpoint, so the next poll retries the same window instead
        of skipping past unread messages.
        """
        out: list[IncomingMessage] = []
        cursors = dict(checkpoints)
        for channel_id in watched_channels:
            since = checkpoints.get(channel_id, "0")
            try:
                raw = await self._client.fetch_history(channel_id, since, _POLL_LIMIT)
            except Exception:
                # Keep the old cursor: a transient API error must not look like
                # "nothing new" and consume the unread window.
                logger.debug("inbox poll failed for %s", channel_id, exc_info=True)
                continue
            newest = since
            # Slack returns newest-first; reverse so the Inbox reads oldest-first.
            for msg in reversed(raw):
                ts = str(msg.get("ts", ""))
                if not ts:
                    continue
                # Cursor advances for every message SEEN, including ones filtered
                # below — they were judged, not missed.
                if _ts_newer(ts, newest):
                    newest = ts
                sender = str(msg.get("user", ""))
                # Skip our own messages and anything without a human author (bots,
                # joins, topic changes) — those are not inbox-worthy.
                if not sender or sender == user_id or msg.get("bot_id"):
                    continue
                out.append(
                    IncomingMessage(
                        id=ts,
                        channel_id=channel_id,
                        channel_name=channel_id,
                        thread_id=str(msg.get("thread_ts")) if msg.get("thread_ts") else None,
                        text=str(msg.get("text", "")),
                        sender_id=sender,
                        sender_name=await self.resolve_user_name(sender),
                        timestamp=_ts_epoch(ts),
                        is_dm=channel_id.startswith("D"),
                    )
                )
            cursors[channel_id] = newest
        return out, cursors

    async def send_reply(self, channel_id: str, text: str, thread_ts: str | None = None) -> bool:
        try:
            await self._client.post_message(channel_id, text, thread_ts)
            return True
        except Exception:
            logger.debug("inbox send_reply failed for %s", channel_id, exc_info=True)
            return False

    async def add_reaction(self, channel_id: str, ts: str, emoji: str) -> bool:
        try:
            # The client returns None and swallows its own API errors (reactions are
            # best-effort by design), so a clean return is the only success signal
            # available here.
            await self._client.add_reaction(channel_id, ts, emoji)
            return True
        except Exception:
            logger.debug("inbox add_reaction failed for %s", channel_id, exc_info=True)
            return False

    async def get_channel_history(
        self, channel_id: str, oldest: str, limit: int = 200
    ) -> list[dict[str, Any]]:
        """Raw history for digest/context use. Empty list on failure.

        Unlike ``poll``, an error here is not cursor-bearing — the caller wants
        best-effort context, so degrading to "no history" is correct.
        """
        try:
            return [dict(m) for m in await self._client.fetch_history(channel_id, oldest, limit)]
        except Exception:
            logger.debug("inbox history failed for %s", channel_id, exc_info=True)
            return []

    async def resolve_user_name(self, user_id: str) -> str:
        if user_id in self._names:
            return self._names[user_id]
        try:
            info = await self._client.get_user_info(user_id) or {}
            name = info.get("real_name") or info.get("name") or user_id
        except Exception:
            logger.debug("inbox resolve_user_name failed for %s", user_id, exc_info=True)
            name = user_id
        self._names[user_id] = name
        return name


def _ts_epoch(ts: str) -> float:
    """Slack ``ts`` ("1700000000.000100") as epoch seconds; 0.0 if unparseable."""
    try:
        return float(ts)
    except (TypeError, ValueError):
        return 0.0


def _ts_newer(candidate: str, current: str) -> bool:
    """True if ``candidate`` is a later Slack ts than ``current``.

    Compared NUMERICALLY, not as strings: Slack ts strings are fixed-width in
    practice, but a lexicographic compare would order "9999999999.0" above
    "10000000000.0" the moment the epoch gains a digit. Unparseable values never
    win, so a malformed ts cannot poison the cursor.
    """
    try:
        return float(candidate) > float(current)
    except (TypeError, ValueError):
        return False


def create_provider(config: dict[str, Any] | None = None) -> SlackDeskInboxSource:
    """Manifest factory — mirrors ``transport.create_provider``'s contract."""
    return SlackDeskInboxSource(config)
