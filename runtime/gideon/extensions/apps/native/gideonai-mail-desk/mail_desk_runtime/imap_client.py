"""The blocking IMAP mechanics, behind a narrow protocol the transport can fake.

``imaplib`` is a **synchronous** API: every call blocks the calling thread on socket IO.
The transport therefore never calls this module from the event loop — it hands each
operation to a thread executor (``asyncio.to_thread``). A single blocking ``select()`` on
the loop would stall the whole gateway (every session, every WebSocket) for as long as
the mail server takes to answer.

Three IMAP facts shape this file:

* **UID, never sequence numbers.** Sequence numbers renumber on every expunge, so a
  cursor kept in them silently skips or reprocesses mail the moment the user deletes a
  message from another client. Every command here is a ``UID`` command, and the cursor is
  a UID.
* **``UID SEARCH n:*`` always returns at least one message.** The range is inclusive and
  the server clamps ``*`` to the highest existing UID, so searching ``(last+1):*`` when
  nothing is new still returns the last UID. The result is filtered to strictly greater
  than the cursor, which is what makes "no new mail" mean an empty list.
* **``imaplib`` has a line-length ceiling.** ``imaplib._MAXLINE`` bounds one response
  line; a large UID set or a big literal past it raises
  ``imaplib.IMAP4.error("got more than N bytes")``. It is raised once at import here, and
  every call is wrapped so the poll loop degrades instead of dying.

``UIDVALIDITY`` is checked on select: when a server renumbers a mailbox (a restore, a
migration) every UID becomes meaningless, and a cursor kept across that boundary would
skip the whole mailbox. The client reports the value so the transport can reset.
"""

from __future__ import annotations

import imaplib
import logging
import re
from typing import Protocol

logger = logging.getLogger(__name__)

# imaplib's default line cap truncates long IMAP responses (big UID sets, large messages)
# and raises "got more than N bytes". Raise it once at import; the calls below still
# handle the error, because a hostile/broken server can exceed any bound.
imaplib._MAXLINE = max(getattr(imaplib, "_MAXLINE", 0), 10_000_000)  # type: ignore[attr-defined]

#: Socket timeout for every IMAP operation. Without it a half-open connection parks a
#: worker thread forever and the poll loop never fires again.
IMAP_TIMEOUT_SECS = 60

_UIDVALIDITY_RE = re.compile(rb"UIDVALIDITY\s+(\d+)", re.IGNORECASE)


class ImapError(Exception):
    """Any IMAP transport/auth/protocol failure. The caller degrades, never crashes."""


class ImapClient(Protocol):
    """The narrow surface the transport needs. A fake in tests implements just this."""

    def connect(self) -> None: ...

    def select_folder(self, folder: str) -> int: ...

    def fetch_uids_since(self, folder: str, last_uid: int) -> list[int]: ...

    def fetch_message(self, folder: str, uid: int) -> bytes: ...

    def close(self) -> None: ...


