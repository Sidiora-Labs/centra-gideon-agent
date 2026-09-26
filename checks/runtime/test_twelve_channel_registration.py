"""All requested messaging manifests resolve to real channel providers."""

from __future__ import annotations

from pathlib import Path

from gideon.extensions.apps.manifest import AppManifest
from gideon.extensions.providers.loader import load_factory
from gideon.extensions.providers.registry import RegisteredProvider
from gideon.integrations.channel_transports import (
    get_transport,
    register_transport,
    unregister_transport,
)
from gideon.integrations.channel_transports.base import ChannelTransportProvider

_NATIVE = Path(__file__).parents[2] / "runtime/gideon/extensions/apps/native"
_APPS = {
    "slack": "gideonai-slack-desk",
    "discord": "gideonai-discord-desk",
    "telegram": "telegram-channel",
    "mail-desk": "gideonai-mail-desk",
    "matrix": "matrix-channel",
    "wecom": "wecom-channel",
    "dingtalk": "dingtalk-channel",
    "qq": "qq-channel",
    "feishu": "feishu-channel",
    "mochat": "mochat-channel",
    "whatsapp": "whatsapp-channel",
    "weixin": "weixin-channel",
}


def test_twelve_manifest_factories_register_real_transports() -> None:
    try:
        for provider_name, app_name in _APPS.items():
            manifest = AppManifest.from_json_file(_NATIVE / app_name / "app.json")
            assert manifest.native
            channel = next(
                item for item in manifest.all_providers() if item.type == "channel"
            )
            factory = load_factory(
                RegisteredProvider(app_name, manifest, channel, enabled=True)
            )
            provider = factory({})
            assert isinstance(provider, ChannelTransportProvider)
            assert provider.name == provider_name
            assert provider.capabilities().inbound
            assert type(provider).start_inbound is not ChannelTransportProvider.start_inbound
            register_transport(provider)
            assert get_transport(provider_name) is provider
    finally:
        for provider_name in _APPS:
            unregister_transport(provider_name)
