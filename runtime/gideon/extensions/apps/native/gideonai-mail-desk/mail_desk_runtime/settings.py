"""MailDeskSettings — this app's own non-secret configuration plus its credential keys.

Where every value lives, and why (the app/core boundary, ``provider-boundary.md``
§2.5/§2.6):

- The IMAP/SMTP **hosts, ports, TLS mode, logins, mailbox address, folder and poll
  cadence** are non-secret behavioural configuration, so they belong in this app's own
  ``ProviderSettings`` store (``~/.gideon/apps/gideonai-mail-desk/data/config.json``)
  rather than in core ``config.json``. Core ships no mail-desk configuration of its own.
- The IMAP and SMTP **passwords** are secrets, so they live ONLY in the shared
  credential store under this app's own keys ``MAIL_DESK_IMAP_PASS`` /
  ``MAIL_DESK_SMTP_PASS``. The setup step writes them there and the runtime reads them
  back by name. They never touch the settings store and never appear in a log line.

Who may talk is owned by the **core sender-trust seam** (``channel_trust``, provider
``"mail-desk"``), so this app keeps NO allowlist of its own — that separation is the
whole point of CE-1. (The sibling ``mail-inbox`` app carries an app-local allowlist
because it is an *inbox source*, not a channel; a channel binds to the seam.)

Only the two ``*_PASS`` keys are genuinely secret, so only those two go to the
credential store; hosts/users/ports stay in the app store where the user can see and
edit them in the Configure form. Filing a hostname under the credential store would
claim a secrecy it does not have while hiding it from the UI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from gideon.sdk.channel import ProviderSettings

logger = logging.getLogger(__name__)

_APP = "gideonai-mail-desk"

#: Credential-store keys for the two real secrets. App-owned: the setup step writes them
#: and the runtime reads them back by name (never from ProviderSettings).
CRED_IMAP_PASS = "MAIL_DESK_IMAP_PASS"
CRED_SMTP_PASS = "MAIL_DESK_SMTP_PASS"

# Non-secret settings keys — the app-store keys (the settingsSchema properties).
KEY_IMAP_HOST = "imap_host"
KEY_IMAP_PORT = "imap_port"
KEY_IMAP_USER = "imap_user"
KEY_SMTP_HOST = "smtp_host"
KEY_SMTP_PORT = "smtp_port"
KEY_SMTP_USER = "smtp_user"

DEFAULT_IMAP_PORT = 993
DEFAULT_SMTP_PORT = 587
DEFAULT_FOLDER = "INBOX"
#: Inbound cadence: poll IMAP every 60s. IDLE is deferred (rationale in the transport's
#: module docstring).
DEFAULT_POLL_SECS = 60

# SMTP transport modes. "starttls" (587) upgrades a plaintext connection; "ssl" (465) is
# implicit TLS from the first byte; "plain" is unencrypted and only sensible against a
# loopback relay.
SMTP_STARTTLS = "starttls"
SMTP_SSL = "ssl"
SMTP_PLAIN = "plain"
_VALID_SMTP_SECURITY = frozenset({SMTP_STARTTLS, SMTP_SSL, SMTP_PLAIN})

# Inbound activation modes, mirroring the telegram/discord apps: "always" answers mail
# from every paired sender; "off" disables inbound while leaving outbound delivery up,
# which is how a user runs the desk as a pure notification sink.
ACTIVATION_ALWAYS = "always"
ACTIVATION_OFF = "off"
_VALID_ACTIVATIONS = frozenset({ACTIVATION_ALWAYS, ACTIVATION_OFF})


def _validate_activation(value: str) -> str:
    return value if value in _VALID_ACTIVATIONS else ACTIVATION_ALWAYS


def _validate_smtp_security(value: str) -> str:
    return value if value in _VALID_SMTP_SECURITY else SMTP_STARTTLS


def _coerce_port(value: object, default: int) -> int:
    try:
        port = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return port if 1 <= port <= 65535 else default


def _coerce_poll_secs(value: object) -> int:
    """Clamp the poll cadence to a sane band.

    Under 10s a poll loop pesters the provider (many hosts throttle or ban for it);
    past an hour the channel stops feeling conversational. Anything unparseable falls
    back to the 60s default rather than disabling the loop."""
    try:
        secs = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_POLL_SECS
    return max(10, min(secs, 3600))


@dataclass
class MailDeskSettings:
    """The mail desk's behavioural configuration (its own store). No secrets here."""

    imap_host: str = ""
    imap_port: int = DEFAULT_IMAP_PORT
    imap_user: str = ""
    imap_use_ssl: bool = True
    folder: str = DEFAULT_FOLDER
    smtp_host: str = ""
    smtp_port: int = DEFAULT_SMTP_PORT
    smtp_user: str = ""
    smtp_security: str = SMTP_STARTTLS
    address: str = ""
    poll_secs: int = DEFAULT_POLL_SECS
    dm_activation: str = ACTIVATION_ALWAYS

    @property
    def mailbox_address(self) -> str:
        """The address this channel sends AS and receives AT.

        Falls back to the IMAP login, which is the full address at every provider whose
        app-password flow we document. The value is load-bearing twice: it is the
        ``From`` of every outbound message AND the anchor of the self-message filter, so
        an empty one would let the bot's own mail back in."""
        return self.address or self.imap_user

    @property
    def inbound_configured(self) -> bool:
        """True once IMAP is fully specified (the password lives elsewhere)."""
        return bool(self.imap_host and self.imap_user)

    @property
    def outbound_configured(self) -> bool:
        """True once SMTP is fully specified (the password lives elsewhere)."""
        return bool(self.smtp_host and self.smtp_user)

    @classmethod
    def from_dict(cls, d: dict) -> "MailDeskSettings":
        """Coerce a raw settings dict. THE one coercion path.

        Both :meth:`load` (the app store) and the transport's per-instance config overlay
        route through here, so a value can never be validated on one route and trusted raw
        on the other."""
        return cls(
            imap_host=str(d.get(KEY_IMAP_HOST, "")).strip(),
            imap_port=_coerce_port(d.get(KEY_IMAP_PORT, DEFAULT_IMAP_PORT), DEFAULT_IMAP_PORT),
            imap_user=str(d.get(KEY_IMAP_USER, "")).strip(),
            imap_use_ssl=bool(d.get("imap_use_ssl", True)),
            folder=str(d.get("folder", DEFAULT_FOLDER)).strip() or DEFAULT_FOLDER,
            smtp_host=str(d.get(KEY_SMTP_HOST, "")).strip(),
            smtp_port=_coerce_port(d.get(KEY_SMTP_PORT, DEFAULT_SMTP_PORT), DEFAULT_SMTP_PORT),
            smtp_user=str(d.get(KEY_SMTP_USER, "")).strip(),
            smtp_security=_validate_smtp_security(str(d.get("smtp_security", SMTP_STARTTLS))),
            address=str(d.get("address", "")).strip(),
            poll_secs=_coerce_poll_secs(d.get("poll_secs", DEFAULT_POLL_SECS)),
            dm_activation=_validate_activation(str(d.get("dm_activation", ACTIVATION_ALWAYS))),
        )

    @classmethod
    def load(cls) -> "MailDeskSettings":
        """Read + coerce the app store (never the credential store)."""
        return cls.from_dict(ProviderSettings.load(_APP))


