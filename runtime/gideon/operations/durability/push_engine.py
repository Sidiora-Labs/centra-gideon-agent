"""The transport-driven push half of the sync cycle (DURABILITY-AND-SYNC §4.1, DAS-6c-ii-f).

The mirror of the pull engine. Given a fresh local shard export (from ``shards.export_shards``),
it publishes that export as this machine's next seq and announces it in the shared registry:

    seq       = registry.bump(self_id, manifest_sha=…, now=…)   # 6c-ii-a — monotonic
    objects   = every file in the export dir, keyed machines/<self_id>/seq-NNNN/<rel>
    outbox.enqueue(target, seq, …)                              # 6c-ii-b — durable obligation
    drain: transport.push(objects) → outbox.record_outcome(...) # never-drop outcomes
    CAS:   transport.cas_registry(expected_sha, registry.to_bytes())
    on a lost race → re-pull the registry and retry without changing the published seq

Every step composes a piece already shipped and tested in isolation; this module owns only the
orchestration and the CAS-retry loop. Insert-only object keys (seq-numbered, never rewritten)
make a retried push a no-op, so a CAS race costs a re-pull, never a double-write or a lost push.

Clock-free: the timestamp is passed in (``now``), like the registry and outbox models.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from gideon.integrations.sync_transports.base import SyncObject, SyncTransportProvider
from gideon.operations.durability.outbox import (
    OUTCOME_PERMANENT,
    OUTCOME_TRANSIENT,
    Outbox,
)
from gideon.operations.durability.registry import REGISTRY_KEY, Registry, shard_prefix

logger = logging.getLogger(__name__)

_MAX_CAS_ATTEMPTS = 5


@dataclass
class PushReport:
    """What one publish did."""

    seq: int = 0
    objects: int = 0
    pushed: int = 0
    push_outcome: str = ""
    registry_committed: bool = False
    cas_attempts: int = 0
    detail: str = ""


def _objects_for(export_dir: Path, prefix: str) -> list[SyncObject]:
    """Every file under ``export_dir`` as a :class:`SyncObject` keyed by ``prefix`` + its
    export-relative path — the exact inverse of the pull engine's ``_materialize``."""
    objs: list[SyncObject] = []
    for path in sorted(export_dir.rglob("*")):
        if path.is_file() and not path.is_symlink():
            rel = path.relative_to(export_dir).as_posix()
            objs.append(SyncObject(key=prefix + rel, data=path.read_bytes()))
    return objs


def publish_export(
    transport: SyncTransportProvider,
    export_dir: Path,
    registry: Registry,
    outbox: Outbox,
    *,
    self_id: str,
    manifest_sha: str,
    now: str = "",
    reload_registry=None,
    codec=None,
) -> PushReport:
    """Publish ``export_dir`` as this machine's next seq and announce it via a CAS registry bump.

    ``reload_registry`` is an optional ``() -> Registry`` the CAS loop calls to re-pull the
    shared registry after a lost race (the cycle passes one that reads + parses the remote
    ``registry.json``); without it a CAS failure ends the attempt (single-writer/test path).
    ``codec`` is an optional :class:`~gideon.operations.durability.crypto.SyncCodec` (DAS-8): when
    present, every non-routing object is AES-256-GCM encrypted here — the LAST step before the
    transport, so no unencrypted shard byte can reach an untrusted store.
    Returns a :class:`PushReport`. The push obligation is recorded in the durable outbox first,
    so a crash between push and registry-commit leaves a pending entry the next cycle re-drains
    (the object keys are insert-only, so that re-drain is a no-op).
    """
    report = PushReport()
    seq = registry.bump(self_id, manifest_sha=manifest_sha, now=now)
    report.seq = seq
    prefix = shard_prefix(self_id, seq)
    objects = _objects_for(export_dir, prefix)
    report.objects = len(objects)

    if codec is not None:
        objects, refused = codec.encrypt_for_push(objects)
        if refused:
            entry = outbox.enqueue(
                transport.name, seq, prefix=prefix, local_dir=str(export_dir), now=now
            )
            outbox.record_outcome(
                entry.id,
                OUTCOME_PERMANENT,
                now=now,
                detail="; ".join(refused.reasons[:5]),
            )
            report.push_outcome = OUTCOME_PERMANENT
            report.detail = f"encryption refused {len(refused)} plaintext object(s)"
            return report

    entry = outbox.enqueue(
        transport.name, seq, prefix=prefix, local_dir=str(export_dir), now=now
    )

    push = transport.push(objects)
    report.pushed = push.pushed
    report.push_outcome = push.outcome
    outbox.record_outcome(entry.id, push.outcome, now=now, detail=push.detail)
    if push.outcome in (OUTCOME_TRANSIENT, OUTCOME_PERMANENT):
        report.detail = f"push {push.outcome}: {push.detail}"
        return report

    report.registry_committed = _commit_registry(
        transport, registry, self_id, manifest_sha, now, reload_registry, report
    )
    return report


def _commit_registry(
    transport, registry, self_id, manifest_sha, now, reload_registry, report
) -> bool:
    """CAS-update the shared registry without announcing an uncertain shard sequence twice."""
    expected: str | None = None
    create_retry_used = False
    while report.cas_attempts < _MAX_CAS_ATTEMPTS:
        report.cas_attempts += 1
        if transport.cas_registry(expected, registry.to_bytes()):
            return True
        if reload_registry is None:
            report.detail = "registry CAS lost and no reloader provided"
            return False
        try:
            remote = reload_registry()
        except FileNotFoundError as exc:
            report.detail = f"registry read-back unavailable; refusing retry: {exc}"
            return False

        # A transport may lose the acknowledgement after the conditional write landed.
        # Read-back is authoritative: recognize our exact announcement and never push it
        # again. If this machine advanced past our seq, its shard ownership is ambiguous;
        # stop rather than risk announcing a second shard set.
        remote_self = remote.machines.get(self_id)
        if remote_self is not None and remote_self.seq >= report.seq:
            if remote_self.seq == report.seq and remote_self.manifest_sha == manifest_sha:
                return True
            report.detail = "registry read-back advanced this machine; refusing a duplicate shard announcement"
            return False

        # Some providers can reject an update-style write against a registry that is still
        # absent. Only a read-back with no registry state proves this create is safe to
        # retry, and it gets exactly one retry with the absent-object precondition.
        if not remote.machines and not remote.ancestors:
            if create_retry_used:
                report.detail = "registry create retry remained unlanded"
                return False
            create_retry_used = True
            expected = None
            continue

        merged = Registry.loads(remote.to_bytes())
        # The shard objects already use report.seq. Preserve that exact sequence while
        # incorporating the latest peers; bumping from a stale remote could announce a
        # different prefix and make the published shard set undiscoverable.
        local_self = registry.machines.get(self_id)
        if local_self is None or local_self.seq != report.seq:
            report.detail = "local registry sequence no longer matches the published shard set"
            return False
        merged.machines[self_id] = local_self
        for mid, e in registry.machines.items():
            if mid != self_id and e.seq > merged.seq_of(mid):
                merged.machines[mid] = e
        for entry_id, rows in registry.ancestors.items():
            merged.record_ancestors(entry_id, rows)
        registry.machines = merged.machines
        registry.ancestors = merged.ancestors
        expected = remote.sha()
    report.detail = f"registry CAS lost after {report.cas_attempts} attempts"
    return False


__all__ = ["PushReport", "publish_export", "REGISTRY_KEY"]
