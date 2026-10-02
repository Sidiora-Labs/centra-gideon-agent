from __future__ import annotations

import pytest

from gideon.hypermid.foundation import Cursor, Digest, Id, Scope
from gideon.hypermid.history import RawSourceJournal
from gideon.hypermid.portability import (
    ContextAuthority,
    ContextExportBundle,
    ContextExportBuilder,
    ContextImportStore,
    ContextMigrationCoordinator,
    ContextPortabilityEntryKind,
    ContextPortabilityError,
    ContextPortableRecord,
    ContextSessionBinding,
)


def _scope() -> Scope:
    return Scope(Id("owner-1"), Id("project-1"), Id("workspace-1"))


def _bundle() -> ContextExportBundle:
    scope = _scope()
    source_digest = Digest.sha256(b"authoritative source")
    builder = ContextExportBuilder(
        Id("manifest-1"),
        ContextSessionBinding(scope, Id("session-1"), Cursor(4, 2)),
        "2026-10-02T10:00:00Z",
    )
    builder.add(
        Id("source-entry-1"),
        ContextPortabilityEntryKind.SOURCE_REFERENCE,
        {
            "scope": scope.to_wire(),
            "session_id": "session-1",
            "item_id": "item-1",
            "source_event_id": "gideon-event-1",
            "source_digest": str(source_digest),
            "cursor": Cursor(4, 1).to_wire(),
        },
    )
    builder.add(
        Id("summary-entry-1"),
        ContextPortabilityEntryKind.SUMMARY,
        {
            "scope": scope.to_wire(),
            "session_id": "session-1",
            "summary_id": "summary-1",
            "source_start": Cursor(4, 1).to_wire(),
            "source_end": Cursor(4, 1).to_wire(),
        },
    )
    return builder.build()


def test_staged_import_is_invisible_until_every_digest_commits(tmp_path) -> None:
    bundle = _bundle()
    sources = RawSourceJournal(tmp_path / "sources.sqlite3")
    sources.append(
        _scope(),
        Id("session-1"),
        Id("gideon-event-1"),
        b"authoritative source",
    )
    imports = ContextImportStore()
    imports.stage(Id("stage-1"), _scope(), bundle)
    assert imports.visible(_scope(), Id("session-1")) is None

    receipt = imports.commit(Id("stage-1"))
    assert receipt.committed is True
    restored = imports.restore(_scope(), Id("session-1"), sources)
    assert len(restored.source_references) == 1
    assert len(restored.derived_records) == 1

    imports.stage(Id("stage-replay"), _scope(), bundle)
    assert imports.commit(Id("stage-replay")).bundle_digest == receipt.bundle_digest


def test_import_rejects_tampering_and_scope_changes() -> None:
    bundle = _bundle()
    first = bundle.records[0]
    tampered_record = ContextPortableRecord(
        first.entry_id,
        first.kind,
        {**first.payload, "source_event_id": "changed-event"},
    )
    tampered = ContextExportBundle(
        bundle.binding,
        bundle.manifest,
        (tampered_record, *bundle.records[1:]),
    )
    imports = ContextImportStore()
    with pytest.raises(ContextPortabilityError) as digest_error:
        imports.stage(Id("stage-1"), _scope(), tampered)
    assert digest_error.value.code == "DIGEST_MISMATCH"

    with pytest.raises(ContextPortabilityError) as scope_error:
        imports.stage(
            Id("stage-2"),
            Scope(Id("owner-1"), Id("another-project")),
            bundle,
        )
    assert scope_error.value.code == "SCOPE_MISMATCH"


def test_export_rejects_credentials_leases_queues_and_sdk_payloads() -> None:
    for forbidden in (
        "credentials",
        "writer_lease",
        "transient_queue",
        "sdk_payload",
    ):
        builder = ContextExportBuilder(
            Id("manifest-1"),
            ContextSessionBinding(_scope(), Id("session-1"), Cursor(1, 0)),
            "2026-10-02T10:00:00Z",
        )
        with pytest.raises(ContextPortabilityError) as rejected:
            builder.add(
                Id("entry-1"),
                ContextPortabilityEntryKind.POLICY_REVISION,
                {
                    "scope": _scope().to_wire(),
                    "session_id": "session-1",
                    forbidden: {"secret": "must-not-export"},
                },
            )
        assert rejected.value.code == "FORBIDDEN_STATE"


def test_migration_resume_is_idempotent_and_rejects_duplicate_sources() -> None:
    migration = ContextMigrationCoordinator()
    start = Cursor(7, 0)
    migration.begin(Id("migration-1"), _scope(), Id("session-1"), start)
    checkpoint = migration.commit_batch(
        Id("migration-1"),
        Id("batch-1"),
        start,
        Cursor(7, 2),
        (Id("source-1"), Id("source-2")),
    )
    assert checkpoint.committed_cursor == Cursor(7, 2)
    assert (
        migration.commit_batch(
            Id("migration-1"),
            Id("batch-1"),
            start,
            Cursor(7, 2),
            (Id("source-1"), Id("source-2")),
        )
        == checkpoint
    )
    with pytest.raises(ContextPortabilityError) as duplicate:
        migration.commit_batch(
            Id("migration-1"),
            Id("batch-2"),
            checkpoint.committed_cursor,
            Cursor(7, 3),
            (Id("source-2"),),
        )
    assert duplicate.value.code == "DUPLICATE_SOURCE_IDENTITY"


def test_cutover_failure_keeps_previous_authority_and_requires_quiescence() -> None:
    migration = ContextMigrationCoordinator()
    migration.begin(
        Id("migration-1"), _scope(), Id("session-1"), Cursor(2, 0)
    )
    with pytest.raises(ContextPortabilityError) as failed:
        migration.cutover(
            Id("migration-1"), quiescent=True, validation_passed=False
        )
    assert failed.value.code == "VALIDATION_FAILED"
    assert (
        migration.checkpoint(Id("migration-1")).authority
        is ContextAuthority.PREVIOUS
    )

    with pytest.raises(ContextPortabilityError) as active_turn:
        migration.cutover(
            Id("migration-1"), quiescent=False, validation_passed=True
        )
    assert active_turn.value.code == "NOT_QUIESCENT"
    assert (
        migration.cutover(
            Id("migration-1"), quiescent=True, validation_passed=True
        ).authority
        is ContextAuthority.HYPERMID
    )
    assert (
        migration.rollback(Id("migration-1"), quiescent=True).authority
        is ContextAuthority.PREVIOUS
    )
