"""Reconcile a peer's rows into the live store (DURABILITY-AND-SYNC §4.1, DAS-6c-ii-d).

The bridge that turns "I pulled a peer's shards" into "the peer's rows are now in my live
store", composing the three pure pieces already built:

    local rows  ←  read the entry's on-disk form the same way the exporter extracts it
    merged      ←  merge.merge_rows(entry.merge, local, remote, tombstones=entry.tombstones)
    live store  →  writeback.apply_rows(entry.kind, dest, merged)

It is deliberately the ROW path only — the kinds whose merge is a deterministic row
reconciliation (`json_entity_dir`, `json_file`, `jsonl_append`). A `sqlite` entry is merged
by the ATTACH-OR-IGNORE path in ``snapshot.py`` and a `tree` entry is rehydrated from the
content-addressed blob store, so :func:`reconcile_entry` DECLINES those (returns a
``handled=False`` outcome) rather than raising — the cycle engine (the atom above this) reads
that verdict and routes the entry to its DB/blob path. A row-mergeable entry that raises
mid-reconcile is caught and reported as a `payload-bad` verdict so one poison entry can't
abort the whole pull; the caller advances its cursor past it (per §4.1) rather than looping.

Reads the local rows exactly as ``shards.export_shards`` would, so the merge sees the same
row shapes on both sides — the invariant that makes convergence hold (criterion 4).
"""

from __future__ import annotations

import logging
import json
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Mapping, Optional

from gideon.operations.durability import conflicts as conflicts_mod
from gideon.operations.durability import inventory as inv
from gideon.operations.durability import writeback
from gideon.operations.durability.cursor import CONSUMED, PAYLOAD_BAD, PREREQ_ABSENT
from gideon.operations.durability.home_paths import home_path, LinkInTheWay
from gideon.operations.durability.merge import MergeResult, merge_rows
from gideon.operations.durability.shards import (
    _json_rows_from_entity_dir,
    _json_rows_from_file,
    _json_record_rows_from_file,
    _jsonl_rows_by_year,
)

logger = logging.getLogger(__name__)

_ROW_KINDS = frozenset(
    {inv.KIND_JSON_ENTITY_DIR, inv.KIND_JSON_FILE, inv.KIND_JSONL_APPEND}
)


@dataclass
class ReconcileResult:
    """The outcome of reconciling one entry — a consume verdict the cursor understands.

    ``handled`` is False when the entry is not a row-merge kind (sqlite/tree) — the caller
    routes it elsewhere and does NOT treat that as consumed. ``verdict`` is the
    cursor verdict for a handled entry: ``consumed`` on a clean merge, ``payload-bad`` when
    the entry's rows were structurally unusable (advance past it, don't loop).
    """

    entry_id: str
    handled: bool = True
    verdict: str = CONSUMED
    added: int = 0
    updated: int = 0
    removed: int = 0
    detail: str = ""
    conflicts: int = 0
    new_ancestors: dict[str, str] = dataclass_field(default_factory=dict)
    deleted_there: dict = dataclass_field(default_factory=dict)


def read_local_rows(entry: inv.StateEntry, src: Path) -> list[dict]:
    """The entry's current on-disk rows, read the same way the exporter extracts them, so
    both sides of the merge speak the same row shape. A missing store is an empty list.

    Public because the conflict resolver reads the same rows for the same reason (DAS-10):
    a resolution substitutes one row into this exact set, so reading it any other way would
    let a review write reshape the store."""
    if entry.kind == inv.KIND_JSON_ENTITY_DIR:
        return _json_rows_from_entity_dir(src, entry_path=entry.path) if src.is_dir() else []
    if entry.kind == inv.KIND_JSON_FILE:
        if entry.records_field:
            return (
                _json_record_rows_from_file(src, entry.records_field)
                if src.is_file()
                else []
            )
        return _json_rows_from_file(src, entry=entry) if src.is_file() else []
    if entry.kind == inv.KIND_JSONL_APPEND:
        files = (
            [src]
            if src.is_file()
            else (sorted(src.rglob("*.jsonl")) if src.is_dir() else [])
        )
        rows: list[dict] = []
        for path in files:
            for _year, bucket in _jsonl_rows_by_year(path).items():
                rows.extend(bucket)
        return rows
    return []


