import logging
from pathlib import Path

from gideon.core.config import loader
from gideon.core.config.locations import configuration_home, directory_partition


def test_workspace_precedence_and_outbox_use_real_paths(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.delenv("GIDEON_WORKSPACE", raising=False)
    saved = tmp_path / "saved"
    (home / "workspace_dir").write_text(str(saved), encoding="utf-8")
    assert loader.workspace_root() == saved
    assert saved.is_dir()
    override = tmp_path / "override"
    monkeypatch.setenv("GIDEON_WORKSPACE", str(override))
    assert loader.workspace_root() == override
    assert loader.outbox_dir() == override / "outbox"
    assert (override / "outbox").is_dir()


def test_home_guard_does_not_accept_system_locations(tmp_path):
    logger = logging.getLogger(__name__)
    for forbidden in ("/", "/usr", "/etc/private", "/System/Library"):
        assert configuration_home(forbidden, tmp_path, logger) == tmp_path
    allowed = tmp_path / "alternate"
    assert configuration_home(str(allowed), tmp_path, logger) == allowed
    assert not allowed.exists()


def test_memory_partition_canonicalizes_symlinks_and_bounds_long_names(
    tmp_path, monkeypatch
):
    target = tmp_path / "workspace"
    target.mkdir()
    link = tmp_path / "shortcut"
    link.symlink_to(target, target_is_directory=True)
    assert directory_partition(str(link)) == directory_partition(str(target))
    first = directory_partition(str(target / ("a" * 200)))
    second = directory_partition(str(target / ("a" * 199 + "b")))
    assert len(first) == len(second) == 120
    assert first != second
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    assert loader.memory_dir_for_cwd().parts[-2:] == ("_ext", "_default")
    assert loader.memory_dir_for_cwd(str(target)).name == directory_partition(
        str(target)
    )
