"""One full sync cycle: pull → merge → export → push (DURABILITY-AND-SYNC §4.1, DAS-6c-ii-i).

The orchestrator that assembles every piece built in 6c-i … 6c-ii-h into the loop §4.1 names —
``pull → merge-import remote rows → export local union → push`` — against a resolved transport:

    registry = read the shared registry.json from the remote     (transport.pull of REGISTRY_KEY)
    pull_from_peers(transport, home, registry, cursor,            # 6c-ii-e + the 6c-ii-h db_merger
                    db_merger=make_db_merger(home))
    export_shards(home, out, include_databases=True)              # 6b + 6c-ii-g (DB copies)
    publish_export(transport, out, registry, outbox, …)           # 6c-ii-f (+ CAS registry bump)

Everything below the orchestration was already unit-tested in isolation; this module owns only
the wiring and the read-the-registry step. It is clock-free (``now`` is passed in) and does not
own scheduling — the ``stale_after_secs`` staleness window and the "is sync enabled / which
transport" resolution live in the service layer (6c-ii-j) that calls this. A transport error at
any step is caught and reported in the :class:`SyncCycleReport`, never raised, so one bad cycle
never kills the durability service loop.
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from gideon.integrations.sync_transports.base import RemoteRef, SyncTransportProvider
from gideon.operations.durability import conflicts as conflicts_mod
from gideon.operations.durability import inventory as inv
from gideon.operations.durability import reconcile
from gideon.operations.durability.ancestors import Ancestors
from gideon.operations.durability.conflicts import ConflictQueue
from gideon.operations.durability.cursor import Cursor
from gideon.operations.durability.db_merge import make_db_merger
from gideon.operations.durability.outbox import Outbox
from gideon.operations.durability.published import (
    KEEP_PREVIOUS_SECS,
    Published,
    export_digest,
    superseded,
)
from gideon.operations.durability.pull_engine import PullReport, pull_from_peers
from gideon.operations.durability.push_engine import PushReport, publish_export
from gideon.operations.durability.registry import REGISTRY_KEY, Registry
from gideon.operations.durability.shards import export_shards, import_shards

logger = logging.getLogger(__name__)


@dataclass
class SyncCycleReport:
    """What one cycle did, honestly — including the step that failed, if any."""

    ok: bool = True
    pulled: PullReport | None = None
    pushed: PushReport | None = None
    error: str = ""
    failure: str = ""
    skipped: str = ""
    rows_added: int = 0
    rows_removed: int = 0
    seq_published: int = 0
    conflicts: int = 0
    unchanged_since: int = 0
    copies_removed: list[int] = field(default_factory=list)
    removal_failed: str = ""
    left_out: dict[str, str] = field(default_factory=dict)

    @property
    def detail(self) -> str:
        if self.skipped:
            return f"skipped: {self.skipped}"
        if not self.ok:
            return f"error: {self.error}"
        sent = (
            f"nothing new to send; seq {self.unchanged_since} stands"
            if self.unchanged_since
            else f"published seq {self.seq_published}"
        )
        base = f"+{self.rows_added} -{self.rows_removed} rows; {sent}"
        if self.copies_removed:
            base += f"; removed {len(self.copies_removed)} older copies"
        if self.removal_failed:
            base += f"; older copies not removed ({self.removal_failed})"
        return base + (
            f"; {self.conflicts} conflict(s) queued" if self.conflicts else ""
        )


def read_registry(transport: SyncTransportProvider) -> Registry:
    """Read and parse the shared ``registry.json`` from the remote.

    Absent (a brand-new sync root) → an empty registry, so the first machine publishes from
    scratch. A listing/pull error propagates to the caller, which records it as a failed cycle.
    """
    refs = transport.list_remote(REGISTRY_KEY)
    if not refs:
        return Registry.empty()
    exact = [r for r in refs if r.key == REGISTRY_KEY]
    if not exact:
        raise FileNotFoundError(
            "registry listing advertised objects, but not the registry object"
        )
    objs = transport.pull(exact)
    for obj in objs:
        if obj.key == REGISTRY_KEY:
            return Registry.loads(obj.data)
    raise FileNotFoundError("registry was advertised but unavailable on read-back")


def run_sync_cycle(
    transport: SyncTransportProvider,
    home: Path,
    *,
    self_id: str,
    manifest_sha: str = "",
    now: str = "",
    encrypt: str = "auto",
) -> SyncCycleReport:
    """Run one full pull→merge→export→push cycle against ``transport``.

    ``self_id`` is this machine's id (``shards.machine_id(home)``); ``manifest_sha`` is the sha of
    the export we're about to publish (the caller computes it after export, or passes "" — it is
    a cheap change-probe field, not load-bearing). ``encrypt`` is the ``durability.sync_encrypt``
    tri-state (``auto``/``on``/``off``) resolved against the transport's own default (§4.4).
    Never raises: any transport failure lands in the report so the service loop survives.
    """
    report = SyncCycleReport()
    now = now or datetime.now(timezone.utc).isoformat()
    sync_root = Path(home) / "sync"
    cursor = Cursor(sync_root)
    outbox = Outbox(sync_root)
    conflict_queue = ConflictQueue(home)
    ancestors = Ancestors(sync_root)
    published = Published.load(sync_root)

    codec = None
    try:
        from gideon.operations.durability.crypto import (
            MissingPassphrase,
            SyncEncryptionError,
            codec_for,
        )

        codec = codec_for(transport, setting=encrypt)
    except SyncEncryptionError as exc:
        logger.warning("sync cycle: encryption unavailable (%s)", exc)
        report.ok = False
        report.failure = "passphrase" if isinstance(exc, MissingPassphrase) else "salt"
        report.error = f"encryption: {exc}"
        return report
    except (
        Exception
    ) as exc:  # noqa: BLE001 — salt/metadata transport reads fail the pull
        logger.warning(
            "sync cycle: encryption metadata read failed (%s)", exc, exc_info=True
        )
        report.ok = False
        report.failure = "pull"
        report.error = f"pull: encryption metadata read failed: {exc}"
        return report

    try:
        registry = read_registry(transport)
        _migrate_legacy_deletes(home, ancestors, registry)
        report.pulled = pull_from_peers(
            transport,
            home,
            registry,
            cursor,
            self_id=self_id,
            db_merger=make_db_merger(home),
            queue=conflict_queue,
            now=now,
            codec=codec,
            ancestors=ancestors,
        )
        report.rows_added = report.pulled.added
        report.rows_removed = report.pulled.removed
        report.conflicts = report.pulled.conflicts
    except (
        Exception
    ) as exc:  # noqa: BLE001 — a bad cycle must not kill the service loop
        logger.warning("sync cycle: pull failed (%s)", exc, exc_info=True)
        report.ok = False
        report.failure = "pull"
        report.error = f"pull: {exc}"
        return report

    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            exported = export_shards(
                home, out, include_databases=True, agreements=ancestors.agreements()
            )
            report.left_out = dict(exported.skipped)
            if report.left_out:
                report.ok = False
                report.failure = "error"
                report.error = (
                    f"local export could not carry {len(report.left_out)} store(s)"
                )
                return report
            observed = import_shards(out)
            for entry_id, rows in observed.rows.items():
                entry = inv.by_id(entry_id)
                if (
                    entry is not None
                    and reconcile.handles_kind(entry.kind)
                    and entry.merge in conflicts_mod._ID_KEYED_MERGES
                    and (home / entry.path).exists()
                ):
                    ancestors.publish(
                        entry.id,
                        {
                            conflicts_mod.row_id(row): conflicts_mod.row_sha(
                                conflicts_mod.compared(entry, row)
                            )
                            for row in rows
                            if conflicts_mod.row_id(row)
                            and not reconcile.writeback._is_tombstone(row)
                        },
                        now=now,
                    )
            ancestors.save()
            exported = export_shards(
                home,
                out,
                include_databases=True,
                agreements=ancestors.agreements(),
                deletions=ancestors.deletions(),
            )
            digest = export_digest(out)
            listed = transport.list_remote(f"machines/{self_id}/")
            newest = registry.seq_of(self_id)
            has_copy = _copy_complete(transport, newest, self_id, codec)
            if published.stands(digest, remote_seq=newest, remote_has_it=has_copy):
                report.unchanged_since = newest
            else:
                _prepare_retry_prefix(transport, out, self_id, newest + 1, codec)
                report.pushed = publish_export(
                    transport,
                    out,
                    registry,
                    outbox,
                    self_id=self_id,
                    manifest_sha=digest or manifest_sha,
                    now=now,
                    reload_registry=lambda: read_registry(transport),
                    codec=codec,
                )
                if not report.pushed.registry_committed:
                    report.ok = False
                    report.failure = "push"
                    report.error = "push: the uploaded copy was not announced in the shared registry"
                    return report
                newest = report.seq_published = report.pushed.seq
                published.record(newest, digest, now=now)
                published.save(sync_root)
                ancestors.forget_old_deletes(now)
                ancestors.save()
            _retire(
                transport,
                listed,
                self_id,
                newest,
                published,
                sync_root,
                outbox,
                now,
                report,
            )
    except Exception as exc:
        logger.warning("sync cycle: push failed (%s)", exc, exc_info=True)
        report.ok = False
        report.failure = "push"
        report.error = f"push: {exc}"
        return report
    held = [
        outcome for outcome in report.pulled.outcomes if outcome.verdict != "consumed"
    ]
    if held:
        report.ok = False
        report.failure = "refused"
        report.error = "; ".join(
            outcome.detail or outcome.verdict for outcome in held[:4]
        )
    return report


def _copy_complete(transport, seq, self_id, codec):
    if seq <= 0:
        return False
    prefix = f"machines/{self_id}/seq-{seq:04d}/"
    refs = transport.list_remote(prefix)
    objects = transport.pull(refs)
    if {ref.key for ref in refs} != {obj.key for obj in objects}:
        return False
    if codec is not None:
        objects, refused = codec.decrypt_after_pull(objects)
        if refused.keys or refused.unreadable:
            return False
    from gideon.operations.durability.pull_engine import _materialize

    try:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            _materialize(objects, prefix, folder)
            import_shards(folder)
    except (OSError, ValueError, TypeError, KeyError):
        return False
    return True


def _prepare_retry_prefix(transport, out, self_id, seq, codec):
    prefix = f"machines/{self_id}/seq-{seq:04d}/"
    refs = transport.list_remote(prefix)
    if not refs:
        return
    if transport.removes_old_copies:
        transport.remove([ref.key for ref in refs])
        if transport.list_remote(prefix):
            raise OSError("unpublished copy could not be cleared before retry")
        return
    objects = transport.pull(refs)
    if codec is not None:
        objects, refused = codec.decrypt_after_pull(objects)
        if refused.keys or refused.unreadable:
            raise OSError("unpublished copy cannot be verified before retry")
    for obj in objects:
        relative = obj.key.removeprefix(prefix)
        path = out / relative
        if not path.is_file():
            raise OSError(
                "transport retains an incompatible unpublished copy; clear that copy before retry"
            )
        expected = path.read_bytes()
        actual = obj.data
        if relative == "manifest.json":
            expected_document, actual_document = json.loads(expected), json.loads(
                actual
            )
            expected_document.pop("generated_at", None)
            actual_document.pop("generated_at", None)
            if expected_document == actual_document:
                continue
        if expected != actual:
            raise OSError(
                "transport retains an incompatible unpublished copy; clear that copy before retry"
            )


def _retire(
    transport, listed, self_id, newest, published, sync_root, outbox, now, report
):
    if not transport.removes_old_copies or newest <= 0:
        return
    pattern = re.compile(rf"^machines/{re.escape(self_id)}/seq-(\d+)/")
    by_seq: dict[int, list] = {}
    for ref in listed:
        match = pattern.match(ref.key)
        if match:
            by_seq.setdefault(int(match.group(1)), []).append(ref.key)
    try:
        for seq in superseded(by_seq, newest=newest, landed=published.landed, now=now):
            keys = by_seq[seq]
            transport.remove(keys)
            if transport.list_remote(f"machines/{self_id}/seq-{seq:04d}/"):
                raise OSError(f"older copy {seq} was not completely removed")
            report.copies_removed.append(seq)
        kept = {newest} | (set(by_seq) - set(report.copies_removed))
        published.keep_only(kept)
        published.save(sync_root)
        outbox.forget_below(transport.name, min(kept))
    except Exception as exc:
        report.removal_failed = str(exc) or type(exc).__name__


def _migrate_legacy_deletes(home, ancestors, registry):
    from gideon.operations.durability.home_paths import guard_path, home_path
    from gideon.operations.durability.tombstones import TOMBSTONE_FILE, read_tombstones

    for entry in inv.export_entries():
        if entry.kind != inv.KIND_JSON_ENTITY_DIR or not entry.tombstones:
            continue
        folder = home_path(home, entry.path)
        log = folder / TOMBSTONE_FILE
        if not log.exists():
            continue
        guard_path(log, read=True)
        if not reconcile._read_proves_absence(entry, folder):
            continue
        rows = read_tombstones(folder)
        live = {
            conflicts_mod.row_id(row)
            for row in reconcile.read_local_rows(entry, folder)
        }
        ancestors.migrate_legacy(entry.id, rows, live, registry.ancestors_for(entry.id))
    ancestors.save()