def rows_for_store(entry: inv.StateEntry, dest: Path, rows: list[dict]) -> list[dict]:
    """Wrap record-shaped collection rows back into their native JSON file envelope."""
    if entry.kind != inv.KIND_JSON_FILE or not entry.records_field:
        return rows
    rows = [row for row in rows if not writeback._is_tombstone(row)]
    field = entry.records_field
    if field == "$root":
        document = [row.get("data", {}) for row in rows]
    else:
        try:
            document = json.loads(dest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            document = {}
        if not isinstance(document, dict):
            document = {}
        document[field] = [row.get("data", {}) for row in rows]
    return [{"id": dest.name, "data": document}]


def handles_kind(kind: str) -> bool:
    """Whether :func:`reconcile_entry` owns this inventory kind (a row-merge kind)."""
    return kind in _ROW_KINDS


def reconcile_entry(
    home: Path,
    entry: inv.StateEntry,
    remote_rows: list[dict],
    *,
    ancestors: Optional[Mapping[str, str]] = None,
    published: Optional[Mapping[str, list[str]]] = None,
    agreed_there: Optional[Mapping[str, str]] = None,
    history: Optional[Mapping[str, list[str]]] = None,
    deleted: Optional[Mapping] = None,
    peer_id: str = "",
    queue: Optional[conflicts_mod.ConflictQueue] = None,
    now: str = "",
) -> ReconcileResult:
    """Merge ``remote_rows`` into ``entry``'s live store under ``home`` and write the result
    back. Returns a :class:`ReconcileResult` carrying the cursor verdict.

    Declines (``handled=False``) a non-row kind — the cycle routes sqlite via ATTACH-IGNORE
    and tree via the blob store. A row kind that throws mid-merge is caught and reported
    ``payload-bad`` so a single bad entry advances the cursor past itself rather than
    wedging every later seq (§4.1).

    **Conflict handling (DAS-7, §4.2).** With ``ancestors`` (the shared registry's agreed
    shas for this family) and a ``queue``, every id whose local AND remote row both moved
    since the ancestor is recorded for review and then **HELD**: its remote row is dropped
    before the merge, so the local bytes are untouched and the local version stays
    authoritative until a human resolves. Held ids also keep their old ancestor, so the
    conflict re-detects next cycle instead of quietly self-resolving. A conflicted entry is
    still ``consumed`` — the divergence is durably recorded, so re-pulling the same seq
    forever would add nothing and would wedge the cursor.
    """
    if entry.machine_local:
        return ReconcileResult(
            entry.id,
            handled=True,
            verdict=CONSUMED,
            detail="machine-local state is never reconciled from a peer",
        )
    if not handles_kind(entry.kind):
        return ReconcileResult(
            entry.id, handled=False, detail=f"non-row kind {entry.kind}"
        )
    dest = Path(home) / entry.path
    try:
        dest = home_path(home, entry.path)
        local = read_local_rows(entry, dest)
        shared_local = [inv.shared_value(entry, row) for row in local]
        shared_remote = [inv.shared_value(entry, row) for row in remote_rows]
        live = {conflicts_mod.row_id(row): row for row in shared_local}
        removed_here = dict(deleted or {})
        # Only a readable existing store establishes absence; never infer from a missing store.
        if _read_proves_absence(entry, dest):
            for rid, versions in (history or {}).items():
                if rid not in live and rid not in removed_here:
                    removed_here[rid] = conflicts_mod.Deletion(now, tuple(versions))
        removed_here = {rid:mark for rid,mark in removed_here.items() if mark.by != peer_id or not peer_id}
        weighed = conflicts_mod.weigh_deletions(entry, live, shared_remote, removed_here, now=now)
        deletion_ids = {record.entity_id for record in weighed.conflicts}
        if queue is not None:
            deletion_recorded = sum(queue.record(record) for record in weighed.conflicts)
        else:
            deletion_recorded = 0
        delete_rows = {rid:row for rid,row in weighed.applied.items()}
        shared_remote = [row for row in shared_remote if not writeback._is_tombstone(row) and conflicts_mod.row_id(row) not in weighed.declined | deletion_ids]
        ancestors, handed_back = _in_common(entry, shared_remote, ancestors or {}, published or {}, agreed_there or {})
        held, recorded = _record_conflicts(
            entry, shared_local, shared_remote, ancestors, queue, now
        )
        held |= deletion_ids
        recorded += deletion_recorded
        effective_remote = (
            [r for r in shared_remote if conflicts_mod.row_id(r) not in held]
            if held
            else shared_remote
        )
        ahead, behind = _one_side_changed(entry, shared_local, effective_remote, ancestors)
        effective_remote = [row for row in effective_remote if conflicts_mod.row_id(row) not in behind]
        if entry.records_field or entry.machine_local_fields:
            merged = _merge_shared_records(shared_local, effective_remote)
        else:
            merged = merge_rows(
                entry.merge,
                local,
                effective_remote,
                tombstones=entry.tombstones,
                dedup_key="id",
            )
        if delete_rows:
            merged.rows = [row for row in merged.rows if conflicts_mod.row_id(row) not in delete_rows] + list(delete_rows.values())
        merged.rows = [ahead.get(conflicts_mod.row_id(row), row) for row in merged.rows]
        local_by_id = {conflicts_mod.row_id(row): row for row in local}
        merged.rows = [
            inv.apply_machine_local_fields(
                entry, row, local_by_id.get(conflicts_mod.row_id(row))
            )
            for row in merged.rows
        ]
        applied = writeback.apply_rows(
            entry.kind, dest, rows_for_store(entry, dest, merged.rows), entry=entry, read_rows=local
        )
    except LinkInTheWay as exc:
        return ReconcileResult(entry.id, verdict=PREREQ_ABSENT, detail=str(exc))
    except (
        Exception
    ) as exc:  # noqa: BLE001 — one bad entry must not abort the whole pull
        logger.warning("reconcile: %s failed (%s) — advancing past it", entry.id, exc)
        return ReconcileResult(entry.id, verdict=PAYLOAD_BAD, detail=str(exc))
    if applied.linked:
        return ReconcileResult(entry.id, verdict=PREREQ_ABSENT, detail="linked local file left unchanged: " + "; ".join(applied.linked.values()))
    if applied.moved:
        return ReconcileResult(entry.id, verdict=PREREQ_ABSENT, detail=f"newer local file edit left unchanged: {', '.join(applied.moved)}")
    detail = f"+{merged.added} ~{merged.updated} -{applied.removed}"
    if recorded or held:
        detail += f" !{recorded} conflict(s), {len(held)} id(s) held local"
    return ReconcileResult(
        entry.id,
        verdict=CONSUMED,
        added=merged.added,
        updated=merged.updated,
        removed=applied.removed,
        detail=detail,
        conflicts=recorded,
        deleted_there={rid:conflicts_mod.Deletion(str(row.get("deleted_at",now)), tuple(sorted(conflicts_mod.held_by_the_delete(row))), peer_id) for rid,row in delete_rows.items()},
        new_ancestors=_agreed_shas(
            effective_remote,
            [inv.shared_value(entry, row) for row in merged.rows],
            held,
        ),
    )


def _merge_shared_records(local: list[dict], remote: list[dict]) -> MergeResult:
    """Merge collection records independently; the pulled shared version wins absent conflict."""
    by_id = {conflicts_mod.row_id(row): row for row in local}
    result = MergeResult(rows=[])
    for row in remote:
        rid = conflicts_mod.row_id(row)
        if not rid:
            continue
        previous = by_id.get(rid)
        if previous is None:
            result.added += 1
        elif previous != row:
            result.updated += 1
        by_id[rid] = row
    result.rows = [by_id[rid] for rid in sorted(by_id)]
    result.kept = max(0, len(local) - result.updated - result.added)
    return result


def _agreed_shas(
    remote_rows: list[dict], merged_rows: list[dict], held: set[str]
) -> dict[str, str]:
    """The ids whose merged row is byte-identical to the row the peer published — the only
    ones we can honestly call a common ancestor (see ``ReconcileResult.new_ancestors``).
    """
    remote_shas = {
        conflicts_mod.row_id(r): conflicts_mod.row_sha(r)
        for r in remote_rows
        if conflicts_mod.row_id(r)
    }
    out: dict[str, str] = {}
    for row in merged_rows:
        rid = conflicts_mod.row_id(row)
        if not rid or rid in held:
            continue
        sha = conflicts_mod.row_sha(row)
        if remote_shas.get(rid) == sha:
            out[rid] = sha
    return out


def _record_conflicts(
    entry: inv.StateEntry,
    local: list[dict],
    remote_rows: list[dict],
    ancestors: Optional[Mapping[str, str]],
    queue: Optional[conflicts_mod.ConflictQueue],
    now: str,
) -> tuple[set[str], int]:
    """Detect + queue this entry's both-sides-edited divergences.

    Returns ``(held ids, newly recorded count)``. Held is the union of what was detected now
    and what is still unresolved in the queue from an earlier cycle — "local stays
    authoritative until resolved" has to survive across cycles, not just the cycle that
    detected the conflict. Without a queue nothing is held: a caller that cannot record a
    conflict must not silently suppress a remote row either (the merge stays as it was).
    """
    if queue is None:
        return set(), 0
    detected = conflicts_mod.detect_conflicts(
        entry, local, remote_rows, ancestors or {}, now=now
    )
    recorded = 0
    for rec in detected:
        if queue.record(rec):
            recorded += 1
            logger.warning(
                "reconcile: %s/%s diverged on both sides — queued for review (local held)",
                entry.id,
                rec.entity_id,
            )
    held = {rec.entity_id for rec in detected} | queue.held_ids(entry.id)
    return held, recorded


def _in_common(entry, remote, ancestors, published, agreed_there):
    bases = dict(ancestors)
    handed_back = {}
    for row in remote:
        identity = conflicts_mod.row_id(row)
        agreed = bases.get(identity)
        versions = list(published.get(identity) or [])
        if not agreed or agreed not in versions:
            continue
        since = versions[len(versions) - versions[::-1].index(agreed):]
        sha = conflicts_mod.row_sha(conflicts_mod.compared(entry, row))
        if sha in since:
            bases[identity] = handed_back[identity] = sha
        elif agreed_there.get(identity) in since:
            bases[identity] = agreed_there[identity]
    return bases, handed_back


def _one_side_changed(entry, local, remote, ancestors):
    ahead, behind = {}, set()
    if entry.merge not in conflicts_mod._ID_KEYED_MERGES:
        return ahead, behind
    here = {conflicts_mod.row_id(row):row for row in local}
    for row in remote:
        identity = conflicts_mod.row_id(row)
        mine, base = here.get(identity), ancestors.get(identity)
        if mine is None or not base:
            continue
        mine_sha = conflicts_mod.row_sha(conflicts_mod.compared(entry, mine))
        there_sha = conflicts_mod.row_sha(conflicts_mod.compared(entry, row))
        if mine_sha != there_sha:
            if mine_sha == base:
                ahead[identity] = row
            elif there_sha == base:
                behind.add(identity)
    return ahead, behind


def _read_proves_absence(entry, dest):
    """Only a complete readable row store establishes that a known record is absent."""
    try:
        from gideon.operations.durability.home_paths import guard_path
        guard_path(dest, read=True)
        if entry.kind == inv.KIND_JSON_ENTITY_DIR:
            if not dest.is_dir():
                return False
            left_out = {}
            _json_rows_from_entity_dir(dest, entry_path=entry.path, left_out=left_out, path_id=entry.path)
            return not left_out
        if entry.kind == inv.KIND_JSON_FILE and dest.is_file():
            document = json.loads(dest.read_text())
            if entry.records_field:
                records = document if entry.records_field == "$root" else document.get(entry.records_field)
                return isinstance(records, list)
            return True
    except (OSError, ValueError, TypeError, AttributeError, LinkInTheWay):
        return False
    return False
