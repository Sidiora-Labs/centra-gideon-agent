"""The blocking SMTP mechanics, behind a narrow protocol the delivery can fake.

``smtplib`` is a **synchronous** API, exactly like ``imaplib``: every call blocks the
calling thread. :class:`~mail_desk_runtime.delivery.MailDeskDelivery` therefore hands each
send to a thread executor and never calls into this module from the event loop.

A connection is opened per send rather than held open. That is deliberate: providers drop
idle SMTP sessions aggressively (Gmail at ~a minute), so a cached connection is usually
dead by the time the next result needs delivering, and the failure surfaces as a lost
message instead of a retry. Sends here are occasional, not a stream.

**Authentication is an app password.** OAuth2 (XOAUTH2) is DEFERRED — see the DISCOVERY
note in the app README. Every provider whose flow the setup step documents
(Gmail/Fastmail/iCloud) issues a per-application password precisely for clients like
this, and it needs no token-refresh machinery, no client registration, and no browser
round-trip in a headless gateway.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import Protocol

logger = logging.getLogger(__name__)

#: Socket timeout for every SMTP operation — a half-open connection must not park a
#: worker thread forever.
SMTP_TIMEOUT_SECS = 60


class SmtpError(Exception):
    """Any SMTP transport/auth failure. The caller degrades, never crashes."""


class SmtpSender(Protocol):
    """The narrow surface delivery needs. A fake in tests implements just this."""

    def send(self, msg: EmailMessage) -> None: ...


class SmtplibSender:
    """The real sender — wraps ``smtplib.SMTP`` / ``SMTP_SSL``.

    :meth:`send` is BLOCKING and must be called from a thread executor.

    ``security`` selects the transport: ``"ssl"`` (implicit TLS, port 465), ``"starttls"``
    (upgrade an established plaintext session, port 587), or ``"plain"``. STARTTLS is
    verified rather than attempted: if the upgrade fails the send is ABORTED, never
    retried in the clear — silently downgrading would put an app password on the wire in
    plaintext, which is the whole failure this check exists to prevent."""

    def __init__(
        self, host: str, port: int, username: str, password: str, *, security: str = "starttls"
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._security = security

    def send(self, msg: EmailMessage) -> None:
        client: smtplib.SMTP | None = None
        try:
            if self._security == "ssl":
                client = smtplib.SMTP_SSL(self._host, self._port, timeout=SMTP_TIMEOUT_SECS)
            else:
                client = smtplib.SMTP(self._host, self._port, timeout=SMTP_TIMEOUT_SECS)
                client.ehlo()
                if self._security == "starttls":
                    client.starttls()
                    # RFC 3207: the session resets on upgrade — re-EHLO so the AUTH
                    # capabilities read are the post-TLS ones.
                    client.ehlo()
            if self._username and self._password:
                client.login(self._username, self._password)
            client.send_message(msg)
        except (smtplib.SMTPException, OSError) as exc:
            raise SmtpError(f"SMTP send failed: {exc}") from exc
        finally:
            if client is not None:
                try:
                    client.quit()
                except (smtplib.SMTPException, OSError):
                    logger.debug("mail-desk: SMTP quit error", exc_info=True)


def probe_login(
    host: str, port: int, username: str, password: str, *, security: str = "starttls"
) -> tuple[bool, str]:
    """The doctor/Test probe: connect, upgrade, log in. No mail is sent. BLOCKING."""
    client: smtplib.SMTP | None = None
    try:
        if security == "ssl":
            client = smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT_SECS)
        else:
            client = smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT_SECS)
            client.ehlo()
            if security == "starttls":
                client.starttls()
                client.ehlo()
        if username and password:
            client.login(username, password)
        return True, f"SMTP login OK ({security})"
    except (smtplib.SMTPException, OSError) as exc:
        return False, f"SMTP login failed: {exc}"
    finally:
        if client is not None:
            try:
                client.quit()
            except (smtplib.SMTPException, OSError):
                logger.debug("mail-desk: SMTP probe quit error", exc_info=True)
