"""Prepare private SQLite files before a database driver can write them."""
from __future__ import annotations

import os
import stat
from pathlib import Path

SQLITE_SIDECARS = ("-journal", "-wal", "-shm")


def prepare_database(database, *, anywhere: bool = False) -> None:
    """Privatize Gideon-owned files while preserving SQLite special locations."""
    location = os.fsdecode(os.fspath(database))
    if not location or location == ":memory:" or location.startswith("file:"):
        return
    path = Path(os.path.abspath(location))
    from gideon.core.config.loader import resolve_config_dir
    home = Path(os.path.abspath(resolve_config_dir()))
    private = anywhere or path.is_relative_to(home)
    if private:
        from gideon.operations.durability.home_paths import guard_path
        for candidate in [path, *(Path(f"{path}{suffix}") for suffix in SQLITE_SIDECARS)]:
            guard_path(candidate)
    missing = []
    parent = path.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700 if private else 0o777, exist_ok=True)
        if private:
            os.chmod(directory, 0o700)
    if not private:
        return
    os.chmod(path.parent, 0o700)
    if path.is_relative_to(home) and home.exists():
        os.chmod(home, 0o700)
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(descriptor)
        current = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)):
            raise OSError(f"database is not a regular file: {path}")
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)
    for suffix in SQLITE_SIDECARS:
        sidecar = Path(f"{path}{suffix}")
        try:
            info = sidecar.lstat()
        except FileNotFoundError:
            continue
        descriptor = os.open(sidecar, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(descriptor)
            if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                    or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)):
                raise OSError(f"database sidecar changed or is linked: {sidecar}")
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
