"""The transport-driven pull half of the sync cycle (DURABILITY-AND-SYNC §4.1, DAS-6c-ii-e).

This is where the pure pieces meet a real remote. Given a transport (DAS-6a), the local
:class:`registry.Registry` just pulled, and the durable :class:`cursor.Cursor`, it walks the
peers' unseen shard sets and merges each into the live store:

    for each peer prefix the cursor hasn't consumed (registry.new_prefixes_since, ascending):
        refs   = transport.list_remote(prefix)          # cheap
        objs   = transport.pull(refs)                   # bytes
        dir    = materialize objs (strip the prefix)    # a validatable shard dir
        rows   = shards.import_shards(dir)              # 6b — validates, reassembles
        for each entry: reconcile.reconcile_entry(...)  # 6c-i + 6c-ii-c + 6c-ii-d
        cursor.record(peer, seq, aggregate_verdict)     # 6c-ii-b — consumed-only

The **DB path is an injected seam**, not skipped. A `sqlite`/`tree` entry can't be losslessly
rebuilt from row shards (the exporter stores embedding/blob columns as size placeholders), so
those go to an optional ``db_merger`` callback. Until it's provided (DAS-6c-ii-f), a seq that
contains a DB entry is **held** — the cursor is not advanced, so the seq is re-pulled once the
seam lands, rather than silently skipping unmerged database data (§4.1: advance only on
consumed rows). That is the honest partial-slice behavior, and it keeps the row-entry
convergence path (criterion 4) fully working today.

Aggregate verdict for a seq: any held entry (prerequisite-absent, or a DB entry with no
merger) holds the whole seq; otherwise ``payload-bad`` if any entry was poison (advance past
it), else ``consumed``. A prefix the remote can't actually serve yet (a partial push) holds.
"""

from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from gideon.core.atomic_write import atomic_write_bytes
from gideon.core.record_ids import is_safe_record_id
from gideon.integrations.sync_transports.base import SyncTransportProvider
from gideon.operations.durability import inventory as inv
from gideon.operations.durability import reconcile
from gideon.operations.durability.conflicts import ConflictQueue
from gideon.operations.durability.cursor import (
    CONSUMED,
    PAYLOAD_BAD,
    PREREQ_ABSENT,
    Cursor,
)
from gideon.operations.durability.home_paths import LinkInTheWay, home_path
from gideon.operations.durability.registry import Registry, shard_prefix
from gideon.operations.durability.shards import declared_paths, import_shards

logger = logging.getLogger(__name__)

DbMerger = Callable[[inv.StateEntry, Path], str]


@dataclass
class SeqOutcome:
    """What pulling one peer's one seq did."""

    peer_id: str
    seq: int
    verdict: str = CONSUMED
    advanced: bool = False
    entries: int = 0
    added: int = 0
    updated: int = 0
    removed: int = 0
    deferred_db: list[str] = field(default_factory=list)
    conflicts: int = 0
    detail: str = ""
    refused: dict[str, str] = field(default_factory=dict)


@dataclass
class PullReport:
    """Every seq outcome from one pull sweep, plus roll-ups for the caller/doctor."""

    outcomes: list[SeqOutcome] = field(default_factory=list)

    @property
    def advanced(self) -> int:
        return sum(1 for o in self.outcomes if o.advanced)

    @property
    def held(self) -> int:
        return sum(1 for o in self.outcomes if not o.advanced)

    @property
    def added(self) -> int:
        return sum(o.added for o in self.outcomes)

    @property
    def removed(self) -> int:
        return sum(o.removed for o in self.outcomes)

    @property
    def conflicts(self) -> int:
        return sum(o.conflicts for o in self.outcomes)


def _materialize(objs, prefix: str, dest: Path) -> int:
    """Write pulled objects into ``dest`` as a validatable shard dir, stripping ``prefix``
    from each key so paths are shard-dir-relative (``manifest.json``, ``tasks/entities.jsonl``).
    Returns how many objects landed. Objects outside ``prefix`` are ignored defensively.
    """
    selected = []
    for obj in objs:
        if not isinstance(obj.key, str) or not obj.key.startswith(prefix):
            raise ValueError("object key does not belong to the selected export")
        rel = obj.key[len(prefix) :]
        target = home_path(dest, rel)
        selected.append((target, obj.data))
    for target, data in selected:
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(target, data, mode=0o600)
    return len(selected)


