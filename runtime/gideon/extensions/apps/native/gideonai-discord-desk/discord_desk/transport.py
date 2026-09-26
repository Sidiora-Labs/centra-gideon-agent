"""DiscordDeskTransport — the ChannelTransportProvider that owns the Discord desk.

Outbound plus health/test are token-gated and always available. Inbound is the
Gateway WebSocket loop started by :meth:`start_inbound`, which the gateway calls
once at boot with a :class:`GatewayServices` handle. The loop:

1. holds a gateway connection (:class:`DiscordDeskGateway` owns identify/heartbeat/
   resume — see its module docstring for the WS lifecycle);
2. normalizes each ``MESSAGE_CREATE`` to a :class:`ChannelMessage`;
3. hands it to the platform's guarded door (``services.deliver_channel_inbound``) —
   the trust gate, DM pairing, guild-channel tracked-only, non-owner-content
   fencing, redaction, session linking and the turn itself all happen in core, so
   this transport can't forget any of them; it keeps only the outbound half,
   delivering the verdict's canned reply. Core mirrors agent replies back out
   through the :class:`DiscordDeskDelivery` this transport registers at boot (the
   outbound half of the seam). ``INTERACTION_CREATE`` events (button presses)
   resolve a pending approval in the delivery.

Two Discord-specific inbound facts shape this file:

* **The bot sees its OWN messages.** ``MESSAGE_CREATE`` fires for the bot's own
  sends, so a transport that doesn't filter them feeds its own reply back into the
  session and loops forever. Telegram's ``getUpdates`` never does this, so the trap
  is new here: :meth:`_is_self_authored` drops anything from a bot account or from
  our own user id (captured from READY), and a test proves it.
* **DM-ness is the ABSENCE of ``guild_id``.** Discord marks a guild message by
  attaching ``guild_id``; a DM simply has none. That is the signal used for
  ``is_dm`` — not a channel-``type`` guess, which would need an extra REST lookup
  the event payload makes unnecessary.
"""

from __future__ import annotations

import asyncio
import logging
import sys as _sys
from pathlib import Path as _Path
from typing import Any

# The app loader only keeps this app's dir on sys.path while it execs the entry
# module. This is a multi-module package whose modules import each other for the
# life of the process (the gateway loop, delivery, and settings all resolve
# ``discord_desk.*`` long after boot). Pin the app dir on sys.path so those
# imports keep resolving — a real installed package would be permanently importable.
_APP_DIR = str(_Path(__file__).resolve().parents[1])
if _APP_DIR not in _sys.path:
    _sys.path.insert(0, _APP_DIR)

from gideon.sdk.channel import (
    ChannelCapabilities,
    ChannelMessage,
    ChannelTransportProvider,
    OutboundMessage,
)

# Import ALL runtime deps at MODULE level (not lazily inside methods): the loader
# only keeps this app's dir on sys.path while it execs this module, so a
# ``from discord_desk.X import`` inside a method would run LATER, off the path,
# and fail. Binding them here, during exec, captures them for the process life.
from discord_desk.api import DISCORD_DESK_MAX_TEXT, DiscordDeskApi, DiscordDeskHttpApi
from discord_desk.delivery import DiscordDeskDelivery, split_message
from discord_desk.gateway import DEFAULT_GATEWAY_URL, DiscordDeskGateway
from discord_desk.inbound_tap import publish as publish_inbound
from discord_desk.settings import (
    ACTIVATION_OFF,
    CRED_BOT_TOKEN,
    get_settings,
    reload_settings,
)
from discord_desk.writes import SendRefused, live_writes_disabled

logger = logging.getLogger(__name__)

PROVIDER = "discord"


