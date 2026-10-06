from __future__ import annotations

import os
from pathlib import Path

import pytest

from gideon.extensions.apps import app_manager, manager
from gideon.extensions.apps.staging import UnsafeBundleError, survey
from gideon.security.supply_chain import Verdict, default_scanner


def test_mutation_after_survey_never_produces_install_tree(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    payload = source / "main.py"
    payload.write_text("value = 1\n", encoding="utf-8")
    snapshot = survey(source)
    payload.write_text("import os; os.system('rm -rf /')\n", encoding="utf-8")
    destination = tmp_path / "live"
    with pytest.raises(UnsafeBundleError, match="changed"):
        snapshot.copy_to(destination)
    assert not destination.exists()


def test_scanner_checks_formerly_skipped_shipped_directory(tmp_path: Path) -> None:
    staged = tmp_path / "staged"
    nested = staged / ".venv" / "lib"
    nested.mkdir(parents=True)
    (nested / "payload.py").write_text(
        "import os; os.system('rm -rf /')\n", encoding="utf-8"
    )
    report = default_scanner.scan(staged)
    assert report.verdict is Verdict.DANGEROUS
    assert any(f.path == ".venv/lib/payload.py" for f in report.findings)


def test_install_uses_staged_bundle_and_refuses_external_link(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    source = tmp_path / "source-app"
    source.mkdir()
    (source / "app.json").write_text(
        '{"name":"snapshot-app","version":"1.0.0","displayName":"Snapshot",'
        '"description":"test"}',
        encoding="utf-8",
    )
    (source / "main.py").write_text("print('safe')\n", encoding="utf-8")
    installed = app_manager.install(source)
    assert installed.ok, installed.error
    live = manager.app_dir("snapshot-app")
    assert (live / "main.py").read_text(encoding="utf-8") == "print('safe')\n"

    outside = tmp_path / "outside.py"
    outside.write_text("print('outside')\n", encoding="utf-8")
    os.symlink(outside, source / "escape.py")
    refused = app_manager.install(source)
    assert not refused.ok
    assert (live / "main.py").read_text(encoding="utf-8") == "print('safe')\n"
