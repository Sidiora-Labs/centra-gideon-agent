"""A configured workspace checkout does not define the running package kind."""

from __future__ import annotations

import shutil
from pathlib import Path

from gideon.operations import self_update


def test_install_kind_follows_running_package_when_workspace_is_another_checkout(
    tmp_path: Path, monkeypatch
) -> None:
    other_checkout = tmp_path / "workspace-checkout"
    (other_checkout / ".git").mkdir(parents=True)
    monkeypatch.setenv("GIDEON_PROJECT_DIR", str(other_checkout))
    monkeypatch.delenv("GIDEON_INSTALL_KIND", raising=False)

    expected = self_update._install_kind_for_package(self_update.__file__)
    assert self_update.detect_install_kind() == expected


def test_package_outside_a_checkout_remains_a_pip_install(tmp_path: Path) -> None:
    installed_package = (
        tmp_path / "venv" / "lib" / "python3.12" / "site-packages" / "gideon"
    )
    operations = installed_package / "operations"
    operations.mkdir(parents=True)
    copied_module = operations / "self_update.py"
    shutil.copy2(self_update.__file__, copied_module)

    assert self_update._install_kind_for_package(copied_module) == "pip"


def test_installed_target_inside_checkout_remains_a_pip_install(tmp_path: Path) -> None:
    checkout = tmp_path / "project"
    (checkout / ".git").mkdir(parents=True)
    operations = checkout / "vendor" / "gideon" / "operations"
    operations.mkdir(parents=True)
    copied_module = operations / "self_update.py"
    shutil.copy2(self_update.__file__, copied_module)

    assert self_update._install_kind_for_package(copied_module) == "pip"
