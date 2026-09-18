"""Seed native (Tier-1) apps — ``native`` manifests become real installed apps on
first run (seed_builtin_apps), seeded ONCE, registered through the installed-app
path (never double), and LOCKED ON (disable/uninstall/force-uninstall refused).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from gideon.extensions.apps import app_manager, manager
from gideon.extensions.providers import loader


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


def test_ollama_migration_demotes_builtin_to_local(tmp_path):
    """ollama-models was de-cored from native to first-party but its installed.json
    still says origin=builtin, locking it. The migration in seed_builtin_apps() must
    downgrade origin to local and remove it from the seed marker so the user can
    disable/uninstall like any first-party app."""
    ollama_dir = tmp_path / "apps" / "ollama-models"
    ollama_dir.mkdir(parents=True)
    mani = {
        "name": "ollama-models",
        "version": "1.0.0",
        "displayName": "Ollama",
        "description": "local model runtime",
        "provider": {"type": "model", "implementation": "provider:create_provider"},
    }
    (ollama_dir / "app.json").write_text(json.dumps(mani), encoding="utf-8")
    meta = {
        "name": "ollama-models",
        "version": "1.0.0",
        "displayName": "Ollama",
        "enabled": True,
        "installedAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z",
        "source": "builtin",
        "origin": "builtin",
        "resources": "gateway",
        "lifecycle": "gateway",
        "schemaVersion": 2,
    }
    (ollama_dir / "installed.json").write_text(json.dumps(meta), encoding="utf-8")

    marker_path = tmp_path / "apps" / ".seeded-builtins.json"
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(json.dumps({"seeded": ["ollama-models"]}), encoding="utf-8")

    app_manager.seed_builtin_apps()

    updated_meta = manager._read_installed("ollama-models")
    assert updated_meta is not None
    assert updated_meta.origin == "local"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert "ollama-models" not in marker["seeded"]

    assert not app_manager._is_native("ollama-models")


def _retire(tmp_path: Path, name: str) -> None:
    """Retire a built-in the way core actually does: delete it from ``apps/native/``."""
    shutil.rmtree(tmp_path / "native" / name)


def _marker(tmp_path: Path) -> list[str]:
    path = tmp_path / "apps" / ".seeded-builtins.json"
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8"))["seeded"]


def test_a_retired_builtin_with_no_data_is_removed(tmp_path):
    """The general rule the ollama-models migration was one hardcoded case of.

    A built-in is retired by deleting it from ``apps/native/``; the installed copy in
    the user's home outlives that deletion and stays LOCKED ON (origin ``builtin`` ⇒
    every removal rung refuses), so it sits in the Apps UI calling into a core
    implementation that no longer ships. With nothing behind it and nothing in it,
    there is nothing to keep.
    """
    _native_manifest(tmp_path, "brave-search", native=True)
    assert app_manager.seed_builtin_apps() == ["brave-search"]
    _retire(tmp_path, "brave-search")

    app_manager.seed_builtin_apps()

    assert manager._read_installed("brave-search") is None
    assert not manager.app_dir("brave-search").exists()
    assert "brave-search" not in _marker(tmp_path)


def test_an_empty_data_dir_does_not_count_as_user_data(tmp_path):
    """Every seeded app gets a ``data/`` whether or not it was ever used, so a
    present-but-empty one must not keep a dead app alive forever."""
    _native_manifest(tmp_path, "brave-search", native=True)
    app_manager.seed_builtin_apps()
    assert (manager.app_dir("brave-search") / "data").is_dir()
    _retire(tmp_path, "brave-search")

    app_manager.seed_builtin_apps()

    assert manager._read_installed("brave-search") is None


def test_a_retired_builtin_holding_user_data_is_preserved_and_unlocked(tmp_path):
    """Data-holding retired built-ins are kept and DE-CORED, never deleted.

    Removing one would be a silent destructive act the user never asked for, taken at
    gateway startup. Unlocking hands the decision back to them: the app becomes an
    ordinary local app, so the uninstall rungs (including the keep-data one) are
    available for the first time.
    """
    _native_manifest(tmp_path, "brave-search", native=True)
    app_manager.seed_builtin_apps()
    notes = manager.app_dir("brave-search") / "data" / "config.json"
    notes.write_text(json.dumps({"api_key_set": True}), encoding="utf-8")
    _retire(tmp_path, "brave-search")

    app_manager.seed_builtin_apps()

    meta = manager._read_installed("brave-search")
    assert meta is not None, "the user's data was deleted with the app"
    assert meta.origin == "local"
    assert meta.enabled is True
    assert json.loads(notes.read_text(encoding="utf-8")) == {"api_key_set": True}

    assert not app_manager._is_native("brave-search")
    installed_manifest = json.loads(
        (manager.app_dir("brave-search") / "app.json").read_text(encoding="utf-8")
    )
    assert "native" not in installed_manifest, "the manifest still locks the app"
    assert app_manager.disable("brave-search") is True
    assert "brave-search" not in _marker(tmp_path)


def test_a_parked_copy_of_data_also_counts_as_user_data(tmp_path):
    """The same 'unconsumed copy' the keep-data rung refuses over keeps the app, too.

    A park left by an earlier keep-data uninstall (or by a failed one) is data the
    user has not consumed. ``describe_app_data`` is the one place that question is
    answered, so this path asks it rather than looking only at the live ``data/``.
    """
    _native_manifest(tmp_path, "brave-search", native=True)
    app_manager.seed_builtin_apps()
    parked = app_manager._preserved_data_dir("brave-search")
    parked.mkdir(parents=True)
    (parked / "note.md").write_text("earlier work\n", encoding="utf-8")
    _retire(tmp_path, "brave-search")

    app_manager.seed_builtin_apps()

    meta = manager._read_installed("brave-search")
    assert meta is not None and meta.origin == "local"
    assert (parked / "note.md").is_file()


def test_a_retired_builtin_implemented_in_its_own_package_stays_operational(tmp_path):
    """An app whose code ships inside its own package did not lose anything.

    Its provider module is a file in ``apps/<name>/``, which is where the loader
    imports it from, so it keeps working with core's copy gone. It is unlocked (it is
    no longer ours to lock) and left running — deleting it would delete a working app.
    """
    name = "ollama-models"
    d = tmp_path / "native" / name
    d.mkdir(parents=True)
    (d / "app.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "1.0.0",
                "displayName": "Ollama",
                "description": "local model runtime",
                "native": True,
                "provider": {
                    "type": "model",
                    "implementation": "provider:create_provider",
                },
            }
        ),
        encoding="utf-8",
    )
    (d / "provider.py").write_text(
        "def create_provider(config):\n    return config\n", encoding="utf-8"
    )
    assert app_manager.seed_builtin_apps() == [name]
    _retire(tmp_path, name)

    app_manager.seed_builtin_apps()

    meta = manager._read_installed(name)
    assert meta is not None, "an independently implemented app was deleted"
    assert meta.origin == "local" and meta.enabled is True
    assert (manager.app_dir(name) / "provider.py").is_file()
    assert not app_manager._is_native(name)


def test_an_independent_app_sharing_a_retired_builtins_name_is_untouched(tmp_path):
    """A local app that merely shares the name is somebody else's app.

    The marker remembers the name we once seeded; it says nothing about what is
    installed under it now. Origin is what distinguishes them, and a non-``builtin``
    origin is never ours to remove, unlock, or rewrite.
    """
    name = "brave-search"
    marker_path = tmp_path / "apps" / ".seeded-builtins.json"
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(json.dumps({"seeded": [name]}), encoding="utf-8")
    d = manager.app_dir(name)
    d.mkdir(parents=True)
    (d / "app.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "9.9.9",
                "displayName": "Mine",
                "description": "a local app of my own",
            }
        ),
        encoding="utf-8",
    )
    (d / "installed.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "9.9.9",
                "displayName": "Mine",
                "enabled": True,
                "origin": "local",
                "source": "/home/me/src/brave-search",
                "schemaVersion": 2,
            }
        ),
        encoding="utf-8",
    )
    before = (d / "installed.json").read_text(encoding="utf-8")

    app_manager.seed_builtin_apps()

    assert (d / "installed.json").read_text(encoding="utf-8") == before
    assert (d / "app.json").is_file()
    meta = manager._read_installed(name)
    assert meta is not None and meta.version == "9.9.9"


def test_shipped_builtins_are_never_retired(tmp_path):
    """The rule keys on the SHIPPED catalog, so a built-in still in it is untouched —
    with data or without, locked on either way, across any number of restarts."""
    _native_manifest(tmp_path, "brave-search", native=True)
    _native_manifest(tmp_path, "native-tasks", native=True)
    app_manager.seed_builtin_apps()
    (manager.app_dir("native-tasks") / "data" / "config.json").write_text(
        json.dumps({"keep": True}), encoding="utf-8"
    )

    assert app_manager.seed_builtin_apps() == []
    assert app_manager.seed_builtin_apps() == []

    for name in ("brave-search", "native-tasks"):
        meta = manager._read_installed(name)
        assert meta is not None and meta.origin == "builtin"
        assert app_manager._is_native(name), f"{name} lost its lock"
        assert (manager.app_dir(name) / "app.json").is_file()
    assert sorted(_marker(tmp_path)) == ["brave-search", "native-tasks"]
    assert json.loads(
        (manager.app_dir("native-tasks") / "data" / "config.json").read_text(
            encoding="utf-8"
        )
    ) == {"keep": True}


def test_an_unparseable_shipped_manifest_does_not_read_as_retirement(tmp_path):
    """A built-in whose ``app.json`` fails to parse is a broken build, not a retired
    app: the directory is still there, so the app keeps its record and its data."""
    _native_manifest(tmp_path, "brave-search", native=True)
    app_manager.seed_builtin_apps()
    (tmp_path / "native" / "brave-search" / "app.json").write_text(
        "{not json", encoding="utf-8"
    )

    app_manager.seed_builtin_apps()

    meta = manager._read_installed("brave-search")
    assert meta is not None and meta.origin == "builtin"


def test_a_missing_native_dir_retires_nothing(tmp_path):
    """Every built-in gone at once is a broken install. Acting on it would wipe the
    user's whole app tree on the first boot after a bad package."""
    _native_manifest(tmp_path, "brave-search", native=True)
    app_manager.seed_builtin_apps()
    shutil.rmtree(tmp_path / "native")

    assert app_manager.seed_builtin_apps() == []

    assert manager._read_installed("brave-search") is not None
    assert (manager.app_dir("brave-search") / "app.json").is_file()
