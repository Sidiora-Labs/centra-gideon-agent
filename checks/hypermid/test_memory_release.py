from __future__ import annotations

import hashlib
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from gideon.hypermid.client import HypermidClient
from gideon.hypermid.contracts import (
    AccessRequest,
    GrantOperation,
    LineageEdge,
    MaintenanceJobSpec,
    MaintenanceKind,
    MemoryOperation,
    MutationRequest,
    PredicateClause,
    PredicateComparison,
    PredicateField,
    PredicateOperator,
    ProvenanceSpan,
    RecordDraft,
    RecordKind,
    RecordStatus,
    RevisionPrecondition,
    SearchMode,
    SearchRequest,
    SourceKind,
    SourceSnapshot,
    SmartPredicate,
    VerificationState,
)
from gideon.hypermid.foundation import Digest, Id, Scope, Trace
from gideon.hypermid.memory import HypermidMemoryProvider
from gideon.hypermid.memory_client import MemoryClient
from gideon.hypermid.model_budget import ModelBudget

ROOT = Path(__file__).resolve().parents[2]


def _daemon_binary() -> Path:
    configured = os.environ.get("HYPERMID_DAEMON_BINARY")
    if configured:
        binary = Path(configured)
        if binary.is_file() and os.access(binary, os.X_OK):
            return binary.resolve()
        raise AssertionError("HYPERMID_DAEMON_BINARY is not an executable file")
    target = Path(
        os.environ.get(
            "CARGO_TARGET_DIR",
            ROOT / "target",
        )
    )
    binary = target / "debug" / "hypermid-daemon"
    if binary.is_file() and os.access(binary, os.X_OK):
        return binary.resolve()
    raise AssertionError("focused release gate requires the built hypermid-daemon binary")


@dataclass(slots=True)
class LiveDaemon:
    process: subprocess.Popen[str]
    record: Path

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)


