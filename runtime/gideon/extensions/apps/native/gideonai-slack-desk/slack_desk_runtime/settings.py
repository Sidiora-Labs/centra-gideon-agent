"""SlackDeskSettings — this bundle's OWN config store (never core AppConfig).

Slack behavioral config (allowed users, tracking/open channels, slash command, trusted
bot ids, enterprise allowlist, phase reactions, per-channel activation) is Slack-specific,
so it lives HERE in the app bundle, persisted in the app's own store
(``~/.gideon/apps/gideonai-slack-desk/data/config.json`` via ``ProviderSettings``), NOT in
core ``config.json``. Core defines no ``SlackConfig``.

Read path: ``SlackDeskSettings.load()`` reads + coerces the app store (same hardening the old
core loader applied). Write path: the ``persist_*`` helpers do a read-modify-write via
``ProviderSettings.update`` so the allowlist/channel editors mutate the app store.

Migration: ``migrate_from_core()`` lifts a legacy ``config.json → "slack"`` block into the
app store once (then deletes the core key), so existing installs keep their data.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field

from gideon.sdk.channel import (
    CRED_SLACK_APP_TOKEN,
    CRED_SLACK_BOT_TOKEN,
    ProviderSettings,
    atomic_write,
    config_path,
)

logger = logging.getLogger(__name__)

_APP = "gideonai-slack-desk"

#: The two settings-schema keys that are CREDENTIALS rather than behaviour, so they are
#: not :class:`SlackDeskSettings` fields (a token never belongs in a dataclass that gets
#: logged, diffed or echoed). The Configure form still writes them into the same app
#: store, which is why they are named here: the schema↔runtime rail iterates
#: ``dataclasses.fields(SlackDeskSettings)`` plus these, and a schema key in neither is a
#: dashboard control with nothing behind it.
CREDENTIAL_SETTING_KEYS = ("bot_token", "app_token")

# Channel activation modes (moved from core config.loader).
ACTIVATION_ALWAYS = "always"
ACTIVATION_MENTION = "mention"
ACTIVATION_OBSERVE = "observe"
ACTIVATION_REVIEW = "review"
ACTIVATION_OFF = "off"
_VALID_ACTIVATIONS = frozenset(
    {ACTIVATION_ALWAYS, ACTIVATION_MENTION, ACTIVATION_OBSERVE, ACTIVATION_REVIEW, ACTIVATION_OFF}
)
_VALID_CHANNEL_PREFIXES = ("C", "D", "G")

# The behavioral keys this app owns (migrate_from_core lifts these out of a legacy core block).
_OWNED_KEYS = (
    "allowed_users", "tracking_channels", "open_channels", "command",
    "trusted_bot_ids", "allowed_enterprise_ids", "reactions", "reactions_enabled",
    "channels", "dm_activation",
)


def load_tokens(
    config: dict | None = None, creds: dict[str, str] | None = None
) -> tuple[str, str]:
    """Resolve ``(bot_token, app_token)`` — the ONE resolution order for this channel.

    Order: the per-instance ``config`` the provider registry hands the transport (which
    *is* this app's store, ``ProviderSettings.load(_APP)``) → core's credential store
    (``.env`` / keychain / env, passed in as *creds* by whoever holds an ``AppConfig``)
    → the process environment.

    **Why this function exists (#952).** The outbound half (``SlackDeskTransport``) resolved
    these two tokens from the app store, and the inbound half (``SlackDeskRuntime``) resolved
    them from ``AppConfig.load_credentials()`` — which reads ``.env``, the keychain and the
    environment and never looks at the app store. So an operator who configured Slack the
    way the dashboard invites got a live outbound half (provider row green, "connected to
    Slack" printed) and a dead inbound half, with the two disagreeing silently. Two
    resolutions of the same credential is the defect; one shared resolution is the fix.

    ``config`` is honoured when it is a dict *even if empty*, so a transport constructed
    explicitly with ``{}`` (the channel conformance kit) does not silently pick the
    installed store up behind the test's back. Pass ``None`` to mean "read the store".
    """
    src = ProviderSettings.load(_APP) if config is None else config
    creds = creds or {}
    bot = (
        src.get("bot_token", "")
        or creds.get(CRED_SLACK_BOT_TOKEN, "")
        or os.environ.get(CRED_SLACK_BOT_TOKEN, "")
    )
    app = (
        src.get("app_token", "")
        or creds.get(CRED_SLACK_APP_TOKEN, "")
        or os.environ.get(CRED_SLACK_APP_TOKEN, "")
    )
    return bot, app


@dataclass
class ChannelConfig:
    """Per-channel Slack configuration."""

    activation: str = ACTIVATION_MENTION
    agent: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "ChannelConfig":
        activation = data.get("activation", ACTIVATION_MENTION)
        if activation not in _VALID_ACTIVATIONS:
            activation = ACTIVATION_MENTION
        return cls(activation=activation, agent=data.get("agent", ""))


def _validate_activation(value: str) -> str:
    return value if value in _VALID_ACTIVATIONS else ACTIVATION_MENTION


def _validate_tracking_channels(raw: list) -> list[dict]:
    """Coerce tracking_channels: pass {channel_id,name} dicts, coerce bare 'C…' strings,
    drop the rest (same hardening the old core loader applied)."""
    if not raw:
        return []
    result: list[dict] = []
    coerced = rejected = 0
    for entry in raw:
        if isinstance(entry, dict) and entry.get("channel_id"):
            result.append(entry)
        elif isinstance(entry, str) and len(entry) > 1 and entry[0] in _VALID_CHANNEL_PREFIXES:
            result.append({"channel_id": entry})
            coerced += 1
        else:
            rejected += 1
    if coerced:
        logger.warning("slack tracking_channels: coerced %d bare string(s) to {channel_id}", coerced)
    if rejected:
        logger.warning("slack tracking_channels: ignored %d invalid entries", rejected)
    return result


@dataclass
class SlackDeskSettings:
    """The gideonai-slack-desk app's behavioral config (its own store)."""

    allowed_users: list[dict] = field(default_factory=list)
    tracking_channels: list[dict] = field(default_factory=list)
    open_channels: list[str] = field(default_factory=list)
    command: str = "gideon"
    trusted_bot_ids: set[str] = field(default_factory=set)
    allowed_enterprise_ids: list[str] = field(default_factory=list)
    reactions: dict[str, str | None] = field(default_factory=dict)
    reactions_enabled: bool = True
    channels: dict[str, ChannelConfig] = field(default_factory=dict)
    dm_activation: str = ACTIVATION_ALWAYS

    @classmethod
    def load(cls) -> "SlackDeskSettings":
        """Read + coerce the app store (after a one-time migration from core)."""
        migrate_from_core()
        d = ProviderSettings.load(_APP)
        return cls(
            allowed_users=[u for u in d.get("allowed_users", []) if isinstance(u, dict) and u.get("slack_id")],
            tracking_channels=_validate_tracking_channels(d.get("tracking_channels", [])),
            open_channels=[c for c in d.get("open_channels", []) if isinstance(c, str)],
            command=d.get("command", "gideon") or "gideon",
            trusted_bot_ids=set(d.get("trusted_bot_ids", [])),
            allowed_enterprise_ids=[
                e for e in d.get("allowed_enterprise_ids", []) if isinstance(e, str) and e.startswith("E")
            ],
            reactions={
                k: v for k, v in d.get("reactions", {}).items()
                if isinstance(k, str) and (v is None or (isinstance(v, str) and v))
            },
            reactions_enabled=bool(d.get("reactions_enabled", True)),
            channels={
                ch_id: ChannelConfig.from_dict(ch_data)
                for ch_id, ch_data in d.get("channels", {}).items()
                if isinstance(ch_data, dict)
            },
            dm_activation=_validate_activation(d.get("dm_activation", ACTIVATION_ALWAYS)),
        )

    def channel_config(self, channel_id: str) -> ChannelConfig:
        """Per-channel config: explicit override, else DM default for D-channels, else mention."""
        if channel_id in self.channels:
            return self.channels[channel_id]
        if channel_id.startswith("D"):
            return ChannelConfig(activation=self.dm_activation)
        return ChannelConfig(activation=ACTIVATION_MENTION)

    def enterprise_ids(self) -> set[str]:
        return set(self.allowed_enterprise_ids)


# ── Writers (read-modify-write the app store) ──

def persist_list_entry(section: str, id_field: str, target_id: str, *, remove: bool = False, name: str = "") -> None:
    """Add/remove {id_field: target_id[, name]} in the app store's *section* list. Idempotent."""
    cur = ProviderSettings.load(_APP)
    entries: list[dict] = list(cur.get(section, []))
    if remove:
        filtered = [e for e in entries if not (isinstance(e, dict) and e.get(id_field) == target_id)]
        if len(filtered) == len(entries):
            return
        ProviderSettings.update(_APP, {section: filtered})
    else:
        if any(isinstance(e, dict) and e.get(id_field) == target_id for e in entries):
            return
        entry = {id_field: target_id}
        if name:
            entry["name"] = name
        entries.append(entry)
        ProviderSettings.update(_APP, {section: entries})


# ── Cached accessor (one live instance; reload after writes) ──

_current: SlackDeskSettings | None = None


def get_settings() -> SlackDeskSettings:
    """The live SlackDeskSettings instance (loaded once; refresh via reload_settings)."""
    global _current
    if _current is None:
        _current = SlackDeskSettings.load()
    return _current


def reload_settings() -> SlackDeskSettings:
    """Re-read the app store (call after a persist_* write so changes take effect)."""
    global _current
    _current = SlackDeskSettings.load()
    return _current


def _migration_marker_path():
    """Done-marker FILE beside the app store (data/ survives updates). A file — not
    a store key — because the schema-validated Configure form rejects/drops
    undeclared store keys, and not "store owns keys" because the store write lands
    BEFORE the core rewrite (a failed rewrite must retry next boot)."""
    return ProviderSettings.config_path(_APP).parent / ".core_migration_done"


def _migrated_marker_present() -> bool:
    return _migration_marker_path().is_file()


def _mark_migration_done() -> None:
    p = _migration_marker_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
    except OSError:
        logger.warning("Could not write migration done-marker %s", p, exc_info=True)


def migrate_from_core() -> None:
    """One-time: lift a legacy core ``config.json → "slack"`` block into the app store,
    then delete the core key. The done-marker is set ONLY once the core rewrite
    succeeded; a failed rewrite logs at ERROR (naming the leftover keys) and retries
    on the next boot. On a retry, store-present keys win so a user edit made in
    between is never clobbered by the stale core copies. Idempotent throughout."""
    if _migrated_marker_present():
        return
    try:
        cpath = config_path()
        if not cpath.exists():
            return
        core = json.loads(cpath.read_text(encoding="utf-8"))
    except Exception:
        return  # unreadable core config — leave unmarked, re-check next boot
    slack_desk = core.get("slack")
    if not isinstance(slack_desk, dict) or not slack_desk:
        # Nothing legacy to lift (fresh install, or already migrated before the
        # explicit marker existed) — mark done so we stop re-reading core each boot.
        _mark_migration_done()
        return
    leftover = [k for k in _OWNED_KEYS if k in slack_desk]
    if not leftover:
        # slack block holds none of our keys (e.g. stale observe_* copies —
        # core reads those top-level only) — mark done, leave the block alone.
        _mark_migration_done()
        return
    # Copy the behavioral keys (observe_* stay in core, top-level now — not moved
    # here). Store-present keys win (see docstring).
    store = ProviderSettings.load(_APP)
    moved = {k: slack_desk[k] for k in leftover if k not in store}
    try:
        if moved:
            ProviderSettings.update(_APP, moved)
        # Drop the migrated behavioral keys from core (observe_* is core's own,
        # top-level — never lived here to migrate).
        for k in _OWNED_KEYS:
            slack_desk.pop(k, None)
        if slack_desk:
            core["slack"] = slack_desk
        else:
            core.pop("slack", None)
        atomic_write(cpath, json.dumps(core, indent=2) + "\n")
    except Exception:
        # LOUD: the app store may already hold the lifted keys, but the legacy
        # copies are still sitting in core config.json. The absent done-marker
        # makes the next boot retry the rewrite.
        logger.error(
            "Slack config migration: rewriting core config.json failed — legacy key(s) "
            "%s left behind in %s (will retry next boot)",
            ", ".join(leftover),
            cpath,
            exc_info=True,
        )
        return
    _mark_migration_done()
    logger.info("Migrated %d Slack config key(s) from core config.json to the app store", len(moved))
