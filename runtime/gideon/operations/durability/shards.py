"""Deterministic shard export — JSONL + SHA manifest (DURABILITY §2).

A tar snapshot is opaque: you cannot diff it, review it, or sync it. Shards are the
*other* representation of the same state — canonical JSONL, one directory per
inventory entry, byte-identical for identical input. That determinism is what makes
the export reviewable (``git diff`` over shards answers "what did the assistant
learn this week?") and, later, syncable.

Three properties are load-bearing, and each is tested:

* **Byte-identical for identical state.** Rows sorted by id, JSON with sorted keys
  and no incidental whitespace, LF endings, UTF-8. Two exports of an unchanged home
  produce the same bytes and therefore the same sha256 — a sync that re-uploads
  unchanged data, or a git history full of no-op commits, is the failure this
  prevents.
* **Every shard is verifiable.** ``manifest.json`` records ``{bytes, rows, sha256}``
  per shard; :func:`validate` re-derives all three and re-parses every row, so a
  truncated or corrupted export is detected rather than trusted.
* **Secrets never shard.** Shards are the representation that *leaves the machine*
  (§2), so ``secret=True`` entries are excluded unconditionally — unlike a local
  snapshot tar, which keeps them because a same-machine restore needs them.

Databases are read through the sqlite backup API into a scratch copy first, so a
live WAL store is exported consistently — the same hazard Session 1 closed for tars.
Table discovery reads the schema rather than a hand-written allowlist: the previous
allowlist in ``snapshot._merge_memory`` names two tables (``knowledge_facts``,
``knowledge_edges``) that do not exist in ``memory.db`` at all.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gideon.core.atomic_write import atomic_write, atomic_write_bytes
from gideon.operations.durability import inventory as inv
from gideon.operations.durability.home_paths import (
    LinkInTheWay,
    export_path,
    guard_path,
)

logger = logging.getLogger(__name__)

SHARD_SCHEMA_VERSION = 1

PART_SPLIT_BYTES = 48 * 1024 * 1024

_MANIFEST = "manifest.json"
_MACHINE_ID_FILE = "machine_id"
_UNKNOWN_YEAR = "unknown"

_YEAR_RE = re.compile(r"(19|20)\d{2}")


def canonical_json(value: Any) -> str:
    """One line of canonical JSON: sorted keys, compact separators, UTF-8 text.

    ``ensure_ascii=False`` keeps real characters readable in a diff instead of
    escaping them; ``sort_keys`` plus fixed separators is what makes two exports of
    the same state byte-identical.
    """
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def machine_id(home: Path) -> str:
    """A stable, non-secret per-machine id, created on first use.

    Deliberately NOT ``telemetry_salt``: that is marked ``secret=True`` and must
    never leave the machine, while this id is written into every manifest so a sync
    can tell "which machine produced this export".
    """
    path = home / _MACHINE_ID_FILE
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except (FileNotFoundError, OSError):
        pass
    fresh = uuid.uuid4().hex
    try:
        atomic_write(path, fresh + "\n")
    except OSError:
        logger.debug("could not persist machine_id", exc_info=True)
    return fresh


@dataclass
class ShardFile:
    """One written shard file and its verification triple."""

    path: str
    bytes: int
    rows: int
    sha256: str


@dataclass
class DbCopy:
    """A consistent whole-database copy staged for sync (DAS-6c-ii-g).

    The diffable row shards store byte/embedding columns as size placeholders, so they
    can't rebuild a DB losslessly; a sync export additionally stages the real DB file
    (backup-API copy, WAL-checkpointed) under ``db/<entry_id>.db`` so the merger can
    ATTACH it. Only written when ``export_shards(include_databases=True)`` — the hourly
    incremental backup never carries these, so its determinism is untouched.
    """

    path: str
    entry_id: str
    bytes: int
    sha256: str


@dataclass
class ExportResult:
    entries: int = 0
    shards: list[ShardFile] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    blobs: int = 0
    databases: list[DbCopy] = field(default_factory=list)
    agreements: ShardFile | None = None

    @property
    def rows(self) -> int:
        return sum(s.rows for s in self.shards)


class FileRows(list):
    """Rows with local raw-file fingerprints used only to avoid replacing a newer edit."""

    def __init__(self, rows=(), *, file_shas=None):
        super().__init__(rows)
        self.file_shas = dict(file_shas or {})


def row_file(row: dict) -> str:
    """Original file path; older JSON rows retain their stem convention."""
    rid = str(row.get("id", ""))
    return rid if "text" in row or "base64" in row else f"{rid}.json"


def row_bytes(row: dict) -> bytes:
    forms = [name for name in ("data", "text", "base64") if name in row]
    if len(forms) > 1:
        raise ValueError("a file row must have one content representation")
    if "text" in row:
        if not isinstance(row["text"], str):
            raise ValueError("file text must be a string")
        return row["text"].encode("utf-8")
    if "base64" in row:
        if not isinstance(row["base64"], str):
            raise ValueError("base64 file content must be a string")
        return base64.b64decode(row["base64"], validate=True)
    return (canonical_json(row.get("data", {})) + "\n").encode("utf-8")


def store_file(entry, relative):
    """Only this store's files may be carried or landed as rows."""
    from pathlib import PurePosixPath

    path = PurePosixPath(relative)
    if (
        not relative
        or "\\" in relative
        or "\0" in relative
        or path.is_absolute()
        or path.as_posix() != relative
        or any(part in (".", "..") for part in path.parts)
    ):
        return False
    if relative.endswith(
        (".db", ".sqlite", ".sqlite3", "-journal", "-wal", "-shm")
    ) or path.name in (".gideon-record-files.lock", "_tombstones.jsonl"):
        return False
    if entry is None:
        return not path.name.endswith(".tmp")
    full = f"{entry.path}/{relative}"
    from gideon.workspace.portability import _is_derived_within

    if inv.is_ignored(full) or _is_derived_within(entry.path, relative):
        return False
    owner = inv.claim_for(full)
    return owner is None or not owner.path.startswith(entry.path + "/")


