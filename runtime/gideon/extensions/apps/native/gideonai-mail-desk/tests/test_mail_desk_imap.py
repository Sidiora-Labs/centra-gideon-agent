"""Imap4Client: UID-only commands, the strictly-greater resume agreement, UIDVALIDITY,
read-only select, and error containment.

The real client is driven against a double ``imaplib`` connection, so the UID range math
and response parsing are tested without a live server. The UID-vs-sequence assertions are
the weight-carrying ones: a sequence-number cursor silently skips or reprocesses mail after
any expunge, and that bug is invisible until a user deletes a message from another
client.
"""

from __future__ import annotations

import imaplib

import pytest

from mail_desk_runtime.imap_client import (
    IMAP_TIMEOUT_SECS,
    Imap4Client,
    ImapError,
    probe_login,
)


class FakeConn:
    """A fake ``imaplib.IMAP4`` connection recording precisely which commands ran."""

    def __init__(
        self, *, search=("OK", [b""]), fetch=("OK", []),
        status=("OK", [b'"INBOX" (UIDVALIDITY 7)']),
        login_ok=True, select_ok=True, raise_on=None,
    ):
        self._search = search
        self._fetch = fetch
        self._status = status
        self._login_ok = login_ok
        self._select_ok = select_ok
        #: command name that should raise ("SEARCH"/"FETCH"/"select"/"status")
        self._raise_on = raise_on
        self.uid_calls: list[tuple] = []
        self.selected: list[tuple[str, bool]] = []
        self.logged_out = False
        self.status_calls: list[tuple] = []

    def login(self, user, pw):
        if not self._login_ok:
            raise imaplib.IMAP4.error("bad auth")
        return ("OK", [b"ok"])

    def select(self, folder, readonly=False):
        if self._raise_on == "select":
            raise imaplib.IMAP4.error("select boom")
        self.selected.append((folder, readonly))
        return ("OK" if self._select_ok else "NO", [b"1"])

    def status(self, folder, what):
        self.status_calls.append((folder, what))
        if self._raise_on == "status":
            raise imaplib.IMAP4.error("status boom")
        return self._status

    def uid(self, command, *args):
        self.uid_calls.append((command, args))
        if self._raise_on == command:
            raise imaplib.IMAP4.error(f"{command} boom")
        if command == "SEARCH":
            return self._search
        if command == "FETCH":
            return self._fetch
        return ("NO", [])

    def logout(self):
        self.logged_out = True
        return ("BYE", [b""])


def _client(conn) -> Imap4Client:
    c = Imap4Client("mail.test", 993, "u@test", "pw", use_ssl=True)
    c._conn = conn  # inject the fake connection (skip the socket connect)
    return c


class TestUidNotSequenceNumbers:
    """Every command must be a ``UID`` command — sequence numbers renumber on expunge."""

    def test_search_is_a_uid_search_over_a_uid_range(self):
        conn = FakeConn(search=("OK", [b"3 5 8"]))
        _client(conn).fetch_uids_since("INBOX", 2)
        command, args = conn.uid_calls[0]
        assert command == "SEARCH"
        # The criterion names UID explicitly: "UID 3:*", never a bare "3:*" (which the
        # server would read as a SEQUENCE range).
        assert args[-1] == "UID 3:*"

    def test_fetch_is_a_uid_fetch(self):
        conn = FakeConn(fetch=("OK", [(b"1 (BODY[] {5}", b"hello"), b")"]))
        _client(conn).fetch_message("INBOX", 8)
        commands = [c for c, _ in conn.uid_calls]
        assert commands == ["FETCH"]
        _, args = conn.uid_calls[0]
        assert args[0] == "8"

    def test_fetch_peeks_so_the_mail_is_not_marked_read(self):
        """``RFC822`` sets \\Seen on many servers; ``BODY.PEEK[]`` never does. A channel
        must not mark the user's mail read behind their back."""
        conn = FakeConn(fetch=("OK", [(b"1 (BODY[] {2}", b"hi"), b")"]))
        _client(conn).fetch_message("INBOX", 1)
        _, args = conn.uid_calls[0]
        assert "PEEK" in args[1]