# One cached live instance, mirroring the telegram/discord/mail-inbox apps: built once,
# refreshed on write via reload_settings().
_settings: MailDeskSettings | None = None


def get_settings() -> MailDeskSettings:
    """The app's live settings (cached; refreshed by :func:`reload_settings`)."""
    global _settings
    if _settings is None:
        _settings = MailDeskSettings.load()
    return _settings


def reload_settings() -> MailDeskSettings:
    """Force a re-read of the app store and refresh the cached instance."""
    global _settings
    _settings = MailDeskSettings.load()
    return _settings


def load_raw_settings() -> dict:
    """The app store's RAW dict — the base a per-instance config overlays before coercion."""
    return ProviderSettings.load(_APP)


def load_credentials() -> tuple[str, str]:
    """``(imap_password, smtp_password)`` from the shared credential store.

    An empty SMTP password falls back to the IMAP one: at Gmail/Fastmail/iCloud a single
    app password authenticates BOTH protocols, so demanding it twice is the kind of
    friction that ends in a half-configured channel. A separate SMTP secret is still
    honored when the user sets one (some corporate relays differ)."""
    from gideon.sdk.channel import AppConfig

    try:
        creds = AppConfig.load().load_credentials()
    except Exception:
        logger.debug("mail-desk: credential load failed", exc_info=True)
        return "", ""
    imap_pass = creds.get(CRED_IMAP_PASS, "")
    smtp_pass = creds.get(CRED_SMTP_PASS, "") or imap_pass
    return imap_pass, smtp_pass
