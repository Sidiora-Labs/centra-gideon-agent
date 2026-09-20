import json

from gideon.extensions.apps.app_config import write_config
from gideon.extensions.providers.instances import create_instance, update_instance
from gideon.extensions.providers.settings import ProviderSettings


def test_app_config_partial_write_preserves_existing_values(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "gideon.extensions.apps.app_config.app_dir", lambda _name: tmp_path
    )
    schema = {
        "properties": {
            "endpoint": {"type": "string"},
            "timeout": {"type": "integer"},
        }
    }
    write_config("search", {"endpoint": "https://example.test", "timeout": 30}, schema)

    saved = write_config("search", {"timeout": 60}, schema)

    assert saved == {"endpoint": "https://example.test", "timeout": 60}
    assert json.loads((tmp_path / "data" / "config.json").read_text()) == saved


def test_provider_settings_partial_save_preserves_existing_values(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "gideon.extensions.providers.settings.app_dir", lambda _name: tmp_path
    )
    ProviderSettings.save("search", {"api_key": "secret", "region": "us"})

    ProviderSettings.save("search", {"region": "eu"})

    assert ProviderSettings.load("search") == {"api_key": "secret", "region": "eu"}


def test_instance_partial_config_update_preserves_existing_values(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "gideon.extensions.providers.instances._instances_dir",
        lambda _name: tmp_path,
    )
    create_instance(
        "search",
        "Primary",
        {"api_key": "secret", "region": "us"},
        instance_id="primary",
    )

    updated = update_instance("search", "primary", config={"region": "eu"})

    assert updated is not None
    assert updated.config == {"api_key": "secret", "region": "eu"}
    assert (
        json.loads((tmp_path / "primary.json").read_text())["config"] == updated.config
    )
