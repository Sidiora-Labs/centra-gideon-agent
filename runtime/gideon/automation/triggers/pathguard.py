"""Canonical filesystem scopes for unattended trigger actions."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)
_SEQUENCE_TYPES = (list, tuple, set, frozenset)


def canonicalize(raw: str) -> str:
    if not isinstance(raw, str) or not raw:
        return ""
    try:
        expanded = os.path.expandvars(raw)
        return os.path.realpath(os.path.expanduser(expanded))
    except (OSError, ValueError):
        logger.debug("pathguard: could not canonicalize %r", raw, exc_info=True)
        return ""


@dataclass(frozen=True)
class PathScope:
    root: str

    @classmethod
    def from_entry(cls, entry: str) -> PathScope:
        directory = entry[:-1] if entry.endswith("*") else entry
        return cls(canonicalize(directory.rstrip(os.sep) or os.sep))

    def contains(self, resolved: str) -> bool:
        if not self.root or not resolved:
            return False
        try:
            return os.path.commonpath((resolved, self.root)) == self.root
        except ValueError:
            return False


def _entries(allowlist: object) -> Iterator[str]:
    if isinstance(allowlist, _SEQUENCE_TYPES):
        yield from (entry for entry in allowlist if isinstance(entry, str) and entry)


def is_within(candidate: str, root: str) -> bool:
    return PathScope(canonicalize(root)).contains(canonicalize(candidate))


def path_allowed(allowlist: object, candidate: str) -> tuple[bool, str]:
    if not allowlist:
        return (
            False,
            "this trigger declares no paths, so no filesystem access is permitted",
        )
    if not isinstance(allowlist, _SEQUENCE_TYPES):
        return False, (
            "the paths allowlist must be a list; a "
            f"{type(allowlist).__name__} is refused rather than coerced, so a malformed fence "
            "cannot silently grant filesystem access"
        )
    resolved = canonicalize(candidate)
    if not resolved:
        return (
            False,
            f"{candidate!r} could not be resolved to a real path, so it is refused",
        )
    from gideon.security.security import is_sensitive_path

    if is_sensitive_path(resolved):
        return False, (
            f"{resolved!r} is a sensitive path (credentials/keys); it is refused even when "
            "allowlisted, because no automation should reach it unattended"
        )
    for entry in _entries(allowlist):
        if PathScope.from_entry(entry).contains(canonicalize(resolved)):
            return True, ""
    return False, (
        f"{resolved!r} is outside this trigger's frozen paths allowlist "
        f"(resolved from {candidate!r})"
    )


def unsafe_entries(allowlist: object) -> list[tuple[str, str]]:
    findings = []
    for entry in _entries(allowlist):
        prefix = entry[:-1] if entry.endswith("*") else entry
        problem = ""
        if not prefix.rstrip(os.sep):
            problem = "matches the whole filesystem, so it bounds nothing"
        elif not Path(os.path.expanduser(prefix)).is_absolute():
            problem = (
                "is a relative path, so it resolves against the gateway's working directory "
                "rather than a fixed location"
            )
        if problem:
            findings.append((entry, problem))
    return findings
