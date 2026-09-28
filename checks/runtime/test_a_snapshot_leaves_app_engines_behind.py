"""App sidecar environments are machine-built and never travel with durable state."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest

from gideon.integrations.local_models.sidecar import SidecarInstall
from gideon.operations.durability import inventory as inv
from gideon.operations.durability.shards import export_shards
from gideon.workspace.portability import apply_import_zip, create_export_zip
from gideon.workspace.snapshot import restore_main, snapshot_main


def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> Path:
    home = tmp_path / name
    home.mkdir()
    (home / "config.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("GIDEON_HOME", str(home))
    return home


def _app(home: Path, name: str, display_name: str) -> Path:
    app = home / "apps" / name
    (app / "data").mkdir(parents=True)
    (app / "app.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "1.0.0",
                "displayName": display_name,
                "description": "sidecar engine portability fixture",
                "provider": {
                    "type": "model",
                    "implementation": "provider:create",
                    "execution": "sidecar",
                },
                "dependencies": {"pythonDependencies": []},
            }
        ),
        encoding="utf-8",
    )
    (app / "installed.json").write_text(
        json.dumps({"name": name, "version": "1.0.0", "enabled": True}),
        encoding="utf-8",
    )
    (app / "data" / "config.json").write_text('{"retained":true}\n', encoding="utf-8")
    return app


def _install_engine(app_name: str) -> SidecarInstall:
    install = SidecarInstall.for_app(app_name)
    assert install is not None
    assert install.run(), install.error
    assert install.installed
    return install


def _snapshot(tmp_path: Path) -> Path:
    output = tmp_path / "snapshots"
    assert snapshot_main([str(output)]) == 0
    [archive] = sorted(output.glob("gideon-snapshot-*.tar.gz"))
    return archive


def _members(archive: Path) -> list[str]:
    with tarfile.open(archive, "r:gz") as source:
        return [member.name.split("/", 1)[1] for member in source.getmembers() if "/" in member.name]


def _old_archive(tmp_path: Path, app: Path) -> Path:
    root = tmp_path / "gideon-snapshot-old"
    (root / "apps" / app.name).parent.mkdir(parents=True)
    import shutil

    shutil.copytree(app, root / "apps" / app.name)
    archive = tmp_path / "old-gideon-snapshot.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        output.add(root, arcname=root.name)
    return archive


def _old_import_zip(tmp_path: Path, app: Path) -> Path:
    archive = tmp_path / "old-gideon-export.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for source in app.rglob("*"):
            if source.is_file():
                output.write(source, f"gideon-export/apps/{app.name}/{source.relative_to(app).as_posix()}")
    return archive


def _engine_members(paths: list[str]) -> list[str]:
    return [path for path in paths if "/venv/" in f"/{path}/" or path.endswith("/venv")]


def test_snapshot_export_and_shards_leave_every_app_engine_behind(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch, "home")
    app = _app(home, "fixture-sidecar", "Fixture Sidecar")
    _install_engine(app.name)
    rollback = home / "apps" / f".{app.name}.rollback"
    rollback.mkdir()
    # A real second sidecar installation exercises hidden rollback trees too.
    rollback_app = rollback / "app.json"
    rollback_app.write_text((app / "app.json").read_text(encoding="utf-8"), encoding="utf-8")
    rollback_install = SidecarInstall(
        app.name, requirements=[], venv=rollback / "venv"
    )
    assert rollback_install.run(), rollback_install.error

    archive = _snapshot(tmp_path)
    members = _members(archive)
    assert _engine_members(members) == []
    for retained in (
        f"apps/{app.name}/app.json",
        f"apps/{app.name}/installed.json",
        f"apps/{app.name}/data/config.json",
    ):
        assert retained in members

    portable, _manifest = create_export_zip()
    with zipfile.ZipFile(io.BytesIO(portable)) as source:
        exported = [name.split("/", 1)[1] for name in source.namelist() if "/" in name]
    assert _engine_members(exported) == []
    assert f"apps/{app.name}/data/config.json" in exported

    shards = tmp_path / "shards"
    export_shards(home, shards)
    blob_hashes = {path.name for path in (shards / "apps" / "blobs").rglob("*") if path.is_file()}
    engine_files = [path for path in (app / "venv").rglob("*") if path.is_file() and not path.is_symlink()]
    assert engine_files
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() not in blob_hashes for path in engine_files)
    assert hashlib.sha256((app / "data" / "config.json").read_bytes()).hexdigest() in blob_hashes


@pytest.mark.parametrize("mode", ["replace", "merge"])
def test_old_snapshot_engine_is_not_restored_and_existing_install_action_works(
    tmp_path, monkeypatch, capsys, mode
):
    source_home = _home(tmp_path, monkeypatch, "source")
    source_app = _app(source_home, "fixture-sidecar", "Fixture Sidecar")
    _install_engine(source_app.name)
    old = _old_archive(tmp_path, source_app)
    assert f"apps/{source_app.name}/venv/pyvenv.cfg" in _members(old)

    restored_home = _home(tmp_path, monkeypatch, "restored")
    capsys.readouterr()
    assert restore_main([str(old), "--mode", mode]) == 0

    restored = restored_home / "apps" / source_app.name
    assert (restored / "data" / "config.json").read_text(encoding="utf-8") == '{"retained":true}\n'
    assert not (restored / "venv").exists()
    output = capsys.readouterr().out
    assert output.count("Fixture Sidecar") == 1
    assert "app engine" in output
    if "has no app engine here" in output:
        assert f"POST /api/models/sidecar/{source_app.name}/install" in output
        install = SidecarInstall.for_app(source_app.name)
        assert install is not None and not install.installed
        assert install.run(), install.error
        assert SidecarInstall.for_app(source_app.name).installed


@pytest.mark.parametrize("mode", ["replace", "merge"])
def test_old_import_engine_is_not_planted_in_either_mode(tmp_path, monkeypatch, mode):
    source_home = _home(tmp_path, monkeypatch, "source")
    source_app = _app(source_home, "fixture-sidecar", "Fixture Sidecar")
    _install_engine(source_app.name)
    archive = _old_import_zip(tmp_path, source_app)
    target_home = _home(tmp_path, monkeypatch, "target")

    result = apply_import_zip(archive, mode=mode)

    restored = target_home / "apps" / source_app.name
    assert result["mode"] == mode
    assert (restored / "data" / "config.json").read_text(encoding="utf-8") == '{"retained":true}\n'
    assert not (restored / "venv").exists()
    install = SidecarInstall.for_app(source_app.name)
    assert install is not None and not install.installed


def test_restore_names_only_missing_declared_engines(tmp_path, monkeypatch, capsys):
    source_home = _home(tmp_path, monkeypatch, "source")
    missing_app = _app(source_home, "needs-engine", "Needs Engine")
    _install_engine(missing_app.name)
    for name, display, execution in (
        ("plain-app", "Plain App", ""),
        ("in-process", "In Process", "in-process"),
    ):
        app = _app(source_home, name, display)
        manifest = json.loads((app / "app.json").read_text(encoding="utf-8"))
        if execution:
            manifest["provider"]["execution"] = execution
        else:
            manifest["provider"].pop("execution")
        (app / "app.json").write_text(json.dumps(manifest), encoding="utf-8")

    archive = _snapshot(tmp_path)
    _home(tmp_path, monkeypatch, "fresh")
    capsys.readouterr()
    assert restore_main([str(archive), "--mode", "replace"]) == 0
    notices = [line for line in capsys.readouterr().out.splitlines() if "app engine" in line]
    assert len(notices) == 1
    assert "Needs Engine" in notices[0]
    if "has no app engine here" in notices[0]:
        assert "POST /api/models/sidecar/needs-engine/install" in notices[0]


def test_merge_keeps_a_real_local_engine_and_does_not_name_it(tmp_path, monkeypatch, capsys):
    source_home = _home(tmp_path, monkeypatch, "source")
    source_app = _app(source_home, "fixture-sidecar", "Fixture Sidecar")
    _install_engine(source_app.name)
    old = _old_archive(tmp_path, source_app)

    here = _home(tmp_path, monkeypatch, "here")
    local_app = _app(here, source_app.name, "Fixture Sidecar")
    _install_engine(local_app.name)
    capsys.readouterr()
    assert restore_main([str(old), "--mode", "merge"]) == 0

    assert SidecarInstall.for_app(local_app.name).installed
    assert (local_app / "venv" / "bin" / "python").is_file()
    assert "app engine" not in capsys.readouterr().out


def test_apps_inventory_declares_only_the_machine_engine_as_derived():
    assert inv.by_id("apps").derived_within == ("*/venv",)
