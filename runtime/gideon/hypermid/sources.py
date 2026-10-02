"""Attributed, digest-addressed source capture for Hypermid memory records."""

from __future__ import annotations

import hashlib
import json
import struct
import time
from dataclasses import dataclass
from pathlib import Path

from gideon.security.net.git import LocalGitSnapshot, read_local_git_snapshot

from .contracts import SourceKind, SourceSnapshot
from .foundation import Digest, Id, Scope

_MAX_SOURCE_BYTES = 4 * 1024 * 1024


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def _digest(value: bytes) -> Digest:
    return Digest(hashlib.sha256(value).hexdigest())


def scope_digest(scope: Scope) -> Digest:
    hasher = hashlib.sha256(b"hypermid.memory.scope.v1\0")
    for component in (str(scope.owner_id), str(scope.project_id)):
        encoded = component.encode("utf-8")
        hasher.update(struct.pack(">Q", len(encoded)))
        hasher.update(encoded)
    if scope.workspace_id is None:
        hasher.update(b"\0")
    else:
        hasher.update(b"\1")
        encoded = str(scope.workspace_id).encode("utf-8")
        hasher.update(struct.pack(">Q", len(encoded)))
        hasher.update(encoded)
    return Digest(hasher.hexdigest())


@dataclass(frozen=True, slots=True)
class GitSourceCapture:
    source: SourceSnapshot
    repository: Path
    head: str
    refs_digest: Digest

    def revalidate(self) -> bool:
        current = read_local_git_snapshot(self.repository, max_commits=1)
        return current.head == self.head and _refs_digest(current) == self.refs_digest


def source_record_from_note(
    note: object,
    *,
    scope: Scope,
    observed_at_ms: int | None = None,
) -> SourceSnapshot:
    relpath = str(getattr(note, "relpath", "") or "")
    content = getattr(note, "content", None)
    if not relpath or Path(relpath).is_absolute() or ".." in Path(relpath).parts:
        raise ValueError("note source path must be relative and contained")
    if not isinstance(content, str) or not content:
        raise ValueError("note source content must be non-empty text")
    content_bytes = content.encode("utf-8")
    if len(content_bytes) > _MAX_SOURCE_BYTES:
        raise ValueError("note source exceeds the byte limit")
    source_key = f"vault:{relpath}"
    identity = _digest(_canonical([scope.to_wire(), source_key]))
    return SourceSnapshot(
        source_id=Id(f"note-{str(identity)[:32]}"),
        owner_scope_digest=scope_digest(scope),
        kind=SourceKind.FILE,
        source_digest=_digest(content_bytes),
        locator=source_key,
        captured_content=content,
        capture_method="rendered_vault_note",
        observed_at_ms=_observed_at(observed_at_ms),
    )


def capture_git_source(
    repository: str | Path,
    *,
    scope: Scope,
    observed_at_ms: int | None = None,
    max_commits: int = 64,
) -> GitSourceCapture:
    snapshot = read_local_git_snapshot(repository, max_commits=max_commits)
    repository_identity = _digest(snapshot.repository_root.encode("utf-8"))
    refs_digest = _refs_digest(snapshot)
    content = _canonical_git_content(snapshot)
    content_bytes = content.encode("utf-8")
    if len(content_bytes) > _MAX_SOURCE_BYTES:
        raise ValueError("Git source exceeds the byte limit")
    source_key = f"git:{repository_identity}"
    identity = _digest(_canonical([scope.to_wire(), source_key]))
    source = SourceSnapshot(
        source_id=Id(f"git-{str(identity)[:32]}"),
        owner_scope_digest=scope_digest(scope),
        kind=SourceKind.GIT_COMMIT,
        source_digest=_digest(content_bytes),
        locator=source_key,
        captured_content=content,
        capture_method="gideon_guarded_local_git",
        observed_at_ms=_observed_at(observed_at_ms),
    )
    return GitSourceCapture(
        source=source,
        repository=Path(snapshot.repository_root),
        head=snapshot.head,
        refs_digest=refs_digest,
    )


def _canonical_git_content(snapshot: LocalGitSnapshot) -> str:
    return _canonical(
        {"head": snapshot.head, "refs": list(snapshot.refs), "commits": list(snapshot.commits)}
    ).decode("utf-8")


def _refs_digest(snapshot: LocalGitSnapshot) -> Digest:
    return _digest(_canonical({"head": snapshot.head, "refs": list(snapshot.refs)}))


def _observed_at(value: int | None) -> int:
    result = int(time.time() * 1000) if value is None else value
    if isinstance(result, bool) or result < 0:
        raise ValueError("observed_at_ms must be non-negative")
    return result


__all__ = [
    "GitSourceCapture",
    "capture_git_source",
    "scope_digest",
    "source_record_from_note",
]
