"""Deterministic file snapshots and bounded content-change projection."""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)
MAX_WATCHED_FILES = 2_000
HASH_BYTES = 65_536
VCS_GLOBS: tuple[str, ...] = (".git/refs/heads/*", ".git/HEAD")


@dataclass(frozen=True)
class WatchPattern:
    text: str
    root: Path

    def files(self) -> Iterator[Path]:
        pattern = os.path.expanduser(self.text)
        if pattern == "**" or pattern.endswith("/**"):
            pattern += "/*"
        try:
            if os.path.isabs(pattern):
                path = Path(pattern)
                anchor = Path(path.anchor)
                entries = anchor.glob(str(path.relative_to(anchor)))
            else:
                entries = self.root.glob(pattern)
            yield from (entry for entry in entries if entry.is_file())
        except (OSError, ValueError, IndexError):
            logger.debug("file-watch pattern %r could not be expanded", pattern)


@dataclass
class WatchState:
    hashes: dict[str, str] = field(default_factory=dict)
    seeded: bool = False

    def to_dict(self) -> dict[str, Any]:
        return dict(hashes=dict(self.hashes), seeded=self.seeded)

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> WatchState:
        data = raw if isinstance(raw, dict) else {}
        values = data.get("hashes")
        hashes: dict = {}
        if isinstance(values, dict):
            hashes.update((str(key), str(value)) for key, value in values.items())
        return cls(hashes, bool(data.get("seeded")))


@dataclass
class Delta:
    added: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    truncated: bool = False
    seeding: bool = False

    @property
    def changed(self) -> list[str]:
        return sorted((*self.added, *self.modified))

    @property
    def any_change(self) -> bool:
        return any((self.added, self.modified, self.removed))

    def to_dict(self) -> dict[str, Any]:
        record: dict = {
            key: list(getattr(self, key)) for key in ("added", "modified", "removed")
        }
        record.update(
            changed=self.changed, truncated=self.truncated, seeding=self.seeding
        )
        return record


@dataclass(frozen=True)
class FileSnapshot:
    fingerprints: dict[str, str]
    truncated: bool

    @classmethod
    def collect(cls, paths: list[Path], cap: int) -> FileSnapshot:
        truncated = len(paths) > cap
        selected = paths[:cap] if truncated else paths
        fingerprints = {}
        for path in selected:
            digest = content_hash(path)
            if digest:
                fingerprints[str(path)] = digest
        return cls(fingerprints, truncated)

    def compare(self, prior: WatchState) -> Delta:
        delta = Delta(truncated=self.truncated, seeding=not prior.seeded)
        if not prior.seeded:
            return delta
        for path in sorted(self.fingerprints):
            previous = prior.hashes.get(path)
            if previous is None:
                delta.added.append(path)
            elif self.fingerprints[path] != previous:
                delta.modified.append(path)
        delta.removed = sorted(
            path for path in prior.hashes if path not in self.fingerprints
        )
        return delta


def expand_globs(
    patterns: list[str] | tuple[str, ...], *, base: Path | None = None
) -> list[Path]:
    root = base or Path.cwd()
    found = {}
    for raw in patterns or ():
        text = str(raw or "").strip()
        if text:
            for path in WatchPattern(text, root).files():
                found[str(path)] = path
    return [found[key] for key in sorted(found)]


def content_hash(path: Path) -> str:
    try:
        size = path.stat().st_size
        with path.open("rb") as source:
            sample = source.read(HASH_BYTES)
    except OSError:
        return ""
    fingerprint = hashlib.sha256(str(size).encode("ascii"))
    fingerprint.update(sample)
    return fingerprint.hexdigest()


def changed_files(
    patterns: list[str] | tuple[str, ...],
    state: WatchState,
    *,
    base: Path | None = None,
    cap: int = MAX_WATCHED_FILES,
) -> tuple[Delta, WatchState]:
    snapshot = FileSnapshot.collect(expand_globs(patterns, base=base), cap)
    return snapshot.compare(state), WatchState(snapshot.fingerprints, True)


def fire_payload(
    delta: Delta, *, trigger_id: str = "", trigger_name: str = ""
) -> dict[str, Any]:
    record = delta.to_dict()
    record.pop("seeding")
    record.update(
        trigger_id=trigger_id,
        trigger_name=trigger_name,
        kind="file",
        count=len(record["changed"]) + len(record["removed"]),
    )
    return record


def should_fire(delta: Delta) -> bool:
    return False if delta.seeding else delta.any_change


def vcs_patterns(repo_root: str | Path) -> list[str]:
    directory = Path(os.path.expanduser(str(repo_root)))
    return list(map(lambda pattern: str(directory / pattern), VCS_GLOBS))
