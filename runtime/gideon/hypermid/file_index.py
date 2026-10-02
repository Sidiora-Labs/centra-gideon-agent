from __future__ import annotations

import fnmatch
import hashlib
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class FileDecisionReason(str, Enum):
    INSIDE_ROOT = "inside_root"
    OUTSIDE_ROOT = "outside_root"
    SYMLINK_ESCAPE = "symlink_escape"
    IGNORED = "ignored"
    BINARY = "binary"
    TOO_LARGE = "too_large"
    GENERATED = "generated"
    VENDOR = "vendor"
    SECRET_POLICY = "secret_policy"
    CHANGED_DURING_READ = "changed_during_read"


@dataclass(frozen=True, slots=True)
class FilePolicy:
    max_bytes: int = 1_000_000
    ignored: tuple[str, ...] = ()
    denied: tuple[str, ...] = ()
    generated: tuple[str, ...] = ("*.min.js", "*.map", "package-lock.json", "pnpm-lock.yaml")
    vendor_directories: frozenset[str] = frozenset({"vendor", "node_modules", ".venv", "target"})
    secret_names: frozenset[str] = frozenset({".env", ".env.local", "id_rsa", "id_ed25519"})

    @property
    def digest(self) -> str:
        canonical = repr((self.max_bytes, self.ignored, self.denied, self.generated, sorted(self.vendor_directories), sorted(self.secret_names)))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class FileDecision:
    requested_path: Path
    canonical_path: Path
    authorized_root: Path
    allowed: bool
    reason: FileDecisionReason
    policy_digest: str
    content_digest: str | None = None
    content: str | None = None

    def __post_init__(self) -> None:
        if not self.allowed and self.content is not None:
            raise ValueError("rejected file decisions cannot retain content")


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def inspect_file(path: Path, authorized_root: Path, policy: FilePolicy) -> FileDecision:
    requested = Path(path)
    root = Path(authorized_root).resolve(strict=True)
    lexical = Path(os.path.abspath(requested))
    canonical = requested.resolve(strict=True)
    common = dict(requested_path=requested, canonical_path=canonical, authorized_root=root, policy_digest=policy.digest)
    if not _inside(lexical, root):
        return FileDecision(**common, allowed=False, reason=FileDecisionReason.OUTSIDE_ROOT)
    if not _inside(canonical, root):
        return FileDecision(**common, allowed=False, reason=FileDecisionReason.SYMLINK_ESCAPE)
    relative = canonical.relative_to(root).as_posix()
    if any(fnmatch.fnmatch(relative, pattern) for pattern in policy.denied):
        return FileDecision(**common, allowed=False, reason=FileDecisionReason.OUTSIDE_ROOT)
    if any(fnmatch.fnmatch(relative, pattern) for pattern in policy.ignored):
        return FileDecision(**common, allowed=False, reason=FileDecisionReason.IGNORED)
    if any(part in policy.vendor_directories for part in canonical.relative_to(root).parts[:-1]):
        return FileDecision(**common, allowed=False, reason=FileDecisionReason.VENDOR)
    if any(fnmatch.fnmatch(canonical.name, pattern) for pattern in policy.generated):
        return FileDecision(**common, allowed=False, reason=FileDecisionReason.GENERATED)
    if canonical.name in policy.secret_names:
        return FileDecision(**common, allowed=False, reason=FileDecisionReason.SECRET_POLICY)
    size = canonical.stat().st_size
    if size > policy.max_bytes:
        return FileDecision(**common, allowed=False, reason=FileDecisionReason.TOO_LARGE)
    raw = canonical.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if b"\x00" in raw[:8192]:
        return FileDecision(**common, allowed=False, reason=FileDecisionReason.BINARY, content_digest=digest)
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        return FileDecision(**common, allowed=False, reason=FileDecisionReason.BINARY, content_digest=digest)
    return FileDecision(
        **common,
        allowed=True,
        reason=FileDecisionReason.INSIDE_ROOT,
        content_digest=digest,
        content=content,
    )


def revalidate_for_publication(decision: FileDecision) -> FileDecision:
    if not decision.allowed or decision.content_digest is None:
        return decision
    try:
        canonical = decision.requested_path.resolve(strict=True)
    except OSError:
        return FileDecision(
            requested_path=decision.requested_path,
            canonical_path=decision.canonical_path,
            authorized_root=decision.authorized_root,
            allowed=False,
            reason=FileDecisionReason.CHANGED_DURING_READ,
            policy_digest=decision.policy_digest,
        )
    if canonical != decision.canonical_path or not _inside(canonical, decision.authorized_root):
        return FileDecision(
            requested_path=decision.requested_path,
            canonical_path=canonical,
            authorized_root=decision.authorized_root,
            allowed=False,
            reason=FileDecisionReason.SYMLINK_ESCAPE,
            policy_digest=decision.policy_digest,
        )
    raw = canonical.read_bytes()
    if hashlib.sha256(raw).hexdigest() != decision.content_digest:
        return FileDecision(
            requested_path=decision.requested_path,
            canonical_path=canonical,
            authorized_root=decision.authorized_root,
            allowed=False,
            reason=FileDecisionReason.CHANGED_DURING_READ,
            policy_digest=decision.policy_digest,
        )
    return decision