class DiscordDeskTransport(ChannelTransportProvider):
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        import os

        cfg = config or {}
        # Per-instance config wins; else the shared credential store the gateway
        # propagates into the environment under this app's own key.
        self._token = cfg.get("bot_token", "") or os.environ.get(CRED_BOT_TOKEN, "")
        self._services: Any = None
        self._api: DiscordDeskApi | None = None
        self._delivery: DiscordDeskDelivery | None = None
        self._gateway: DiscordDeskGateway | None = None
        self._gateway_task: asyncio.Task | None = None
        # The bot's own user id, captured from READY — half of the self-message filter.
        self._own_user_id = ""

    @property
    def name(self) -> str:
        return PROVIDER

    @property
    def display_name(self) -> str:
        return "Discord"

    def capabilities(self) -> ChannelCapabilities:
        # Honest, and every True below has an implementation behind it:
        #   reactions        → DiscordDeskDelivery.add_reaction (PUT .../reactions/{e}/@me)
        #   typing_indicator → DiscordDeskDelivery.show_typing (POST /channels/{id}/typing)
        #   threads          → thread_id carried on ChannelMessage; a thread IS a
        #                      channel id in Discord, so replies target it directly
        #   edits            → DiscordDeskDelivery streaming PATCHes the message
        #   rich_text        → Discord renders standard markdown natively
        # Discord caps a message body at 2000 chars.
        return ChannelCapabilities(
            inbound=True, threads=True, attachments=True, reactions=True,
            edits=True, rich_text=True, typing_indicator=True,
            max_text_len=DISCORD_DESK_MAX_TEXT,
        )

    async def connect(self) -> bool:
        return bool(self._token)

    async def disconnect(self) -> None:
        if self._api is not None:
            await self._api.close()

    @property
    def connected(self) -> bool:
        return bool(self._token)

    # ── Inbound: the gateway drives this once at boot ──
    async def start_inbound(self, services: Any) -> None:
        if not self._token:
            logger.info("DiscordDeskTransport: no bot token — inbound stays offline")
            return
        self._services = services
        reload_settings()
        self._api = DiscordDeskHttpApi(self._token)

        # Register outbound delivery on the gateway + dashboard. Core delivers every
        # channel result through this ONE provider-agnostic ChannelDelivery handle —
        # it never sees the Discord API client.
        owner_id = self._resolve_owner_id(services)
        self._delivery = DiscordDeskDelivery(self._api, owner_id)
        if hasattr(services, "register_channel_delivery"):
            services.register_channel_delivery(self._delivery)
        if getattr(services, "dashboard_state", None) is not None:
            services.dashboard_state.channel_delivery = self._delivery

        self._gateway = DiscordDeskGateway(
            self._token,
            gateway_url=await self._discover_gateway_url(),
            on_message=self._on_message_create,
            on_interaction=self._on_interaction_create,
            on_ready=self._on_ready,
        )
        self._gateway_task = asyncio.ensure_future(self._gateway.run())
        logger.info("DiscordDeskTransport: gateway inbound started")

    async def _discover_gateway_url(self) -> str:
        """The bot's own gateway URL from ``GET /gateway/bot``.

        Discord asks clients to fetch this rather than hardcode the host (it can move
        and it carries the session-start budget). A failure here is not fatal — the
        documented default host still works — so degrade to it and let the gateway
        loop's own backoff report the real problem."""
        try:
            info = await self._api.get_gateway_bot()  # type: ignore[union-attr]
            return str(info.get("url", "")) or DEFAULT_GATEWAY_URL
        except Exception:
            logger.warning("discord: GET /gateway/bot failed — using the default gateway URL")
            return DEFAULT_GATEWAY_URL

    @staticmethod
    def _resolve_owner_id(services: Any) -> str:
        from gideon.sdk.channel import CRED_OWNER_ID

        try:
            creds = services.config.load_credentials()
            return creds.get(CRED_OWNER_ID, "") or getattr(services, "owner_id", "")
        except Exception:
            return getattr(services, "owner_id", "")

    async def stop_inbound(self) -> None:
        if self._gateway is not None:
            await self._gateway.stop()
        if self._gateway_task is not None:
            self._gateway_task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(self._gateway_task), timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception:
                logger.debug("discord: gateway task stop error", exc_info=True)
        if self._api is not None:
            await self._api.close()

    # ── event handlers (the gateway calls these) ──

    async def _on_ready(self, data: dict[str, Any]) -> None:
        """Capture the bot's own user id — the anchor of the self-message filter."""
        self._own_user_id = str((data.get("user") or {}).get("id", ""))
        logger.info("DiscordDeskTransport: ready as user %s", self._own_user_id or "?")

    async def _on_interaction_create(self, interaction: dict[str, Any]) -> None:
        """A component press — resolve a pending approval (delivery owns it + the ack)."""
        if self._delivery is not None:
            await self._delivery.resolve_interaction(interaction)

    def _is_self_authored(self, message: dict[str, Any]) -> bool:
        """Whether this MESSAGE_CREATE is our own (or another bot's) message.

        MESSAGE_CREATE fires for the bot's OWN sends. Without this filter the reply
        we just posted arrives back as inbound, gets routed to the same session, and
        the bot talks to itself forever. Two independent signals, because either
        alone has a hole: ``author.bot`` also covers other bots and webhooks (which
        should never drive a session), and the id compare still works before READY
        lands or if a payload omits the flag."""
        author = message.get("author") or {}
        if bool(author.get("bot")):
            return True
        return bool(self._own_user_id) and str(author.get("id", "")) == self._own_user_id

    def _to_channel_message(self, message: dict[str, Any]) -> ChannelMessage:
        author = message.get("author") or {}
        return ChannelMessage(
            channel_id=str(message.get("channel_id", "")),
            text=message.get("content", "") or "",
            sender=str(author.get("id", "")),
            # In Discord a thread IS a channel, so the reply target is the channel id
            # whether it's a top-level channel or a thread inside one.
            thread_id=str(message.get("channel_id", "")),
            message_id=str(message.get("id", "")),
            metadata={
                # The presence/absence of guild_id is Discord's DM signal.
                "guild_id": str(message.get("guild_id", "") or ""),
                "sender_name": str(author.get("global_name") or author.get("username") or ""),
                "username": str(author.get("username", "") or ""),
            },
        )

    async def _on_message_create(self, message: dict[str, Any]) -> None:
        if self._is_self_authored(message):
            return
        cm = self._to_channel_message(message)
        if not cm.text.strip():
            return  # nothing to act on (embed-only, sticker, attachment without text)

        guild_id = cm.metadata.get("guild_id", "")
        is_dm = not guild_id
        if self._delivery is not None and guild_id:
            # Remember the channel's guild so build_thread_link can form a real URL.
            self._delivery.note_channel_guild(cm.channel_id, guild_id)

        settings = get_settings()
        if is_dm and settings.dm_activation == ACTIVATION_OFF:
            return

        # The guarded door (EA-7). Core applies the trust gate, the pairing-code
        # redemption, the fence for non-owner guild content, redaction, session
        # linking and the turn itself — the routing this transport used to carry a
        # copy of. This transport keeps only the channel-specific outbound half:
        # delivering the verdict's canned reply as a Discord message.
        verdict = await self._services.deliver_channel_inbound(PROVIDER, cm, is_dm=is_dm)
        if verdict.canned_reply and self._delivery is not None:
            try:
                await self._delivery.deliver_text(cm.channel_id, verdict.canned_reply)
            except Exception:
                logger.debug("discord: canned reply send failed", exc_info=True)

        # CE-10: hand the message to this bundle's own trigger source, if one is attached.
        # AFTER the door and gated on `verdict.allowed`, which is the entire security
        # property: a denied poster gets no session, and must equally get no trigger fire —
        # otherwise anyone sharing a guild with the bot can arm the owner's automations.
        # The tap never raises and knows nothing about events; see inbound_tap's docstring.
        if verdict.allowed:
            publish_inbound(cm, is_dm=is_dm)

    # ── outbound / health ──

    async def send(self, message: OutboundMessage) -> bool | SendRefused:
        """Transmit one outbound message. ``True`` delivered, ``False`` failed, or a
        :class:`SendRefused` when the platform's live-writes kill switch is on.

        The refusal is checked AFTER the token gate on purpose: an unconfigured
        transport could not have written anything, so reporting "refused" there would
        claim the guard suppressed a write that was never possible. Only a transport
        that WOULD have transmitted reports a refusal.
        """
        if not self._token:
            return False
        # DISABLE_LIVE_WRITES (§1.4). A Discord message is a live, outward,
        # instantly-human-visible write — the same class core refuses for non-GET egress
        # and model deletion. Typed refusal, never a silent no-op: a test (or an
        # operator) asserting a send must be able to see that the guard, not the
        # network, stopped it. Checked BEFORE the API client is built so a suppressed
        # send opens no connection at all.
        if live_writes_disabled():
            refusal = SendRefused(channel=PROVIDER, target=message.channel_id)
            logger.warning("DiscordDeskTransport.send refused: %s", refusal)
            return refusal
        api = self._api or DiscordDeskHttpApi(self._token)
        try:
            for part in split_message(message.text):
                await api.create_message(message.channel_id, part)
            return True
        except Exception as exc:
            logger.warning("DiscordDeskTransport.send failed: %s", exc)
            return False
        finally:
            if api is not self._api:
                await api.close()

    async def health(self) -> dict[str, Any]:
        if not self._token:
            return {"state": "offline", "detail": "No bot token configured"}
        return {"state": "ready", "detail": "Bot token configured"}

    async def test(self) -> dict[str, Any]:
        """The Channels-page Test action: the live "gateway hello" probe (T4.4).

        ``GET /gateway/bot`` is the cheapest call that proves BOTH halves at once —
        the token authenticates AND a gateway session is available (it returns the
        remaining session-start budget, which is what actually stops a bot from
        connecting once it's exhausted)."""
        if not self._token:
            return {"ok": False, "detail": "No bot token configured"}
        api = DiscordDeskHttpApi(self._token)
        try:
            info = await api.get_gateway_bot()
            limit = info.get("session_start_limit") or {}
            remaining = limit.get("remaining")
            detail = f"Gateway reachable at {info.get('url', '?')}"
            if remaining is not None:
                detail += f" ({remaining} session starts remaining)"
            return {"ok": True, "detail": detail}
        except Exception as exc:
            return {"ok": False, "detail": f"GET /gateway/bot failed: {exc}"}
        finally:
            await api.close()


def create_provider(config: dict[str, Any] | None = None) -> "DiscordDeskTransport":
    return DiscordDeskTransport(config)
