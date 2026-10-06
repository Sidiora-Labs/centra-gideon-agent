"""Consistent SQLite copies for archives and app data preservation."""
from __future__ import annotations

import os
import shutil
from contextlib import closing
from pathlib import Path

from gideon.core.database_privacy import SQLITE_SIDECARS
from gideon.core.sqlite_compat import connect
from gideon.operations.durability.home_paths import guard_path


def is_database(path) -> bool:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        return False
    try:
        with path.open('rb') as source:
            return source.read(16) == b'SQLite format 3\x00'
    except OSError:
        return False


def is_sidecar(directory, name: str) -> bool:
    for suffix in SQLITE_SIDECARS:
        if name.endswith(suffix):
            base = name[:-len(suffix)]
            return is_database(Path(directory) / base) or base.endswith(('.db', '.sqlite', '.sqlite3'))
    return False


def sidecars_in(directory, names):
    return {name for name in names if is_sidecar(directory, name)}


def copy_file(source, destination, *, follow_symlinks=True):
    """Use SQLite's backup API for actual databases; propagate backup failures."""
    source, destination = guard_path(source, read=True), guard_path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not is_database(source):
        return shutil.copy2(source, destination, follow_symlinks=follow_symlinks)
    with closing(connect(str(source))) as reader, closing(connect(str(destination))) as writer:
        reader.backup(writer)
    return str(destination)


def bring_in(source, destination) -> bool:
    """Install an absent file without replaying orphaned database sidecars."""
    source, destination = guard_path(source, read=True), guard_path(destination)
    if os.path.lexists(destination) or is_sidecar(source.parent, source.name):
        return False
    if is_database(source):
        for suffix in SQLITE_SIDECARS:
            sidecar = Path(f'{destination}{suffix}')
            if os.path.lexists(sidecar):
                if sidecar.is_symlink() or not sidecar.is_file():
                    raise ValueError(f'unsafe database sidecar: {sidecar}')
                sidecar.unlink()
    copy_file(source, destination)
    return True


def databases_in(root):
    return (path for path in Path(root).rglob('*') if is_database(path))


def move_aside(source, destination):
    """Keep a database and its pending journal state together for rollback."""
    source, destination = guard_path(source, read=True), guard_path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    database = is_database(source)
    shutil.move(str(source), str(destination))
    if database:
        for suffix in SQLITE_SIDECARS:
            sidecar = Path(f'{source}{suffix}')
            if os.path.lexists(sidecar):
                shutil.move(str(sidecar), f'{destination}{suffix}')
