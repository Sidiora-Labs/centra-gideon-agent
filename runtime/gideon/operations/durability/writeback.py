"""Apply merged rows back to the live on-disk store (DURABILITY-AND-SYNC §4.1, DAS-6c-ii-c).

The sync cycle's last pure primitive: the inverse of ``shards.py``'s row *extraction*.
``shards.export_shards`` turned each inventory entry's on-disk form into a flat row list;
after :mod:`durability.merge` reconciles a peer's rows with the local ones, this writes the
merged set back into the live store in that entry's native shape, dispatched by its
inventory ``kind``. It is the exact mirror of ``export_shards``' per-kind dispatch, so a
round-trip (extract → merge with empty remote → apply) reproduces the same files.

Row shapes, matching the extractors verbatim:

* ``json_entity_dir`` — rows are ``{"id": "<rel-stem>", "data": {...}}`` → one JSON file
  per row at ``<dest>/<id>.json``. A **tombstone** row (a ``deleted_at`` marker) removes
  that file instead of writing it, so a delete synced from a peer propagates to the live
  store rather than resurrecting the entity.
* ``json_file`` — a single row ``{"id": "<name>", "data": {...}}`` → its ``data`` written
  to the file at ``dest``; a tombstone removes the file.
* ``jsonl_append`` — rows are the raw event dicts → written as canonical JSONL. If ``dest``
  is (or would be) a directory the stream is re-bucketed by year exactly as the exporter
  shards it (``<dest>/<year>.jsonl``); a single-file stream is rewritten at ``dest``.

``sqlite`` and ``tree`` are NOT handled here — the cycle merges DBs via the ATTACH-OR-IGNORE
path (``snapshot.py``) and rehydrates ``tree`` payloads from the content-addressed blob
store — so routing one through :func:`apply_rows` is a caller bug, raised loudly, mirroring
:func:`merge.merge_rows`. Writes are atomic (temp-file + rename); the caller owns the merge
and the choice of which entries to apply.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from gideon.operations.durability import inventory as inv
from gideon.operations.durability import record_files
from gideon.operations.durability.shards import _year_of, canonical_json

logger = logging.getLogger(__name__)

_TOMBSTONE_FIELD = "deleted_at"


@dataclass
class ApplyResult:
    """A reviewable tally of what an apply did to the live store."""

    written: int = 0
    removed: int = 0
    skipped: int = 0


def _is_tombstone(row: dict) -> bool:
    if row.get(_TOMBSTONE_FIELD):
        return True
    data = row.get("data")
    return bool(isinstance(data, dict) and data.get(_TOMBSTONE_FIELD))


def apply_rows(kind: str, dest: Path, rows: list[dict]) -> ApplyResult:
    """Write ``rows`` back to ``dest`` in the on-disk shape for inventory ``kind``.

    Atomic per file. Returns an :class:`ApplyResult`. Raises ``ValueError`` for
    ``sqlite``/``tree`` (handled by other paths) and any unknown kind.
    """
    if not isinstance(rows, list):
        raise ValueError("rows must be a list")
    dest = Path(dest)
    if kind == inv.KIND_JSON_ENTITY_DIR:
        prepared, skipped = _prepare_entity_rows(rows)
        result = _apply_entity_dir(dest, prepared)
        result.skipped = skipped
        return result
    if kind == inv.KIND_JSON_FILE:
        prepared = _prepare_document_rows(rows)
        return _apply_json_file(dest, prepared)
    if kind == inv.KIND_JSONL_APPEND:
        prepared = _prepare_jsonl_rows(rows)
        return _apply_jsonl(dest, prepared)
    if kind in (inv.KIND_SQLITE, inv.KIND_TREE):
        raise ValueError(
            f"{kind!r} is not row-applied — the cycle merges sqlite via ATTACH-OR-IGNORE and "
            "rehydrates tree payloads from the blob store; do not route it through apply_rows"
        )
    raise ValueError(f"unknown inventory kind {kind!r}")


def _validate_json(value: object) -> None:
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("durability row is not valid JSON data") from exc


def _prepare_entity_rows(rows: list[dict]) -> tuple[list[tuple[str, bool, str | None]], int]:
    prepared: list[tuple[str, bool, str | None]] = []
    skipped = 0
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("entity row must be an object")
        _validate_json(row)
        rid = row.get("id")
        if not isinstance(rid, str) or not rid:
            skipped += 1
            continue
        relative = f"{rid}.json"
        # Validate the id as a store-relative path before any write can begin.
        _validate_relative(relative)
        tombstone = _is_tombstone(row)
        payload = None if tombstone else canonical_json(row.get("data", {})) + "\n"
        prepared.append((relative, tombstone, payload))
    return prepared, skipped


def _validate_relative(relative: str) -> None:
    rel = PurePosixPath(relative)
    if (
        not relative
        or "\\" in relative
        or "\0" in relative
        or rel.is_absolute()
        or not rel.parts
        or rel.as_posix() != relative
        or any(part in (".", "..") for part in rel.parts)
    ):
        raise ValueError(f"unsafe record id path: {relative!r}")


def _prepare_document_rows(rows: list[dict]) -> list[tuple[bool, str | None]]:
    prepared: list[tuple[bool, str | None]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("document row must be an object")
        _validate_json(row)
        if "id" in row and (not isinstance(row["id"], str) or not row["id"]):
            raise ValueError("document row id must be a non-empty string")
        tombstone = _is_tombstone(row)
        payload = None if tombstone else canonical_json(row.get("data", {})) + "\n"
        prepared.append((tombstone, payload))
    return prepared


def _prepare_jsonl_rows(rows: list[dict]) -> list[dict]:
    prepared: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("JSONL row must be an object")
        _validate_json(row)
        prepared.append(row)
    return prepared


def _apply_entity_dir(root: Path, rows: list[tuple[str, bool, str | None]]) -> ApplyResult:
    """One JSON file per row at ``root/<id>.json``; a tombstone removes the file."""
    result = ApplyResult()
    result.skipped = 0
    if not rows:
        return result
    with record_files.locked_store(root) as store:
        for relative, _, _ in rows:
            record_files.safe_target(store, relative)
        for relative, tombstone, payload in rows:
            if tombstone:
                result.removed += int(record_files.remove_locked(store, relative))
            else:
                result.written += int(record_files.write_text_locked(store, relative, payload or ""))
    return result


def _apply_json_file(dest: Path, rows: list[tuple[bool, str | None]]) -> ApplyResult:
    """A single-document store: write the one row's ``data`` to ``dest`` (or remove it on a
    tombstone). More than one row is a merge bug (a json_file has exactly one id) — the last
    row wins, logged, rather than a silent half-write."""
    result = ApplyResult()
    if not rows:
        return result
    if len(rows) > 1:
        logger.warning(
            "apply_rows(json_file): %d rows for a single-document store", len(rows)
        )
    tombstone, payload = rows[-1]
    with record_files.locked_store(dest.parent) as store:
        relative = dest.name
        if tombstone:
            result.removed = int(record_files.remove_locked(store, relative))
        else:
            result.written = int(record_files.write_text_locked(store, relative, payload or ""))
    return result


def _apply_jsonl(dest: Path, rows: list[dict]) -> ApplyResult:
    """Rewrite an append-only stream as canonical JSONL. A ``dest`` that is a directory (or
    already exists as one) is year-sharded exactly as the exporter shards it; otherwise the
    whole stream is one file at ``dest``. The merged rows are the full, deduped stream, so a
    rewrite is the correct inverse — order preserved."""
    result = ApplyResult()
    if dest.is_symlink():
        raise ValueError(f"JSONL destination is a symlink: {dest}")
    if dest.is_dir() or (not dest.suffix and not dest.exists()):
        buckets: dict[str, list[dict]] = {}
        for row in rows:
            buckets.setdefault(_year_of(row), []).append(row)
        with record_files.locked_store(dest) as store:
            for year in buckets:
                record_files.safe_target(store, f"{year}.jsonl")
            for year in sorted(buckets):
                body = "".join(canonical_json(r) + "\n" for r in buckets[year])
                result.written += len(buckets[year]) * int(
                    record_files.write_text_locked(store, f"{year}.jsonl", body)
                )
        return result
    body = "".join(canonical_json(r) + "\n" for r in rows)
    with record_files.locked_store(dest.parent) as store:
        record_files.safe_target(store, dest.name)
        result.written = len(rows) * int(record_files.write_text_locked(store, dest.name, body))
    return result
