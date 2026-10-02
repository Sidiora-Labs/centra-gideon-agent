use crate::model::scope_digest;
use crate::recovery::{authoritative_digest, inspect_store};
use crate::{error, Digest, EffectState, MemoryResult, MemoryStore, Scope};
use hypermid_store::backup::{create_sqlite_snapshot, SnapshotReceipt};
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
        create_sqlite_snapshot(
            connection,
            artifact_path,
            scope,
            cursor,
            crate::MEMORY_SCHEMA_VERSION,
            source_digest,
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

pub fn snapshot_manifest_digest(receipt: &SnapshotReceipt) -> Digest {
    receipt.manifest_digest
}
