from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitIndexError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class GitCommitDocument:
    commit_id: str
    source_key: str
    content: str
    content_digest: str
    source_time_ms: int
    parent_count: int


@dataclass(frozen=True, slots=True)
class GitScan:
    repository: Path
    repository_identity: str
    refs: tuple[str, ...]
    refs_digest: str
    documents: tuple[GitCommitDocument, ...]


@dataclass(frozen=True, slots=True)
class GitProbeFailure:
    reason: str
    next_probe_at_ms: int


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise GitIndexError(result.stderr.strip() or "git command failed")
    return result.stdout


def _identity(repository: Path) -> str:
    common = _git(repository, "rev-parse", "--git-common-dir").strip()
    canonical_common = (repository / common).resolve(strict=True)
    return hashlib.sha256(str(canonical_common).encode("utf-8")).hexdigest()


def _resolve_refs(repository: Path, refs: tuple[str, ...]) -> tuple[tuple[str, ...], str]:
    resolved = tuple(_git(repository, "rev-parse", "--verify", ref).strip() for ref in refs)
    digest = hashlib.sha256("\n".join(resolved).encode("ascii")).hexdigest()
    return resolved, digest


def scan_repository(
    repository: Path,
    refs: tuple[str, ...],
    *,
    max_commits: int,
    skip_merges: bool = True,
) -> GitScan:
    if not refs or max_commits < 1:
        raise ValueError("at least one ref and a positive commit limit are required")
    repository = Path(repository).resolve(strict=True)
    identity = _identity(repository)
    resolved, refs_digest = _resolve_refs(repository, refs)
    arguments = ["log", f"--max-count={max_commits}", "--format=%H%x1f%P%x1f%ct%x1f%B%x1e"]
    if skip_merges:
        arguments.append("--no-merges")
    arguments.extend(resolved)
    raw = _git(repository, *arguments)
    documents: list[GitCommitDocument] = []
    for entry in raw.split("\x1e"):
        entry = entry.strip()
        if not entry:
            continue
        commit_id, parents, timestamp, message = entry.split("\x1f", 3)
        content = message.strip()
        documents.append(
            GitCommitDocument(
                commit_id=commit_id,
                source_key=commit_id,
                content=content,
                content_digest=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                source_time_ms=int(timestamp) * 1000,
                parent_count=len(parents.split()) if parents else 0,
            )
        )
    if not documents:
        raise GitIndexError("repository has no reachable commits")
    return GitScan(repository, identity, refs, refs_digest, tuple(documents))


def revalidate_for_publication(scan: GitScan) -> bool:
    try:
        identity = _identity(scan.repository)
        _, refs_digest = _resolve_refs(scan.repository, scan.refs)
    except (GitIndexError, OSError):
        return False
    return identity == scan.repository_identity and refs_digest == scan.refs_digest


def cooldown_failure(reason: str, *, now_ms: int, cooldown_ms: int) -> GitProbeFailure:
    if cooldown_ms <= 0:
        raise ValueError("cooldown must be positive")
    return GitProbeFailure(reason=reason, next_probe_at_ms=now_ms + cooldown_ms)
