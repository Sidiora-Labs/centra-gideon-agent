"""Immutable, no-follow staging for application bundles.

The survey is the authority for the exact tree copied into quarantine.  A caller must
scan and make every install decision against that copy, then install that same copy.
"""
from __future__ import annotations

import errno
import os
import shutil
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


class UnsafeBundleError(ValueError):
    """The source tree is unsafe or changed after it was surveyed."""


@dataclass(frozen=True)
class Entry:
    path: tuple[str, ...]
    kind: str
    mode: int
    size: int
    mtime_ns: int
    identity: tuple[int, int]
    link: str = ""
    hardlinks: int = 1


@dataclass(frozen=True)
class Survey:
    root: Path
    entries: tuple[Entry, ...]

    def copy_to(self, destination: Path) -> Path:
        """Copy the surveyed tree; refuse replacements, additions, and removals."""
        destination = Path(destination)
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(destination)
        current, _root_stat = _walk(self.root)
        if _tree_signature(current) != _tree_signature(self.entries):
            raise UnsafeBundleError("app source changed after it was surveyed")
        os.mkdir(destination, 0o700)
        try:
            dirs: list[Entry] = []
            for entry in self.entries:
                out = destination.joinpath(*entry.path)
                if entry.kind == "dir":
                    if entry.path:
                        os.mkdir(out, 0o700)
                    dirs.append(entry)
                elif entry.kind == "link":
                    try:
                        with _parent_fd(self.root, entry.path[:-1]) as (parent_fd, name):
                            link_name = entry.path[-1]
                            st = os.stat(link_name, dir_fd=parent_fd, follow_symlinks=False)
                            target = os.readlink(link_name, dir_fd=parent_fd)
                    except OSError as exc:
                        raise _changed(entry) from exc
                    if not stat.S_ISLNK(st.st_mode) or target != entry.link:
                        raise _changed(entry)
                    os.symlink(entry.link, out)
                else:
                    _copy_file(self.root, out, entry)
            # Recheck inventory after reads: a concurrent addition/removal cannot silently
            # change what the caller believes it scanned.
            current, _root_stat = _walk(self.root)
            if _tree_signature(current) != _tree_signature(self.entries):
                raise UnsafeBundleError("app source changed while it was being staged")
            for entry in sorted(dirs, key=lambda e: len(e.path), reverse=True):
                target = destination.joinpath(*entry.path)
                os.chmod(target, stat.S_IMODE(entry.mode) & 0o777)
            return destination
        except BaseException:
            shutil.rmtree(destination, ignore_errors=True)
            raise


def survey(source: Path) -> Survey:
    """Survey all entries without opening link targets; only regular files, folders,
    and relative links resolving to an in-tree regular file are accepted."""
    root = Path(os.path.realpath(Path(source)))
    root_st = os.lstat(root)
    if not stat.S_ISDIR(root_st.st_mode):
        raise UnsafeBundleError(f"{source!s} is not a directory")
    entries, _ = _walk(root, root_st)
    names: dict[tuple[int, int], int] = {}
    for item in entries:
        if item.kind == "file":
            names[item.identity] = names.get(item.identity, 0) + 1
    for item in entries:
        if item.kind == "file" and item.hardlinks > names[item.identity]:
            raise UnsafeBundleError(f"{_display(item.path)} is linked outside the app")
    known = {entry.path: entry for entry in entries}
    for entry in entries:
        if entry.kind == "link":
            _validate_link(entry, known)
    return Survey(root, tuple(entries))


