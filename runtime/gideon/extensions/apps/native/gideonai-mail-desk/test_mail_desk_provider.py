"""GideonAI mail desk: MailDeskTransport declares its real channel capabilities.

Loads the transport from the app's own ``mail_desk_runtime`` package (app dir on sys.path)
— the whole mail integration lives in the bundle, importing core only through
``gideon.sdk.*``.

This root-level smoke test is the per-app deliverable the apps CI runs; the deep behaviour
(UID persistence, the self-message filter, threading headers, pairing) lives in ``tests/``.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

# App dir on sys.path so this root-level test imports the app's mail_desk_runtime package
# the way the gateway's app loader does (tests/conftest.py does the same for the tests/
# subdir; inlined here since this file lives at the app root).
_APP_DIR = Path(__file__).resolve().parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from mail_desk_runtime.settings import (  # noqa: E402
    CRED_IMAP_PASS,
    CRED_SMTP_PASS,
    DEFAULT_POLL_SECS,
    MailDeskSettings,
    _validate_activation,
    _validate_smtp_security,
)
from mail_desk_runtime.transport import MailDeskTransport, create_provider  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    """Point GIDEON_HOME at a tmp dir and drop the settings cache.

    ``MailDeskTransport.connected`` READS the app store, so without this the "offline
    without configuration" assertions would depend on whether the developer running the
    suite happens to have a real mailbox configured — a test that passes or fails on the
    machine's state, not the code's."""
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    for key in (CRED_IMAP_PASS, CRED_SMTP_PASS):
        monkeypatch.delenv(key, raising=False)
    from mail_desk_runtime import settings as s

    s._settings = None
    yield
    s._settings = None


def test_mail_desk_capabilities():
    caps = MailDeskTransport().capabilities()
    assert caps.inbound and caps.threads and caps.attachments and caps.rich_text
    assert caps.reactions is False
    assert caps.typing_indicator is False
    # streaming=false is declared as edits=False (the dataclass has no streaming field);
    # see MailDeskTransport.capabilities() for the mapping and
    # tests/test_mail_desk_transport.py for the paired assertion that the streaming trio
    # really is inert.
    assert caps.edits is False
    assert caps.max_text_len == 0


def test_offline_without_configuration():
    transport = MailDeskTransport()
    assert transport.connected is False
    assert asyncio.run(transport.health())["state"] == "offline"


def test_instance_config_makes_it_connected():
    """A configured instance must report ready — otherwise the Channels surface lies
    'offline' for a channel that works."""
    transport = MailDeskTransport({"imap_host": "imap.test", "imap_user": "u@test"})
    assert transport.connected is True


def test_outbound_only_configuration_is_still_connected():
    """A send-only notification sink is a legitimate configuration."""
    transport = MailDeskTransport({"smtp_host": "smtp.test", "smtp_user": "u@test"})
    assert transport.connected is True


def test_info_exposes_caps():
    info = MailDeskTransport().info()
    assert info["capabilities"]["inbound"] is True
    assert info["display_name"] == "Mail Desk"
    assert info["name"] == "mail-desk"


def test_create_provider_returns_transport():
    assert type(create_provider({})).__name__ == "MailDeskTransport"


def test_settings_defaults_and_validation():
    assert _validate_activation("bogus") == "always"
    assert _validate_activation("off") == "off"
    assert _validate_smtp_security("bogus") == "starttls"
    assert _validate_smtp_security("ssl") == "ssl"
    assert MailDeskSettings().poll_secs == DEFAULT_POLL_SECS
    assert MailDeskSettings().folder == "INBOX"


def test_credential_keys_are_app_owned():
    """This app's key vocabulary, verbatim — secrets live in the credential store under
    these names, never in app config."""
    assert CRED_IMAP_PASS == "MAIL_DESK_IMAP_PASS"
    assert CRED_SMTP_PASS == "MAIL_DESK_SMTP_PASS"


def test_the_bundle_does_not_shadow_stdlib_email():
    """A module named ``email`` in the app dir would shadow the stdlib ``email`` package
    that every module here depends on — and the app dir IS on sys.path at runtime (this
    test proved that by importing through it). Asserted structurally so a future rename
    to ``email.py`` fails here rather than at MIME-parse time in production."""
    import email as stdlib_email
    import email.message
    import email.parser
    import email.utils

    assert not (_APP_DIR / "email.py").exists()
    assert not (_APP_DIR / "email").is_dir()
    # The resolved stdlib package must come from the Python stdlib, not this bundle.
    assert str(_APP_DIR) not in str(Path(stdlib_email.__file__).resolve())
    assert email.message.EmailMessage and email.parser.BytesParser and email.utils.parseaddr