def _pull_one_seq(
    transport: SyncTransportProvider,
    home: Path,
    peer_id: str,
    seq: int,
    db_merger: Optional[DbMerger],
    registry: Optional[Registry] = None,
    queue: Optional[ConflictQueue] = None,
    now: str = "",
    codec=None,
    ancestors=None,
    self_id="",
) -> SeqOutcome:
    out = SeqOutcome(peer_id=peer_id, seq=seq)
    if not is_safe_record_id(peer_id):
        out.verdict = PAYLOAD_BAD
        out.detail = "machine id is not one plain name"
        out.refused[peer_id] = out.detail
        return out
    prefix = shard_prefix(peer_id, seq)
    refs = transport.list_remote(prefix)
    if not refs:
        out.verdict = PREREQ_ABSENT
        out.detail = "no objects under prefix (partial push?)"
        return out
    objs = transport.pull(refs)
    if {ref.key for ref in refs} - {obj.key for obj in objs}:
        out.verdict = PREREQ_ABSENT
        out.detail = "listed objects disappeared before download"
        return out
    if codec is not None:
        objs, refused = codec.decrypt_after_pull(objs)
        if refused.keys:
            out.verdict = PAYLOAD_BAD
            out.detail = "encrypted-store violation: " + "; ".join(refused.reasons[:5])
            return out
        if refused.unreadable:
            out.verdict = PREREQ_ABSENT
            out.detail = (
                f"{len(refused.unreadable)} object(s) did not decrypt (wrong passphrase, or "
                "the store was modified) — held for retry"
            )
            return out
    with tempfile.TemporaryDirectory() as tmp:
        shard_dir = Path(tmp)
        try:
            materialized = _materialize(objs, prefix, shard_dir)
        except (ValueError, LinkInTheWay) as exc:
            out.verdict = PAYLOAD_BAD
            out.detail = f"refused export object path: {exc}"
            return out
        if materialized == 0:
            out.verdict = PREREQ_ABSENT
            out.detail = "prefix listed but no bytes pulled"
            return out
        manifest_path = shard_dir / "manifest.json"
        if not manifest_path.is_file():
            out.verdict = PREREQ_ABSENT
            out.detail = "complete-copy manifest is unavailable"
            return out
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            declarations = declared_paths(manifest)
            if any(not (shard_dir / rel).is_file() for rel in declarations):
                out.verdict = PREREQ_ABSENT
                out.detail = "declared objects are missing from the complete copy"
                return out
            imported = import_shards(shard_dir)
        except (ValueError, OSError, TypeError, AttributeError) as exc:
            out.verdict = PAYLOAD_BAD
            out.detail = f"import failed: {exc}"
            return out
        held = False
        poison = False
        for entry_id, rows in imported.rows.items():
            entry = inv.by_id(entry_id)
            if entry is None:
                held = True
                out.deferred_db.append(entry_id)
                continue
            out.entries += 1
            if entry.machine_local:
                logger.info(
                    "pull: consuming legacy shard for machine-local entry %s without applying it",
                    entry.id,
                )
                continue
            if reconcile.handles_kind(entry.kind):
                res = reconcile.reconcile_entry(
                    home,
                    entry,
                    rows,
                    ancestors=(
                        ancestors.of(peer_id, entry.id) if ancestors is not None else {}
                    ),
                    published=(
                        ancestors.published(entry.id) if ancestors is not None else {}
                    ),
                    history=ancestors.held(entry.id) if ancestors is not None else {},
                    deleted=(
                        ancestors.deleted(entry.id) if ancestors is not None else {}
                    ),
                    peer_id=peer_id,
                    agreed_there=imported.agreements.get(self_id, {}).get(entry.id, {}),
                    queue=queue,
                    now=now,
                )
                out.added += res.added
                out.updated += res.updated
                out.removed += res.removed
                out.conflicts += res.conflicts
                if ancestors is not None:
                    ancestors.record(peer_id, entry.id, res.new_ancestors)
                    ancestors.took_deletion(entry.id, res.deleted_there)
                if res.verdict == PREREQ_ABSENT:
                    held = True
                    out.detail = (
                        res.detail or "a local landing prerequisite is unavailable"
                    )
                    out.refused[entry.path] = out.detail
                elif res.verdict == PAYLOAD_BAD:
                    poison = True
                    out.detail = res.detail or "entry payload is unusable"
            elif db_merger is not None:
                verdict = db_merger(entry, shard_dir)
                if verdict == PREREQ_ABSENT:
                    held = True
                elif verdict == PAYLOAD_BAD:
                    poison = True
            else:
                held = True
                out.deferred_db.append(entry_id)
    if held:
        out.verdict = PREREQ_ABSENT
        out.detail = out.detail or ("held for DB seam: " + ", ".join(out.deferred_db))
    else:
        out.verdict = PAYLOAD_BAD if poison else CONSUMED
    return out


def pull_from_peers(
    transport: SyncTransportProvider,
    home: Path,
    registry: Registry,
    cursor: Cursor,
    *,
    self_id: str,
    db_merger: Optional[DbMerger] = None,
    queue: Optional[ConflictQueue] = None,
    now: str = "",
    codec=None,
    ancestors=None,
) -> PullReport:
    """Pull and merge every peer shard set the cursor hasn't consumed, oldest seq first.

    Advances the cursor only on a consumed (or payload-bad) seq; a held seq (a not-yet-servable
    prefix, an unknown entry, or a DB entry with no ``db_merger``) leaves the cursor where it
    is, so it is re-pulled next cycle. ``codec`` is the optional DAS-8 sync codec: when present
    every pulled object is decrypted before it is materialized, and a plaintext one is a
    permanent skip. Returns a :class:`PullReport` of per-seq outcomes.
    """
    report = PullReport()
    seen = cursor.seen()
    for peer in registry.peers(self_id):
        already = int(seen.get(peer.machine_id, 0) or 0)
        for seq in ([peer.seq] if peer.seq > already else []):
            outcome = _pull_one_seq(
                transport,
                home,
                peer.machine_id,
                seq,
                db_merger,
                registry=registry,
                queue=queue,
                now=now,
                codec=codec,
                ancestors=ancestors,
                self_id=self_id,
            )
            if ancestors is not None:
                ancestors.save()
            outcome.advanced = cursor.record(peer.machine_id, seq, outcome.verdict)
            report.outcomes.append(outcome)
            if not outcome.advanced:
                break
    return report