def _json_rows_from_entity_dir(
    root: Path, *, entry_path: str = "", left_out=None, path_id=""
) -> list[dict]:
    """Read JSON entities by stem and original UTF-8/binary files by their full relative name."""
    rows: dict[str, dict] = {}
    file_shas = {}
    entry = next((item for item in inv.all_entries() if item.path == entry_path), None)

    def refused(relative, why):
        if left_out is not None:
            left_out[f"{path_id}/{relative}".strip("/")] = why

    def listing_error(exc):
        refused(".", f"folder could not be listed: {exc}")

    for directory, dirs, files in os.walk(
        root, followlinks=False, onerror=listing_error
    ):
        here = Path(directory)
        allowed = []
        for name in sorted(dirs):
            relative = (here / name).relative_to(root).as_posix()
            if not store_file(entry, relative):
                continue
            try:
                export_path(root, relative)
            except LinkInTheWay as exc:
                refused(relative, str(exc))
            else:
                allowed.append(name)
        dirs[:] = allowed
        for name in sorted(files):
            path = here / name
            relative = path.relative_to(root).as_posix()
            if not store_file(entry, relative):
                continue
            try:
                export_path(root, relative)
                if path.stat().st_size > PART_SPLIT_BYTES * 3 // 4:
                    refused(relative, "file too large for one shard row")
                    continue
                raw = path.read_bytes()
                if raw.startswith(b"SQLite format 3\x00"):
                    continue
                if relative.endswith(".json"):
                    row = {"id": relative[:-5], "data": json.loads(raw.decode("utf-8"))}
                else:
                    try:
                        row = {"id": relative, "text": raw.decode("utf-8")}
                    except UnicodeDecodeError:
                        row = {
                            "id": relative,
                            "base64": base64.b64encode(raw).decode("ascii"),
                        }
                if row["id"] in rows:
                    refused(
                        relative, f"row id already names {row_file(rows[row['id']])}"
                    )
                    continue
                rows[row["id"]] = row
                file_shas[relative] = _sha256(raw)
            except (OSError, ValueError, UnicodeDecodeError, LinkInTheWay) as exc:
                refused(relative, f"file unreadable: {exc}")
    return FileRows([rows[rid] for rid in sorted(rows)], file_shas=file_shas)


def _json_rows_from_file(
    path: Path, *, left_out=None, path_id="", entry=None
) -> list[dict]:
    try:
        guard_path(path, read=True)
        raw = path.read_bytes()
        try:
            row = {"id": path.name, "data": json.loads(raw.decode("utf-8"))}
        except (ValueError, UnicodeDecodeError):
            if entry is None or entry.merge != inv.MERGE_REPLACE_ONLY:
                raise
            try:
                row = {"id": path.name, "text": raw.decode("utf-8")}
            except UnicodeDecodeError:
                row = {"id": path.name, "base64": base64.b64encode(raw).decode("ascii")}
        return FileRows([row], file_shas={path.name: _sha256(raw)})
    except (OSError, ValueError, UnicodeDecodeError, LinkInTheWay):
        if left_out is not None:
            left_out[path_id] = "file unreadable"
        return []


def _json_record_rows_from_file(
    path: Path, records_field: str, *, left_out=None, path_id=""
) -> list[dict]:
    """Extract one stable-id row per record from a JSON collection store."""
    try:
        guard_path(path, read=True)
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, json.JSONDecodeError, LinkInTheWay):
        if left_out is not None:
            left_out[path_id] = "JSON file unreadable"
        return []
    records = (
        value
        if records_field == "$root"
        else (value.get(records_field) if isinstance(value, dict) else None)
    )
    if not isinstance(records, list):
        return []
    return FileRows(
        [
            {"id": str(record["id"]), "data": record}
            for record in records
            if isinstance(record, dict) and record.get("id") is not None
        ],
        file_shas={path.name: _sha256(raw)},
    )


def _year_of(row: dict) -> str:
    """Best-effort year for an append-only row, for year sharding.

    Looks at the usual timestamp fields; anything unparseable lands in
    ``unknown`` rather than being assigned a plausible-looking year.
    """
    for key in ("ts", "timestamp", "created_at", "started_at", "at"):
        raw = row.get(key)
        if raw is None:
            continue
        if isinstance(raw, (int, float)):
            try:
                return str(datetime.fromtimestamp(float(raw), timezone.utc).year)
            except (OverflowError, OSError, ValueError):
                continue
        match = _YEAR_RE.search(str(raw))
        if match:
            return match.group(0)
    return _UNKNOWN_YEAR


def _jsonl_rows_by_year(
    path: Path, *, left_out=None, path_id=""
) -> dict[str, list[dict]]:
    """Parse an append-only JSONL file into ``{year: rows}``, order preserved."""
    buckets: dict[str, list[dict]] = {}
    try:
        guard_path(path, read=True)
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, LinkInTheWay):
        if left_out is not None:
            left_out[f"{path_id}/{path.name}"] = "JSONL file unreadable"
        return buckets
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            if left_out is not None:
                left_out[f"{path_id}/{path.name}"] = "JSONL contains unreadable rows"
            continue
        if not isinstance(row, dict):
            continue
        buckets.setdefault(_year_of(row), []).append(row)
    return buckets


