import json
import os
import stat
import tarfile
from argparse import Namespace
from datetime import datetime as RealDateTime, timezone

import pytest

from gideon.workspace import snapshot
from gideon.operations.durability import archive
from gideon.operations.durability.home_paths import LinkInTheWay


class FixedTime:
    @staticmethod
    def now(tz=None):
        return RealDateTime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


def setup_home(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    (home / 'workspace').mkdir()
    (home / 'workspace/note.txt').write_text('actual snapshot content')
    monkeypatch.setenv('GIDEON_HOME', str(home))
    monkeypatch.setattr(snapshot, 'datetime', FixedTime)
    output = tmp_path / 'backups'
    destination = output / 'gideon-snapshot-20261006T120000Z.tar.gz'
    return output, destination


def run_snapshot(output):
    return snapshot.snapshot_main(parsed=Namespace(keep=7, output_dir=str(output), list_snapshots=False))


def test_public_snapshot_private_before_first_write_and_atomic(tmp_path, monkeypatch):
    output, destination = setup_home(tmp_path, monkeypatch)
    output.mkdir()
    destination.write_bytes(b'previous archive')
    outside = tmp_path / 'outside'
    outside.write_bytes(b'untouched')
    destination.with_suffix('.tar.gz.tmp').symlink_to(outside)
    original_open = tarfile.open
    observed = []

    def checked_open(*args, **kwargs):
        stream = kwargs.get('fileobj')
        if stream is not None and kwargs.get('mode') == 'w:gz':
            assert stat.S_IMODE(os.fstat(stream.fileno()).st_mode) == 0o600
            assert os.fstat(stream.fileno()).st_size == 0
            assert destination.read_bytes() == b'previous archive'
            observed.append(True)
        return original_open(*args, **kwargs)

    monkeypatch.setattr(tarfile, 'open', checked_open)
    assert run_snapshot(output) == 0
    assert observed == [True]
    assert outside.read_bytes() == b'untouched'
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert stat.S_IMODE(archive.sidecar_path(destination).stat().st_mode) == 0o600
    with original_open(destination, 'r:gz') as tar:
        member = next(item for item in tar if item.name.endswith('/workspace/note.txt'))
        assert tar.extractfile(member).read() == b'actual snapshot content'


def test_failed_snapshot_preserves_previous_archive(tmp_path, monkeypatch):
    output, destination = setup_home(tmp_path, monkeypatch)
    output.mkdir()
    destination.write_bytes(b'previous archive')

    def fail_add(self, *args, **kwargs):
        raise OSError('archive write interrupted')

    monkeypatch.setattr(tarfile.TarFile, 'add', fail_add)
    with pytest.raises(OSError, match='interrupted'):
        run_snapshot(output)
    assert destination.read_bytes() == b'previous archive'
    assert list(output.iterdir()) == [destination]


def test_linked_output_parent_refused_before_creation(tmp_path, monkeypatch):
    output, _ = setup_home(tmp_path, monkeypatch)
    outside = tmp_path / 'outside'
    outside.mkdir()
    output.symlink_to(outside, target_is_directory=True)
    with pytest.raises(LinkInTheWay):
        run_snapshot(output / 'new')
    assert list(outside.iterdir()) == []


def test_archive_browser_sidecar_links_not_read_or_overwritten(tmp_path):
    destination = tmp_path / 'snapshot.tar.gz'
    outside = tmp_path / 'outside'
    outside.write_text(json.dumps({'domains': {'secret': 1}}))
    outside.chmod(0o644)
    os.link(outside, archive.sidecar_path(destination))
    assert archive.read_manifest(destination) is None
    archive.write_sidecar(destination, {'domains': {}})
    assert json.loads(outside.read_text()) == {'domains': {'secret': 1}}
    assert stat.S_IMODE(outside.stat().st_mode) == 0o644
