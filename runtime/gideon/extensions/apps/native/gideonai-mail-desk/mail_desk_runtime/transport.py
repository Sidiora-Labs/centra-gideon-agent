"""MailDeskTransport — the ChannelTransportProvider that owns the mail desk channel.

Outbound plus health/test are configuration-gated and always available. Inbound is an IMAP
poll loop started by :meth:`start_inbound`, which the gateway calls once at boot with a
:class:`GatewayServices` handle. Each cycle:

1. UID-SEARCHes the folder for messages newer than the persisted cursor (every IMAP call
   runs in a thread executor — ``imaplib`` blocks);
2. parses each message to an :class:`~mail_desk_runtime.mime.InboundMail` (fail-closed: an
   unparseable message or one with no ``From`` address surfaces nothing);
3. drops our OWN mail (the mailbox receives copies of what we send, and any auto-reply
   from the far side would otherwise loop);
4. hands the message to the platform's guarded door (``services.deliver_channel_inbound``,
   provider ``"mail-desk"``) — the address allowlist, the pairing flow, fencing, redaction,
   session linking and the turn itself all live in core, so this transport can't forget any
   of them. A reply containing an active pairing code redeems it BEFORE the door (pairing
   here is "a reply containing the code" — an in-body search core's whole-message check
   cannot do), and the transport keeps only the outbound half, delivering the verdict's
   canned reply in-thread. Core mirrors agent replies back out through the
   :class:`~mail_desk_runtime.delivery.MailDeskDelivery` this transport registers at boot.

**Why not IMAP IDLE.** IDLE would give push-latency instead of a 60s poll, but ``imaplib``
has no IDLE support at all (it would mean hand-rolling the command plus its 29-minute
re-issue cycle and the dead-connection detection that comes with it), and a held-open
connection is a second failure mode to supervise. DEFERRED ("IDLE optional later"); the
poll cadence is user-configurable.

Two inbound facts shape this file:

* **The mailbox sees its own mail.** Most providers copy sent mail into the account, and an
  auto-responder on the far side mails straight back. :meth:`_is_self_authored` drops
  anything whose ``From`` is our own mailbox address. Discord had the same trap on
  ``MESSAGE_CREATE``; here it can also loop through a THIRD party's vacation responder,
  which is why the drop is on the address, not on a message id we remember.
* **A ``From`` display name is attacker-controlled.** Trust is keyed on the ``parseaddr``
  address ONLY (:func:`~mail_desk_runtime.mime.sender_address`); the display name never
  reaches a trust check.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import replace
import sys as _sys
from pathlib import Path as _Path
from typing import Any

# The app loader only keeps this app's dir on sys.path while it execs the entry module.
# This is a multi-module package whose modules import each other for the life of the
# process (the poll loop, delivery and settings all resolve ``mail_desk_runtime.*`` long
# after boot). Pin the app dir on sys.path so those imports keep resolving — a real
# installed package would be permanently importable.
_APP_DIR = str(_Path(__file__).resolve().parents[1])
if _APP_DIR not in _sys.path:
    _sys.path.insert(0, _APP_DIR)

from gideon.sdk.channel import (
    ChannelCapabilities,
    ChannelMessage,
    ChannelTransportProvider,
    OutboundMessage,
    redeem_pairing_code,
)
from gideon.sdk.util import app_data_dir

# Import ALL runtime deps at MODULE level (not lazily inside methods): the loader only
# keeps this app's dir on sys.path while it execs this module, so a
# ``from mail_desk_runtime.X import`` inside a method would run LATER, off the path, and
# fail. Binding them here, during exec, captures them for the process life.
from mail_desk_runtime.delivery import MailDeskDelivery, ThreadStore
from mail_desk_runtime.imap_client import Imap4Client, ImapClient, ImapError
from mail_desk_runtime.imap_client import probe_login as imap_probe
from mail_desk_runtime.inbound_tap import publish as publish_inbound
from mail_desk_runtime.mime import parse_inbound, strip_quoted_reply
from mail_desk_runtime.settings import (
    ACTIVATION_OFF,
    MailDeskSettings,
    get_settings,
    load_credentials,
    load_raw_settings,
    reload_settings,
)
from mail_desk_runtime.smtp_client import SmtplibSender
from mail_desk_runtime.smtp_client import probe_login as smtp_probe
from mail_desk_runtime.writes import SendRefused, live_writes_disabled

logger = logging.getLogger(__name__)

PROVIDER = "mail-desk"
_APP = "gideonai-mail-desk"
_CURSOR_FILE = "imap_cursor.json"
#: Longest backoff between failed poll cycles. A mail server that is down for an hour must
#: not be retried every 60s, but the channel must recover without a restart.
_MAX_BACKOFF = 900.0


class MailDeskTransport(ChannelTransportProvider):
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        # Per-instance config wins for the non-secret connection fields; the persisted app
        # store is the normal source (read at start_inbound via reload_settings).
        self._config = dict(cfg)
        self._services: Any = None
        self._delivery: MailDeskDelivery | None = None
        self._poll_task: asyncio.Task | None = None
        self._stopping = False
        self._cursor = 0
        self._uidvalidity = 0
        # Test seams: inject a fake IMAP client factory / a fake SMTP sender, so no test
        # touches a socket. Dependency injection rather than monkeypatching the stdlib.
        self._client_factory: Any = None
        self._sender_factory: Any = None

    # ── identity ──

    @property
    def name(self) -> str:
        return PROVIDER

    @property
    def display_name(self) -> str:
        return "Mail Desk"

    def capabilities(self) -> ChannelCapabilities:
        """Honest capabilities — every ``True`` has an implementation behind it.

        * ``inbound`` → the IMAP poll loop in :meth:`start_inbound`.
        * ``threads`` → ``Message-ID``/``In-Reply-To``/``References`` chains, kept by
          :class:`~mail_desk_runtime.delivery.ThreadStore`.
        * ``attachments`` → ``MailDeskDelivery.upload_attachment`` adds a MIME part.
        * ``rich_text`` → ``MailDeskDelivery.deliver_rich`` sends an HTML alternative.
        * ``reactions`` → mail has no reaction concept.
        * ``typing_indicator`` → nothing to show between messages.
        * ``edits`` → **False, and this is how "streaming=false" is declared.** The shipped
          ``ChannelCapabilities`` dataclass has no ``streaming`` field; in every other
          channel a stream IS a repeatedly-edited message, so no-edits means no-streaming.
          The C3 row (streaming trio MUST-NOT here) is implemented as ``start_stream``
          returning ``""`` with no-op append/stop, and a test pins both halves of that
          mapping together.
        * ``max_text_len`` → 0 (unbounded): SMTP imposes no practical body limit that a
          chat reply would hit, so claiming a number would be a lie in the other direction.
        """
        return ChannelCapabilities(
            inbound=True, threads=True, attachments=True, reactions=False,
            edits=False, rich_text=True, typing_indicator=False, max_text_len=0,
        )

    # ── settings + credentials ──

    def _settings(self) -> MailDeskSettings:
        """Live settings with any per-instance config overlaid.

        The registry builds this provider with the app store's dict, and a user editing the
        Configure form writes that same store — so the overlay only matters for a test or a
        second instance handed an explicit dict. Both routes go through
        :meth:`MailDeskSettings.from_dict`, so instance config gets the SAME coercion the
        stored config does — an overlaid port or cadence can't skip validation."""
        if not self._config:
            return get_settings()
        return MailDeskSettings.from_dict({**load_raw_settings(), **self._config})

    async def connect(self) -> bool:
        settings = self._settings()
        return settings.inbound_configured or settings.outbound_configured

    async def disconnect(self) -> None:
        return None

    @property
    def connected(self) -> bool:
        settings = self._settings()
        return settings.inbound_configured or settings.outbound_configured

    # ── UID cursor persistence (resume the poll across restarts) ──

    def _cursor_path(self) -> _Path:
        return app_data_dir(_APP) / _CURSOR_FILE

    def _load_cursor(self) -> tuple[int, int]:
        """``(last_uid, uidvalidity)`` from the app's data dir; ``(0, 0)`` when absent."""
        try:
            data = json.loads(self._cursor_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0, 0
        if not isinstance(data, dict):
            return 0, 0
        try:
            return int(data.get("last_uid", 0)), int(data.get("uidvalidity", 0))
        except (TypeError, ValueError):
            return 0, 0

    def _save_cursor(self, last_uid: int, uidvalidity: int) -> None:
        from gideon.sdk.channel import atomic_write

        try:
            atomic_write(
                self._cursor_path(),
                json.dumps({"last_uid": int(last_uid), "uidvalidity": int(uidvalidity)}) + "\n",
            )
        except OSError:
            logger.debug("mail-desk: failed to persist IMAP cursor", exc_info=True)

    # ── factories (real, or the injected test fakes) ──

    def _make_client(self, settings: MailDeskSettings, password: str) -> ImapClient:
        if self._client_factory is not None:
            return self._client_factory(settings, password)
        return Imap4Client(
            settings.imap_host, settings.imap_port, settings.imap_user, password,
            use_ssl=settings.imap_use_ssl,
        )

    def _make_sender(self, settings: MailDeskSettings, password: str) -> Any:
        if self._sender_factory is not None:
            return self._sender_factory(settings, password)
        return SmtplibSender(
            settings.smtp_host, settings.smtp_port, settings.smtp_user, password,
            security=settings.smtp_security,
        )

    # ── Inbound: the gateway drives this once at boot ──

    async def start_inbound(self, services: Any) -> None:
        self._services = services
        settings = reload_settings()
        imap_pass, smtp_pass = load_credentials()

        # Register outbound delivery on the gateway + dashboard. Core delivers every
        # channel result through this ONE provider-agnostic ChannelDelivery handle — it
        # never sees an SMTP client. Registered even when inbound can't start, so a
        # send-only configuration still receives cron/heartbeat results.
        if settings.outbound_configured and smtp_pass:
            self._delivery = MailDeskDelivery(
                self._make_sender(settings, smtp_pass),
                settings.mailbox_address,
                owner_id=settings.mailbox_address,
                threads=ThreadStore(),
            )
            if hasattr(services, "register_channel_delivery"):
                services.register_channel_delivery(self._delivery)
            if getattr(services, "dashboard_state", None) is not None:
                services.dashboard_state.channel_delivery = self._delivery
        else:
            logger.info("MailDeskTransport: SMTP not configured — outbound delivery unavailable")

        if not (settings.inbound_configured and imap_pass):
            logger.info("MailDeskTransport: IMAP not configured — inbound stays offline")
            return
        if settings.dm_activation == ACTIVATION_OFF:
            logger.info("MailDeskTransport: dm_activation=off — inbound disabled by settings")
            return

        self._cursor, self._uidvalidity = self._load_cursor()
        self._stopping = False
        self._poll_task = asyncio.ensure_future(self._poll_loop())
        logger.info(
            "MailDeskTransport: IMAP poll inbound started (folder=%s cursor=%d every %ds)",
            settings.folder, self._cursor, settings.poll_secs,
        )

    async def stop_inbound(self) -> None:
        self._stopping = True
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(self._poll_task), timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception:
                logger.debug("mail-desk: poll task stop error", exc_info=True)

    async def _poll_loop(self) -> None:
        """Poll IMAP on the configured cadence. Degrades on error, never crashes."""
        backoff = 0.0
        while not self._stopping:
            settings = get_settings()
            try:
                await self._poll_once(settings)
                backoff = 0.0
                delay = float(settings.poll_secs)
            except asyncio.CancelledError:
                raise
            except Exception:
                backoff = min(backoff * 2, _MAX_BACKOFF) if backoff else float(settings.poll_secs)
                logger.warning(
                    "mail-desk: poll cycle failed — retrying in %ss", backoff, exc_info=True
                )
                delay = backoff
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise

    async def _poll_once(self, settings: MailDeskSettings) -> None:
        """One IMAP cycle: fetch new UIDs, dispatch each, advance the cursor.

        The blocking IMAP work happens in a thread executor and returns plain data; the
        dispatch (which touches the trust seam, sessions and the event loop) happens back on
        the loop. That split is the reason no ``imaplib`` call can stall the gateway."""
        imap_pass, _ = load_credentials()
        if not imap_pass:
            logger.warning("mail-desk: no IMAP password in the credential store — cannot poll")
            return

        fetched, uidvalidity, reset_to = await asyncio.to_thread(
            self._fetch_batch, settings, imap_pass, self._cursor, self._uidvalidity
        )

        # UIDVALIDITY changed ⇒ every stored UID is meaningless. The worker already
        # re-derived the newest UID under the NEW numbering (it must, because a search from
        # the stale cursor would return nothing and leave the mailbox skipped forever). 0
        # means "server didn't report it" — not a change.
        if reset_to is not None:
            logger.warning(
                "mail-desk: UIDVALIDITY changed (%d → %d) — cursor reset to %d",
                self._uidvalidity, uidvalidity, reset_to,
            )
            self._cursor = reset_to
            self._uidvalidity = uidvalidity
            self._save_cursor(self._cursor, self._uidvalidity)
            return
        if uidvalidity and not self._uidvalidity:
            self._uidvalidity = uidvalidity

        advanced = False
        try:
            for uid, raw in fetched:
                # Advance PAST this uid BEFORE dispatch so a handler that dies can't wedge
                # the loop on the same message forever (the offset-before-dispatch rule the
                # Telegram/Discord transports follow). ``except Exception`` covers an
                # ordinary handler error; the ``finally`` below covers the rest, because
                # ``asyncio.CancelledError`` is a BaseException and would otherwise carry
                # the whole batch's advance away unsaved — and every message in it would be
                # replayed on the next boot.
                if uid > self._cursor:
                    self._cursor = uid
                    advanced = True
                try:
                    await self._dispatch(raw, uid, settings)
                except Exception:
                    logger.warning(
                        "mail-desk: message dispatch failed for uid %s", uid, exc_info=True
                    )
        finally:
            if advanced:
                self._save_cursor(self._cursor, self._uidvalidity)

    def _fetch_batch(
        self, settings: MailDeskSettings, password: str, last_uid: int, known_uidvalidity: int
    ) -> tuple[list[tuple[int, bytes]], int, int | None]:
        """BLOCKING: connect, select, search, fetch.

        Returns ``([(uid, raw)], uidvalidity, reset_to)``. ``reset_to`` is ``None`` in the
        normal case and the mailbox's newest UID when UIDVALIDITY changed — the check
        happens HERE, before the search, because a search from the stale cursor under new
        numbering returns nothing and would leave the cursor (and the mailbox) stuck
        forever.

        Runs on a worker thread. A per-UID fetch that comes back empty STOPS the batch so
        the cursor never advances past a message we never read — the next cycle resumes
        there."""
        out: list[tuple[int, bytes]] = []
        uidvalidity = 0
        client = self._make_client(settings, password)
        try:
            client.connect()
            uidvalidity = client.select_folder(settings.folder)
            if uidvalidity and known_uidvalidity and uidvalidity != known_uidvalidity:
                # Re-derive the cursor from scratch under the new numbering: the newest
                # UID, so the renumbered mailbox neither replays its whole history nor
                # stays permanently skipped.
                newest = max(client.fetch_uids_since(settings.folder, 0), default=0)
                return out, uidvalidity, newest
            for uid in client.fetch_uids_since(settings.folder, last_uid):
                raw = client.fetch_message(settings.folder, uid)
                if not raw:
                    logger.debug("mail-desk: empty fetch for uid %s — pausing at cursor", uid)
                    break
                out.append((uid, raw))
        except ImapError as exc:
            logger.warning("mail-desk: IMAP poll failed: %s — will retry next cycle", exc)
        finally:
            try:
                client.close()
            except Exception:
                logger.debug("mail-desk: IMAP client close error", exc_info=True)
        return out, uidvalidity, None

    # ── per-message handling ──

    def _is_self_authored(self, from_addr: str, settings: MailDeskSettings) -> bool:
        """Whether this message is our own outbound mail coming back.

        Most providers file a copy of sent mail into the account, and an auto-responder
        anywhere in the chain mails straight back at us. Without this the agent answers its
        own reply, forever. Matched on the ADDRESS (not a remembered message id) so a third
        party's vacation responder quoting our address is caught too."""
        mailbox = (settings.mailbox_address or "").strip().lower()
        return bool(mailbox) and from_addr.strip().lower() == mailbox

    def _to_channel_message(self, mail: Any) -> ChannelMessage:
        """Normalize an :class:`InboundMail` to the canonical inbound shape.

        ``channel_id`` is the correspondent's address — that IS how core addresses a reply
        back — and ``thread_id`` is the chain root, which is the session key."""
        return ChannelMessage(
            channel_id=mail.from_addr,
            text=mail.body,
            sender=mail.from_addr,
            thread_id=mail.thread_root,
            message_id=mail.message_id,
            ts=mail.ts,
            attachments=[{"name": name} for name in mail.attachments],
            metadata={
                "sender_name": mail.from_name,
                "subject": mail.subject,
                "uid": str(mail.uid),
                "references": mail.references,
                "in_reply_to": mail.in_reply_to,
            },
        )

    async def _dispatch(self, raw: bytes, uid: int, settings: MailDeskSettings) -> None:
        """Run one raw message through parse → self-filter → trust → session."""
        mail = parse_inbound(raw, uid)
        if mail is None:
            return  # fail-closed: unparseable / no From ⇒ nothing surfaces
        if self._is_self_authored(mail.from_addr, settings):
            logger.debug("mail-desk: dropped our own message uid %s", uid)
            return

        # Only the new text of a reply becomes the turn — the quoted history below it is
        # the previous conversation (often including our own words).
        text = strip_quoted_reply(mail.body).strip()
        if not text:
            return  # nothing to act on (an empty body, or attachments only)

        cm = self._to_channel_message(mail)

        # A reply from a not-yet-allowed sender that CONTAINS an active pairing code
        # redeems it ("pairing code = a reply containing the code"). Checked before the
        # door so the pairing reply doesn't get the canned nudge again — the code is
        # searched INSIDE the body, which core's whole-message digit check cannot do
        # through a mail client's quoting and signature.
        if await self._try_pairing(cm, text, settings):
            return

        # Remember the inbound message FIRST so any reply — the agent's, or the door's
        # canned nudge — threads under the sender's own message.
        if self._delivery is not None:
            self._delivery.note_inbound(mail)
            # An ALLOWED sender's body may carry an approval reply-token; resolving it
            # consumes the message (it is an answer, not a new turn). Gated on the
            # allowlist read, never on the raw body: an unknown sender must not be able to
            # resolve an approval by mailing a token.
            from gideon.sdk.channel import is_allowed_sender

            if is_allowed_sender(PROVIDER, cm.sender) and self._delivery.resolve_reply_token(text):
                return

        # The guarded door (EA-7). Core applies the trust gate, the non-owner-content fence,
        # redaction, session linking and the turn itself — the routing this transport used
        # to carry a copy of. Mail to our mailbox is a direct message by construction (no
        # "room" concept), so is_dm is always True. This transport keeps only the outbound
        # half: the canned reply, delivered in-thread so the nudge lands under the sender's
        # own message.
        cm_for_door = cm if text == cm.text else replace(cm, text=text)
        verdict = await self._services.deliver_channel_inbound(
            PROVIDER, cm_for_door, is_dm=True
        )
        if verdict.canned_reply and self._delivery is not None:
            try:
                await self._delivery.deliver_text(
                    cm.channel_id, verdict.canned_reply, cm.thread_id
                )
            except Exception:
                logger.debug("mail-desk: canned reply send failed", exc_info=True)

        # CE-10: hand the mail to this bundle's own trigger source, if one is attached.
        # AFTER the door and gated on `verdict.allowed`, which is the entire security
        # property: a `From` address is trivially forged, so only core's allowlist decision
        # may admit a sender — and a denied sender must arm no automation either. The
        # quote-STRIPPED `text` is published rather than `cm.text`, so a reply's event
        # carries its new prose and not the history of our own previous words. The tap
        # never raises and knows nothing about events; see inbound_tap's docstring.
        if verdict.allowed:
            publish_inbound(cm, text=text)

    async def _try_pairing(self, cm: ChannelMessage, text: str, settings: MailDeskSettings) -> bool:
        """Redeem a pairing code found in *text*. Returns whether pairing happened.

        Pairing on this channel is "a reply containing the code", so the code is searched
        for inside the body rather than required to be the whole message — a mail client's
        quoting and signature make an exact-match rule unusable."""
        from gideon.sdk.channel import is_allowed_sender

        if is_allowed_sender(PROVIDER, cm.sender):
            return False
        import re

        for candidate in re.findall(r"\b\d{8}\b", text):
            if redeem_pairing_code(PROVIDER, cm.sender, candidate):
                if self._delivery is not None:
                    try:
                        await self._delivery.deliver_text(
                            cm.channel_id,
                            "Paired — you can talk to me by replying to this thread.",
                            cm.thread_id,
                        )
                    except Exception:
                        logger.debug("mail-desk: pairing confirmation send failed", exc_info=True)
                logger.info("mail-desk: sender %s paired via code", cm.sender)
                return True
        return False

    # ── outbound / health ──

    async def send(self, message: OutboundMessage) -> bool | SendRefused:
        """Send one message. Used by the generic Channels surface, not the reply path.

        ``True`` delivered, ``False`` failed/unconfigured, or a :class:`SendRefused` when
        the platform's live-writes kill switch is on. The refusal is checked AFTER the
        configuration gate on purpose: an unconfigured transport could not have written
        anything, so reporting "refused" there would claim the guard suppressed a write
        that was never possible. Only a transport that WOULD have transmitted reports a
        refusal.
        """
        settings = self._settings()
        _, smtp_pass = load_credentials()
        if not (settings.outbound_configured and smtp_pass):
            return False
        # DISABLE_LIVE_WRITES (§1.4). An SMTP hand-off is the least reversible write this
        # repo makes — once the server accepts the message there is no recall — so it is
        # squarely the class core refuses for non-GET egress and model deletion. Typed
        # refusal, never a silent no-op: a test (or an operator) asserting a send must be
        # able to see that the guard, not the network, stopped it. Checked BEFORE the
        # sender is constructed so a suppressed send opens no SMTP session.
        if live_writes_disabled():
            refusal = SendRefused(channel=PROVIDER, target=message.channel_id)
            logger.warning("MailDeskTransport.send refused: %s", refusal)
            return refusal
        delivery = self._delivery or MailDeskDelivery(
            self._make_sender(settings, smtp_pass),
            settings.mailbox_address,
            owner_id=settings.mailbox_address,
        )
        sent = await delivery.deliver_text(message.channel_id, message.text, message.thread_id)
        return bool(sent)

    async def health(self) -> dict[str, Any]:
        settings = self._settings()
        if not settings.inbound_configured and not settings.outbound_configured:
            return {"state": "offline", "detail": "No IMAP/SMTP configuration"}
        imap_pass, smtp_pass = load_credentials()
        missing = []
        if settings.inbound_configured and not imap_pass:
            missing.append("IMAP password")
        if settings.outbound_configured and not smtp_pass:
            missing.append("SMTP password")
        if missing:
            return {"state": "error", "detail": f"Missing: {', '.join(missing)}"}
        halves = []
        if settings.inbound_configured:
            halves.append(f"IMAP {settings.imap_host}")
        if settings.outbound_configured:
            halves.append(f"SMTP {settings.smtp_host}")
        return {"state": "ready", "detail": " · ".join(halves)}

    async def test(self) -> dict[str, Any]:
        """The Channels-page Test action: probe = login+select.

        Both halves are probed — IMAP login plus a SELECT of the polled folder, and an SMTP
        login — because a channel with one working half is still broken, and the two most
        common misconfigurations (wrong folder, SMTP port/security mismatch) each hide
        behind a green login on the other protocol. Both probes block, so both run in a
        thread executor."""
        settings = self._settings()
        imap_pass, smtp_pass = load_credentials()
        results: list[str] = []
        ok = True

        if settings.inbound_configured:
            if not imap_pass:
                ok = False
                results.append("IMAP: no password configured")
            else:
                good, detail = await asyncio.to_thread(
                    imap_probe, settings.imap_host, settings.imap_port, settings.imap_user,
                    imap_pass, settings.folder, use_ssl=settings.imap_use_ssl,
                )
                ok = ok and good
                results.append(detail)
        if settings.outbound_configured:
            if not smtp_pass:
                ok = False
                results.append("SMTP: no password configured")
            else:
                good, detail = await asyncio.to_thread(
                    smtp_probe, settings.smtp_host, settings.smtp_port, settings.smtp_user,
                    smtp_pass, security=settings.smtp_security,
                )
                ok = ok and good
                results.append(detail)

        if not results:
            return {"ok": False, "detail": "No IMAP/SMTP configuration"}
        return {"ok": ok, "detail": " · ".join(results)}


def create_provider(config: dict[str, Any] | None = None) -> "MailDeskTransport":
    return MailDeskTransport(config)