class TestResumeContract:
    def test_filters_strictly_greater_than_the_cursor(self):
        """``UID n:*`` always returns at least the highest UID even when nothing is
        newer — filtering to strictly-greater is what makes "no new mail" empty."""
        conn = FakeConn(search=("OK", [b"3 5 8"]))
        assert _client(conn).fetch_uids_since("INBOX", 8) == []
        assert _client(FakeConn(search=("OK", [b"3 5 8"]))).fetch_uids_since("INBOX", 5) == [8]

    def test_cursor_zero_returns_everything(self):
        conn = FakeConn(search=("OK", [b"1 2 3"]))
        assert _client(conn).fetch_uids_since("INBOX", 0) == [1, 2, 3]
        _, args = conn.uid_calls[0]
        assert args[-1] == "UID 1:*"

    def test_results_are_sorted_and_deduped(self):
        conn = FakeConn(search=("OK", [b"8 3 5 5"]))
        assert _client(conn).fetch_uids_since("INBOX", 0) == [3, 5, 8]

    def test_non_numeric_tokens_are_ignored(self):
        conn = FakeConn(search=("OK", [b"3 NIL 5"]))
        assert _client(conn).fetch_uids_since("INBOX", 0) == [3, 5]

    def test_empty_and_none_payloads(self):
        assert _client(FakeConn(search=("OK", [b""]))).fetch_uids_since("INBOX", 0) == []
        assert _client(FakeConn(search=("OK", [None]))).fetch_uids_since("INBOX", 0) == []
        assert _client(FakeConn(search=("OK", []))).fetch_uids_since("INBOX", 0) == []
        assert _client(FakeConn(search=("NO", [b"3"]))).fetch_uids_since("INBOX", 0) == []

    def test_negative_cursor_is_clamped(self):
        conn = FakeConn(search=("OK", [b"1"]))
        _client(conn).fetch_uids_since("INBOX", -5)
        _, args = conn.uid_calls[0]
        assert args[-1] == "UID 1:*"


class TestSelectIsReadOnly:
    def test_select_never_mutates_the_mailbox(self):
        conn = FakeConn()
        _client(conn).select_folder("INBOX")
        assert conn.selected == [("INBOX", True)]

    def test_search_selects_read_only_too(self):
        conn = FakeConn(search=("OK", [b"1"]))
        _client(conn).fetch_uids_since("Archive", 0)
        assert all(readonly for _, readonly in conn.selected)

    def test_select_failure_raises(self):
        with pytest.raises(ImapError):
            _client(FakeConn(select_ok=False)).select_folder("Nope")

    def test_select_exception_becomes_imap_error(self):
        with pytest.raises(ImapError):
            _client(FakeConn(raise_on="select")).select_folder("INBOX")


class TestUidValidity:
    def test_reports_the_servers_uidvalidity(self):
        assert _client(FakeConn()).select_folder("INBOX") == 7

    def test_unreported_uidvalidity_is_zero_not_an_error(self):
        """0 means "unknown" to the transport, which treats it as unchanged — a missing
        value must not look like a renumbering and wipe a good cursor."""
        assert _client(FakeConn(status=("OK", [b'"INBOX" ()']))).select_folder("INBOX") == 0
        assert _client(FakeConn(status=("NO", []))).select_folder("INBOX") == 0

    def test_status_failure_degrades_to_zero(self):
        assert _client(FakeConn(raise_on="status")).select_folder("INBOX") == 0

    def test_parses_a_str_status_payload(self):
        conn = FakeConn(status=("OK", ['"INBOX" (UIDVALIDITY 99)']))
        assert _client(conn).select_folder("INBOX") == 99


class TestFetchMessage:
    def test_extracts_the_literal_payload(self):
        fetch = ("OK", [(b"1 (BODY[] {5}", b"hello"), b")"])
        assert _client(FakeConn(fetch=fetch)).fetch_message("INBOX", 1) == b"hello"

    def test_missing_payload_is_empty_bytes(self):
        assert _client(FakeConn(fetch=("OK", []))).fetch_message("INBOX", 1) == b""
        assert _client(FakeConn(fetch=("NO", [b"x"]))).fetch_message("INBOX", 1) == b""

    def test_fetch_exception_becomes_imap_error(self):
        with pytest.raises(ImapError):
            _client(FakeConn(raise_on="FETCH")).fetch_message("INBOX", 1)


