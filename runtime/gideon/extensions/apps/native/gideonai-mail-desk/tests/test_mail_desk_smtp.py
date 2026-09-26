"""SmtplibSender: transport selection, the no-plaintext-fallback rule, error containment,
and the login probe.

``smtplib`` classes are replaced with recording doubles instead of any socket being opened.
The security-weight-carrying assertion is the STARTTLS one: a failed upgrade must ABORT, not
retry in the clear, or an app password rides the wire in plaintext.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

import pytest

from mail_desk_runtime.smtp_client import (
    SMTP_TIMEOUT_SECS,
    SmtpError,
    SmtplibSender,
    probe_login,
)


class FakeSmtp:
    """Records the call sequence: ehlo / starttls / login / send_message / quit.

    ``faults`` is a class-level set the fixture resets; a test adds a fault name before
    driving the sender, so no test has to swap ``__init__`` to inject a failure."""

    instances: list["FakeSmtp"] = []
    faults: set[str] = set()

    def __init__(self, host="", port=0, timeout=None, **kwargs):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.calls: list[str] = []
        self.logins: list[tuple[str, str]] = []
        self.sent: list[EmailMessage] = []
        self.starttls_fails = "starttls" in FakeSmtp.faults
        self.login_fails = "login" in FakeSmtp.faults
        self.send_fails = "send" in FakeSmtp.faults
        FakeSmtp.instances.append(self)

    def ehlo(self, *a):
        self.calls.append("ehlo")
        return (250, b"ok")

    def starttls(self, *a, **k):
        self.calls.append("starttls")
        if self.starttls_fails:
            raise smtplib.SMTPNotSupportedError("STARTTLS not supported")
        return (220, b"ready")

    def login(self, user, password):
        self.calls.append("login")
        if self.login_fails:
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")
        self.logins.append((user, password))

    def send_message(self, msg, *a, **k):
        self.calls.append("send_message")
        if self.send_fails:
            raise smtplib.SMTPRecipientsRefused({"x@y": (550, b"no")})
        self.sent.append(msg)

    def quit(self):
        self.calls.append("quit")


@pytest.fixture(autouse=True)
def _fresh_fakes(monkeypatch):
    FakeSmtp.instances = []
    FakeSmtp.faults = set()
    monkeypatch.setattr(smtplib, "SMTP", FakeSmtp)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSmtp)
    yield FakeSmtp
    FakeSmtp.faults = set()


def _msg() -> EmailMessage:
    m = EmailMessage()
    m["From"] = "agent@example.com"
    m["To"] = "bob@example.com"
    m["Subject"] = "hi"
    m.set_content("body")
    return m


class TestTransportSelection:
    def test_starttls_upgrades_then_reissues_ehlo(self):
        """RFC 3207: the session resets on upgrade, so AUTH capabilities must be re-read
        after TLS — otherwise login is attempted against the pre-TLS advertisement."""
        SmtplibSender("mail.test", 587, "u", "p", security="starttls").send(_msg())
        client = FakeSmtp.instances[-1]
        assert client.calls == ["ehlo", "starttls", "ehlo", "login", "send_message", "quit"]

    def test_ssl_skips_starttls_entirely(self):
        SmtplibSender("mail.test", 465, "u", "p", security="ssl").send(_msg())
        client = FakeSmtp.instances[-1]
        assert "starttls" not in client.calls
        assert client.calls == ["login", "send_message", "quit"]

    def test_plain_neither_upgrades_nor_uses_ssl(self):
        SmtplibSender("localhost", 25, "", "", security="plain").send(_msg())
        client = FakeSmtp.instances[-1]
        assert client.calls == ["ehlo", "send_message", "quit"]

    def test_no_login_without_credentials(self):
        SmtplibSender("localhost", 25, "", "", security="plain").send(_msg())
        assert FakeSmtp.instances[-1].logins == []

    def test_timeout_is_always_passed(self):
        SmtplibSender("mail.test", 587, "u", "p").send(_msg())
        assert FakeSmtp.instances[-1].timeout == SMTP_TIMEOUT_SECS

    def test_the_message_reaches_the_server_unmodified(self):
        msg = _msg()
        SmtplibSender("mail.test", 587, "u", "p").send(msg)
        assert FakeSmtp.instances[-1].sent == [msg]


class TestNoPlaintextFallback:
    def test_a_failed_starttls_aborts_the_send(self):
        """The whole reason this is checked: falling back to plaintext would put the app
        password and the message body on the wire in the clear."""
        FakeSmtp.faults.add("starttls")
        with pytest.raises(SmtpError):
            SmtplibSender("mail.test", 587, "u", "p", security="starttls").send(_msg())

        client = FakeSmtp.instances[-1]
        assert "login" not in client.calls  # never authenticated in the clear
        assert client.sent == []  # and never sent
        assert "quit" in client.calls  # still torn down


class TestErrorContainment:
    def test_auth_failure_becomes_smtp_error(self):
        FakeSmtp.faults.add("login")
        with pytest.raises(SmtpError):
            SmtplibSender("mail.test", 587, "u", "p").send(_msg())

    def test_recipient_refusal_becomes_smtp_error(self):
        FakeSmtp.faults.add("send")
        with pytest.raises(SmtpError):
            SmtplibSender("mail.test", 587, "u", "p").send(_msg())

    def test_connection_error_becomes_smtp_error(self, monkeypatch):
        def boom(*a, **k):
            raise OSError("connection refused")

        monkeypatch.setattr(smtplib, "SMTP", boom)
        with pytest.raises(SmtpError):
            SmtplibSender("mail.test", 587, "u", "p").send(_msg())

    def test_a_noisy_quit_does_not_mask_a_successful_send(self, monkeypatch):
        class NoisyQuit(FakeSmtp):
            def quit(self):
                raise smtplib.SMTPServerDisconnected("already gone")

        monkeypatch.setattr(smtplib, "SMTP", NoisyQuit)
        SmtplibSender("mail.test", 587, "u", "p").send(_msg())  # must not raise
        assert FakeSmtp.instances[-1].sent  # the message DID land before the noisy quit


class TestProbeLogin:
    def test_ok_for_each_security_mode(self):
        for security in ("starttls", "ssl", "plain"):
            ok, detail = probe_login("mail.test", 587, "u", "p", security=security)
            assert ok is True, detail
            assert security in detail

    def test_probe_sends_no_mail(self):
        probe_login("mail.test", 587, "u", "p")
        assert FakeSmtp.instances[-1].sent == []

    def test_failed_login_reports_not_ok(self):
        FakeSmtp.faults.add("login")
        ok, detail = probe_login("mail.test", 587, "u", "p")
        assert ok is False
        assert "login failed" in detail.lower()

    def test_unreachable_host_reports_not_ok(self, monkeypatch):
        def boom(*a, **k):
            raise OSError("no route to host")

        monkeypatch.setattr(smtplib, "SMTP", boom)
        ok, detail = probe_login("mail.test", 587, "u", "p")
        assert ok is False
        assert "no route" in detail
