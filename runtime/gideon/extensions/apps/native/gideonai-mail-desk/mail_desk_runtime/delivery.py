"""MailDeskDelivery — the app-side ChannelDelivery the gateway delivers through.

All mail rendering (subject construction, MIME assembly, the HTML alternative, the
reply-token approval prompt) lives HERE, so core delivers plain text plus structured intent
and never imports a mail module. The transport registers an instance onto the gateway +
dashboard at ``start_inbound``.

**Every send crosses a thread boundary.** ``smtplib`` blocks, so :meth:`_send` hands the
message to :func:`asyncio.to_thread`; nothing in this module touches a socket on the event
loop. That is not a nicety — one blocking ``sendmail`` against a slow relay would freeze
every session and WebSocket in the gateway for the duration.

**There is no streaming.** The C3 table marks the streaming trio MUST-NOT for this
channel, and rightly: a "live-updating message" would mean re-sending a mail per token.
:meth:`start_stream` returns ``""`` and the append/stop calls are no-ops, which is exactly
what core's mirror path checks before animating (``_mirror_stream_ts = await
start_stream(...) or ""``). Because ``ChannelCapabilities`` has no ``streaming`` field,
that falsity is declared as ``edits=False`` — in every other channel streaming rides on
message edits, so no-edits IS no-streaming. The transport's ``capabilities()`` docstring
and a test pin that mapping.

**Threading is the channel's identity.** :class:`ThreadStore` remembers, per thread root
``Message-ID``, the last message id in the chain, the accumulated ``References``, the
subject and the correspondent. An outbound reply sets ``In-Reply-To`` to the last id and
``References`` to the chain, then records its own id — so the third message in a
conversation references both prior ones, in order, and a mail client shows one thread.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from email.message import EmailMessage
from typing import Any
from urllib.parse import quote

from gideon.sdk.channel import (
    atomic_write,
    is_tracked_channel,
    redact_credentials,
    redact_exfiltration_urls,
)
from gideon.sdk.util import app_data_dir

from mail_desk_runtime.mime import build_outbound, build_references, reply_subject
from mail_desk_runtime.smtp_client import SmtpError, SmtpSender

logger = logging.getLogger(__name__)

_APP = "gideonai-mail-desk"
_THREADS_FILE = "threads.json"
#: Bound the persisted thread map so a long-lived mailbox can't grow it without limit.
#: Oldest entries age out; a thread that falls out simply starts a fresh chain.
MAX_THREADS = 500
#: Approval prompts wait this long for the owner's reply mail before defaulting to
#: rejected (mirrors Slack/Telegram/Discord's 2h ceiling). Mail is slower than a button
#: press, but an approval that outlives the operation it guards is worse than a denial.
_APPROVAL_TIMEOUT = 7200
#: Reply-token vocabulary. The owner replies with either word plus the token.
APPROVE_WORD = "APPROVE"
DENY_WORD = "DENY"
_TOKEN_BYTES = 4  # 8 hex chars — short enough to retype, wide enough not to collide


def _safe(text: str) -> str:
    """Redact before anything reaches the wire (exfil URLs, then credentials).

    Every delivery path funnels through here — an unredacted path is the whole class of bug
    this centralization prevents."""
    body, _ = redact_exfiltration_urls(text or "")
    body, _ = redact_credentials(body)
    return body


class ThreadState:
    """What one mail thread needs for the next reply to land in the same conversation."""

    __slots__ = ("root", "last_message_id", "references", "subject", "correspondent", "name")

    def __init__(
        self, root: str, *, last_message_id: str = "", references: str = "",
        subject: str = "", correspondent: str = "", name: str = "",
    ) -> None:
        self.root = root
        self.last_message_id = last_message_id
        self.references = references
        self.subject = subject
        self.correspondent = correspondent
        self.name = name

    def to_dict(self) -> dict[str, str]:
        return {
            "last_message_id": self.last_message_id,
            "references": self.references,
            "subject": self.subject,
            "correspondent": self.correspondent,
            "name": self.name,
        }

    @classmethod
    def from_dict(cls, root: str, data: dict[str, Any]) -> "ThreadState":
        return cls(
            root,
            last_message_id=str(data.get("last_message_id", "")),
            references=str(data.get("references", "")),
            subject=str(data.get("subject", "")),
            correspondent=str(data.get("correspondent", "")),
            name=str(data.get("name", "")),
        )


class ThreadStore:
    """Per-thread reply state, persisted so a restart keeps threading correctly.

    Without persistence an outbound-initiated delivery after a restart (a cron result into
    an existing thread) would have no parent id and would start a NEW conversation in the
    user's client — the thread state is as load-bearing as the UID cursor."""

    def __init__(self, path_provider: Any = None) -> None:
        self._threads: dict[str, ThreadState] = {}
        self._path_provider = path_provider or (lambda: app_data_dir(_APP) / _THREADS_FILE)
        self._loaded = False

    def _path(self):
        return self._path_provider()

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            data = json.loads(self._path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        for root, entry in data.items():
            if isinstance(entry, dict):
                self._threads[str(root)] = ThreadState.from_dict(str(root), entry)

    def _persist(self) -> None:
        # Trim oldest-first (dict preserves insertion order) before writing.
        if len(self._threads) > MAX_THREADS:
            for root in list(self._threads)[: len(self._threads) - MAX_THREADS]:
                del self._threads[root]
        try:
            payload = {root: st.to_dict() for root, st in self._threads.items()}
            atomic_write(self._path(), json.dumps(payload) + "\n")
        except OSError:
            logger.debug("mail-desk: failed to persist thread state", exc_info=True)

    def get(self, root: str) -> ThreadState | None:
        self._ensure_loaded()
        return self._threads.get(root)

    def note_message(
        self, root: str, *, message_id: str, references: str = "", subject: str = "",
        correspondent: str = "", name: str = "",
    ) -> ThreadState:
        """Record a message (inbound or outbound) as the newest link in *root*'s chain."""
        self._ensure_loaded()
        st = self._threads.pop(root, None) or ThreadState(root)
        if message_id:
            st.last_message_id = message_id
            st.references = build_references(references or st.references, message_id)
        elif references:
            st.references = references
        if subject:
            st.subject = subject
        if correspondent:
            st.correspondent = correspondent
        if name:
            st.name = name
        # Re-insert at the end so the trim in _persist drops genuinely-cold threads.
        self._threads[root] = st
        self._persist()
        return st


class _PendingApproval:
    __slots__ = ("future", "token", "request_id", "channel")

    def __init__(self, request_id: str, token: str, channel: str) -> None:
        self.future: asyncio.Future = asyncio.get_event_loop().create_future()
        self.token = token
        self.request_id = request_id
        self.channel = channel


class MailDeskDelivery:
    """Renders + delivers gateway results over SMTP. Implements ChannelDelivery."""

    def __init__(
        self, sender: SmtpSender, from_addr: str, owner_id: str = "",
        threads: ThreadStore | None = None,
    ) -> None:
        self._sender = sender
        self._from = from_addr
        self._owner_id = owner_id
        self._threads = threads if threads is not None else ThreadStore()
        # keyed by the uppercase reply token; the transport matches an inbound body
        # against these to resolve an approval.
        self._pending: dict[str, _PendingApproval] = {}

    # ── thread bookkeeping (the transport feeds inbound; sends feed themselves) ──

    @property
    def threads(self) -> ThreadStore:
        return self._threads

    def note_inbound(self, mail: Any) -> None:
        """Record an inbound message so the next reply threads onto it."""
        root = getattr(mail, "thread_root", "") or getattr(mail, "message_id", "")
        if not root:
            return
        self._threads.note_message(
            root,
            message_id=getattr(mail, "message_id", ""),
            references=getattr(mail, "references", ""),
            subject=getattr(mail, "subject", ""),
            correspondent=getattr(mail, "from_addr", ""),
            name=getattr(mail, "from_name", ""),
        )

    # ── the one send path ──

    async def _send(self, msg: EmailMessage) -> bool:
        """Hand one built message to SMTP in a thread executor. Never raises."""
        try:
            await asyncio.to_thread(self._sender.send, msg)
            return True
        except SmtpError as exc:
            logger.warning("mail-desk: send failed: %s", exc)
            return False
        except Exception:
            logger.warning("mail-desk: unexpected send failure", exc_info=True)
            return False

    async def _deliver(
        self, channel: str, thread_ts: str, subject: str, body: str, *,
        html_body: str = "", attachments: list[tuple[str, str, bytes]] | None = None,
    ) -> str:
        """Build + send one message into *channel*, threaded onto *thread_ts*.

        Returns the sent ``Message-ID`` (the "ts" core threads follow-ups on), or ``""``
        when there was nothing to send to or the send failed."""
        to_addr = self._resolve_recipient(channel, thread_ts)
        if not to_addr:
            logger.debug("mail-desk: no recipient for channel=%r thread=%r", channel, thread_ts)
            return ""

        state = self._threads.get(thread_ts) if thread_ts else None
        in_reply_to = state.last_message_id if state else ""
        references = build_references(
            state.references if state else "", in_reply_to
        ) if state else ""
        subj = subject or (reply_subject(state.subject) if state else "Gideon")

        msg = build_outbound(
            from_addr=self._from, to_addr=to_addr, subject=subj, body=_safe(body),
            html_body=_safe(html_body) if html_body else "",
            in_reply_to=in_reply_to, references=references, attachments=attachments,
        )
        if not await self._send(msg):
            return ""

        sent_id = str(msg["Message-ID"])
        # Record OUR message as the newest link, so the next reply in this thread
        # references it too — this is what makes three-message continuity hold.
        root = thread_ts or sent_id
        self._threads.note_message(
            root, message_id=sent_id, references=references, subject=subj, correspondent=to_addr
        )
        return sent_id

    def _resolve_recipient(self, channel: str, thread_ts: str = "") -> str:
        """The address to send to: the channel id when it is one, else the thread's peer.

        Core addresses a channel by the id the transport put on ``ChannelMessage`` — here
        that IS the correspondent's address. A caller that only has a thread id (a
        notification threaded onto an old conversation) falls back to the recorded peer,
        then to the owner."""
        if channel and "@" in channel:
            return channel.strip()
        if thread_ts:
            state = self._threads.get(thread_ts)
            if state is not None and state.correspondent:
                return state.correspondent
        return self._owner_id if "@" in (self._owner_id or "") else ""

    # ── DM resolution ──

    async def open_dm(self, user_id: str) -> str:
        """A mail "DM channel" IS the address — there is nothing to open.

        Returns "" for a non-address so a caller degrades instead of handing an opaque user
        id to SMTP as a recipient."""
        addr = str(user_id or "").strip()
        return addr if "@" in addr else ""

    # ── text / rich ──

    async def deliver_text(
        self, channel: str, text: str, thread_ts: str = "", *,
        unfurl_links: bool | None = None, unfurl_media: bool | None = None,
        reply_broadcast: bool | None = None,
    ) -> str:
        """Post plain text. The link-preview/broadcast hints have no mail analogue."""
        return await self._deliver(channel, thread_ts, "", text)

    async def deliver_rich(
        self, channel: str, payload: Any, fallback_text: str, *,
        thread_ts: str = "", unfurl_links: bool = True, unfurl_media: bool = True,
        reply_broadcast: bool = False,
    ) -> str:
        """Deliver a rich payload as an HTML alternative (C3: MAY, and we do).

        A caller handing through ``{"html": "<p>…</p>"}`` gets it as the HTML part with
        ``fallback_text`` as the plain part; anything else falls back to plain text only,
        per the contract."""
        html = ""
        if isinstance(payload, dict) and isinstance(payload.get("html"), str):
            html = payload["html"]
        return await self._deliver(channel, thread_ts, "", fallback_text, html_body=html)

    async def deliver_cron_result(
        self, channel: str, job_name: str, job_id: str, text: str, thread_ts: str = ""
    ) -> str:
        subject = f"[Gideon] Cron: {job_name}"
        return await self._deliver(channel, thread_ts, subject, text)

    async def deliver_notification(
        self, channel: str, title: str, text: str, thread_ts: str = ""
    ) -> str:
        """Deliver a titled notification. This is also plan 42's ``channel_dm`` / digest
        delivery target for this channel — see the README's deferral note."""
        return await self._deliver(channel, thread_ts, f"[Gideon] {title}", text)

    async def deliver_chat_mirror(self, channel: str, text: str, thread_ts: str = "") -> None:
        """Mirror a dashboard reply into the mail thread.

        A trailing ``[OPTIONS: …]`` block becomes a numbered list the recipient answers by
        replying — mail's only interactive affordance is a reply, so rendering the options
        as anything else would promise a button that does not exist."""
        from gideon.sdk.channel import extract_options

        body, options = extract_options(_safe(text))
        if options:
            listed = "\n".join(f"  {i + 1}. {opt}" for i, opt in enumerate(options))
            body = f"{body}\n\nReply with one of:\n{listed}"
        await self._deliver(channel, thread_ts, "", body)

    async def deliver_subagent_reply(
        self, channel: str, text: str, thread_ts: str = "", elapsed_secs: float = 0.0
    ) -> None:
        body = text
        if elapsed_secs:
            body = f"{text}\n\n(took {elapsed_secs:.1f}s)"
        await self._deliver(channel, thread_ts, "", body)

    # ── identity resolution (mail gives no directory; be honest) ──

    async def resolve_user_name(self, user_id: str) -> str:
        """A display name if a thread ever carried one, else the address itself."""
        addr = str(user_id or "").strip()
        for root in reversed(list(self._threads._threads)):  # newest thread first
            st = self._threads.get(root)
            if st is not None and st.correspondent == addr.lower() and st.name:
                return st.name
        return addr

    async def resolve_user_profile(self, user_id: str) -> dict:
        name = await self.resolve_user_name(user_id)
        return {"id": str(user_id), "name": name, "email": str(user_id)}

    async def channel_info(self, channel_id: str) -> dict:
        """A mail conversation is 1:1 with the correspondent, so it IS a DM."""
        return {"name": str(channel_id), "is_im": True}

    def list_reply_channels(self) -> list[dict]:
        """The addresses this delivery can post into for the dashboard picker.

        The allowed-sender list lives in the core trust seam (CE-1 owns it) and the SDK
        exposes only a membership check — no enumeration — so the picker offers the owner's
        own address when one is configured. Deliberately minimal, per the ChannelDelivery
        contract ("may be empty")."""
        if self._owner_id and "@" in self._owner_id:
            return [{"id": self._owner_id, "name": self._owner_id}]
        return []

    def is_tracked_channel(self, channel_id: str) -> bool:
        return is_tracked_channel("mail-desk", channel_id)

    def build_thread_link(self, channel: str, ts: str) -> str:
        """An RFC 2392 ``mid:`` anchor for a message id (C3: MAY, message-id anchor).

        ``mid:`` is the standard URL form for "this message", and mail clients that
        register the scheme jump straight to it. The angle brackets are stripped (they are
        ``Message-ID`` syntax, not part of the id) and the id is percent-encoded. Returns
        "" without a message id — honest over a URL that resolves to nothing."""
        mid = (ts or "").strip().strip("<>")
        if not mid:
            return ""
        return f"mid:{quote(mid, safe='@.')}"

    # ── attachments (C3: SHOULD — MIME parts) ──

    async def upload_attachment(
        self, channel: str, file_path: str, *, filename: str = "", thread_ts: str = "",
        title: str = "", initial_comment: str = "",
    ) -> str:
        """Attach a file as a MIME part on a message into the thread.

        Reading the file blocks, so it goes through the executor too — the same reason the
        send does."""
        import mimetypes
        import os

        name = filename or os.path.basename(file_path)
        try:
            payload = await asyncio.to_thread(_read_bytes, file_path)
        except OSError:
            logger.warning("mail-desk: cannot read attachment %s", file_path, exc_info=True)
            return ""
        mimetype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        body = initial_comment or title or f"Attached: {name}"
        return await self._deliver(
            channel, thread_ts, title or "", body, attachments=[(name, mimetype, payload)]
        )

    # ── streaming: MUST-NOT for this channel (C3). Explicit no-ops, not accidents. ──

    async def start_stream(self, channel: str, thread_ts: str = "", initial_text: str = "") -> str:
        """No streaming affordance: returns "" so core skips live animation entirely.

        Sending a mail per token would be absurd, and core's mirror path treats "" as "this
        channel cannot stream" (``await start_stream(...) or ""``)."""
        return ""

    async def append_stream_task(
        self, channel: str, stream_ts: str, task_id: str, title: str, status: str,
    ) -> None:
        """No-op: there is no in-flight message to append to (see :meth:`start_stream`)."""
        return None

    async def stop_stream(self, channel: str, stream_ts: str) -> None:
        """No-op: nothing was started (see :meth:`start_stream`)."""
        return None

    # ── approval via reply token (C3: SHOULD) ──

    async def request_approval(
        self, event: Any, *, source: str, parent_session_key: str = "",
        sessions: Any = None, on_prompted: Any = None,
    ) -> bool | None:
        """Mail the owner an approve/deny prompt and wait for their reply.

        Returns approved/rejected, or ``None`` when we can't prompt (no address) so the
        gateway falls back to the dashboard. The prompt carries a random token; the
        transport resolves this future when an inbound message from an ALLOWED sender
        contains ``APPROVE <token>`` or ``DENY <token>`` (:meth:`resolve_reply_token`).
        ``on_prompted(pending)`` lets core race a dashboard prompt against this one."""
        channel = ""
        thread_ts = ""
        if parent_session_key and sessions is not None:
            try:
                thread_ts, channel = sessions.get_channel_link(parent_session_key)
            except Exception:
                thread_ts, channel = "", ""
        channel = channel or self._owner_id
        if not channel or "@" not in channel:
            return None

        request_id = str(getattr(event, "request_id", ""))
        title = _safe(str(getattr(event, "title", "")))
        token = secrets.token_hex(_TOKEN_BYTES).upper()
        pending = _PendingApproval(request_id, token, channel)
        self._pending[token] = pending

        body = (
            f"Gideon needs your approval for a {source} action:\n\n"
            f"    {title}\n\n"
            f"Reply to this message with exactly one of:\n"
            f"    {APPROVE_WORD} {token}\n"
            f"    {DENY_WORD} {token}\n\n"
            f"No reply within {_APPROVAL_TIMEOUT // 3600}h counts as a denial."
        )
        sent = await self._deliver(
            channel, thread_ts or "", f"[Gideon] Approval needed: {title}"[:200], body
        )
        if not sent:
            self._pending.pop(token, None)
            return None

        if on_prompted:
            try:
                on_prompted(pending)
            except Exception:
                logger.debug("mail-desk: on_prompted hook failed", exc_info=True)

        try:
            outcome = await asyncio.wait_for(pending.future, timeout=_APPROVAL_TIMEOUT)
        except asyncio.TimeoutError:
            outcome = "rejected"
        finally:
            self._pending.pop(token, None)
        return outcome == "approved"

    def resolve_reply_token(self, text: str) -> bool:
        """Resolve a pending approval from a reply body. Returns whether one matched.

        The token must appear alongside the verb, so an unrelated mail that happens to
        contain the word "approve" cannot decide anything. Called by the transport ONLY for
        a sender the trust seam already allowed — an approval is the highest-value thing a
        channel can carry, so it never rides an unauthenticated message."""
        upper = (text or "").upper()
        for token, pending in list(self._pending.items()):
            if token not in upper:
                continue
            approved = f"{APPROVE_WORD} {token}" in upper or f"{APPROVE_WORD}{token}" in upper
            denied = f"{DENY_WORD} {token}" in upper or f"{DENY_WORD}{token}" in upper
            if not approved and not denied:
                continue
            if not pending.future.done():
                # An explicit DENY wins over a body that somehow contains both — a request
                # to stop must never be read as consent.
                pending.future.set_result("rejected" if denied else "approved")
            return True
        return False


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()