class Imap4Client:
    """The real client — wraps ``imaplib.IMAP4_SSL`` (or plain ``IMAP4``).

    Every public method is BLOCKING and must be called from a thread executor."""

    def __init__(
        self, host: str, port: int, username: str, password: str, *, use_ssl: bool = True
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._use_ssl = use_ssl
        self._conn: imaplib.IMAP4 | None = None

    def connect(self) -> None:
        """Open the connection and log in. Raises :class:`ImapError` on any failure."""
        try:
            conn: imaplib.IMAP4 = (
                imaplib.IMAP4_SSL(self._host, self._port, timeout=IMAP_TIMEOUT_SECS)
                if self._use_ssl
                else imaplib.IMAP4(self._host, self._port, timeout=IMAP_TIMEOUT_SECS)
            )
            conn.login(self._username, self._password)
        except (imaplib.IMAP4.error, OSError) as exc:
            raise ImapError(f"IMAP connect/login failed: {exc}") from exc
        self._conn = conn

    def select_folder(self, folder: str) -> int:
        """Select *folder* read-only and return its ``UIDVALIDITY`` (0 if unreported).

        ``readonly=True``: polling must never set ``\\Seen`` or otherwise mutate the
        user's mailbox — the mail is still unread in their client after we answer it."""
        if self._conn is None:
            raise ImapError("not connected")
        try:
            typ, data = self._conn.select(folder, readonly=True)
        except (imaplib.IMAP4.error, OSError) as exc:
            raise ImapError(f"IMAP select {folder!r} failed: {exc}") from exc
        if typ != "OK":
            raise ImapError(f"IMAP select {folder!r} failed: {typ}")
        return self._read_uidvalidity(folder)

    def _read_uidvalidity(self, folder: str) -> int:
        """``UIDVALIDITY`` for the selected folder via ``STATUS``, or 0 if unavailable.

        A server that refuses STATUS (or answers oddly) yields 0, which the transport
        reads as "unknown" and treats as unchanged — a missing value must not look like a
        renumbering and wipe a good cursor."""
        if self._conn is None:
            return 0
        try:
            typ, data = self._conn.status(f'"{folder}"', "(UIDVALIDITY)")
        except (imaplib.IMAP4.error, OSError):
            logger.debug("mail-desk: IMAP STATUS UIDVALIDITY failed", exc_info=True)
            return 0
        if typ != "OK" or not data:
            return 0
        for part in data:
            raw = part if isinstance(part, (bytes, bytearray)) else str(part).encode()
            match = _UIDVALIDITY_RE.search(bytes(raw))
            if match:
                try:
                    return int(match.group(1))
                except ValueError:
                    return 0
        return 0

    def fetch_uids_since(self, folder: str, last_uid: int) -> list[int]:
        """UIDs in *folder* strictly greater than *last_uid*, ascending.

        ``last_uid=0`` means every message (UID 0 is never assigned, so ``1:*``)."""
        if self._conn is None:
            raise ImapError("not connected")
        self.select_folder(folder)
        start = max(0, last_uid) + 1
        try:
            typ, data = self._conn.uid("SEARCH", None, f"UID {start}:*")
        except (imaplib.IMAP4.error, OSError) as exc:
            raise ImapError(f"IMAP UID SEARCH failed: {exc}") from exc
        if typ != "OK" or not data:
            return []
        raw = data[0]
        if not raw:
            return []
        text = raw.decode("ascii", errors="replace") if isinstance(raw, bytes) else str(raw)
        uids: list[int] = []
        for tok in text.split():
            try:
                uid = int(tok)
            except ValueError:
                continue
            # "start:*" always returns at least the highest UID even when none are newer,
            # so filter to strictly-greater to honor the resume contract.
            if uid > last_uid:
                uids.append(uid)
        return sorted(set(uids))

    def fetch_message(self, folder: str, uid: int) -> bytes:
        """Raw RFC822 bytes for one UID (``b""`` when the server returns nothing).

        ``BODY.PEEK[]`` rather than ``RFC822``: a bare ``RFC822`` fetch sets ``\\Seen`` on
        most servers even inside a read-only select on some implementations, and a channel
        must not mark the user's mail read behind their back."""
        if self._conn is None:
            raise ImapError("not connected")
        self.select_folder(folder)
        try:
            typ, data = self._conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
        except (imaplib.IMAP4.error, OSError) as exc:
            raise ImapError(f"IMAP UID FETCH {uid} failed: {exc}") from exc
        if typ != "OK" or not data:
            return b""
        for part in data:
            if not (isinstance(part, tuple) and len(part) >= 2):
                continue
            if isinstance(part[1], (bytes, bytearray)):
                return bytes(part[1])
        return b""

    def close(self) -> None:
        """Log out, swallowing the usual teardown noise. Idempotent."""
        conn = self._conn
        self._conn = None
        if conn is None:
            return
        try:
            conn.logout()
        except (imaplib.IMAP4.error, OSError):
            logger.debug("mail-desk: IMAP logout error", exc_info=True)


def probe_login(
    host: str, port: int, username: str, password: str, folder: str, *, use_ssl: bool = True
) -> tuple[bool, str]:
    """The doctor/Test probe: connect + login + SELECT the folder. BLOCKING.

    A login alone proves the credential but not that the folder we poll exists, and a
    wrong folder name is the second most common misconfiguration after a wrong password."""
    client = Imap4Client(host, port, username, password, use_ssl=use_ssl)
    try:
        client.connect()
        client.select_folder(folder)
        return True, f"IMAP login OK; folder {folder!r} selectable"
    except ImapError as exc:
        return False, str(exc)
    finally:
        client.close()
