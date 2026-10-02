use crate::model::scope_digest;
use crate::recovery::{authoritative_digest, inspect_store};
use crate::{error, Digest, EffectState, MemoryResult, MemoryStore, Scope};
use hypermid_store::backup::{create_sqlite_snapshot_with_transform, SnapshotReceipt};
use rusqlite::Connection;
use std::path::Path;

pub fn create_memory_snapshot(
    store: &MemoryStore,
    artifact_path: impl AsRef<Path>,
    scope: Scope,
) -> MemoryResult<SnapshotReceipt> {
    let cursor = store.cursor(&scope)?;
    let artifact_path = artifact_path.as_ref().to_path_buf();
    store.read(|connection| {
        inspect_store(connection, &scope)?;
        let source_digest =
            authoritative_digest(connection, &scope_digest(&scope).to_hex(), cursor).map_err(
                |message| {
                    error(
                        "SNAPSHOT_INTEGRITY_FAILED",
                        message,
                        EffectState::NotStarted,
                    )
                },
            )?;
        create_sqlite_snapshot_with_transform(
            connection,
            artifact_path,
            scope,
            cursor,
            crate::MEMORY_SCHEMA_VERSION,
            source_digest,
            remove_runtime_authority,
        )
        .map_err(|cause| {
            error(
                "SNAPSHOT_FAILED",
                format!("memory snapshot failed: {cause}"),
                EffectState::NotStarted,
            )
        })
    })
}

fn remove_runtime_authority(connection: &Connection) -> Result<(), String> {
    connection
        .execute_batch(
            "PRAGMA secure_delete=ON;
             DELETE FROM hypermid_capability_operations;
             DELETE FROM hypermid_capability_resources;
             DELETE FROM hypermid_capabilities;
             VACUUM;",
        )
        .map_err(|error| format!("runtime authority could not be excluded: {error}"))?;
    let remaining: u64 = connection
        .query_row(
            "SELECT
                 (SELECT COUNT(*) FROM hypermid_capabilities) +
                 (SELECT COUNT(*) FROM hypermid_capability_operations) +
                 (SELECT COUNT(*) FROM hypermid_capability_resources)",
            [],
            |row| row.get(0),
        )
        .map_err(|error| format!("runtime authority exclusion could not be verified: {error}"))?;
    if remaining != 0 {
        return Err("runtime authority remained in the recovery snapshot".to_owned());
    }
    Ok(())
}

pub fn snapshot_manifest_digest(receipt: &SnapshotReceipt) -> Digest {
    receipt.manifest_digest
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::Id;
    use rusqlite::params;

    #[test]
    fn lifecycle_snapshot_excludes_runtime_authority_bytes_and_rows() {
        let directory = tempfile::tempdir().unwrap();
        let active_path = directory.path().join("memory.sqlite3");
        let mut store = MemoryStore::open(&active_path).unwrap();
        let scope = Scope::new(
            Id::new("owner-snapshot").unwrap(),
            Id::new("project-snapshot").unwrap(),
            None,
        );
        store.ensure_scope(&scope, 1).unwrap();
        let credential = "credential-snapshot-authority-canary";
        store
            .immediate(|transaction| {
                transaction
                    .raw()
                    .execute(
                        "INSERT INTO hypermid_capabilities(
                             capability_id,issuer_owner_id,principal_id,
                             claimed_owner_id,claimed_project_id,claimed_workspace_id,
                             target_owner_id,target_project_id,target_workspace_id,
                             expires_at_ms,revision,revoked)
                         VALUES (?1,?2,?3,?2,?4,NULL,?2,?4,NULL,10000,1,0)",
                        params![
                            "cap-snapshot",
                            "owner-snapshot",
                            credential,
                            "project-snapshot"
                        ],
                    )
                    .unwrap();
                transaction
                    .raw()
                    .execute(
                        "INSERT INTO hypermid_capability_operations(capability_id,operation)
                         VALUES ('cap-snapshot','export')",
                        [],
                    )
                    .unwrap();
                transaction
                    .raw()
                    .execute(
                        "INSERT INTO hypermid_capability_resources(capability_id,resource_id)
                         VALUES ('cap-snapshot','memory-service')",
                        [],
                    )
                    .unwrap();
                Ok(())
            })
            .unwrap();

        let artifact = directory.path().join("snapshot");
        create_memory_snapshot(&store, &artifact, scope).unwrap();
        let image_path = artifact.join("store.sqlite3");
        let image = std::fs::read(&image_path).unwrap();
        assert!(!image
            .windows(credential.len())
            .any(|window| window == credential.as_bytes()));

        let snapshot = Connection::open(image_path).unwrap();
        for table in [
            "hypermid_capabilities",
            "hypermid_capability_operations",
            "hypermid_capability_resources",
        ] {
            let count: u64 = snapshot
                .query_row(&format!("SELECT COUNT(*) FROM {table}"), [], |row| {
                    row.get(0)
                })
                .unwrap();
            assert_eq!(count, 0, "{table} retained runtime authority");
        }
        let integrity: String = snapshot
            .query_row("PRAGMA integrity_check", [], |row| row.get(0))
            .unwrap();
        assert_eq!(integrity, "ok");
    }
}
