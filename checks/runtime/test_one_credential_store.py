from __future__ import annotations

import json
from pathlib import Path

from gideon.integrations.llm.credentials import CredentialStore


def test_legacy_descriptors_migrate_to_the_shared_credential_backend(
    tmp_path: Path, monkeypatch
):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(tmp_path / "user-home"))
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("GIDEON_CREDENTIAL_BACKEND", "dotenv")
    home.mkdir()
    legacy = home / "credentials.json"
    legacy.write_text(
        json.dumps({"service": {"type": "api_key", "value": "legacy-secret"}})
    )
    first = CredentialStore(home)
    assert first.resolve("service").secret == "legacy-secret"
    persisted = json.loads(legacy.read_text())
    assert persisted["service"].get("value") is None
    assert persisted["service"]["value_ref"] == "service"
    assert "legacy-secret" not in legacy.read_text()
    assert CredentialStore(home).resolve("service").secret == "legacy-secret"


def test_new_descriptors_write_values_only_to_the_core_backend(
    tmp_path: Path, monkeypatch
):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(tmp_path / "user-home"))
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("GIDEON_CREDENTIAL_BACKEND", "dotenv")
    store = CredentialStore(home)
    store.save({"api": {"type": "api_key", "value": "new-secret"}})
    payload = (home / "credentials.json").read_text()
    assert "new-secret" not in payload
    assert store.resolve("api").secret == "new-secret"
    assert "new-secret" in (home / ".env").read_text()