def _start_daemon(
    root: Path,
    name: str,
    scope: Scope,
    capability_id: Id,
    resources: set[Id],
) -> LiveDaemon:
    home = root / name
    home.mkdir(mode=0o700)
    socket = home / "hypermid.sock"
    record = home / "connection.json"
    expires_at_ms = int(time.time() * 1000) + 600_000
    arguments = [
        str(_daemon_binary()),
        "--socket",
        str(socket),
        "--connection-record",
        str(record),
        "--local-credential-id",
        "release-credential",
        "--local-owner-id",
        str(scope.owner_id),
        "--local-project-id",
        str(scope.project_id),
        "--local-capability-id",
        str(capability_id),
        "--local-capability-expires-ms",
        str(expires_at_ms),
    ]
    if scope.workspace_id is not None:
        arguments.extend(["--local-workspace-id", str(scope.workspace_id)])
    for operation in (
        "read",
        "append",
        "revise",
        "archive",
        "restore",
        "delete",
        "export",
        "administer",
    ):
        arguments.extend(["--local-capability-operation", operation])
    for resource in sorted(map(str, resources)):
        arguments.extend(["--local-capability-resource", resource])
    process = subprocess.Popen(
        arguments,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 10
    while not record.is_file():
        if process.poll() is not None:
            stderr = process.stderr.read() if process.stderr is not None else ""
            raise AssertionError(
                f"hypermid-daemon exited before readiness ({process.returncode}): {stderr}"
            )
        if time.monotonic() >= deadline:
            process.terminate()
            process.wait(timeout=5)
            raise AssertionError("hypermid-daemon connection record timed out")
        time.sleep(0.01)
    if not (home / "state" / "memory.sqlite3").is_file():
        process.terminate()
        process.wait(timeout=5)
        raise AssertionError("hypermid-daemon did not create its authoritative memory store")
    return LiveDaemon(process, record)


def _scope_digest(scope: Scope) -> Digest:
    hasher = hashlib.sha256(b"hypermid.memory.scope.v1\0")

    def component(value: Id) -> None:
        encoded = str(value).encode()
        hasher.update(len(encoded).to_bytes(8, "big"))
        hasher.update(encoded)

    component(scope.owner_id)
    component(scope.project_id)
    if scope.workspace_id is None:
        hasher.update(b"\0")
    else:
        hasher.update(b"\1")
        component(scope.workspace_id)
    return Digest(hasher.hexdigest())


def _trace(suffix: str) -> Trace:
    return Trace(Id(f"release-trace-{suffix}"), Id(f"release-request-{suffix}"))


def _mutation(
    scope: Scope,
    operation: MemoryOperation,
    resource: Id | None,
    suffix: str,
    revision: RevisionPrecondition | None = None,
) -> MutationRequest:
    return MutationRequest(
        operation=operation,
        actor_scope=scope,
        target_scope=scope,
        record_id=resource,
        category="release",
        revision=revision or RevisionPrecondition.must_not_exist(),
        trace=_trace(suffix),
    )


def _access(
    scope: Scope,
    operation: GrantOperation,
    resource: Id,
    suffix: str,
) -> AccessRequest:
    return AccessRequest(
        operation=operation,
        actor_scope=scope,
        target_scope=scope,
        resource_id=resource,
        category="release",
        trace=_trace(suffix),
    )


def _draft(
    scope: Scope,
    record_id: Id,
    kind: RecordKind,
    content: str,
    **changes: object,
) -> RecordDraft:
    values: dict[str, object] = {
        "id": record_id,
        "scope": scope,
        "kind": kind,
        "category": "release",
        "content": content,
        "importance": 0.75,
        "confidence": 0.8,
        "retention_until_ms": int(time.time() * 1000) + 600_000,
    }
    values.update(changes)
    if kind is RecordKind.SMART_NOTE and "smart_predicate" not in values:
        values["smart_predicate"] = SmartPredicate(
            PredicateOperator.ALL,
            (
                PredicateClause(
                    PredicateField.RECORD_CATEGORY,
                    PredicateComparison.EQ,
                    "release",
                ),
            ),
        )
    return RecordDraft(**values)


async def _open_memory(
    daemon: LiveDaemon, scope: Scope, capability_id: Id
) -> tuple[HypermidClient, MemoryClient]:
    client = HypermidClient(daemon.record, scope=scope)
    await client.connect()
    return client, MemoryClient(client, capability_id=capability_id)


@pytest.mark.asyncio
async def test_memory_release_real_daemon_round_trip(tmp_path: Path) -> None:
    scope = Scope(Id("release-owner"), Id("release-project"), Id("release-workspace"))
    capability_id = Id("release-capability")
    ids = {
        kind: Id(f"release-{kind.value}")
        for kind in RecordKind
    }
    list_id = Id("release-list")
    search_id = Id("release-search")
    diagnostics_id = Id("release-diagnostics")
    export_id = Id("release-export")
    restored_export_id = Id("release-restored-export")
    import_id = Id("release-import")
    job_id = Id("release-job")
    resources = set(ids.values()) | {
        list_id,
        search_id,
        diagnostics_id,
        export_id,
        restored_export_id,
        import_id,
        job_id,
        Id("memory-maintenance"),
        Id("memory-portability"),
        Id("memory-records"),
        Id("memory-service"),
    }

    source_daemon = _start_daemon(
        tmp_path, "source", scope, capability_id, resources
    )
    source_client: HypermidClient | None = None
    try:
        source_client, memory = await _open_memory(source_daemon, scope, capability_id)
        health = await memory.health(trace=_trace("health"))
        assert health.state == "ready" and health.durable and health.lexical_available

        raw_source = "byte-exact source: alpha βeta"
        source = SourceSnapshot(
            source_id=Id("release-source"),
            owner_scope_digest=_scope_digest(scope),
            kind=SourceKind.MESSAGE,
            source_digest=Digest.sha256(raw_source.encode()),
            locator="session-release:message-1",
            captured_content=raw_source,
            capture_method="journal-copy",
            observed_at_ms=1,
        )
        fact_draft = _draft(
            scope,
            ids[RecordKind.FACT],
            RecordKind.FACT,
            "alpha fact",
            provenance=(
                ProvenanceSpan(
                    source.source_id,
                    quoted_digest=source.source_digest,
                ),
            ),
        )
        fact = await memory.create(
            _mutation(scope, MemoryOperation.CREATE, fact_draft.id, "fact-create"),
            fact_draft,
            sources=(source,),
            now_ms=1,
        )
        assert fact.record is not None

        contents = {
            RecordKind.EPISODE: "episode memory",
            RecordKind.NOTE: "note memory",
            RecordKind.SMART_NOTE: "smart note memory",
            RecordKind.ANCHOR: "chronological anchor",
        }
        created = {}
        for kind, content in contents.items():
            item = _draft(scope, ids[kind], kind, content)
            receipt = await memory.create(
                _mutation(scope, MemoryOperation.CREATE, item.id, f"{kind.value}-create"),
                item,
                now_ms=1,
            )
            assert receipt.record is not None
            created[kind] = receipt.record

        summary_draft = _draft(
            scope,
            ids[RecordKind.SUMMARY],
            RecordKind.SUMMARY,
            "summary derived from alpha fact",
            lineage=(
                LineageEdge(
                    fact.record.id,
                    "derived_from",
                    fact.record.current.digest,
                ),
            ),
            summary={
                "input_set_digest": str(Digest.sha256(bytes(fact.record.current.digest, "ascii"))),
                "level": "standard",
                "decay_half_life_ms": 60_000,
            },
        )
        summary = await memory.create(
            _mutation(scope, MemoryOperation.CREATE, summary_draft.id, "summary-create"),
            summary_draft,
            now_ms=1,
        )
        assert summary.record is not None

        episode = created[RecordKind.EPISODE]
        verified = await memory.verify(
            _mutation(
                scope,
                MemoryOperation.VERIFY,
                episode.id,
                "episode-verify",
                RevisionPrecondition.match(str(episode.current.digest)),
            ),
            state=VerificationState.SUPPORTED,
            confidence=0.95,
            evidence_source_id=source.source_id,
            now_ms=1,
        )
        assert verified.record is not None
        assert verified.record.verification is VerificationState.SUPPORTED

        fact_draft = _draft(
            scope,
            ids[RecordKind.FACT],
            RecordKind.FACT,
            "corrected alpha fact",
            provenance=(
                ProvenanceSpan(source.source_id, quoted_digest=source.source_digest),
            ),
        )
        revised = await memory.update(
            _mutation(
                scope,
                MemoryOperation.UPDATE,
                fact.record.id,
                "fact-update",
                RevisionPrecondition.match(str(fact.record.current.digest)),
            ),
            fact_draft,
            sources=(source,),
            now_ms=1,
        )
        assert revised.record is not None and revised.record.current.number == 2
        assert summary.record.id in revised.invalidated_ids

        note = created[RecordKind.NOTE]
        archived = await memory.archive(
            _mutation(
                scope,
                MemoryOperation.ARCHIVE,
                note.id,
                "note-archive",
                RevisionPrecondition.match(str(note.current.digest)),
            ),
            now_ms=1,
        )
        assert archived.record is not None
        restored = await memory.restore(
            _mutation(
                scope,
                MemoryOperation.RESTORE,
                note.id,
                "note-restore",
                RevisionPrecondition.match(str(archived.record.current.digest)),
            ),
            now_ms=1,
        )
        assert restored.record is not None
        deleted = await memory.delete(
            _mutation(
                scope,
                MemoryOperation.DELETE,
                note.id,
                "note-delete",
                RevisionPrecondition.match(str(restored.record.current.digest)),
            ),
            now_ms=1,
        )
        assert deleted.record is not None
        assert deleted.record.status is RecordStatus.TOMBSTONED

        fetched, _ = await memory.get(
            _access(scope, GrantOperation.READ, revised.record.id, "fact-get")
        )
        assert fetched is not None and fetched.current.digest == revised.record.current.digest
        page = await memory.list(
            _access(scope, GrantOperation.READ, list_id, "list"),
            category="release",
        )
        assert len(page.records) == 6
        search_trace = _trace("search")
        search = await memory.search(
            AccessRequest(
                GrantOperation.SEARCH,
                scope,
                scope,
                search_id,
                search_trace,
                "release",
            ),
            SearchRequest(
                query="corrected alpha",
                mode=SearchMode.LEXICAL,
                limit=10,
                trace=search_trace,
                now_ms=1,
                sources=("memory",),
                kinds=("fact",),
                categories=("release",),
            ),
        )
        assert [hit.id for hit in search.hits] == [revised.record.id]

        maintenance_request = MutationRequest(
            operation=MemoryOperation.SUMMARIZE,
            actor_scope=scope,
            target_scope=scope,
            record_id=job_id,
            category=None,
            revision=RevisionPrecondition.must_not_exist(),
            trace=_trace("maintenance-enqueue"),
        )
        spec = MaintenanceJobSpec(
            id=job_id,
            kind=MaintenanceKind.REFRESH_SUMMARIES,
            target_scope=scope,
            actor_scope=scope,
            required_operation=MemoryOperation.SUMMARIZE,
            input_cursor=page.cursor,
            input_digest=Digest.sha256(b"release-input"),
            config_digest=Digest.sha256(b"release-config"),
            budget=ModelBudget(
                max_items=10,
                max_input_tokens=1_000,
                max_output_tokens=1_000,
                max_requests=5,
                max_cost_units=100,
                max_retries=1,
                max_wall_ms=60_000,
            ),
            available_at_ms=0,
            created_at_ms=1,
        )
        await memory.summary_enqueue(maintenance_request, spec)
        claim_request = MutationRequest(
            operation=MemoryOperation.SUMMARIZE,
            actor_scope=scope,
            target_scope=scope,
            record_id=None,
            category=None,
            revision=RevisionPrecondition.must_not_exist(),
            trace=_trace("maintenance-claim"),
        )
        claim = await memory.maintenance_claim(
            claim_request,
            worker_id=Id("release-worker"),
            ttl_ms=5_000,
        )
        assert claim is not None and claim.job_id == job_id
        await memory.maintenance_terminate(
            MutationRequest(
                operation=MemoryOperation.SUMMARIZE,
                actor_scope=scope,
                target_scope=scope,
                record_id=job_id,
                category=None,
                revision=RevisionPrecondition.must_not_exist(),
                trace=_trace("maintenance-terminate"),
            ),
            proof=claim.proof,
            state="abandoned",
            error_code="RELEASE_CONFORMANCE_COMPLETE",
        )

        diagnostics = await memory.diagnostics(
            _access(scope, GrantOperation.READ, diagnostics_id, "diagnostics")
        )
        assert diagnostics.schema_version == 2
        assert diagnostics.record_count == 6
        assert diagnostics.stale_record_count == 1
        assert diagnostics.embedding_count == 0
        assert diagnostics.queued_job_count == 0
        assert diagnostics.active_lease_count == 0
        assert raw_source not in repr(diagnostics)

        bundle = await memory.export_scope(
            _mutation(scope, MemoryOperation.EXPORT, None, "export"),
            export_id=export_id,
        )
        assert bundle.manifest.record_count == 6
        source_entries = [entry for entry in bundle.entries if entry.kind == "source"]
        assert len(source_entries) == 1
        assert source_entries[0].payload["captured_content"] == raw_source
        await source_client.close()
        source_client = None
    finally:
        if source_client is not None:
            await source_client.close()
        source_daemon.stop()

    restored_daemon = _start_daemon(
        tmp_path, "restored", scope, capability_id, resources
    )
    restored_client: HypermidClient | None = None
    try:
        restored_client, restored_memory = await _open_memory(
            restored_daemon, scope, capability_id
        )
        import_request = _mutation(scope, MemoryOperation.IMPORT, None, "import")
        batch = await restored_memory.stage_import(
            import_request,
            batch_id=import_id,
            bundle=bundle,
            target_scope=scope,
            scope_mapping={},
        )
        assert batch.state == "validated" and not batch.replayed
        applied = await restored_memory.apply_import(
            import_request,
            batch=batch,
            bundle=bundle,
        )
        assert applied.state == "applied" and not applied.replayed
        replayed = await restored_memory.apply_import(
            import_request,
            batch=applied,
            bundle=bundle,
        )
        assert replayed.state == "applied" and replayed.replayed

        restored_fact, _ = await restored_memory.get(
            _access(scope, GrantOperation.READ, ids[RecordKind.FACT], "restored-fact")
        )
        assert restored_fact is not None
        assert restored_fact.current.digest == revised.record.current.digest
        restored_page = await restored_memory.list(
            _access(scope, GrantOperation.READ, list_id, "restored-list"),
            category="release",
        )
        assert len(restored_page.records) == 6

        restored_bundle = await restored_memory.export_scope(
            _mutation(
                scope,
                MemoryOperation.EXPORT,
                None,
                "restored-export",
            ),
            export_id=restored_export_id,
        )
        restored_sources = [
            entry for entry in restored_bundle.entries if entry.kind == "source"
        ]
        assert len(restored_sources) == 1
        assert restored_sources[0].payload["captured_content"].encode() == raw_source.encode()

        provider = HypermidMemoryProvider(
            restored_daemon.record,
            scope=scope,
            capability_id=capability_id,
        )
        try:
            provider.init()
            provider_fact = provider.get(str(ids[RecordKind.FACT]))
            assert provider_fact is not None
            assert provider_fact.text == "corrected alpha fact"
        finally:
            provider.close()

        drained = await restored_memory.drain(trace=_trace("drain"))
        assert drained.state == "draining"
        await restored_client.close()
        restored_client = None
    finally:
        if restored_client is not None:
            await restored_client.close()
        restored_daemon.stop()
