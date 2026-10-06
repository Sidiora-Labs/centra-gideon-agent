import os
import stat

import pytest

from gideon.core.database_privacy import prepare_database
from gideon.operations.durability.home_paths import LinkInTheWay


@pytest.mark.parametrize("suffix", ["", "-journal", "-wal", "-shm"])
def test_linked_database_family_refused_before_changes(tmp_path, monkeypatch, suffix):
    home = tmp_path / "home"
    home.mkdir(mode=0o755)
    monkeypatch.setenv("GIDEON_HOME", str(home))
    outside = tmp_path / "outside"
    outside.write_bytes(b"unchanged")
    outside.chmod(0o644)
    database = home / "store.db"
    os.link(outside, str(database) + suffix)
    with pytest.raises(LinkInTheWay):
        prepare_database(database)
    assert outside.read_bytes() == b"unchanged"
    assert stat.S_IMODE(outside.stat().st_mode) == 0o644
    assert stat.S_IMODE(home.stat().st_mode) == 0o755


def test_linked_parent_refused_before_mkdir(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    outside = tmp_path / "outside"
    outside.mkdir()
    (home / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(LinkInTheWay):
        prepare_database(home / "linked/new/store.db")
    assert not (outside / "new").exists()


def test_external_database_keeps_supported_permissions(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    database = tmp_path / "external/store.db"
    prepare_database(database)
    assert database.parent.is_dir()
    assert not database.exists()