def _sqlite_tables(db: Path) -> list[str]:
    """User tables in a database, discovered from the schema.

    Discovery rather than an allowlist: the pre-existing merge allowlist in
    ``snapshot.py`` names ``knowledge_facts``/``knowledge_edges``, which do not
    exist in ``memory.db`` — a hand-written list drifts, a schema read cannot.
    Internal sqlite bookkeeping and FTS shadow tables are excluded (the latter are
    derived data, rebuilt on import).
    """
    try:
        with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as conn:
            names = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            ]
    except sqlite3.Error:
        logger.debug("shards: cannot read schema of %s", db, exc_info=True)
        return []
    out = []
    for name in names:
        if name.startswith("sqlite_"):
            continue
        if re.search(r"_(data|idx|docsize|config|content)$", name):
            continue
        out.append(name)
    return out


def _sqlite_rows(db: Path, table: str) -> list[dict]:
    """Every row of one table as dicts, stably ordered.

    Ordered by the table's own ``id``/``key`` when it has one, else by ``rowid`` —
    so the same database always dumps in the same order.
    """
    try:
        with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
            order = "id" if "id" in cols else ("key" if "key" in cols else "rowid")
            rows = conn.execute(
                f'SELECT * FROM "{table}" ORDER BY "{order}"'
            ).fetchall()
    except sqlite3.Error:
        logger.debug("shards: cannot read %s.%s", db, table, exc_info=True)
        return []
    out: list[dict] = []
    for row in rows:
        record = {}
        for key in row.keys():
            value = row[key]
            if isinstance(value, (bytes, bytearray, memoryview)):
                record[key] = {"__bytes__": len(bytes(value))}
            else:
                record[key] = value
        out.append(record)
    return out


def _consistent_db_copy(src: Path, workdir: Path) -> Path | None:
    """A consistent scratch copy of a live database via the backup API."""
    dst = workdir / src.name
    try:
        with (
            closing(sqlite3.connect(str(src))) as src_conn,
            closing(sqlite3.connect(str(dst))) as dst_conn,
        ):
            src_conn.backup(dst_conn)
        return dst
    except sqlite3.Error:
        logger.debug("shards: backup-API copy failed for %s", src, exc_info=True)
        return None


def _write_shard(root: Path, rel: str, rows: list[dict]) -> list[ShardFile]:
    """Write rows as canonical JSONL, splitting deterministically past the cap."""
    lines = [canonical_json(r).encode("utf-8") + b"\n" for r in rows]
    total = sum(len(b) for b in lines)
    if total <= PART_SPLIT_BYTES:
        body = b"".join(lines)
        out = root / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(out, body)
        return [
            ShardFile(path=rel, bytes=len(body), rows=len(rows), sha256=_sha256(body))
        ]

    parts: list[list[bytes]] = [[]]
    size = 0
    for line in lines:
        if size + len(line) > PART_SPLIT_BYTES and parts[-1]:
            parts.append([])
            size = 0
        parts[-1].append(line)
        size += len(line)
    written: list[ShardFile] = []
    stem = rel[:-6] if rel.endswith(".jsonl") else rel
    for index, chunk in enumerate(parts):
        body = b"".join(chunk)
        part_rel = f"{stem}.part-{index:04d}.jsonl"
        out = root / part_rel
        out.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(out, body)
        written.append(
            ShardFile(
                path=part_rel, bytes=len(body), rows=len(chunk), sha256=_sha256(body)
            )
        )
    return written


