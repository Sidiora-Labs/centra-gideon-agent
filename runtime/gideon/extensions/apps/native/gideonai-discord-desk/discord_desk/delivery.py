"""DiscordDeskDelivery — the app-side ChannelDelivery the gateway delivers through.

All Discord rendering (message splitting, throttled edit-streaming, the
button-component approval prompt + owner-response wait, reactions, the typing
indicator) lives HERE, so core delivers with plain text + structured intent and
never imports Discord code. The transport registers an instance onto the gateway +
dashboard at ``start_inbound``.

Two Discord-specific shapes drive this module:

* **Streaming is edit-based.** Discord has no chunk-append API, so a "stream" is one
  message repeatedly PATCHed. Message edits share a per-channel rate bucket with
  sends, so :class:`DiscordDeskDelivery` throttles to at most one edit per
  :data:`_EDIT_MIN_INTERVAL` seconds and always flushes the exact final text on
  ``stop_stream`` — the contract the fake-API tests pin.
* **Approvals are message COMPONENTS.** An action row of two buttons; the press
  arrives back as an ``INTERACTION_CREATE`` (not a message), which MUST be answered
  within three seconds or Discord shows the user "This interaction failed". When the
  decision resolves, the prompt is edited to show the outcome AND its ``components``
  are cleared — a still-clickable approval button on a hours-old decided request is
  a real footgun, not a cosmetic one.

Discord renders standard markdown, so unlike Telegram's MarkdownV2 there is no
escaping layer: the model's markdown goes out as-is. Length is the only rendering
constraint, hence :func:`split_message`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from discord_desk.api import (
    BUTTON_STYLE_DANGER,
    BUTTON_STYLE_SUCCESS,
    COMPONENT_ACTION_ROW,
    COMPONENT_BUTTON,
    DISCORD_DESK_MAX_TEXT,
    DiscordDeskApi,
)

from gideon.sdk.channel import (
    Principal,
    is_allowed_sender,
    is_tracked_channel,
    on_channel,
    redact_credentials,
    redact_exfiltration_urls,
)

logger = logging.getLogger(__name__)

# Minimum wall-clock seconds between two edits of the same streamed message.
# Discord's per-channel message bucket is roughly 5 requests / 5 seconds, and edits
# spend from the same budget as the sends around them, so 1.1s leaves headroom.
_EDIT_MIN_INTERVAL = 1.1
# custom_id prefixes. Discord caps custom_id at 100 chars; a request id is short.
_APPROVE = "approve"
_DENY = "deny"
# The component interaction type on INTERACTION_CREATE (3 = MESSAGE_COMPONENT).
# Slash commands (2) and modals (5) arrive on the same event and are NOT ours.
INTERACTION_TYPE_COMPONENT = 3


def split_message(text: str, limit: int = DISCORD_DESK_MAX_TEXT) -> list[str]:
    """Split *text* into parts no longer than *limit*, preferring newline breaks.

    Discord rejects a message body over 2000 chars with ``50035 Invalid Form Body``,
    so long replies stream across several messages. Splits on the last newline
    before the limit when possible, else hard-splits."""
    if len(text) <= limit:
        return [text] if text else []
    parts: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        parts.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    if remaining:
        parts.append(remaining)
    return parts


def _safe(text: str) -> str:
    """Redact before anything reaches the wire (exfil URLs, then credentials).

    Every delivery path funnels through here — an unredacted path is the whole
    class of bug this centralization prevents."""
    body, _ = redact_exfiltration_urls(text)
    body, _ = redact_credentials(body)
    return body


class _StreamState:
    """Bookkeeping for one edit-streamed message."""

    __slots__ = (
        "channel_id",
        "message_id",
        "last_edit",
        "last_text",
        "pending_text",
        "base_text",
        "task_lines",
    )

    def __init__(self, channel_id: str, message_id: str, initial_text: str) -> None:
        self.channel_id = channel_id
        self.message_id = message_id
        self.last_edit = 0.0
        self.last_text = initial_text
        self.pending_text = ""
        self.base_text = initial_text
        self.task_lines: dict[str, tuple[str, str]] = {}


class _PendingApproval:
    __slots__ = (
        "future",
        "channel_id",
        "message_id",
        "request_id",
        "answerer",
        "answers",
        "delivery",
        "tenant",
        "owner",
        "guild",
        "application",
        "on_answer",
        "chosen_answer",
    )

    def __init__(self, request_id: str, channel_id: str, message_id: str) -> None:
        from gideon.sdk.channel import (
            ONE_CALL_ANSWERS,
            ApprovalAnswer,
        )

        self.future: asyncio.Future = asyncio.get_event_loop().create_future()
        self.channel_id = channel_id
        self.message_id = message_id
        self.request_id = request_id
        self.answerer: Principal | None = None
        self.on_answer: Callable[[str, Principal], bool] | None = None

        self.answers: tuple[ApprovalAnswer, ...] = ONE_CALL_ANSWERS
        self.delivery: DiscordDeskDelivery | None = None
        self.tenant = self.owner = self.guild = self.application = (
            self.chosen_answer
        ) = ""


def _apply_verified_interaction(
    interaction: dict[str, Any], pending: _PendingApproval | None, owner_id: str
) -> bool:
    """Apply only the live offered capability from its current authenticated bot."""
    if interaction.get("type") != INTERACTION_TYPE_COMPONENT:
        return False
    custom_id = str((interaction.get("data") or {}).get("custom_id", ""))
    action, _, request_id = custom_id.partition(":")
    answer = {"approve": "approved", "deny": "rejected"}.get(action, action)
    actor = (
        (interaction.get("member") or {}).get("user") or interaction.get("user") or {}
    )
    actor_id = str(actor.get("id", ""))
    message = interaction.get("message") or {}
    channel_id = str(interaction.get("channel_id", ""))
    if pending is None or pending.future.done() or pending.delivery is None:
        return False
    identity = pending.delivery.approval_identity(channel_id)
    if (
        identity is None
        or actor.get("bot")
        or not owner_id
        or actor_id != owner_id
        or actor_id != identity["owner"]
        or actor_id != pending.owner
        or identity["tenant"] != pending.tenant
        or str(interaction.get("application_id", "")) != pending.application
        or pending.application != identity["application"]
        or str(interaction.get("guild_id") or "") != pending.guild
        or channel_id != pending.channel_id
        or str(message.get("id", "")) != pending.message_id
        or request_id != pending.request_id
    ):
        return False
    chosen = next((item for item in pending.answers if item.key == answer), None)
    if chosen is None:
        return False
    by = on_channel("discord", actor_id, pending.tenant)
    if callable(pending.on_answer):
        if not pending.on_answer(answer, by):
            return False
    elif answer == "trust":
        return False
    pending.answerer = by
    pending.chosen_answer = answer
    if not pending.future.done():
        pending.future.set_result(chosen.ends)
    return True


def _approval_ending(outcome: str) -> str:
    return {
        "approved": "✅ Approved",
        "rejected": "🚫 Rejected",
        "expired": "⌛ Expired",
        "cancelled": "↩️ Cancelled",
        "failed": "⚠️ Could not confirm",
    }.get(str(outcome), "🚫 Rejected")


def _render_stream_tasks(base_text: str, task_lines: dict[str, tuple[str, str]]) -> str:
    marks = {
        "in_progress": "⏳",
        "complete": "✅",
        "failed": "❌",
        "rejected": "🚫",
        "expired": "⌛",
        "cancelled": "↩️",
    }
    lines = [
        f"{marks.get(status, '⏳')} {title}" for title, status in task_lines.values()
    ]
    return "\n".join([base_text, *lines]).strip()


class DiscordDeskDelivery:
    """Renders + delivers gateway results to Discord. Implements ChannelDelivery."""

    def __init__(
        self, api: DiscordDeskApi, owner_id: str, *, transport: Any = None
    ) -> None:
        self._api = api
        self._owner_id = owner_id
        self._transport = transport
        self._approval_channels: dict[str, dict] = {}
        self._streams: dict[str, _StreamState] = {}
        # keyed by "req:<request_id>" (from the button custom_id) and by
        # "<channel>:<message>" (the prompt the buttons live on).
        self._pending: dict[str, _PendingApproval] = {}
        # user id → opened DM channel id. create_dm is idempotent server-side but
        # costs a request on a bucket shared with sends, so cache the resolution.
        self._dm_channels: dict[str, str] = {}
        # channel id → guild id, populated from inbound events (a DM carries no
        # guild_id, so a channel absent from this map links as @me — the correct DM
        # form). Per-instance, never a class attribute: two transports would
        # otherwise share one map.
        self._channel_guilds: dict[str, str] = {}
        # monotonic clock is injectable so the throttle test needn't sleep.
        self._now = _monotonic

    def approval_identity(self, channel: str) -> dict | None:
        from gideon.sdk.channel import get_transport, owner_id_for, raw_delivery_for

        owner = owner_id_for("discord")
        application = str(getattr(self._transport, "_own_application_id", "") or "")
        if (
            not owner
            or not application
            or not is_allowed_sender("discord", owner)
            or self._transport is None
            or not self._transport.connected
            or get_transport("discord") is not self._transport
            or raw_delivery_for("discord") is not self
        ):
            return None
        current = self._approval_channels.get(channel, {})
        return {
            "owner": owner,
            "tenant": f"discord:{application}",
            "transport": self._transport,
            "application": application,
            "guild": current.get("guild", ""),
            "private": current.get("private", False) and current.get("owner") == owner,
        }

    async def prepare_approval_channel(self, channel: str) -> bool:
        self._approval_channels.pop(channel, None)
        identity = self.approval_identity(channel)
        if identity is None:
            return False
        try:
            actual = await self._api.get_channel(channel)
        except Exception:
            return False
        if str(actual.get("id", "")) != channel:
            return False
        recipients = actual.get("recipients") or []
        private = (
            actual.get("type") == 1
            and not actual.get("guild_id")
            and len(recipients) == 1
            and str(recipients[0].get("id", "")) == identity["owner"]
            and not recipients[0].get("bot")
        )
        self._approval_channels[channel] = {
            "private": bool(private),
            "guild": str(actual.get("guild_id") or ""),
            "owner": identity["owner"],
        }
        return bool(private)

    # ── DM resolution ──
    async def open_dm(self, user_id: str) -> str:
        """Resolve the DM channel id for a user id.

        Unlike Telegram (where the user id IS the chat id), Discord DMs have their
        own channel id that must be opened via ``POST /users/@me/channels``. Returns
        "" on failure so a caller degrades instead of posting to a user id that
        Discord would 404."""
        if not user_id:
            return ""
        cached = self._dm_channels.get(str(user_id))
        if cached:
            return cached
        try:
            channel = await self._api.create_dm(str(user_id))
        except Exception:
            logger.warning("discord: open_dm failed for %s", user_id, exc_info=True)
            return ""
        cid = str(channel.get("id", ""))
        if cid:
            self._dm_channels[str(user_id)] = cid
        return cid

    # ── text / rich ──
    async def deliver_text(
        self,
        channel: str,
        text: str,
        thread_ts: str = "",
        *,
        unfurl_links: bool | None = None,
        unfurl_media: bool | None = None,
        reply_broadcast: bool | None = None,
    ) -> str:
        last = ""
        for part in split_message(_safe(text)):
            msg = await self._api.create_message(channel, part)
            last = str(msg.get("id", "")) or last
        return last

    async def deliver_rich(
        self,
        channel: str,
        payload: Any,
        fallback_text: str,
        *,
        thread_ts: str = "",
        unfurl_links: bool = True,
        unfurl_media: bool = True,
        reply_broadcast: bool = False,
    ) -> str:
        """Deliver a rich payload. Discord's analogue of Block Kit is ``components``.

        A caller that hands through a Discord-shaped ``{"components": [...]}`` gets
        them attached; anything else falls back to the plain text, per the contract."""
        components = None
        if isinstance(payload, dict) and isinstance(payload.get("components"), list):
            components = payload["components"]
        msg = await self._api.create_message(
            channel, _safe(fallback_text)[:DISCORD_DESK_MAX_TEXT], components=components
        )
        return str(msg.get("id", ""))

    async def deliver_cron_result(
        self, channel: str, job_name: str, job_id: str, text: str, thread_ts: str = ""
    ) -> str:
        header = f"**Cron: {job_name}**\n\n"
        parts = split_message(_safe(text), DISCORD_DESK_MAX_TEXT - len(header))
        last = ""
        for i, part in enumerate(parts or [""]):
            msg = await self._api.create_message(
                channel, (header + part) if i == 0 else part
            )
            last = str(msg.get("id", "")) or last
        return last

    async def deliver_notification(
        self, channel: str, title: str, text: str, thread_ts: str = ""
    ) -> str:
        body = _safe(f"**{title}**\n\n{text}")
        last = ""
        for part in split_message(body):
            msg = await self._api.create_message(channel, part)
            last = str(msg.get("id", "")) or last
        return last

    async def deliver_chat_mirror(
        self, channel: str, text: str, thread_ts: str = ""
    ) -> None:
        """Mirror a dashboard reply, rendering a trailing ``[OPTIONS: …]`` as buttons."""
        from gideon.sdk.channel import extract_options

        body, options = extract_options(_safe(text))
        for part in split_message(body):
            await self._api.create_message(channel, part)
        if options:
            # Discord allows at most 5 buttons per action row; chunk accordingly.
            rows = [
                {
                    "type": COMPONENT_ACTION_ROW,
                    "components": [
                        {
                            "type": COMPONENT_BUTTON,
                            "style": BUTTON_STYLE_SUCCESS,
                            "label": opt[:80],
                            "custom_id": f"opt:{idx}",
                        }
                        for idx, opt in chunk
                    ],
                }
                for chunk in _chunk(list(enumerate(options)), 5)
            ]
            await self._api.create_message(channel, "Options:", components=rows)

    async def deliver_subagent_reply(
        self, channel: str, text: str, thread_ts: str = "", elapsed_secs: float = 0.0
    ) -> None:
        for part in split_message(_safe(text)):
            await self._api.create_message(channel, part)
        if elapsed_secs:
            await self._api.create_message(channel, f"_took {elapsed_secs:.1f}s_")

    # ── identity resolution ──
    async def resolve_user_name(self, user_id: str) -> str:
        """Display name for a user id via ``GET /users/{id}``.

        Prefers ``global_name`` (Discord's display name) over the handle, and falls
        back to the id so a lookup failure degrades to something printable."""
        try:
            user = await self._api.get_user(str(user_id))
        except Exception:
            return str(user_id)
        return str(user.get("global_name") or user.get("username") or user_id)

    async def resolve_user_profile(self, user_id: str) -> dict:
        try:
            return await self._api.get_user(str(user_id)) or {"id": str(user_id)}
        except Exception:
            return {"id": str(user_id)}

    async def channel_info(self, channel_id: str) -> dict:
        """Channel metadata. Discord's channel ``type`` field: 1 is a 1:1 DM."""
        try:
            channel = await self._api.get_channel(str(channel_id))
        except Exception:
            return {"name": str(channel_id), "is_im": False}
        return {
            "name": str(channel.get("name") or channel_id),
            "is_im": int(channel.get("type", 0) or 0) == 1,
            "guild_id": str(channel.get("guild_id", "") or ""),
        }

    def list_reply_channels(self) -> list[dict]:
        """The channels this delivery can post into for the dashboard picker.

        The tracked-channel allowlist lives in the core trust seam (CE-1 owns it),
        and the SDK exposes only a membership check (:func:`is_tracked_channel`) —
        no enumeration — so the picker offers the DM entry, and a guild reply targets
        a specific tracked channel id core already holds. Deliberately minimal, per
        the ChannelDelivery contract ("may be empty")."""
        return [{"id": "dm", "name": "Direct Message"}]

    def is_tracked_channel(self, channel_id: str) -> bool:
        return is_tracked_channel("discord", channel_id)

    def build_thread_link(self, channel: str, ts: str) -> str:
        """Deep link to a message: ``/channels/<guild|@me>/<channel>/<message>``.

        Discord DOES have a stable link format (unlike Telegram's private chats), so
        this returns a real URL. The guild id is not on this call's signature, so it
        is read from the guild the channel was last seen in (:meth:`note_channel_guild`,
        fed by the transport's inbound path); a DM has no guild and uses the literal
        ``@me``. Returns "" when there is no channel to link to — honest over a URL
        that 404s."""
        if not channel:
            return ""
        guild = self._channel_guilds.get(str(channel), "") or "@me"
        base = f"https://discord.com/channels/{guild}/{channel}"
        return f"{base}/{ts}" if ts else base

    def note_channel_guild(self, channel_id: str, guild_id: str) -> None:
        """Remember which guild a channel belongs to (for :meth:`build_thread_link`)."""
        if channel_id and guild_id:
            self._channel_guilds[str(channel_id)] = str(guild_id)

    # ── attachments ──
    async def upload_attachment(
        self,
        channel: str,
        file_path: str,
        *,
        filename: str = "",
        thread_ts: str = "",
        title: str = "",
        initial_comment: str = "",
    ) -> str:
        """Upload a file. Discord renders images inline from the attachment itself,
        so there is no photo-vs-document split to make (unlike Telegram)."""
        caption = _safe(initial_comment or title or "")
        msg = await self._api.upload_file(
            channel,
            file_path,
            filename=filename,
            content=caption[:DISCORD_DESK_MAX_TEXT],
        )
        return str(msg.get("id", ""))

    # ── reactions + typing (implemented, therefore declared True) ──
    async def add_reaction(self, channel: str, message_id: str, emoji: str) -> bool:
        """React to a message as the bot. Returns whether it landed."""
        try:
            await self._api.add_reaction(channel, message_id, emoji)
            return True
        except Exception:
            logger.debug("discord: add_reaction failed", exc_info=True)
            return False

    async def show_typing(self, channel: str) -> bool:
        """Show the typing indicator (~10s, or until the next message)."""
        try:
            await self._api.trigger_typing(channel)
            return True
        except Exception:
            logger.debug("discord: trigger_typing failed", exc_info=True)
            return False

    # ── edit-based streaming ──
    async def start_stream(
        self, channel: str, thread_ts: str = "", initial_text: str = ""
    ) -> str:
        text = initial_text or "…"
        msg = await self._api.create_message(channel, text)
        mid = str(msg.get("id", ""))
        if not mid:
            return ""
        st = _StreamState(channel, mid, text)
        st.last_edit = self._now()
        st.last_text = text
        self._streams[f"{channel}:{mid}"] = st
        return mid

    async def append_stream_task(
        self,
        channel: str,
        stream_ts: str,
        task_id: str,
        title: str,
        status: str,
    ) -> None:
        """Append a progress line to the streamed message, throttled.

        Discord has no task-animation primitive, so a task update is folded into the
        streamed text as a status line and edited in — at most one edit per
        :data:`_EDIT_MIN_INTERVAL`. The final flush happens in :meth:`stop_stream`,
        so a throttled-away update is never lost."""
        st = self._streams.get(f"{channel}:{stream_ts}")
        if st is None:
            return
        st.task_lines[task_id] = (title, status)
        st.pending_text = _render_stream_tasks(st.base_text, st.task_lines)
        await self._maybe_edit(st, force=False)

    async def stop_stream(self, channel: str, stream_ts: str) -> None:
        st = self._streams.pop(f"{channel}:{stream_ts}", None)
        if st is None:
            return
        # Always flush the exact final text, throttle be damned.
        await self._maybe_edit(st, force=True)

    async def _maybe_edit(self, st: _StreamState, *, force: bool) -> None:
        text = st.pending_text or st.last_text
        if text == st.last_text and not force:
            return
        now = self._now()
        if not force and (now - st.last_edit) < _EDIT_MIN_INTERVAL:
            return  # throttled — the pending text rides until the next edit/flush
        try:
            await self._api.edit_message(
                st.channel_id, st.message_id, text[:DISCORD_DESK_MAX_TEXT]
            )
            st.last_edit = now
            st.last_text = text
            st.pending_text = ""
        except Exception:
            logger.debug("discord: stream edit failed", exc_info=True)

    # ── approval via message components ──
    async def request_approval(
        self,
        event: Any,
        *,
        source: str,
        parent_session_key: str = "",
        sessions: Any = None,
        on_prompted: Any = None,
    ) -> bool | None:
        """Post an Approve/Deny button row and wait for the owner's press.

        Returns approved/rejected, or None when we can't prompt (no owner/channel) so
        the gateway falls back to the dashboard. ``on_prompted(pending)`` lets core
        race a dashboard prompt against this one — a dashboard click resolves the
        same future."""
        from gideon.sdk.channel import (
            ONE_CALL_ANSWERS,
            approval_brief_for,
            offered_answers,
        )

        identity = self.approval_identity("")
        if identity is None:
            return None
        channel_id = ""
        if parent_session_key and sessions is not None:
            try:
                history_key = parent_session_key
                if sessions.get_channel_provider(history_key) != "discord":
                    history_key = f"dashboard:{parent_session_key}"
                if sessions.get_channel_provider(history_key) == "discord":
                    channel_id = sessions.get_channel(history_key) or ""
            except Exception:
                channel_id = ""
        if not channel_id:
            # No linked channel: prompt the owner's DM, which must be OPENED first —
            # a Discord user id is not a postable channel id.
            channel_id = await self.open_dm(identity["owner"])
        if not channel_id:
            return None

        private = await self.prepare_approval_channel(channel_id)
        identity = self.approval_identity(channel_id)
        if identity is None:
            return None
        brief = approval_brief_for(event) or {}
        answers = offered_answers(brief.get("answers")) or ONE_CALL_ANSWERS
        if not private:
            answers = ONE_CALL_ANSWERS
        request_id = str(getattr(event, "request_id", ""))
        title = _safe(str(getattr(event, "title", "")))
        lines = [f"🔐 [{source}] {title}"]
        for value in (
            getattr(event, "tool_purpose", ""),
            getattr(event, "tool_input", ""),
            brief.get("summary"),
            brief.get("blastRadiusLine"),
        ):
            if value:
                lines.append(_safe(str(value)))
        lines.extend(answer.promise for answer in answers if answer.promise)
        parts = split_message("\n\n".join(lines))
        msg = {}
        for index, part in enumerate(parts):
            msg = await self._api.create_message(
                channel_id,
                part,
                components=(
                    _approval_components(request_id, answers)
                    if index == len(parts) - 1
                    else None
                ),
            )
        message_id = str(msg.get("id", ""))
        if not message_id:
            return None
        pending = _PendingApproval(request_id, channel_id, message_id)
        pending.answers, pending.delivery = answers, self
        pending.owner, pending.tenant = identity["owner"], identity["tenant"]
        pending.guild, pending.application = identity["guild"], identity["application"]
        self._pending[f"{channel_id}:{message_id}"] = pending
        # Index by request_id too so resolve_interaction can find it from custom_id.
        self._pending[f"req:{request_id}"] = pending
        shared_timeout = False
        if on_prompted:
            try:
                shared_timeout = bool(on_prompted(pending))
            except Exception:
                logger.debug("discord: on_prompted hook failed", exc_info=True)

        from gideon.sdk.channel import approval_window_secs

        outcome = "cancelled"
        cancelled = False
        try:
            if shared_timeout:
                outcome = await pending.future
            else:
                outcome = await asyncio.wait_for(
                    pending.future, timeout=approval_window_secs()
                )
        except asyncio.TimeoutError:
            outcome = "expired"
        except asyncio.CancelledError:
            outcome = "cancelled"
            cancelled = True
        finally:
            self._pending.pop(f"{channel_id}:{message_id}", None)
            self._pending.pop(f"req:{request_id}", None)

        approved = outcome == "approved"
        status = _approval_ending(str(outcome))
        chosen = next((a for a in answers if a.key == pending.chosen_answer), None)
        if chosen is not None and chosen.promise and approved:
            status += " — " + chosen.promise
        try:
            # components=[] strips the buttons: a decided request must not leave a
            # clickable Approve behind.
            await self._api.edit_message(
                channel_id,
                message_id,
                f"🔐 {title} — {status}"[:DISCORD_DESK_MAX_TEXT],
                components=[],
            )
        except Exception:
            logger.debug("discord: approval finalize edit failed", exc_info=True)
        if cancelled:
            raise asyncio.CancelledError
        return approved

    async def resolve_interaction(self, interaction: dict[str, Any]) -> None:
        """Resolve a pending approval from an ``INTERACTION_CREATE`` button press.

        Acknowledging is NOT optional and NOT conditional on the press being ours:
        Discord shows the pressing user "This interaction failed" if nothing answers
        within three seconds, so the ack happens even for an unknown/stale custom_id."""
        if int(interaction.get("type", 0) or 0) != INTERACTION_TYPE_COMPONENT:
            return
        iid = str(interaction.get("id", ""))
        itoken = str(interaction.get("token", ""))
        if iid and itoken:
            try:
                await self._api.create_interaction_response(iid, itoken)
            except Exception:
                logger.debug("discord: interaction ack failed", exc_info=True)

        custom_id = str((interaction.get("data") or {}).get("custom_id", ""))
        action, _, request_id = custom_id.partition(":")
        if request_id:
            pending = self._pending.get(f"req:{request_id}")
            if action == "trust" and not await self.prepare_approval_channel(
                str(interaction.get("channel_id", ""))
            ):
                pending = None
            identity = self.approval_identity(str(interaction.get("channel_id", "")))
            _apply_verified_interaction(
                interaction, pending, identity["owner"] if identity else ""
            )


def _approval_components(
    request_id: str, answers: tuple | None = None
) -> list[dict[str, Any]]:
    """Render exactly the answer capability offered by the native approval owner."""
    from gideon.sdk.channel import ONE_CALL_ANSWERS

    return [
        {
            "type": COMPONENT_ACTION_ROW,
            "components": [
                {
                    "type": COMPONENT_BUTTON,
                    "style": (
                        BUTTON_STYLE_DANGER
                        if answer.key == "rejected"
                        else BUTTON_STYLE_SUCCESS
                    ),
                    "label": answer.label,
                    "custom_id": f"{answer.key}:{request_id}",
                }
                for answer in (answers or ONE_CALL_ANSWERS)
            ],
        }
    ]


def _chunk(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _monotonic() -> float:
    import time

    return time.monotonic()
