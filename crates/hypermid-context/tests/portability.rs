use hypermid_context::export::{ContextExportBuilder, ContextSessionBinding, PortabilityEntryKind};
use hypermid_context::import::{ContextImportError, ContextImportStore};
use hypermid_context::journal::RawSourceJournal;
use hypermid_context::migration::{
    ContextAuthority, ContextMigrationCoordinator, ContextMigrationError,
};
use hypermid_contracts::{Cursor, Digest, Id, Scope};
use serde_json::json;

fn id(value: &str) -> Id {
    Id::new(value).unwrap()
}

fn scope() -> Scope {
    Scope::new(id("owner-1"), id("project-1"), Some(id("workspace-1")))
}

fn export_bundle() -> hypermid_context::export::ContextExportBundle {
    let bound_scope = scope();
    let source_digest = Digest::sha256("authoritative source");
    let binding = ContextSessionBinding {
        scope: bound_scope.clone(),
        session_id: id("session-1"),
        cursor: Cursor::new(4, 2).unwrap(),
    };
    let mut builder =
        ContextExportBuilder::new(id("manifest-1"), binding, "2026-10-02T10:00:00Z").unwrap();
    builder
        .push(
            id("source-entry-1"),
            PortabilityEntryKind::SourceReference,
            json!({
                "scope": bound_scope,
                "session_id": "session-1",
                "item_id": "item-1",
                "source_event_id": "gideon-event-1",
                "source_digest": source_digest,
                "cursor": {"epoch": 4, "sequence": 1}
            }),
        )
        .unwrap();
    builder
        .push(
            id("summary-entry-1"),
            PortabilityEntryKind::Summary,
            json!({
                "scope": scope(),
                "session_id": "session-1",
                "summary_id": "summary-1",
                "source_start": {"epoch": 4, "sequence": 1},
                "source_end": {"epoch": 4, "sequence": 1}
            }),
        )
        .unwrap();
    builder.build().unwrap()
}

#[test]
fn staged_import_is_digest_checked_scope_bound_and_invisible_until_commit() {
    let bundle = export_bundle();
    let source_root = tempfile::tempdir().unwrap();
    let sources = RawSourceJournal::open(source_root.path()).unwrap();
    sources
        .append(
            scope(),
            id("session-1"),
            id("gideon-event-1"),
            b"authoritative source".to_vec(),
        )
        .unwrap();
    let mut imports = ContextImportStore::new();
    imports
        .stage(id("stage-1"), &scope(), bundle.clone())
        .unwrap();
    assert!(imports.visible(&scope(), &id("session-1")).is_none());

    let receipt = imports.commit(&id("stage-1")).unwrap();
    assert!(receipt.committed);
    let restored = imports
        .restore(&scope(), &id("session-1"), &sources)
        .unwrap();
    assert_eq!(restored.source_references.len(), 1);
    assert_eq!(restored.derived_records.len(), 1);

    imports.stage(id("stage-replay"), &scope(), bundle).unwrap();
    assert_eq!(
        imports.commit(&id("stage-replay")).unwrap().bundle_digest,
        receipt.bundle_digest
    );
}

#[test]
fn import_rejects_tampering_and_scope_relocation() {
    let mut tampered = export_bundle();
    tampered.records[0].payload["source_event_id"] = json!("changed-event");
    let mut imports = ContextImportStore::new();
    assert!(matches!(
        imports.stage(id("stage-1"), &scope(), tampered),
        Err(ContextImportError::DigestMismatch)
    ));

    let other_scope = Scope::new(id("owner-1"), id("project-2"), None);
    assert!(matches!(
        imports.stage(id("stage-2"), &other_scope, export_bundle()),
        Err(ContextImportError::ScopeMismatch)
    ));
}

#[test]
fn export_rejects_credentials_leases_transient_queues_and_sdk_payloads() {
    let bound_scope = scope();
    for forbidden in [
        "credentials",
        "writer_lease",
        "transient_queue",
        "sdk_payload",
    ] {
        let binding = ContextSessionBinding {
            scope: bound_scope.clone(),
            session_id: id("session-1"),
            cursor: Cursor::new(1, 0).unwrap(),
        };
        let mut builder =
            ContextExportBuilder::new(id("manifest-1"), binding, "2026-10-02T10:00:00Z").unwrap();
        let result = builder.push(
            id("entry-1"),
            PortabilityEntryKind::PolicyRevision,
            json!({
                "scope": bound_scope.clone(),
                "session_id": "session-1",
                (forbidden): {"secret": "must-not-export"}
            }),
        );
        assert!(result.is_err());
    }
}

#[test]
fn migration_resumes_from_committed_cursor_without_duplicate_sources() {
    let mut migration = ContextMigrationCoordinator::new();
    let start = Cursor::new(7, 0).unwrap();
    migration
        .begin(id("migration-1"), scope(), id("session-1"), start)
        .unwrap();
    let checkpoint = migration
        .commit_batch(
            &id("migration-1"),
            id("batch-1"),
            start,
            Cursor::new(7, 2).unwrap(),
            vec![id("source-1"), id("source-2")],
        )
        .unwrap();
    assert_eq!(checkpoint.committed_cursor, Cursor::new(7, 2).unwrap());
    assert_eq!(
        migration
            .commit_batch(
                &id("migration-1"),
                id("batch-1"),
                start,
                Cursor::new(7, 2).unwrap(),
                vec![id("source-1"), id("source-2")],
            )
            .unwrap(),
        checkpoint
    );
    assert_eq!(
        migration.commit_batch(
            &id("migration-1"),
            id("batch-2"),
            checkpoint.committed_cursor,
            Cursor::new(7, 3).unwrap(),
            vec![id("source-2")],
        ),
        Err(ContextMigrationError::DuplicateSourceIdentity)
    );
}

#[test]
fn failed_or_non_quiescent_cutover_retains_previous_authority() {
    let mut migration = ContextMigrationCoordinator::new();
    migration
        .begin(
            id("migration-1"),
            scope(),
            id("session-1"),
            Cursor::new(2, 0).unwrap(),
        )
        .unwrap();
    assert_eq!(
        migration.cutover(&id("migration-1"), true, false),
        Err(ContextMigrationError::ValidationFailed)
    );
    assert_eq!(
        migration.checkpoint(&id("migration-1")).unwrap().authority,
        ContextAuthority::Previous
    );
    assert_eq!(
        migration.cutover(&id("migration-1"), false, true),
        Err(ContextMigrationError::NotQuiescent)
    );
    assert_eq!(
        migration
            .cutover(&id("migration-1"), true, true)
            .unwrap()
            .authority,
        ContextAuthority::Hypermid
    );
    assert_eq!(
        migration
            .rollback(&id("migration-1"), true)
            .unwrap()
            .authority,
        ContextAuthority::Previous
    );
}
