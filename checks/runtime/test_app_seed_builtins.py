"""Seed native (Tier-1) apps — ``native`` manifests become real installed apps on
first run (seed_builtin_apps), seeded ONCE, registered through the installed-app
path (never double), and LOCKED ON (disable/uninstall/force-uninstall refused).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gideon.extensions.apps import app_manager, manager
from gideon.extensions.providers import loader
from gideon.extensions.providers.registry import ProviderRegistry


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Isolate the apps config dir AND point BUNDLED_DIR at a tmp fixture tree."""
    import gideon.core.config.loader as cfg

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)

    bundled = tmp_path / "native"
    bundled.mkdir()
    monkeypatch.setattr(loader, "BUNDLED_DIR", bundled)
    return tmp_path


def _native_manifest(
    root: Path, name: str, *, native: bool, provider: bool = True
) -> None:
    d = root / "native" / name
    d.mkdir(parents=True)
    mani: dict = {
        "name": name,
        "version": "1.0.0",
        "displayName": name.title(),
        "description": f"{name} fixture",
    }
    if native:
        mani["native"] = True
    if provider:
        mani["provider"] = {
            "type": "search",
            "implementation": "gideon.integrations.search_providers.duckduckgo_provider:create_provider",
        }
    (d / "app.json").write_text(json.dumps(mani), encoding="utf-8")


def test_seeds_native_app_as_installed(tmp_path):
    _native_manifest(tmp_path, "brave-search", native=True)
    seeded = app_manager.seed_builtin_apps()
    assert seeded == ["brave-search"]
    meta = manager._read_installed("brave-search")
    assert meta is not None
    assert meta.origin == "builtin" and meta.enabled
    assert (manager.app_dir("brave-search") / "app.json").is_file()
    assert (manager.app_dir("brave-search") / "data").is_dir()


def test_non_native_is_not_seeded(tmp_path):
    _native_manifest(tmp_path, "duckduckgo-search", native=False)
    seeded = app_manager.seed_builtin_apps()
    assert seeded == []
    assert manager._read_installed("duckduckgo-search") is None


def test_seed_is_idempotent_across_runs(tmp_path):
    _native_manifest(tmp_path, "brave-search", native=True)
    assert app_manager.seed_builtin_apps() == ["brave-search"]
    assert app_manager.seed_builtin_apps() == []


def test_native_app_is_locked_disable_and_uninstall_refused(tmp_path):
    """A native (Tier-1) app is locked on: disable / uninstall / force-uninstall
    all refuse, and it stays enabled + on disk."""
    _native_manifest(tmp_path, "brave-search", native=True)
    app_manager.seed_builtin_apps()
    assert app_manager.disable("brave-search") is False
    assert app_manager.uninstall("brave-search") is False
    assert app_manager.force_uninstall("brave-search") is False
    meta = manager._read_installed("brave-search")
    assert meta is not None and meta.enabled is True
    assert (manager.app_dir("brave-search") / "app.json").is_file()


def test_native_app_survives_restart_reseed(tmp_path):
    """A seeded native app persists (installed record + registered) across a
    restart; the seed-once marker just avoids re-seeding, not de-registration."""
    _native_manifest(tmp_path, "brave-search", native=True)
    app_manager.seed_builtin_apps()
    assert app_manager.seed_builtin_apps() == []
    meta = manager._read_installed("brave-search")
    assert meta is not None and meta.enabled is True


def test_native_manifest_resyncs_from_source_on_restart(tmp_path):
    """Bug #24: a native app's app.json is packaged-source-owned (the app is locked,
    user config lives in data/config.json). A manifest edit in apps/native/ MUST
    reach the existing install on the next boot — the old seed-once-skip stranded it
    (which is why the #21 create-task schema fix didn't propagate). Edit the source
    manifest, re-run seeding, and the INSTALLED app.json must reflect the change."""
    _native_manifest(tmp_path, "brave-search", native=True)
    app_manager.seed_builtin_apps()
    installed = manager.app_dir("brave-search") / "app.json"
    assert "settingsSchema" not in installed.read_text()

    src = tmp_path / "native" / "brave-search" / "app.json"
    mani = json.loads(src.read_text())
    mani["provider"]["settingsSchema"] = {
        "type": "object",
        "properties": {"new_field": {"type": "string"}},
    }
    src.write_text(json.dumps(mani), encoding="utf-8")

    assert app_manager.seed_builtin_apps() == []
    resynced = json.loads(installed.read_text())
    assert "new_field" in (
        resynced["provider"]["settingsSchema"].get("properties") or {}
    )