def _walk(root: Path, root_st: os.stat_result | None = None) -> tuple[list[Entry], os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        root_fd = os.open(root, flags)
    except OSError as exc:
        raise UnsafeBundleError("app source root changed type") from exc
    found: list[Entry] = []
    try:
        root_st = os.fstat(root_fd)
        if not stat.S_ISDIR(root_st.st_mode):
            raise UnsafeBundleError("app source root changed type")

        def visit(folder_fd: int, parent: tuple[str, ...], folder_stat: os.stat_result) -> None:
            found.append(Entry(parent, "dir", folder_stat.st_mode, 0,
                               folder_stat.st_mtime_ns, (folder_stat.st_dev, folder_stat.st_ino)))
            try:
                with os.scandir(folder_fd) as scan:
                    children = [(item.name, item.stat(follow_symlinks=False)) for item in scan]
            except OSError as exc:
                raise UnsafeBundleError(f"cannot survey {_display(parent)}: {exc}") from exc
            for name, st in sorted(children, key=lambda row: row[0]):
                rel = (*parent, name)
                if stat.S_ISDIR(st.st_mode):
                    try:
                        child_fd = os.open(name, flags, dir_fd=folder_fd)
                    except OSError as exc:
                        raise UnsafeBundleError(f"{_display(rel)} changed during survey") from exc
                    try:
                        opened = os.fstat(child_fd)
                        if (opened.st_dev, opened.st_ino) != (st.st_dev, st.st_ino):
                            raise UnsafeBundleError(f"{_display(rel)} changed during survey")
                        visit(child_fd, rel, opened)
                    finally:
                        os.close(child_fd)
                elif stat.S_ISREG(st.st_mode):
                    found.append(Entry(rel, "file", st.st_mode, st.st_size, st.st_mtime_ns,
                                       (st.st_dev, st.st_ino), hardlinks=st.st_nlink))
                elif stat.S_ISLNK(st.st_mode):
                    try:
                        target = os.readlink(name, dir_fd=folder_fd)
                        check = os.stat(name, dir_fd=folder_fd, follow_symlinks=False)
                    except OSError as exc:
                        raise UnsafeBundleError(f"{_display(rel)} changed during survey") from exc
                    if (check.st_dev, check.st_ino) != (st.st_dev, st.st_ino):
                        raise UnsafeBundleError(f"{_display(rel)} changed during survey")
                    found.append(Entry(rel, "link", st.st_mode, 0, st.st_mtime_ns,
                                       (st.st_dev, st.st_ino), target))
                else:
                    raise UnsafeBundleError(f"{_display(rel)} is a special file")

        visit(root_fd, (), root_st)
    finally:
        os.close(root_fd)
    found.sort(key=lambda item: item.path)
    return found, root_st


def _validate_link(entry: Entry, known: dict[tuple[str, ...], Entry]) -> None:
    target = entry.link
    if os.path.isabs(target):
        raise UnsafeBundleError(f"{_display(entry.path)} links outside the app")
    here = list(entry.path[:-1])
    parts = target.split("/")
    hops = 0
    while parts:
        part = parts.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            if not here:
                raise UnsafeBundleError(f"{_display(entry.path)} links outside the app")
            here.pop()
            continue
        target = known.get((*here, part))
        if target is None:
            raise UnsafeBundleError(f"{_display(entry.path)} links to a missing path")
        if target.kind == "link":
            hops += 1
            if hops > 40:
                raise UnsafeBundleError(f"{_display(entry.path)} contains a link loop")
            nested = target.link
            if os.path.isabs(nested):
                raise UnsafeBundleError(f"{_display(entry.path)} links outside the app")
            parts = nested.split("/") + parts
            continue
        if parts and target.kind != "dir":
            raise UnsafeBundleError(f"{_display(entry.path)} links through a non-directory")
        here.append(part)
    final = known.get(tuple(here))
    if final is None or final.kind != "file":
        raise UnsafeBundleError(f"{_display(entry.path)} must resolve to an app file")


@contextmanager
def _parent_fd(root: Path, parts: tuple[str, ...]):
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(root, flags)
    try:
        for part in parts:
            next_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd, ""
    finally:
        os.close(fd)


def _copy_file(root: Path, destination: Path, entry: Entry) -> None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        with _parent_fd(root, entry.path[:-1]) as (parent_fd, _):
            fd = os.open(entry.path[-1], flags, dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOENT):
            raise _changed(entry) from exc
        raise
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_mode != entry.mode
                or (st.st_dev, st.st_ino) != entry.identity
                or st.st_size != entry.size or st.st_mtime_ns != entry.mtime_ns):
            raise _changed(entry)
        with os.fdopen(fd, "rb", closefd=False) as fin, open(destination, "xb") as fout:
            shutil.copyfileobj(fin, fout, 1024 * 1024)
        after = os.fstat(fd)
        if (after.st_size, after.st_mtime_ns, after.st_mode) != (
                entry.size, entry.mtime_ns, entry.mode):
            raise _changed(entry)
        os.chmod(destination, stat.S_IMODE(entry.mode) & 0o777)
    finally:
        os.close(fd)


def _tree_signature(entries: list[Entry] | tuple[Entry, ...]) -> tuple:
    return tuple((e.path, e.kind, e.mode, e.identity, e.size, e.mtime_ns, e.link, e.hardlinks) for e in entries)


def _changed(entry: Entry) -> UnsafeBundleError:
    return UnsafeBundleError(f"{_display(entry.path)} changed while it was being staged")


def _display(parts: tuple[str, ...]) -> str:
    return "/".join(parts) or "."
