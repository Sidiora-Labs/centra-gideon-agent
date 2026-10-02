use crate::backup::{
    file_digest, recorded_schema_version, verify_integrity, LifecycleError, SnapshotEntry,
    SnapshotManifest, StagedRestore,
};
use hypermid_contracts::{Cursor, Digest, Scope};
use rusqlite::{backup::Backup, Connection, Transaction, TransactionBehavior};
use std::path::Path;
use std::time::Duration;

pub fn stage_sqlite_migration(
    active_path: impl AsRef<Path>,
    source: &Connection,
    scope: Scope,
    cursor: Cursor,
    source_digest: Digest,
    target_schema: u64,
    migrate: impl FnOnce(&Transaction<'_>) -> Result<(), String>,
    validate: impl FnOnce(&Connection, &SnapshotManifest) -> Result<(), String>,
) -> Result<StagedRestore, LifecycleError> {
    let active_path = active_path.as_ref().to_path_buf();
    let parent = active_path
        .parent()
        .ok_or(LifecycleError::InvalidManifest)?;
    let staged = tempfile::Builder::new()
        .prefix(".hypermid-migrate-")
        .tempfile_in(parent)?;
    let mut destination = Connection::open(staged.path())?;
    {
        let backup = Backup::new(source, &mut destination)?;
        backup.run_to_completion(64, Duration::from_millis(5), None)?;
    }
    {
        let transaction = destination.transaction_with_behavior(TransactionBehavior::Immediate)?;
        if let Err(error) = migrate(&transaction) {
            return Err(LifecycleError::DomainValidation(error));
        }
        transaction.commit()?;
    }
    verify_integrity(&destination)?;
    if recorded_schema_version(&destination)? != target_schema {
        return Err(LifecycleError::SchemaMismatch);
    }
    drop(destination);
    staged.as_file().sync_all()?;
    let bytes = staged.as_file().metadata()?.len();
    let digest = file_digest(staged.path())?;
    let manifest = SnapshotManifest {
        format_version: 1,
        schema_version: target_schema,
        scope,
        cursor,
        source_digest,
        entries: vec![SnapshotEntry {
            path: "store.sqlite3".to_owned(),
            bytes,
            digest,
        }],
    };
    let validation_connection = Connection::open(staged.path())?;
    validate(&validation_connection, &manifest).map_err(LifecycleError::DomainValidation)?;
    drop(validation_connection);
    StagedRestore::from_staged(active_path, staged, manifest)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::backup::{create_sqlite_snapshot, stage_restore};
    use hypermid_contracts::Id;
    use std::fs;

    fn scope() -> Scope {
        Scope::new(Id::new("owner").unwrap(), Id::new("project").unwrap(), None)
    }

    fn cursor() -> Cursor {
        Cursor::new(1, 7).unwrap()
    }

    fn create_store(path: &Path, value: &str) -> Connection {
        let connection = Connection::open(path).unwrap();
        connection
            .execute_batch(&format!(
                "CREATE TABLE hypermid_migrations(version INTEGER PRIMARY KEY, name TEXT, digest TEXT);\
                 INSERT INTO hypermid_migrations VALUES (1, 'initial', 'digest');\
                 CREATE TABLE records(value TEXT NOT NULL);\
                 INSERT INTO records VALUES ('{value}');"
            ))
            .unwrap();
        connection
    }

    #[test]
    fn lifecycle_invalid_or_interrupted_restore_preserves_active_bytes() {
        let directory = tempfile::tempdir().unwrap();
        let source_path = directory.path().join("source.sqlite3");
        let source = create_store(&source_path, "snapshot");
        let artifact = directory.path().join("artifact");
        let snapshot = create_sqlite_snapshot(
            &source,
            &artifact,
            scope(),
            cursor(),
            1,
            Digest::sha256(b"source"),
        )
        .unwrap();
        drop(source);

        let active_path = directory.path().join("active.sqlite3");
        drop(create_store(&active_path, "active"));
        let before = fs::read(&active_path).unwrap();
        assert!(matches!(
            stage_restore(
                &active_path,
                &artifact,
                Digest::sha256(b"wrong"),
                &scope(),
                1,
                |_, _| Ok(())
            ),
            Err(LifecycleError::ManifestDigestMismatch)
        ));
        assert_eq!(fs::read(&active_path).unwrap(), before);

        let staged = stage_restore(
            &active_path,
            &artifact,
            snapshot.manifest_digest,
            &scope(),
            1,
            |connection, _| {
                let value: String = connection
                    .query_row("SELECT value FROM records", [], |row| row.get(0))
                    .map_err(|error| error.to_string())?;
                (value == "snapshot")
                    .then_some(())
                    .ok_or_else(|| "wrong snapshot".to_owned())
            },
        )
        .unwrap();
        drop(staged);
        assert_eq!(fs::read(&active_path).unwrap(), before);
    }

    #[test]
    fn lifecycle_valid_restore_atomically_replaces_the_expected_store() {
        let directory = tempfile::tempdir().unwrap();
        let source_path = directory.path().join("source.sqlite3");
        let source = create_store(&source_path, "snapshot");
        let artifact = directory.path().join("artifact");
        let snapshot = create_sqlite_snapshot(
            &source,
            &artifact,
            scope(),
            cursor(),
            1,
            Digest::sha256(b"source"),
        )
        .unwrap();
        drop(source);

        let active_path = directory.path().join("active.sqlite3");
        drop(create_store(&active_path, "active"));
        let expected = file_digest(&active_path).unwrap();
        let staged = stage_restore(
            &active_path,
            &artifact,
            snapshot.manifest_digest,
            &scope(),
            1,
            |_, _| Ok(()),
        )
        .unwrap();
        let receipt = staged.activate(Some(expected)).unwrap();
        assert_eq!(receipt.cursor, cursor());
        assert_eq!(receipt.active_digest, snapshot.image_digest);
        let restored = Connection::open(active_path).unwrap();
        let value: String = restored
            .query_row("SELECT value FROM records", [], |row| row.get(0))
            .unwrap();
        assert_eq!(value, "snapshot");
    }

    #[test]
    fn lifecycle_migration_failure_never_changes_the_active_store() {
        let directory = tempfile::tempdir().unwrap();
        let active_path = directory.path().join("active.sqlite3");
        let source = create_store(&active_path, "active");
        let before = fs::read(&active_path).unwrap();
        let result = stage_sqlite_migration(
            &active_path,
            &source,
            scope(),
            cursor(),
            Digest::sha256(b"source"),
            2,
            |transaction| {
                transaction
                    .execute("CREATE TABLE partial(value TEXT)", [])
                    .map_err(|error| error.to_string())?;
                Err("migration refused".to_owned())
            },
            |_, _| Ok(()),
        );
        assert!(matches!(result, Err(LifecycleError::DomainValidation(_))));
        assert_eq!(fs::read(&active_path).unwrap(), before);
    }
}