class TestErrorContainment:
    def test_search_error_is_wrapped(self):
        with pytest.raises(ImapError):
            _client(FakeConn(raise_on="SEARCH")).fetch_uids_since("INBOX", 0)

    def test_unconnected_client_raises_rather_than_returning_empty(self):
        """Silently returning [] would look like "no new mail" and stall the channel
        forever; raising lets the poll loop back off and report."""
        c = Imap4Client("mail.test", 993, "u", "p")
        with pytest.raises(ImapError):
            c.fetch_uids_since("INBOX", 0)
        with pytest.raises(ImapError):
            c.fetch_message("INBOX", 1)
        with pytest.raises(ImapError):
            c.select_folder("INBOX")

    def test_login_failure_is_wrapped(self, monkeypatch):
        class BoomSSL:
            def __init__(self, host, port, timeout=None):
                self.timeout = timeout

            def login(self, u, p):
                raise imaplib.IMAP4.error("auth denied")

        monkeypatch.setattr(imaplib, "IMAP4_SSL", BoomSSL)
        with pytest.raises(ImapError):
            Imap4Client("h", 993, "u", "p").connect()

    def test_os_error_on_connect_is_wrapped(self, monkeypatch):
        def boom(*a, **k):
            raise OSError("network unreachable")

        monkeypatch.setattr(imaplib, "IMAP4_SSL", boom)
        with pytest.raises(ImapError):
            Imap4Client("h", 993, "u", "p").connect()

    def test_close_is_idempotent_and_swallows_teardown_noise(self):
        class NoisyConn(FakeConn):
            def logout(self):
                raise imaplib.IMAP4.error("already gone")

        c = _client(NoisyConn())
        c.close()
        c.close()  # second call must not raise either

    def test_maxline_is_raised_at_import(self):
        """imaplib's default line cap truncates big UID sets / literals and raises
        "got more than N bytes"."""
        assert imaplib._MAXLINE >= 10_000_000


class TestConnectUsesTlsAndTimeout:
    def test_ssl_path_passes_the_timeout(self, monkeypatch):
        seen = {}

        class FakeSSL(FakeConn):
            def __init__(self, host, port, timeout=None):
                super().__init__()
                seen["ssl"] = (host, port, timeout)

        monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeSSL)
        Imap4Client("mail.test", 993, "u", "p", use_ssl=True).connect()
        assert seen["ssl"] == ("mail.test", 993, IMAP_TIMEOUT_SECS)

    def test_plain_path_used_when_ssl_is_off(self, monkeypatch):
        seen = {}
        # The client's ``except (imaplib.IMAP4.error, OSError)`` resolves the class off
        # the (about-to-be-patched) module attribute, so the fake must carry it.
        real_error = imaplib.IMAP4.error

        class FakePlain(FakeConn):
            error = real_error

            def __init__(self, host, port, timeout=None):
                super().__init__()
                seen["plain"] = (host, port, timeout)

        monkeypatch.setattr(imaplib, "IMAP4", FakePlain)
        Imap4Client("mail.test", 143, "u", "p", use_ssl=False).connect()
        assert seen["plain"] == ("mail.test", 143, IMAP_TIMEOUT_SECS)


class TestProbeLogin:
    """The plan's ``probe = login+select``: a login alone doesn't prove the folder."""

    def test_ok_when_login_and_select_both_succeed(self, monkeypatch):
        monkeypatch.setattr(
            imaplib, "IMAP4_SSL", lambda host, port, timeout=None: FakeConn()
        )
        ok, detail = probe_login("h", 993, "u", "p", "INBOX")
        assert ok is True
        assert "INBOX" in detail

    def test_fails_when_the_folder_is_not_selectable(self, monkeypatch):
        monkeypatch.setattr(
            imaplib, "IMAP4_SSL", lambda host, port, timeout=None: FakeConn(select_ok=False)
        )
        ok, detail = probe_login("h", 993, "u", "p", "Missing")
        assert ok is False
        assert "select" in detail.lower()

    def test_fails_when_the_login_is_rejected(self, monkeypatch):
        monkeypatch.setattr(
            imaplib, "IMAP4_SSL", lambda host, port, timeout=None: FakeConn(login_ok=False)
        )
        ok, detail = probe_login("h", 993, "u", "p", "INBOX")
        assert ok is False
        assert "login" in detail.lower()
