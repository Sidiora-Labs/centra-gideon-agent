"""DiscordDeskSettings — this bundle's OWN config and credential keys.

Discord behaviour settings (the DM activation posture, the application id) live
HERE, persisted in the bundle's own store
(``~/.gideon/apps/gideonai-discord-desk/data/config.json`` via
:class:`ProviderSettings`), not in core ``config.json``. Core defines no Discord
configuration.

The bot token is a SECRET, so it lives in the shared credential store (``.env``)
under this bundle's own key — Discord is not one of core's in-core credential
exceptions (only ``SLACK_*``/``GIDEON_OWNER_ID`` are, per
``docs/architecture/provider-boundary.md``), and the credential store reads back
every key by name, so the bundle owns ``DISCORD_BOT_TOKEN`` literally.

The **application id** is deliberately NOT a credential: Discord prints it on the
public "General Information" page, it appears in every invite URL a user clicks,
and leaking it grants nothing (the token is what authenticates). Putting it in the
credential store would claim a secrecy it does not have and hide it from the
Configure form; it belongs in the app store next to ``dm_activation`` where the
user can see and edit it. The setup step needs it for the OAuth2 invite URL it
prints; interaction responses are addressed by interaction id + token, which ride
on the ``INTERACTION_CREATE`` payload itself, so nothing on the hot path depends on
it being set.

Who is allowed to talk (allowlist, pairing) and which guild channels are tracked
are owned by the core sender-trust seam (``channel_trust``, provider=``"discord"``),
so this bundle keeps no allowlist of its own — the whole point of CE-1.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from gideon.sdk.channel import ProviderSettings

logger = logging.getLogger(__name__)

_APP = "gideonai-discord-desk"

#: The credential-store key the bot token is stored under. App-owned (see module
#: docstring): the setup step writes it and the runtime reads it back by name.
CRED_BOT_TOKEN = "DISCORD_BOT_TOKEN"

# DM activation modes. "always" answers every paired DM; "mention" only when the
# bot is @-mentioned (rare in a 1:1 DM but honored for parity); "off" disables DMs.
ACTIVATION_ALWAYS = "always"
ACTIVATION_MENTION = "mention"
ACTIVATION_OFF = "off"
_VALID_ACTIVATIONS = frozenset({ACTIVATION_ALWAYS, ACTIVATION_MENTION, ACTIVATION_OFF})


def _validate_activation(value: str) -> str:
    return value if value in _VALID_ACTIVATIONS else ACTIVATION_ALWAYS


@dataclass
class DiscordDeskSettings:
    """This bundle's behavioral config (its own store)."""

    dm_activation: str = ACTIVATION_ALWAYS
    application_id: str = ""

    @classmethod
    def load(cls) -> "DiscordDeskSettings":
        """Read + coerce the app store."""
        d = ProviderSettings.load(_APP)
        return cls(
            dm_activation=_validate_activation(d.get("dm_activation", ACTIVATION_ALWAYS)),
            application_id=str(d.get("application_id", "") or ""),
        )


# One cached live instance, mirroring the Telegram + Slack apps: build once, refresh
# on write.
_settings: DiscordDeskSettings | None = None


def get_settings() -> DiscordDeskSettings:
    """The app's live settings (cached; refreshed by :func:`reload_settings`)."""
    global _settings
    if _settings is None:
        _settings = DiscordDeskSettings.load()
    return _settings


def reload_settings() -> DiscordDeskSettings:
    """Force a re-read of the app store and refresh the cached instance."""
    global _settings
    _settings = DiscordDeskSettings.load()
    return _settings