def test_native_manifest_resync_preserves_metadata_and_refreshes_registry(
    tmp_path, monkeypatch
):
    """Provider declarations refresh while installed user state remains untouched."""
    _native_manifest(tmp_path, "brave-search", native=True)
    app_manager.seed_builtin_apps()

    registry = ProviderRegistry()
    installed_manifest = app_manager._manifest_of("brave-search")
    assert installed_manifest is not None
    registry.register(installed_manifest)
    monkeypatch.setattr(app_manager, "_provider_registry", lambda: registry)

    src = tmp_path / "native" / "brave-search" / "app.json"
    packaged = json.loads(src.read_text(encoding="utf-8"))
    packaged["version"] = "1.1.0"
    packaged["displayName"] = "Brave Search Updated"
    packaged["provider"]["providerType"] = "web"
    packaged["provider"]["capabilities"] = ["search", "fetch"]
    src.write_text(json.dumps(packaged), encoding="utf-8")

    assert app_manager.seed_builtin_apps() == []

    installed = app_manager._manifest_of("brave-search")
    registered = registry.get("brave-search")
    meta = manager._read_installed("brave-search")
    assert installed is not None and registered is not None and meta is not None
    assert installed.to_dict() == registered.manifest.to_dict()
    assert registered.provider_config.to_dict() == installed.provider.to_dict()
    assert installed.version == "1.1.0"
    assert meta.version == "1.0.0"
    assert meta.displayName == "Brave-Search"


def test_native_manifest_resync_preserves_user_config_data(tmp_path):
    """The re-sync is manifest-only — it must never clobber data/config.json (the
    app's user config) or installed.json (enabled state)."""
    _native_manifest(tmp_path, "brave-search", native=True)
    app_manager.seed_builtin_apps()
    data_cfg = manager.app_dir("brave-search") / "data" / "config.json"
    data_cfg.write_text(json.dumps({"user_setting": "keep-me"}), encoding="utf-8")

    src = tmp_path / "native" / "brave-search" / "app.json"
    mani = json.loads(src.read_text())
    mani["description"] = "updated description"
    src.write_text(json.dumps(mani), encoding="utf-8")
    app_manager.seed_builtin_apps()

    assert (
        "updated description"
        in (manager.app_dir("brave-search") / "app.json").read_text()
    )
    assert json.loads(data_cfg.read_text()) == {"user_setting": "keep-me"}
    assert manager._read_installed("brave-search").enabled is True


def test_native_app_skipped_by_bundled_discovery(tmp_path):
    """Native manifests register via the installed-app (seed) path only, so bundled
    discovery must skip them (no double registration). Post-taxonomy the native dir
    holds only native apps, so discovery is normally empty."""
    _native_manifest(tmp_path, "brave-search", native=True)
    _native_manifest(tmp_path, "stray-nonnative", native=False)
    discovered = {m.name for m in loader.discover_bundled_extensions()}
    assert "brave-search" not in discovered
    assert "stray-nonnative" in discovered


def test_full_bundle_resync_preserves_owned_files(tmp_path):
    _native_manifest(tmp_path, "local-models", native=True)
    source = tmp_path / "native" / "local-models"
    (source / "provider.py").write_text("VERSION = 1")
    app_manager.seed_builtin_apps()
    installed = manager.app_dir("local-models")
    metadata = (installed / "installed.json").read_bytes()
    (installed / "data" / "config.json").write_text("user config")
    (installed / "retained.py").write_text("user file")
    (source / "provider.py").write_text("VERSION = 2")
    (source / "nested").mkdir()
    (source / "nested" / "helper.py").write_text("VALUE = 3")
    (source / "data").mkdir()
    (source / "data" / "config.json").write_text("must not copy")
    (source / "installed.json").write_text("must not copy")
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "provider.pyc").write_bytes(b"must not copy")
    assert app_manager.seed_builtin_apps() == []
    assert (installed / "provider.py").read_text() == "VERSION = 2"
    assert (installed / "nested" / "helper.py").read_text() == "VALUE = 3"
    assert (installed / "data" / "config.json").read_text() == "user config"
    assert (installed / "installed.json").read_bytes() == metadata
    assert (installed / "retained.py").read_text() == "user file"
    assert not (installed / "__pycache__").exists()
    modified = (installed / "provider.py").stat().st_mtime_ns
    app_manager.seed_builtin_apps()
    assert (installed / "provider.py").stat().st_mtime_ns == modified


def test_first_seed_ignores_runtime_state(tmp_path):
    _native_manifest(tmp_path, "local-models", native=True)
    source = tmp_path / "native" / "local-models"
    (source / "data").mkdir()
    (source / "data" / "private.txt").write_text("not package data")
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "provider.pyc").write_bytes(b"cache")
    app_manager.seed_builtin_apps()
    installed = manager.app_dir("local-models")
    assert not (installed / "data" / "private.txt").exists()
    assert not (installed / "__pycache__").exists()
