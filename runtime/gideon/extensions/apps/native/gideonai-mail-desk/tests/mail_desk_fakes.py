"""Test doubles: a fake IMAP server, a fake SMTP server, and RFC822 builders.

Named with a non-``test_*`` module name so pytest doesn't collect it. The apps boundary
lint does NOT skip this file (only ``__pycache__`` and ``test_*.py`` are skipped), so it
imports **stdlib only** at module level — no ``gideon`` import of any depth.

Both fakes are injected, never monkeypatched onto the stdlib: the transport takes a
``_client_factory`` and a ``_sender_factory``, and the delivery takes a sender. Nothing
here opens a socket, sleeps on wall-clock, or writes outside a tmp dir.
"""

from __future__ import annotations

from email.message import EmailMessage


class FakeImapServer:
    """An in-memory IMAP mailbox: ``{folder: {uid: raw_bytes}}`` plus a UIDVALIDITY.

    Implements the narrow ``ImapClient`` protocol the transport depends on
    (connect/select_folder/fetch_uids_since/fetch_message/close), and records every call
    so a test can assert *how* it was driven — notably that the search used a UID range
    and not a sequence range.
    """

    def __init__(
        self,
        messages: dict[str, dict[int, bytes]] | None = None,
        *,
        uidvalidity: int = 1,
        fail_connect: bool = False,
        empty_fetch_uids: set[int] | None = None,
    ) -> None:
        self.messages = messages if messages is not None else {}
        self.uidvalidity = uidvalidity
        self.fail_connect = fail_connect
        #: UIDs whose fetch returns b"" — the transient-failure case that must pause the
        #: cursor rather than skip the message.
        self.empty_fetch_uids = empty_fetch_uids or set()
        self.connected = False
        self.closed = False
        self.selected: list[str] = []
        self.search_calls: list[tuple[str, int]] = []
        self.fetch_calls: list[int] = []

    # ── the ImapClient protocol ──

    def connect(self) -> None:
        if self.fail_connect:
            from mail_desk_runtime.imap_client import ImapError

            raise ImapError("fake: connect refused")
        self.connected = True

    def select_folder(self, folder: str) -> int:
        self.selected.append(folder)
        return self.uidvalidity

    def fetch_uids_since(self, folder: str, last_uid: int) -> list[int]:
        self.search_calls.append((folder, last_uid))
        return sorted(u for u in self.messages.get(folder, {}) if u > last_uid)

    def fetch_message(self, folder: str, uid: int) -> bytes:
        self.fetch_calls.append(uid)
        if uid in self.empty_fetch_uids:
            return b""
        return self.messages.get(folder, {}).get(uid, b"")

    def close(self) -> None:
        self.closed = True

    # ── test helpers ──

    def add(self, uid: int, raw: bytes, folder: str = "INBOX") -> None:
        self.messages.setdefault(folder, {})[uid] = raw

    def expunge(self, uid: int, folder: str = "INBOX") -> None:
        """Delete a message. A sequence-number cursor would now be wrong; a UID one
        stays correct — which is what the UID-vs-sequence test exercises."""
        self.messages.get(folder, {}).pop(uid, None)


class FakeSmtpServer:
    """An in-memory SMTP sink implementing the ``SmtpSender`` protocol.

    Records every ``EmailMessage`` handed to it so a test can read the exact headers that
    went on the wire (``Message-ID``, ``In-Reply-To``, ``References``) — the threading
    contract is a header contract, so it is asserted on headers, not on a summary."""

    def __init__(self, *, fail: bool = False, fail_after: int | None = None) -> None:
        self.sent: list[EmailMessage] = []
        self.fail = fail
        #: Start failing once this many messages have been accepted.
        self.fail_after = fail_after

    def send(self, msg: EmailMessage) -> None:
        if self.fail or (self.fail_after is not None and len(self.sent) >= self.fail_after):
            from mail_desk_runtime.smtp_client import SmtpError

            raise SmtpError("fake: relay refused")
        self.sent.append(msg)

    # ── test helpers ──

    @property
    def last(self) -> EmailMessage:
        return self.sent[-1]

    def header(self, name: str, index: int = -1) -> str:
        return str(self.sent[index][name] or "")

    def body_text(self, index: int = -1) -> str:
        msg = self.sent[index]
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True) or b""
                    return payload.decode("utf-8", errors="replace")
            return ""
        payload = msg.get_payload(decode=True) or b""
        return payload.decode("utf-8", errors="replace")


def build_message(
    *,
    from_addr: str = "bob@example.com",
    to_addr: str = "agent@example.com",
    subject: str = "Hello",
    message_id: str = "<msg-1@example.com>",
    plain: str | None = "plain body",
    html: str | None = None,
    in_reply_to: str = "",
    references: str = "",
    date: str = "Mon, 09 Aug 2026 10:00:00 +0000",
    attachments: list[tuple[str, str, bytes]] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> bytes:
    """Build a raw RFC822 message. ``attachments`` are (filename, mimetype, payload)."""
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    if message_id:
        msg["Message-ID"] = message_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    if date:
        msg["Date"] = date
    for key, value in (extra_headers or {}).items():
        msg[key] = value

    if plain is not None and html is not None:
        msg.set_content(plain)
        msg.add_alternative(html, subtype="html")
    elif html is not None:
        msg.set_content(html, subtype="html")
    else:
        msg.set_content(plain if plain is not None else "")

    for filename, mimetype, payload in attachments or []:
        maintype, _, subtype = mimetype.partition("/")
        msg.add_attachment(
            payload, maintype=maintype, subtype=subtype or "octet-stream", filename=filename
        )
    return msg.as_bytes()


def raw_message(headers: str, body: str = "body") -> bytes:
    """A hand-rolled raw message, for header shapes ``EmailMessage`` refuses to build
    (an encoded-word display name carrying an address, a malformed From, no From)."""
    return (headers.rstrip("\r\n") + "\r\n\r\n" + body + "\r\n").encode("utf-8")


# ── dashboard-state / services stand-ins (mirroring telegram + discord) ──


class FakeSession:
    def __init__(self, key: str = "mail-1") -> None:
        self.key = key
        self.running = False
        self.task = None
        self.appended: list[tuple] = []
        self.queued: list[str] = []

    def append(self, role, text, cls) -> None:
        self.appended.append((role, text, cls))

    def queue_append(self, text) -> None:
        self.queued.append(text)


class FakeState:
    def __init__(self) -> None:
        self.session = FakeSession()
        self.linked: dict = {}
        self._background_tasks: set = set()
        self.notified: list = []
        self.channel_delivery = None
        self.linked_app = ""

    def get_linked_session(self, thread_key):
        return self.linked.get(thread_key)

    def get_or_create_session(self, app=""):
        self.linked_app = app
        return self.session

    def link_channel(self, key, thread_key, channel_id) -> None:
        self.linked[thread_key] = self.session

    def notify(self, *a, **k) -> None:
        self.notified.append((a, k))
