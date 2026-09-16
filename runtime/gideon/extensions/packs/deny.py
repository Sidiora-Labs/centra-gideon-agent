"""Structural redaction layer — the files a pack NEVER opens (AGENT-PACKS §2.2 layer 1).

A pack is shareable *capability configuration*, not a backup. So its exclude-set is
strictly WIDER than a portable export's: an export carries the owner's own data to a new
machine (memory, notifications, run history all travel), but a pack must carry none of it
— a recipient's assistant must not inherit the author's episodic memory, personal
documents, or session transcripts, let alone a credential.

This is the STRUCTURAL half of the two-layer defence. It EXTENDS
``portability.EXPORT_EXCLUDE`` (imported, not re-listed — a second hand-maintained list is
exactly the drift that let stores escape coverage before, per portability.py's own S182
note) with the user-DATA stores a pack additionally refuses. The rule the whole pack
build obeys: a path under any denied name is **never opened**, not merely dropped after
reading — the exporter's readers are an allowlist of the §1 component stores, and every
candidate read passes :func:`is_denied` first. Fails CLOSED: on any doubt the path is
denied.
"""

from __future__ import annotations

from pathlib import PurePosixPath


def _export_exclude() -> frozenset[str]:
    """The portable-export secret exclude-set, read from ``portability`` not re-listed.

    Importing it is the whole point — the two lists cannot drift if there is only one.
    Falls back to the historical secret literals if the import ever fails, so a pack can
    never start opening credential files because a refactor broke an import (fail closed).
    """
    try:
        from gideon.workspace.portability import EXPORT_EXCLUDE

        return frozenset(EXPORT_EXCLUDE)
    except (
        Exception
    ):  # noqa: BLE001 — an import break must NEVER widen what a pack opens
        return frozenset(
            {
                ".env",
                ".local_secret",
                "sel_hmac.key",
                "telemetry_salt",
                "session_map.json",
            }
        )


_PACK_DATA_FILES: frozenset[str] = frozenset(
    {
        "memory.db",
        "memory_index.db",
        "knowledge.db",
        "lexicon.db",
        "learning.db",
        "security_events.jsonl",
        "session_map.json",
    }
)

_PACK_DATA_DIRS: frozenset[str] = frozenset(
    {
        "sessions",
        "workspace",
        "cron-history",
        "snapshots",
        "outbox",
        "uploads",
        "shards",
        "notifications",
        "tasks",
        "projects",
        "inbox",
        "__pycache__",
    }
)

_DB_SIDECAR_SUFFIXES: tuple[str, ...] = ("-wal", "-shm", "-journal")


DENY_FILES: frozenset[str] = _export_exclude() | _PACK_DATA_FILES

DENY_DIRS: frozenset[str] = _PACK_DATA_DIRS


def is_denied(rel_path: str) -> bool:
    """Whether a home-relative path must NEVER be opened while building a pack.

    Fails CLOSED: an empty/invalid path, a denied basename, a denied database sidecar, a
    ``.db`` file (whole databases are user data, never a pack component), or any path
    segment that names a denied directory all return ``True``. Every store reader in
    :mod:`packs.build` passes its candidate path through here BEFORE opening it, so a
    denied file is never read — the structural guarantee, not a post-read filter.
    """
    if not rel_path or not rel_path.strip():
        return True
    parts = PurePosixPath(rel_path).parts
    if not parts:
        return True
    name = parts[-1]
    if name in DENY_FILES:
        return True
    if name.endswith(".db") or name.endswith(_DB_SIDECAR_SUFFIXES):
        return True
    for part in parts:
        if part in DENY_DIRS:
            return True
    return False
