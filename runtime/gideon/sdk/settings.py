"""SDK: an app's persisted per-provider settings.

Stable re-export of ``gideon.extensions.providers.settings.ProviderSettings`` — the
generic, provider-agnostic accessor an app uses to load/save/update its own
configuration (the settingsSchema in its app.json). An app imports this, not the
core module, so the core path can move without breaking installed apps.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from gideon.extensions.providers.settings import ProviderSettings  # noqa: F401

__all__ = ["ProviderSettings"]


_T = TypeVar("_T")


def mutate_channel_config(
    mutator: Callable[[dict[str, Any]], _T], *, path: Path | None = None
) -> _T:
    """Apply a native channel's default-agent edit or legacy settings removal.

    The admitted code identity supplies the app; callers cannot select one. This
    operation never changes security policy, credentials, or another config section.
    """
    import copy

    from gideon.core.config.loader import config_path
    from gideon.core.config.transactions import ConfigWriteError, mutate_config
    from gideon.extensions.apps.catalog import is_packaged_native
    from gideon.extensions.apps.code_provenance import loaded_app, owner
    from gideon.extensions.apps.native_contract import NATIVE_DIR

    app = owner()
    if (
        app != "gideonai-slack-desk"
        or not is_packaged_native(app)
        or loaded_app(str(NATIVE_DIR / app / "app.json")) != app
    ):
        raise ConfigWriteError(
            "channel config changes require admitted native channel code"
        )
    target = config_path()
    if path is not None and path.resolve() != target.resolve():
        raise ConfigWriteError("channel config changes require the active config file")
    legacy_keys = frozenset(
        {
            "allowed_users",
            "tracking_channels",
            "open_channels",
            "command",
            "trusted_bot_ids",
            "allowed_enterprise_ids",
            "reactions",
            "reactions_enabled",
            "channels",
            "dm_activation",
        }
    )

    def bounded(document: dict[str, Any]) -> _T:
        previous = copy.deepcopy(document)
        result = mutator(document)
        for key in previous.keys() | document.keys():
            if previous.get(key) == document.get(key) and (key in previous) == (
                key in document
            ):
                continue
            if key == "default_agent" and isinstance(document.get(key), str):
                continue
            if key == "slack":
                before, after = previous.get(key), document.get(key, {})
                if isinstance(before, dict) and isinstance(after, dict):
                    if (
                        after.keys() <= before.keys()
                        and before.keys() - after.keys() <= legacy_keys
                        and all(before[name] == value for name, value in after.items())
                    ):
                        continue
            raise ConfigWriteError("channel config edit exceeds its allowed fields")
        return result

    return mutate_config(bounded, path=target)


__all__.append("mutate_channel_config")
