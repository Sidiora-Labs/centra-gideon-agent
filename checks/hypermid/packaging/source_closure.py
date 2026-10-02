from __future__ import annotations

import hashlib
import json
import shutil
import stat
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
DOMAIN = b"hypermid.packaging.source-closure.v1\0"
ROOT_FILES = (
    ".dockerignore",
    "Cargo.lock",
    "Cargo.toml",
    "LICENSE",
    "MANIFEST.in",
    "package-lock.json",
    "package.json",
    "pyproject.toml",
    "rust-toolchain.toml",
    "setup.py",
)
SOURCE_ROOTS = (
    "apps/console",
    "apps/desktop",
    "checks/hypermid",
    "clients",
    "crates",
    "infrastructure/docker",
    "runtime",
    "spec/hypermid/schemas",
)
IGNORED_PARTS = frozenset(
    {
        ".coverage",
        ".build",
        ".hypothesis",
        ".mypy_cache",
        ".pytest_cache",
        "__pycache__",
        "build",
        "coverage",
        "node_modules",
        "playwright-report",
        "target",
        "test-results",
    }
)


class SourceClosureError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _included(relative: Path) -> bool:
    return (
        not any(part in IGNORED_PARTS for part in relative.parts)
        and relative.suffix not in {".pyc", ".pyo"}
    )


def _source_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for name in ROOT_FILES:
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise SourceClosureError(f"packaging source file is absent or unsafe: {name}")
        files.append(path)
    for name in SOURCE_ROOTS:
        source = root / name
        if not source.is_dir() or source.is_symlink():
            raise SourceClosureError(f"packaging source directory is absent or unsafe: {name}")
        for path in source.rglob("*"):
            relative = path.relative_to(root)
            if not _included(relative):
                continue
            if path.is_symlink():
                raise SourceClosureError(f"packaging source contains a symlink: {relative}")
            if path.is_file():
                files.append(path)
            elif not path.is_dir():
                raise SourceClosureError(f"packaging source contains a special file: {relative}")
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def capture_source_closure(root: Path) -> dict[str, Any]:
    root = root.resolve()
    entries = []
    for path in _source_files(root):
        mode = stat.S_IMODE(path.stat().st_mode)
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": _sha256(path),
                "size_bytes": path.stat().st_size,
                "executable": bool(mode & 0o111),
            }
        )
    encoded = json.dumps(
        entries, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return {
        "schema_version": SCHEMA_VERSION,
        "source_digest": hashlib.sha256(DOMAIN + encoded).hexdigest(),
        "file_count": len(entries),
        "size_bytes": sum(int(entry["size_bytes"]) for entry in entries),
        "entries": entries,
    }


def _assert_digest(value: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise SourceClosureError("source digest must be 64 lowercase hexadecimal characters")


def _assert_read_only(root: Path) -> None:
    for path in (root, *root.rglob("*")):
        if path.is_symlink():
            raise SourceClosureError(f"frozen source contains a symlink: {path}")
        if stat.S_IMODE(path.stat().st_mode) & 0o222:
            raise SourceClosureError(f"frozen source remains writable: {path}")


def validate_frozen_source(root: Path, expected_digest: str) -> dict[str, Any]:
    _assert_digest(expected_digest)
    root = root.resolve()
    if not root.is_dir() or root.is_symlink():
        raise SourceClosureError("frozen source root is absent or unsafe")
    _assert_read_only(root)
    bound = {path.resolve() for path in _source_files(root)}
    actual = {path.resolve() for path in root.rglob("*") if path.is_file()}
    unexpected = sorted(path.relative_to(root).as_posix() for path in actual - bound)
    if unexpected:
        raise SourceClosureError(
            "frozen source contains unbound files: " + ", ".join(unexpected[:10])
        )
    closure = capture_source_closure(root)
    if closure["source_digest"] != expected_digest:
        raise SourceClosureError(
            "source digest mismatch: "
            f"expected {expected_digest}, observed {closure['source_digest']}"
        )
    return closure


def _make_writable(root: Path) -> None:
    for path in (root, *root.rglob("*")):
        mode = stat.S_IMODE(path.stat().st_mode)
        path.chmod(mode | stat.S_IWUSR)


def materialize_frozen_source(root: Path, destination: Path, closure: dict[str, Any]) -> None:
    if destination.exists():
        raise SourceClosureError(f"source materialization destination exists: {destination}")
    destination.mkdir(parents=True)
    for entry in closure["entries"]:
        relative = Path(str(entry["path"]))
        source = root / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        if _sha256(target) != entry["sha256"]:
            raise SourceClosureError(f"source materialization changed bytes: {relative}")
        target.chmod(0o755 if entry["executable"] else 0o644)
    observed = capture_source_closure(destination)
    if observed["source_digest"] != closure["source_digest"]:
        raise SourceClosureError("source materialization changed the closure digest")
    _make_writable(destination)


def freeze_source_tree(source: Path, destination: Path) -> dict[str, Any]:
    source = source.resolve()
    destination = destination.resolve()
    if destination == source or source in destination.parents:
        raise SourceClosureError("frozen source must be outside the mutable source tree")
    before = capture_source_closure(source)
    materialize_frozen_source(source, destination, before)
    after = capture_source_closure(source)
    if before["source_digest"] != after["source_digest"]:
        shutil.rmtree(destination, ignore_errors=True)
        raise SourceClosureError("packaging source changed while the snapshot was created")
    for path in sorted(destination.rglob("*"), reverse=True):
        mode = stat.S_IMODE(path.stat().st_mode)
        path.chmod(0o555 if path.is_dir() or mode & 0o111 else 0o444)
    destination.chmod(0o555)
    validate_frozen_source(destination, str(before["source_digest"]))
    return before