def _export_blobs(root: Path, src_dir: Path, *, entry_path: str = "") -> int:
    """Content-addressed blob dir for binary originals, deduplicated by sha256."""
    from gideon.workspace.portability import _is_derived_within

    count = 0
    blob_root = root / "blobs"
    for path in sorted(src_dir.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        if entry_path and _is_derived_within(
            entry_path, path.relative_to(src_dir).as_posix()
        ):
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        digest = _sha256(data)
        dest = blob_root / digest[:2] / digest
        if dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(dest, data)
        count += 1
    return count


def _export_blob(root: Path, src: Path) -> int:
    """Export the one file named by a file-shaped tree entry, never its parent."""
    if src.is_symlink() or not src.is_file():
        return 0
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(src, flags)
        with os.fdopen(fd, "rb") as source:
            data = source.read()
    except OSError:
        return 0

    digest = _sha256(data)
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        return 0
    blob_root = root / "blobs"
    if blob_root.is_symlink() or (blob_root.exists() and not blob_root.is_dir()):
        return 0
    dest = blob_root / digest[:2] / digest
    if dest.parent.is_symlink() or (dest.parent.exists() and not dest.parent.is_dir()):
        return 0
    if dest.is_symlink():
        return 0
    if blob_root.is_dir():
        for stale in blob_root.rglob("*"):
            if stale.is_file() and not stale.is_symlink() and stale != dest:
                stale.unlink(missing_ok=True)

    if dest.exists():
        return 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(dest, data)
    return 1


def _stage_db_copy(out_dir: Path, entry_id: str, src_copy: Path) -> DbCopy | None:
    """Stage a consistent whole-DB copy under ``db/<entry_id>.db`` for the sync merger.

    ``src_copy`` is the already-consistent backup-API copy the exporter made for row
    extraction, so this is a plain byte copy (no second live-DB read). Returns the
    :class:`DbCopy` record, or None if the copy can't be read."""
    try:
        data = src_copy.read_bytes()
    except OSError:
        logger.debug("shards: cannot stage db copy for %s", entry_id, exc_info=True)
        return None
    rel = f"db/{entry_id}.db"
    dest = out_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(dest, data)
    return DbCopy(path=rel, entry_id=entry_id, bytes=len(data), sha256=_sha256(data))


def is_an_export(directory: Path) -> bool:
    try:
        guard_path(directory / _MANIFEST, read=True)
        manifest = json.loads((directory / _MANIFEST).read_text())
    except (OSError, ValueError, LinkInTheWay):
        return False
    return (
        isinstance(manifest, dict)
        and "schema_version" in manifest
        and "shards" in manifest
    )


def _drop_shards_of(out_dir: Path, entry_id: str) -> None:
    """Remove only a recognized export's selected store before rebuilding its current copy."""
    if not is_an_export(out_dir):
        return
    folder = guard_path(out_dir / entry_id)
    if folder.is_dir():
        # Never delete user-linked children, even inside an otherwise recognized export.
        for child in folder.rglob("*"):
            guard_path(child)
        shutil.rmtree(folder)


def export_shards(
    home: Path,
    out_dir: Path,
    *,
    entries: list[str] | None = None,
    include_databases: bool = False,
    agreements: dict | None = None,
    deletions: dict | None = None,
) -> ExportResult:
    """Export state to deterministic shards under ``out_dir``.

    ``entries`` optionally restricts to specific inventory entry ids (the hourly
    incremental path exports only dirty entries). Secrets and derived data are
    never exported.

    ``include_databases`` (sync only) additionally stages a consistent whole-DB copy for
    each ``KIND_SQLITE`` entry under ``db/<entry_id>.db``, because the diffable row shards
    store embedding/byte columns as placeholders and can't rebuild a DB losslessly. The
    hourly incremental backup leaves this False, so its byte-for-byte determinism (and its
    tests) are unaffected — DB copies are not byte-identical across runs by nature.
    """
    result = ExportResult()
    out_dir.mkdir(parents=True, exist_ok=True)
    wanted = set(entries) if entries else None

    if entries is not None:
        for entry in inv.export_entries():
            if entry.id in set(entries):
                _drop_shards_of(out_dir, entry.id)

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        for entry in inv.export_entries():
            if wanted is not None and entry.id not in wanted:
                continue
            if not any(char in entry.path for char in "*?["):
                try:
                    export_path(home, entry.path)
                except LinkInTheWay as exc:
                    result.skipped[entry.path] = str(exc)
                    continue
            for rel in inv.paths_for(home, entry):
                try:
                    src = export_path(home, rel)
                except LinkInTheWay as exc:
                    result.skipped[rel] = str(exc)
                    continue
                path_id = (
                    entry.id if rel == entry.path else f"{entry.id}/{Path(rel).name}"
                )
                result.entries += 1

                if entry.kind == inv.KIND_SQLITE:
                    copy = _consistent_db_copy(src, workdir)
                    if copy is None:
                        result.skipped[path_id] = "database unreadable"
                        continue
                    tables = _sqlite_tables(copy)
                    if not tables:
                        result.skipped[path_id] = "no tables"
                        continue
                    for table in tables:
                        rows = _sqlite_rows(copy, table)
                        result.shards.extend(
                            _write_shard(out_dir, f"{path_id}/{table}.jsonl", rows)
                        )
                    if include_databases:
                        staged = _stage_db_copy(out_dir, path_id, copy)
                        if staged is not None:
                            result.databases.append(staged)
                elif entry.kind == inv.KIND_JSON_ENTITY_DIR:
                    rows = (
                        _json_rows_from_entity_dir(
                            src,
                            entry_path=entry.path,
                            left_out=result.skipped,
                            path_id=path_id,
                        )
                        if src.is_dir()
                        else []
                    )
                    rows = [inv.shared_value(entry, row) for row in rows]
                    result.shards.extend(
                        _write_shard(out_dir, f"{path_id}/entities.jsonl", rows)
                    )
                elif entry.kind == inv.KIND_JSON_FILE:
                    if entry.records_field:
                        rows = (
                            _json_record_rows_from_file(
                                src,
                                entry.records_field,
                                left_out=result.skipped,
                                path_id=path_id,
                            )
                            if src.is_file()
                            else []
                        )
                    else:
                        rows = (
                            _json_rows_from_file(
                                src,
                                left_out=result.skipped,
                                path_id=path_id,
                                entry=entry,
                            )
                            if src.is_file()
                            else []
                        )
                    rows = [inv.shared_value(entry, row) for row in rows]
                    result.shards.extend(
                        _write_shard(out_dir, f"{path_id}/value.jsonl", rows)
                    )
                elif entry.kind == inv.KIND_JSONL_APPEND:
                    files = [src] if src.is_file() else sorted(src.rglob("*.jsonl"))
                    buckets: dict[str, list[dict]] = {}
                    for path in files:
                        for year, rows in _jsonl_rows_by_year(
                            path, left_out=result.skipped, path_id=path_id
                        ).items():
                            buckets.setdefault(year, []).extend(
                                inv.shared_value(entry, row) for row in rows
                            )
                    for year in sorted(buckets):
                        result.shards.extend(
                            _write_shard(
                                out_dir, f"{path_id}/{year}.jsonl", buckets[year]
                            )
                        )
                else:
                    if src.is_symlink():
                        continue
                    elif src.is_dir():
                        result.blobs += _export_blobs(
                            out_dir / path_id, src, entry_path=entry.path
                        )
                    else:
                        result.blobs += _export_blob(out_dir / path_id, src)

    if deletions:
        for entry_id, marks in deletions.items():
            deleted_entry = inv.by_id(entry_id)
            if deleted_entry is None or deleted_entry.machine_local:
                continue
            entry = deleted_entry
            matching = [
                record
                for record in result.shards
                if record.path.startswith(entry_id + "/")
            ]
            if not matching and entry.kind in (
                inv.KIND_JSON_ENTITY_DIR,
                inv.KIND_JSON_FILE,
            ):
                relative = f"{entry_id}/" + (
                    "entities.jsonl"
                    if entry.kind == inv.KIND_JSON_ENTITY_DIR
                    else "value.jsonl"
                )
                result.shards.extend(
                    _write_shard(
                        out_dir,
                        relative,
                        [
                            {
                                "id": rid,
                                "deleted_at": mark.at or "unknown",
                                "held": list(mark.held),
                            }
                            for rid, mark in marks.items()
                        ],
                    )
                )
            if matching:
                record = matching[0]
                path = out_dir / record.path
                rows = [
                    json.loads(line)
                    for line in path.read_text().splitlines()
                    if line.strip()
                ]
                live = {str(row.get("id", "")) for row in rows}
                rows.extend(
                    {
                        "id": rid,
                        "deleted_at": mark.at or "unknown",
                        "held": list(mark.held),
                    }
                    for rid, mark in marks.items()
                    if rid not in live
                )
                fresh = _write_shard(out_dir, record.path, rows)
                result.shards = [
                    row for row in result.shards if row.path != record.path
                ] + fresh
    result.shards.sort(key=lambda s: s.path)
    if entries is not None:
        result.shards = _merged_shard_records(
            out_dir, result.shards, touched=set(entries)
        )
    if agreements is not None:
        body = (canonical_json(agreements) + "\n").encode("utf-8")
        atomic_write_bytes(out_dir / "agreements.json", body)
        result.agreements = ShardFile(
            path="agreements.json", bytes=len(body), rows=0, sha256=_sha256(body)
        )
    _write_manifest(home, out_dir, result)
    return result


def _merged_shard_records(
    out_dir: Path, fresh: list[ShardFile], *, touched: set[str]
) -> list[ShardFile]:
    """Fresh records for re-exported entries + carried-forward records for the rest.

    A shard's entry id is its first path segment, which is how a carried record is
    matched to the entry that owns it. Carried records whose file has since vanished
    are dropped rather than kept as a phantom declaration.
    """
    merged = {s.path: s for s in fresh}
    try:
        previous = json.loads((out_dir / _MANIFEST).read_text(encoding="utf-8"))
        records = previous.get("shards") or []
    except (OSError, json.JSONDecodeError, LinkInTheWay):
        records = []
    for record in records:
        rel = str(record.get("path", ""))
        if not rel or rel in merged:
            continue
        if rel.split("/", 1)[0] in touched:
            continue
        if not (out_dir / rel).is_file():
            continue
        merged[rel] = ShardFile(
            path=rel,
            bytes=int(record.get("bytes", 0)),
            rows=int(record.get("rows", 0)),
            sha256=str(record.get("sha256", "")),
        )
    return sorted(merged.values(), key=lambda s: s.path)


def _write_manifest(home: Path, out_dir: Path, result: ExportResult) -> None:
    manifest = {
        "schema_version": SHARD_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "machine_id": machine_id(home),
        "entries": result.entries,
        "blobs": result.blobs,
        "skipped": result.skipped,
        "shards": [
            {"path": s.path, "bytes": s.bytes, "rows": s.rows, "sha256": s.sha256}
            for s in result.shards
        ],
        "databases": [
            {
                "path": d.path,
                "entry_id": d.entry_id,
                "bytes": d.bytes,
                "sha256": d.sha256,
            }
            for d in result.databases
        ],
    }
    if result.agreements is not None:
        agreement = result.agreements
        manifest["agreements"] = {
            "path": agreement.path,
            "bytes": agreement.bytes,
            "sha256": agreement.sha256,
        }
    atomic_write(
        out_dir / _MANIFEST, json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )


@dataclass
class ValidationResult:
    """What :func:`validate` found. ``ok`` is the CI/cron exit signal."""

    problems: list[str] = field(default_factory=list)
    shards_checked: int = 0
    rows_checked: int = 0

    @property
    def ok(self) -> bool:
        return not self.problems


def declared_paths(manifest):
    """A typed manifest's canonical paths; reject malformed or escaping names before any read."""
    from pathlib import PurePosixPath

    if not isinstance(manifest, dict):
        raise ValueError("manifest must be an object")
    records = []
    for name in ("shards", "databases"):
        values = manifest.get(name, [] if name == "databases" else None)
        if not isinstance(values, list):
            raise ValueError(f"manifest {name} must be a list")
        records.extend(values)
    if manifest.get("agreements") is not None:
        records.append(manifest["agreements"])
    paths = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("manifest declaration must be an object")
        rel = record.get("path")
        if not isinstance(rel, str) or not rel or "\\" in rel or "\0" in rel:
            raise ValueError("manifest declaration has an invalid path")
        path = PurePosixPath(rel)
        if (
            path.is_absolute()
            or path.as_posix() != rel
            or any(part in (".", "..") for part in path.parts)
        ):
            raise ValueError(f"manifest path is outside its export: {rel}")
        if rel in paths:
            raise ValueError(f"duplicate manifest path: {rel}")
        paths.append(rel)
    return paths


def validate(shard_dir: Path) -> ValidationResult:
    """Verify an export end to end: a backup nobody has verified is a hope.

    Checks the manifest parses and is well-formed, every declared shard exists,
    its byte length / row count / sha256 all re-derive to the recorded values, and
    every row re-parses as JSON. Any mismatch is reported (not raised) so a caller
    can print all problems at once.
    """
    result = ValidationResult()
    manifest_path = shard_dir / _MANIFEST
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        result.problems.append(f"missing {_MANIFEST}")
        return result
    except json.JSONDecodeError as exc:
        result.problems.append(f"{_MANIFEST} is not valid JSON: {exc}")
        return result

    try:
        declared_paths(manifest)
    except ValueError as exc:
        result.problems.append(str(exc))
        return result

    if manifest.get("schema_version") != SHARD_SCHEMA_VERSION:
        result.problems.append(
            f"unsupported schema_version {manifest.get('schema_version')!r} "
            f"(expected {SHARD_SCHEMA_VERSION})"
        )
    if not manifest.get("machine_id"):
        result.problems.append("manifest has no machine_id")
    shards = manifest.get("shards")
    if not isinstance(shards, list):
        result.problems.append("manifest has no shards list")
        return result

    declared: set[str] = set()
    for record in shards:
        rel = str(record.get("path", ""))
        declared.add(rel)
        path = shard_dir / rel
        if not path.is_file():
            result.problems.append(f"{rel}: declared in manifest but missing on disk")
            continue
        data = path.read_bytes()
        result.shards_checked += 1
        if len(data) != record.get("bytes"):
            result.problems.append(
                f"{rel}: size {len(data)} != manifest {record.get('bytes')}"
            )
        actual_sha = _sha256(data)
        if actual_sha != record.get("sha256"):
            result.problems.append(f"{rel}: sha256 mismatch (content changed)")
        lines = [
            ln
            for ln in data.decode("utf-8", errors="replace").splitlines()
            if ln.strip()
        ]
        if len(lines) != record.get("rows"):
            result.problems.append(
                f"{rel}: {len(lines)} rows != manifest {record.get('rows')}"
            )
        for number, line in enumerate(lines, start=1):
            try:
                json.loads(line)
            except json.JSONDecodeError as exc:
                result.problems.append(f"{rel}:{number}: unparseable row ({exc})")
                break
        result.rows_checked += len(lines)

    for path in sorted(shard_dir.rglob("*.jsonl")):
        rel = path.relative_to(shard_dir).as_posix()
        if rel not in declared:
            result.problems.append(
                f"{rel}: present on disk but not declared in the manifest"
            )

    for record in manifest.get("databases", []) or []:
        rel = str(record.get("path", ""))
        path = shard_dir / rel
        if not path.is_file():
            result.problems.append(f"{rel}: declared database missing on disk")
            continue
        data = path.read_bytes()
        if len(data) != record.get("bytes"):
            result.problems.append(
                f"{rel}: db size {len(data)} != manifest {record.get('bytes')}"
            )
        if _sha256(data) != record.get("sha256"):
            result.problems.append(f"{rel}: db sha256 mismatch (content changed)")
    agreement = manifest.get("agreements")
    if agreement is not None:
        if (
            not isinstance(agreement, dict)
            or agreement.get("path") != "agreements.json"
        ):
            result.problems.append("invalid agreements declaration")
        else:
            path = shard_dir / "agreements.json"
            if not path.is_file():
                result.problems.append("declared agreements missing on disk")
            else:
                data = path.read_bytes()
                if len(data) != agreement.get("bytes") or _sha256(
                    data
                ) != agreement.get("sha256"):
                    result.problems.append("agreements bytes or digest mismatch")
    return result


def export_and_validate(
    home: Path, out_dir: Path
) -> tuple[ExportResult, ValidationResult]:
    """Export then immediately verify — the restore-drill core (§3)."""
    exported = export_shards(home, out_dir)
    return exported, validate(out_dir)


@dataclass
class ImportResult:
    """What :func:`import_shards` read back from a shard directory.

    ``rows`` maps an inventory entry id → its rows, reassembled across year buckets,
    sqlite tables, and ``part-NNNN`` splits (so the caller sees one flat list per
    entry, exactly what `export_shards` was handed). ``blobs`` lists the content-addressed
    blob paths present under ``blobs/`` (KIND_TREE payloads), relative to the shard dir.
    ``problems`` carries any non-fatal read issue; a structurally broken export raises.
    """

    rows: dict[str, list[dict]] = field(default_factory=dict)
    blobs: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    machine_id: str = ""
    databases: dict[str, str] = field(default_factory=dict)
    agreements: dict[str, dict[str, dict[str, str]]] = field(default_factory=dict)

    @property
    def entries(self) -> int:
        return len(self.rows)

    @property
    def total_rows(self) -> int:
        return sum(len(v) for v in self.rows.values())


def _entry_id_of(rel: str) -> str:
    """The inventory entry id that owns a shard — its first path segment.

    Mirrors ``_merged_shard_records``' matching rule: every shard `export_shards`
    writes is rooted at ``<entry_id>/…`` (``tasks/entities.jsonl``,
    ``memory_db/semantic_memory.jsonl``, ``sessions/2026.jsonl``, possibly with a
    ``.part-0001`` suffix), so the leading segment is the entry that produced it.
    """
    return rel.split("/", 1)[0]


def _rows_of_shard(shard_dir: Path, rel: str) -> list[dict]:
    """Parse one shard file's canonical-JSONL rows (blank lines skipped)."""
    data = (shard_dir / rel).read_bytes()
    out: list[dict] = []
    for line in data.decode("utf-8").splitlines():
        if not line.strip():
            continue
        out.append(json.loads(line))
    return out


def import_shards(shard_dir: Path, *, entries: list[str] | None = None) -> ImportResult:
    """Read a shard directory back into rows keyed by inventory entry id.

    The inverse of :func:`export_shards`. Runs :func:`validate` first — a shard whose
    bytes/sha/row-count drifted from the manifest is not trustworthy input for a merge
    or restore, so a failed validation raises :class:`ValueError` rather than importing
    silently corrupt data. ``entries`` optionally restricts to specific entry ids (the
    sync cycle imports only the entries a remote actually changed).

    Rows for an entry are reassembled across every shape the exporter splits into —
    sqlite tables (``<entry>/<table>.jsonl``), year buckets (``<entry>/2026.jsonl``),
    and deterministic ``part-NNNN`` files — into one flat, order-preserving list, so a
    round-trip yields exactly the rows that were exported.
    """
    report = validate(shard_dir)
    if not report.ok:
        raise ValueError(
            "refusing to import an invalid shard export:\n" + "\n".join(report.problems)
        )

    manifest = json.loads((shard_dir / _MANIFEST).read_text(encoding="utf-8"))
    result = ImportResult(machine_id=str(manifest.get("machine_id", "")))
    if manifest.get("agreements"):
        value = json.loads((shard_dir / "agreements.json").read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("agreements are not an object")
        result.agreements = value
    wanted = set(entries) if entries else None

    for record in manifest.get("shards", []):
        rel = str(record.get("path", ""))
        if not rel:
            continue
        entry_id = _entry_id_of(rel)
        if wanted is not None and entry_id not in wanted:
            continue
        try:
            rows = _rows_of_shard(shard_dir, rel)
            entry = next(
                (item for item in inv.all_entries() if item.id == entry_id), None
            )
            if (
                entry is not None
                and entry.kind == inv.KIND_JSON_ENTITY_DIR
                and entry.derived_within
            ):
                from gideon.workspace.portability import _is_derived_within

                rows = [
                    row
                    for row in rows
                    if not _is_derived_within(
                        entry.path, str(row.get("id", "")) + ".json"
                    )
                ]
            result.rows.setdefault(entry_id, []).extend(rows)
        except (OSError, json.JSONDecodeError) as exc:
            result.problems.append(f"{rel}: unreadable during import ({exc})")

    blob_root = shard_dir / "blobs"
    if blob_root.is_dir():
        result.blobs = sorted(
            p.relative_to(shard_dir).as_posix()
            for p in blob_root.rglob("*")
            if p.is_file() and not p.is_symlink()
        )

    for record in manifest.get("databases", []) or []:
        rel = str(record.get("path", ""))
        entry_id = str(record.get("entry_id", ""))
        if not rel or not entry_id:
            continue
        if wanted is not None and entry_id not in wanted:
            continue
        result.databases[entry_id] = rel
    return result


def dirty_entries(home: Path, state_path: Path) -> Changes:
    """Inventory entry ids whose content changed since the last export.

    Uses an mtime fingerprint per entry so the hourly incremental export writes
    only what moved. A missing/corrupt state file means "everything is dirty",
    which is the safe direction — a needless full export costs time, a missed one
    costs data.
    """
    try:
        guard_path(state_path, read=True)
        previous = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(previous, dict):
            previous = {}
    except (OSError, json.JSONDecodeError, LinkInTheWay):
        previous = {}

    current: dict[str, str] = {}
    dirty: list[str] = []
    for entry in inv.export_entries():
        paths = inv.paths_for(home, entry)
        if not paths and entry.id not in previous:
            continue
        fingerprints = [(rel, _fingerprint(home / rel)) for rel in paths]
        fingerprint = hashlib.sha256(
            json.dumps(fingerprints, sort_keys=True).encode("utf-8")
        ).hexdigest()
        current[entry.id] = fingerprint
        if previous.get(entry.id) != fingerprint:
            dirty.append(entry.id)
    return Changes(dirty, current)


class Changes(list):
    """Changed entries and fingerprints awaiting a successful export."""

    def __init__(self, entries, fingerprints):
        super().__init__(entries)
        self.fingerprints = fingerprints


def mark_exported(state_path: Path, changes: Changes, *, skipped=()) -> None:
    failed = {str(path).split("/", 1)[0] for path in skipped}
    guard_path(state_path)
    try:
        fingerprints = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(fingerprints, dict):
            fingerprints = {}
    except (OSError, ValueError):
        fingerprints = {}
    fingerprints.update(
        {key: value for key, value in changes.fingerprints.items() if key not in failed}
    )
    try:
        atomic_write(
            state_path, json.dumps(fingerprints, indent=2, sort_keys=True) + "\n"
        )
    except OSError:
        logger.debug("shards: could not persist exported fingerprints", exc_info=True)


def _fold_wal(db_path: Path) -> None:
    """Fold committed WAL frames into the main DB file with a passive checkpoint.

    Best-effort and non-destructive: ``PASSIVE`` never blocks on a writer and never
    truncates, so it cannot itself become the volatile event the fingerprint is trying to
    avoid. A failure (locked store, missing sidecar, not a database) is swallowed — the
    caller then fingerprints whatever the main file currently is, which for an actively
    written store has already moved on its last commit.
    """
    if not db_path.with_name(db_path.name + "-wal").exists():
        return
    try:
        conn = sqlite3.connect(str(db_path), timeout=0.5)
        try:
            conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        finally:
            conn.close()
    except sqlite3.Error:
        logger.debug(
            "shards: passive checkpoint skipped for %s", db_path, exc_info=True
        )


def _fingerprint(path: Path) -> str:
    """A cheap change fingerprint: newest mtime + total size beneath ``path``.

    For a SQLite file the committed WAL frames are folded into the MAIN FILE with a
    passive checkpoint first, and only the main file's ``(mtime, size)`` is fingerprinted.
    The sidecars are NOT stat'd. Every store here runs in WAL mode, where a committed
    write lands in the ``-wal`` and may not touch the main file for a long time — so
    fingerprinting the ``.db`` alone once reported "unchanged" through an entire session
    of writes, and the incremental export silently backed up nothing (found by writing a
    fact and watching the fingerprint not move). A passive checkpoint makes that committed
    content durable in the main file, so the main-file mtime moves exactly when — and only
    when — durable data changed.

    Neither sidecar is folded in, and that is the fix, not an optimization. Both are
    VOLATILE at moments the writer does not control:

    * ``-shm`` is the WAL index — pure ephemeral shared memory, mmap'd ``MAP_SHARED``, its
      mtime advancing at page-writeback time rather than at store time.
    * ``-wal`` is truncated to zero by a checkpoint (autocheckpoint at 1000 pages, or the
      last connection closing, or any other connection running ``wal_checkpoint``). That
      moves the sidecar's mtime AND size with **no data change at all** — reproduced
      directly: a ``wal_checkpoint(TRUNCATE)`` shifts an unchanged store's fingerprint.

    Folding either one made the fingerprint report change where no data had changed. The
    ``-shm`` fold produced an idle home re-exporting ``memory.db`` forever; the ``-wal``
    fold produced a load-dependent CI flake in the "dirty detection settles completely"
    test — under load a checkpoint lands between two ``dirty_entries`` calls and the second
    reports the store dirty though nothing was written. A change fingerprint must key on
    durable content the writer controls; the passive-checkpoint-then-main-file rule does.

    The checkpoint is best-effort: if the store is locked by an active writer it is a
    no-op, which is the safe direction — an actively-written store is genuinely dirty, and
    the main-file mtime will have moved on its last commit regardless.
    """
    guard_path(path, read=True)
    if path.is_file():
        if path.suffix == ".db":
            _fold_wal(path)
        stat = path.stat()
        return f"{stat.st_mtime_ns}:{stat.st_size}"
    newest = 0
    total = 0
    count = 0
    for child in path.rglob("*"):
        guard_path(child, read=True)
        try:
            if child.is_file():
                stat = child.stat()
                newest = max(newest, stat.st_mtime_ns)
                total += stat.st_size
                count += 1
        except OSError:
            continue
    return f"{newest}:{total}:{count}"


def default_shard_dir(home: Path) -> Path:
    return home / "shards"


def clear_shards(out_dir: Path) -> None:
    """Clear only known export files, preserving unrelated files in the destination."""
    guard_path(out_dir)
    if out_dir.exists() and not out_dir.is_dir():
        raise ValueError("export destination is not a directory")
    if not out_dir.exists():
        out_dir.mkdir(parents=True, mode=0o700)
        return
    if not is_an_export(out_dir):
        if any(out_dir.iterdir()):
            raise ValueError(
                "export destination holds files and no recognized export; choose an empty directory"
            )
        return
    ours = {entry.id for entry in inv.INVENTORY} | {"db"}
    selected = [
        child
        for child in out_dir.iterdir()
        if child.name == _MANIFEST or child.name in ours
    ]
    for child in selected:
        guard_path(child)
        if child.is_dir():
            for nested in child.rglob("*"):
                guard_path(nested)
    for child in selected:
        if child.is_dir():
            shutil.rmtree(child)
        elif child.name == _MANIFEST:
            child.unlink()


def backup_cmd(args) -> int:
    """``gideon backup export|validate`` — the operator entry point.

    ``validate`` is designed for CI/cron use: it prints every problem it found and
    returns non-zero, so a scheduled verification fails loudly instead of quietly
    reporting success over a corrupt export.
    """
    from gideon.core.concurrency import single_flight

    home = _home()
    command = getattr(args, "backup_command", None)

    if command == "export":
        out_dir = (
            Path(args.out_dir).expanduser() if args.out_dir else default_shard_dir(home)
        )
        incremental = bool(getattr(args, "incremental", False))
        with single_flight("shard-export") as acquired:
            if not acquired:
                print("⏭  Another shard export is already running — skipping.")
                return 0
            entries = None
            changes = None
            state_path = home / ".shard-state.json"
            if incremental:
                changes = dirty_entries(home, state_path)
                entries = changes
                if not entries:
                    print("✅ Nothing changed since the last export.")
                    return 0
                print(
                    f"↻ Incremental export: {len(entries)} changed entr"
                    f"{'y' if len(entries) == 1 else 'ies'}"
                )
            else:
                try:
                    clear_shards(out_dir)
                except (ValueError, OSError, LinkInTheWay) as error:
                    print(f"❌ Export destination refused: {error}")
                    return 1
            result = export_shards(home, out_dir, entries=entries)
            if changes is not None:
                mark_exported(state_path, changes, skipped=result.skipped)
        print(
            f"✅ Exported {result.entries} entr"
            f"{'y' if result.entries == 1 else 'ies'} → "
            f"{len(result.shards)} shard(s), {result.rows:,} row(s)"
            + (f", {result.blobs:,} blob(s)" if result.blobs else "")
        )
        print(f"📁 {out_dir}")
        for entry_id, reason in sorted(result.skipped.items()):
            print(f"⚠️  skipped {entry_id}: {reason}")
        return 1 if result.skipped else 0

    if command == "validate":
        shard_dir = (
            Path(args.shard_dir).expanduser()
            if args.shard_dir
            else default_shard_dir(home)
        )
        if not shard_dir.is_dir():
            print(
                f"❌ No shard export at {shard_dir} — run `gideon backup export` first."
            )
            return 1
        report = validate(shard_dir)
        if report.ok:
            print(
                f"✅ Export valid: {report.shards_checked} shard(s), "
                f"{report.rows_checked:,} row(s) verified (bytes + rows + sha256 + parse)."
            )
            return 0
        print(f"❌ Export INVALID — {len(report.problems)} problem(s):")
        for problem in report.problems[:50]:
            print(f"  - {problem}")
        if len(report.problems) > 50:
            print(f"  … and {len(report.problems) - 50} more")
        return 1

    print("Usage: gideon backup {export|validate}")
    return 2


def _home() -> Path:
    from gideon.core.config.loader import config_dir

    return Path(os.environ.get("GIDEON_HOME", config_dir()))
