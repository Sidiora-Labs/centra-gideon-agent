from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from gideon.core.config import credentials
from gideon.core.config.secret_refs import (
    ConfigSecretReferenceError,
    prepare_config_secrets,
    resolve_config_secrets,
)


def test_owner_reference_is_stable_rotatable_and_never_exported(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIDEON_CREDENTIAL_BACKEND", "dotenv")
    monkeypatch.setenv("GIDEON_API_KEY", "process-value")
    original = "first-check-value"
    config = {"providers": {"alpha": {"api_key": original}}}

    first = prepare_config_secrets(config, previous={})
    reference = first.document["providers"]["alpha"]["api_key"]
    assert reference != original
    assert original not in json.dumps(first.document)
    assert credentials.get_secret_value(reference) == original
    assert __import__("os").environ["GIDEON_API_KEY"] == "process-value"
    assert first.commit().status == "complete"

    unchanged = prepare_config_secrets(first.document, previous=first.document)
    assert unchanged.document == first.document
    assert unchanged.commit().status == "complete"

    rotated = prepare_config_secrets(
        {"providers": {"alpha": {"api_key": "rotated-check-value"}}},
        previous=first.document,
    )
    new_reference = rotated.document["providers"]["alpha"]["api_key"]
    assert new_reference != reference
    assert credentials.get_secret_value(reference) == original
    assert rotated.commit().status == "complete"
    assert credentials.get_secret_value(reference) == ""
    assert resolve_config_secrets(rotated.document)["providers"]["alpha"]["api_key"] == "rotated-check-value"


def test_abort_removes_only_staged_secret_and_foreign_reference_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIDEON_CREDENTIAL_BACKEND", "dotenv")
    old = prepare_config_secrets({"api_key": "existing-check-value"}, previous={})
    old_reference = old.document["api_key"]
    old.commit()

    staged = prepare_config_secrets({"api_key": "uncommitted-check-value"}, previous=old.document)
    new_reference = staged.document["api_key"]
    result = staged.abort()
    assert result.status == "aborted"
    assert credentials.get_secret_value(new_reference) == ""
    assert credentials.get_secret_value(old_reference) == "existing-check-value"

    foreign_owner = {"nested": {"api_key": old_reference}}
    with pytest.raises(ConfigSecretReferenceError):
        prepare_config_secrets(foreign_owner, previous={})


def test_missing_reference_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIDEON_CREDENTIAL_BACKEND", "dotenv")
    plan = prepare_config_secrets({"api_key": "missing-check-value"}, previous={})
    reference = plan.document["api_key"]
    plan.commit()
    credentials.delete_secret_value(reference)
    with pytest.raises(ConfigSecretReferenceError):
        resolve_config_secrets({"api_key": reference})


def test_nested_secret_container_is_refused_before_backend_write(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("GIDEON_CREDENTIAL_BACKEND", "dotenv")
    incoming = {"provider": {"api_key": {"value": "nested-check-value"}}}
    with pytest.raises(ConfigSecretReferenceError):
        prepare_config_secrets(incoming, previous={})
    assert incoming["provider"]["api_key"]["value"] == "nested-check-value"
    assert not (home / ".env").exists()


def test_restart_credential_loading_never_exports_owner_reference(tmp_path, monkeypatch):
    import os

    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIDEON_CREDENTIAL_BACKEND", "dotenv")
    plan = prepare_config_secrets({"api_key": "restart-check-value"}, previous={})
    reference = plan.document["api_key"]
    assert credentials.is_config_secret_reference_key(reference)
    plan.commit()

    from gideon.core.config.loader import AppConfig

    loaded = AppConfig.load().load_credentials()
    assert reference not in loaded
    assert reference not in os.environ
    assert "restart-check-value" not in os.environ.values()


def test_provider_connection_locator_is_preserved_only_for_existing_vault_name(tmp_path, monkeypatch):
    from gideon.core.config.loader import config_dir
    from gideon.integrations.llm.credentials import CredentialStore

    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIDEON_CREDENTIAL_BACKEND", "dotenv")
    store = CredentialStore(config_dir())
    store.put("provider-auth", {"type": "api_key", "value_env": "PROVIDER_AUTH_TEST_KEY"})
    original = {"provider_connections": {"gateway": {"credential_ref": "provider-auth"}}}

    plan = prepare_config_secrets(original, previous={})
    assert plan.document == original
    assert resolve_config_secrets(plan.document) == original
    plan.commit()

    with pytest.raises(ConfigSecretReferenceError):
        prepare_config_secrets(
            {"provider_connections": {"gateway": {"credential_ref": "missing-auth"}}},
            previous={},
        )
    with pytest.raises(ConfigSecretReferenceError):
        resolve_config_secrets(
            {"provider_connections": {"gateway": {"credential_ref": "missing-auth"}}}
        )

    owner_reference = prepare_config_secrets({"api_key": "locator-uri-check-value"}, previous={})
    uri = owner_reference.document["api_key"]
    owner_reference.abort()
    with pytest.raises(ConfigSecretReferenceError):
        prepare_config_secrets(
            {"provider_connections": {"gateway": {"credential_ref": uri}}},
            previous={},
        )


def test_default_config_save_load_in_real_process_and_policy_reference_refusal(tmp_path):
    home = tmp_path / "home"
    env = os.environ.copy()
    env.update(
        HOME=str(tmp_path),
        GIDEON_HOME=str(home),
        GIDEON_CREDENTIAL_BACKEND="dotenv",
        PYTHONPATH=str(Path(__file__).resolve().parents[2] / "runtime"),
    )
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            """from gideon.core.config.loader import AppConfig
from gideon.interfaces.cli.server import _boot_config
from gideon.core.config.secret_refs import prepare_config_secrets, ConfigSecretReferenceError
from gideon.core.config import credentials

config = _boot_config()
assert AppConfig.load().security.credential_keychain is False
config.security.credential_keychain = True
config.save()
assert AppConfig.load().security.credential_keychain is True
ref = prepare_config_secrets({'api_key': 'policy-check-secret'}, previous={}).document['api_key']
assert credentials.get_secret_value(ref) == 'policy-check-secret'
try:
    prepare_config_secrets({'security': {'credential_keychain': ref}}, previous={})
except ConfigSecretReferenceError:
    pass
else:
    raise AssertionError('policy field accepted reference')
""",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=40,
    )
    assert child.returncode == 0, child.stderr
    assert json.loads((home / "config.json").read_text())["security"]["credential_keychain"] is True
