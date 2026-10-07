"""Installed service lookup reads actual unit/plist files without service commands."""

import plistlib
from pathlib import Path

import pytest

from gideon.operations.service import controller
from gideon.operations.service.common import Platform


@pytest.mark.parametrize("platform", [Platform.SYSTEMD, Platform.LAUNCHD])
def test_actual_installed_definition_matches_only_its_home(
    tmp_path, monkeypatch, platform
):
    home = tmp_path / "home with spaces"
    other = tmp_path / "another-home"
    path = tmp_path / (
        "gideon.service" if platform == Platform.SYSTEMD else "gideon.plist"
    )
    if platform == Platform.SYSTEMD:
        path.write_text(
            f'[Service]\nEnvironment="HOME={tmp_path}"\nEnvironment="GIDEON_HOME={home}"\nExecStart=/bin/false\n'
        )
        monkeypatch.setattr(controller.linux, "UNIT_PATH", path)
    else:
        path.write_bytes(
            plistlib.dumps(
                {
                    "EnvironmentVariables": {
                        "HOME": str(tmp_path),
                        "GIDEON_HOME": str(home),
                    },
                    "ProgramArguments": ["/bin/false"],
                }
            )
        )
        monkeypatch.setattr(controller.macos, "PLIST_PATH", path)
    monkeypatch.setattr(controller, "current_platform", lambda: platform)
    monkeypatch.setenv("GIDEON_HOME", str(home))
    before = path.read_bytes()
    assert controller.this_homes_service() == path
    assert not home.exists()
    monkeypatch.setenv("GIDEON_HOME", str(other))
    assert controller.this_homes_service() is None
    assert path.read_bytes() == before
    assert not other.exists()


def test_legacy_home_and_stopped_service_definition(tmp_path, monkeypatch):
    path = tmp_path / "legacy.service"
    path.write_text(f'[Service]\nEnvironment="HOME={tmp_path}"\nExecStart=/bin/false\n')
    monkeypatch.setattr(controller, "current_platform", lambda: Platform.SYSTEMD)
    monkeypatch.setattr(controller.linux, "UNIT_PATH", path)
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / ".gideon"))
    assert controller.this_homes_service() == path
    assert not (tmp_path / ".gideon").exists()


@pytest.mark.parametrize(
    "content",
    [
        '[Service]\nEnvironment="broken',
        '[Service]\nEnvironment="GIDEON_HOME=relative"',
        '[Service]\nEnvironment="HOME=/tmp"\nEnvironment=',
        '[Unit]\nEnvironment="GIDEON_HOME=/tmp"',
    ],
)
def test_unreadable_or_unowned_definitions_do_not_claim_service(
    tmp_path, monkeypatch, content
):
    path = tmp_path / "bad.service"
    path.write_text(content)
    monkeypatch.setattr(controller, "current_platform", lambda: Platform.SYSTEMD)
    monkeypatch.setattr(controller.linux, "UNIT_PATH", path)
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    assert controller.this_homes_service() is None
    assert path.read_text() == content
    path.unlink()
    assert controller.this_homes_service() is None
