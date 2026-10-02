use crate::embedding::{registration_fingerprint, EmbeddingMode};
use crate::fts::index_memory_record;
use crate::migrations::{schema_digest, MEMORY_COMPATIBILITY_FLOOR, MEMORY_SCHEMA_VERSION};
use crate::model::scope_digest;
use crate::{error, Cursor, Digest, EffectState, MemoryResult, MemoryStore, Scope};
use hypermid_store::backup::{stage_restore, SnapshotManifest, StagedRestore};
use hypermid_store::migrate::stage_sqlite_migration;
use rusqlite::{params, Connection, Transaction};
use serde::{Deserialize, Serialize};
use sha2::{Digest as _, Sha256};
use std::path::Path;
use std::str::FromStr;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct MemoryRecoveryReport {
    pub schema_version: u64,
    pub compatibility_floor: u64,
    pub schema_digest: Digest,
    pub scope: Scope,
    pub cursor: Cursor,
    pub authoritative_digest: Digest,
    pub record_count: u64,
    pub revision_count: u64,
    pub source_count: u64,
    pub lineage_count: u64,
    pub memory_fts_count: u64,
    pub source_fts_count: u64,
    pub embedding_count: u64,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct DerivativeRebuildReceipt {
    pub memory_fts_rows: u64,
    pub source_fts_rows: u64,
    pub cleared_embeddings: u64,
}

pub fn inspect_store(connection: &Connection, scope: &Scope) -> MemoryResult<MemoryRecoveryReport> {
    validate_store(connection, scope).map_err(domain_error)
}

pub fn stage_repaired_copy(
    store: &MemoryStore,
    active_path: impl AsRef<Path>,
    scope: Scope,
) -> MemoryResult<StagedRestore> {
    let cursor = store.cursor(&scope)?;
    store.read(|connection| {
        let before = validate_store_mode(connection, &scope, false).map_err(domain_error)?;
        stage_sqlite_migration(
            active_path,
            connection,
            scope.clone(),
            cursor,
            before.authoritative_digest,
            MEMORY_SCHEMA_VERSION,
            |transaction| rebuild_derivatives(transaction, &scope).map(|_| ()),
            |staged, manifest| validate_manifest_store(staged, manifest, &scope),
        )
        .map_err(lifecycle_error)
    })
}

pub fn stage_verified_restore(
    active_path: impl AsRef<Path>,
    artifact_path: impl AsRef<Path>,
    expected_manifest_digest: Digest,
    expected_scope: &Scope,
) -> MemoryResult<StagedRestore> {
    stage_restore(
        active_path,
        artifact_path,
        expected_manifest_digest,
        expected_scope,
        MEMORY_SCHEMA_VERSION,
        |connection, manifest| validate_manifest_store(connection, manifest, expected_scope),
    )
    .map_err(lifecycle_error)
}

pub fn stage_repaired_restore(
    active_path: impl AsRef<Path>,
    artifact_path: impl AsRef<Path>,
    expected_manifest_digest: Digest,
    expected_scope: &Scope,
) -> MemoryResult<StagedRestore> {
    let active_path = active_path.as_ref().to_path_buf();
    let verified = stage_restore(
        &active_path,
        artifact_path,
        expected_manifest_digest,
        expected_scope,
        MEMORY_SCHEMA_VERSION,
        |connection, manifest| {
            let report = validate_store_mode(connection, expected_scope, false)?;
            if report.schema_version != manifest.schema_version
                || report.cursor != manifest.cursor
                || report.authoritative_digest != manifest.source_digest
            {
                return Err(
                    "snapshot manifest does not bind the authoritative memory state".to_owned(),
                );
            }
            Ok(())
        },
    )
    .map_err(lifecycle_error)?;
    let manifest = verified.manifest().clone();
    let source = Connection::open(verified.staging_path()).map_err(|cause| {
        crate::error(
            "RECOVERY_FAILED",
            format!("verified recovery copy could not be opened: {cause}"),
            EffectState::NotStarted,
        )
    })?;
    let repaired = stage_sqlite_migration(
        &active_path,
        &source,
        expected_scope.clone(),
        manifest.cursor,
        manifest.source_digest,
        MEMORY_SCHEMA_VERSION,
        |transaction| rebuild_derivatives(transaction, expected_scope).map(|_| ()),
        |connection, repaired_manifest| {
            validate_manifest_store(connection, repaired_manifest, expected_scope)
        },
    )
    .map_err(lifecycle_error)?;
    drop(source);
    drop(verified);
    Ok(repaired)
}

pub fn validate_store(
    connection: &Connection,
    scope: &Scope,
) -> Result<MemoryRecoveryReport, String> {
    validate_store_mode(connection, scope, true)
}

fn validate_store_mode(
    connection: &Connection,
    scope: &Scope,
    require_derivatives: bool,
) -> Result<MemoryRecoveryReport, String> {
    let (current_version, compatibility_floor, stored_schema_digest): (u64, u64, String) =
        connection
            .query_row(
                "SELECT current_version,compatibility_floor,schema_digest
                 FROM hypermid_schema_version WHERE singleton=1",
                [],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
            )
            .map_err(sql)?;
    if current_version != MEMORY_SCHEMA_VERSION
        || compatibility_floor != MEMORY_COMPATIBILITY_FLOOR
        || stored_schema_digest != schema_digest().to_hex()
    {
        return Err("memory schema fence does not match this runtime".to_owned());
    }
    let owner = scope_digest(scope).to_hex();
    let (scope_json, epoch, sequence): (String, u64, u64) = connection
        .query_row(
            "SELECT scope_json,epoch,sequence FROM memory_scopes WHERE scope_digest=?1",
            [&owner],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
        )
        .map_err(sql)?;
    let stored_scope: Scope = serde_json::from_str(&scope_json)
        .map_err(|_| "memory scope document is invalid".to_owned())?;
    if stored_scope != *scope || scope_digest(&stored_scope).to_hex() != owner {
        return Err("memory scope digest does not match its document".to_owned());
    }
    let cursor =
        Cursor::new(epoch, sequence).map_err(|_| "memory scope cursor is invalid".to_owned())?;
    let (record_count, revision_count) = validate_revisions(connection, &owner)?;
    let source_count = validate_sources(connection, &owner)?;
    validate_provenance(connection, &owner)?;
    let lineage_count = validate_lineage(connection, &owner)?;
    validate_mutation_cursor(connection, &owner, cursor)?;
    let (memory_fts_count, source_fts_count, embedding_count) = if require_derivatives {
        validate_derivatives(connection, &owner)?
    } else {
        derivative_counts(connection, &owner)?
    };
    let authoritative_digest = authoritative_digest(connection, &owner, cursor)?;
    Ok(MemoryRecoveryReport {
        schema_version: current_version,
        compatibility_floor,
        schema_digest: schema_digest(),
        scope: scope.clone(),
        cursor,
        authoritative_digest,
        record_count,
        revision_count,
        source_count,
        lineage_count,
        memory_fts_count,
        source_fts_count,
        embedding_count,
    })
}

fn validate_manifest_store(
    connection: &Connection,
    manifest: &SnapshotManifest,
    scope: &Scope,
) -> Result<(), String> {
    let report = validate_store(connection, scope)?;
    if report.schema_version != manifest.schema_version
        || report.cursor != manifest.cursor
        || report.authoritative_digest != manifest.source_digest
    {
        return Err("snapshot manifest does not bind the recovered memory state".to_owned());
    }
    Ok(())
}

fn validate_revisions(connection: &Connection, owner: &str) -> Result<(u64, u64), String> {
    let mut records = connection
        .prepare(
            "SELECT record_id,kind,current_revision,current_revision_digest,
                    normalized_content_digest
             FROM memory_records WHERE owner_scope_digest=?1 ORDER BY record_id",
        )
        .map_err(sql)?;
    let rows = records
        .query_map([owner], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, u64>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, String>(4)?,
            ))
        })
        .map_err(sql)?
        .collect::<Result<Vec<_>, _>>()
        .map_err(sql)?;
    drop(records);
    let mut revision_count = 0_u64;
    for (record_id, kind, current_revision, current_digest, normalized_digest) in &rows {
        let parsed_id = crate::Id::new(record_id.clone())
            .map_err(|_| "memory record id is invalid".to_owned())?;
        let mut statement = connection
            .prepare(
                "SELECT revision,revision_digest,parent_revision_digest,content,
                        content_digest,metadata_json,smart_predicate_json,author_scope_digest,
                        authored_at_ms,immutable_anchor
                 FROM memory_revisions WHERE record_id=?1 ORDER BY revision",
            )
            .map_err(sql)?;
        let revisions = statement
            .query_map([record_id], |row| {
                Ok((
                    row.get::<_, u64>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, Option<String>>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, String>(4)?,
                    row.get::<_, String>(5)?,
                    row.get::<_, Option<String>>(6)?,
                    row.get::<_, String>(7)?,
                    row.get::<_, u64>(8)?,
                    row.get::<_, bool>(9)?,
                ))
            })
            .map_err(sql)?
            .collect::<Result<Vec<_>, _>>()
            .map_err(sql)?;
        if revisions.len() as u64 != *current_revision || revisions.is_empty() {
            return Err("memory revision chain has a gap".to_owned());
        }
        let mut parent: Option<Digest> = None;
        for (index, row) in revisions.iter().enumerate() {
            let (
                number,
                digest,
                stored_parent,
                content,
                content_digest,
                metadata,
                smart_predicate,
                author,
                at,
                anchor,
            ) = row;
            if *number != index as u64 + 1
                || stored_parent.as_deref() != parent.map(|value| value.to_hex()).as_deref()
                || Digest::sha256(content.as_bytes()).to_hex() != *content_digest
                || (*anchor != (kind == "anchor"))
            {
                return Err("memory revision chain is inconsistent".to_owned());
            }
            let content_digest = Digest::from_str(content_digest)
                .map_err(|_| "memory content digest is invalid".to_owned())?;
            let author = Digest::from_str(author)
                .map_err(|_| "memory author scope digest is invalid".to_owned())?;
            let calculated = revision_digest(
                &parsed_id,
                *number,
                parent,
                content_digest,
                metadata.as_bytes(),
                smart_predicate.as_deref().map(str::as_bytes),
                author,
                *at,
            );
            if calculated.to_hex() != *digest {
                return Err("memory revision digest is inconsistent".to_owned());
            }
            parent = Some(calculated);
            if index + 1 == revisions.len()
                && (digest != current_digest
                    || normalized_content(content).to_hex() != *normalized_digest)
            {
                return Err("memory record head does not match its current revision".to_owned());
            }
        }
        if kind == "anchor" && revisions.len() != 1 {
            return Err("anchor revision history is not immutable".to_owned());
        }
        revision_count += revisions.len() as u64;
    }
    Ok((rows.len() as u64, revision_count))
}

fn validate_sources(connection: &Connection, owner: &str) -> Result<u64, String> {
    let mut statement = connection
        .prepare(
            "SELECT source_digest,captured_content FROM memory_sources
             WHERE owner_scope_digest=?1 ORDER BY source_id",
        )
        .map_err(sql)?;
    let rows = statement
        .query_map([owner], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, Option<String>>(1)?))
        })
        .map_err(sql)?
        .collect::<Result<Vec<_>, _>>()
        .map_err(sql)?;
    for (digest, content) in &rows {
        if content
            .as_ref()
            .is_some_and(|content| Digest::sha256(content.as_bytes()).to_hex() != *digest)
        {
            return Err("captured memory source digest is inconsistent".to_owned());
        }
    }
    Ok(rows.len() as u64)
}

fn validate_provenance(connection: &Connection, owner: &str) -> Result<(), String> {
    let mut statement = connection
        .prepare(
            "SELECT s.captured_content,p.span_start,p.span_end,p.quoted_digest
             FROM memory_provenance p
             JOIN memory_records r ON r.record_id=p.record_id
             JOIN memory_sources s ON s.source_id=p.source_id
             WHERE r.owner_scope_digest=?1",
        )
        .map_err(sql)?;
    let rows = statement
        .query_map([owner], |row| {
            Ok((
                row.get::<_, Option<String>>(0)?,
                row.get::<_, Option<u64>>(1)?,
                row.get::<_, Option<u64>>(2)?,
                row.get::<_, Option<String>>(3)?,
            ))
        })
        .map_err(sql)?
        .collect::<Result<Vec<_>, _>>()
        .map_err(sql)?;
    for (content, start, end, quoted_digest) in rows {
        if start.is_some() != end.is_some() {
            return Err("memory provenance span is incomplete".to_owned());
        }
        if let Some(content) = content {
            let bytes = content.as_bytes();
            let selected = match start.zip(end) {
                Some((start, end)) => bytes
                    .get(start as usize..end as usize)
                    .ok_or_else(|| "memory provenance span is outside source bytes".to_owned())?,
                None => bytes,
            };
            std::str::from_utf8(selected)
                .map_err(|_| "memory provenance span splits UTF-8".to_owned())?;
            if quoted_digest
                .as_ref()
                .is_some_and(|digest| Digest::sha256(selected).to_hex() != *digest)
            {
                return Err("memory provenance quote digest is inconsistent".to_owned());
            }
        }
    }
    Ok(())
}

fn validate_lineage(connection: &Connection, owner: &str) -> Result<u64, String> {
    let count: u64 = connection
        .query_row(
            "SELECT count(*) FROM memory_lineage l
             JOIN memory_records r ON r.record_id=l.child_record_id
             WHERE r.owner_scope_digest=?1",
            [owner],
            |row| row.get(0),
        )
        .map_err(sql)?;
    let cycle: bool = connection
        .query_row(
            "WITH RECURSIVE edges(child,parent) AS (
                 SELECT l.child_record_id,l.parent_record_id FROM memory_lineage l
                 JOIN memory_records r ON r.record_id=l.child_record_id
                 WHERE r.owner_scope_digest=?1
             ), reach(start,node) AS (
                 SELECT child,parent FROM edges
                 UNION
                 SELECT reach.start,edges.parent FROM reach JOIN edges ON edges.child=reach.node
             )
             SELECT EXISTS(SELECT 1 FROM reach WHERE start=node)",
            [owner],
            |row| row.get(0),
        )
        .map_err(sql)?;
    if cycle {
        return Err("memory lineage contains a cycle".to_owned());
    }
    let missing_parent: bool = connection
        .query_row(
            "SELECT EXISTS(
                SELECT 1 FROM memory_lineage l
                JOIN memory_records c ON c.record_id=l.child_record_id
                LEFT JOIN memory_revisions p
                  ON p.record_id=l.parent_record_id AND p.revision_digest=l.parent_revision_digest
                WHERE c.owner_scope_digest=?1 AND p.record_id IS NULL)",
            [owner],
            |row| row.get(0),
        )
        .map_err(sql)?;
    if missing_parent {
        return Err("memory lineage parent revision is missing".to_owned());
    }
    Ok(count)
}

fn validate_mutation_cursor(
    connection: &Connection,
    owner: &str,
    cursor: Cursor,
) -> Result<(), String> {
    let (count, distinct, maximum): (u64, u64, Option<u64>) = connection
        .query_row(
            "SELECT count(*),count(DISTINCT sequence),max(sequence) FROM memory_mutation_events
             WHERE owner_scope_digest=?1 AND epoch=?2",
            params![owner, cursor.epoch],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
        )
        .map_err(sql)?;
    if count != distinct || maximum.is_some_and(|sequence| sequence > cursor.sequence) {
        return Err("memory mutation event exceeds or duplicates the scope cursor".to_owned());
    }
    let future_event: bool = connection
        .query_row(
            "SELECT EXISTS(
                SELECT 1 FROM memory_mutation_events
                WHERE owner_scope_digest=?1 AND epoch>?2
             )",
            params![owner, cursor.epoch],
            |row| row.get(0),
        )
        .map_err(sql)?;
    if future_event {
        return Err("memory mutation event belongs to a future cursor epoch".to_owned());
    }
    Ok(())
}

fn validate_derivatives(connection: &Connection, owner: &str) -> Result<(u64, u64, u64), String> {
    let expected_memory: u64 = connection
        .query_row(
            "SELECT count(*) FROM memory_records
             WHERE owner_scope_digest=?1 AND status IN ('active','stale')",
            [owner],
            |row| row.get(0),
        )
        .map_err(sql)?;
    let memory_fts: u64 = connection
        .query_row(
            "SELECT count(*) FROM memory_fts_rows f JOIN memory_records r ON r.record_id=f.record_id
             WHERE r.owner_scope_digest=?1 AND r.status IN ('active','stale')
               AND f.revision_digest=r.current_revision_digest",
            [owner],
            |row| row.get(0),
        )
        .map_err(sql)?;
    if memory_fts != expected_memory {
        return Err("memory FTS fingerprint is stale".to_owned());
    }
    let expected_source: u64 = connection
        .query_row(
            "SELECT count(*) FROM source_index_documents
             WHERE owner_scope_digest=?1 AND tombstoned_at_ms IS NULL",
            [owner],
            |row| row.get(0),
        )
        .map_err(sql)?;
    let source_fts: u64 = connection
        .query_row(
            "SELECT count(*) FROM source_fts_rows f
             JOIN source_index_documents d ON d.document_id=f.document_id
             WHERE d.owner_scope_digest=?1 AND d.tombstoned_at_ms IS NULL
               AND f.content_digest=d.content_digest",
            [owner],
            |row| row.get(0),
        )
        .map_err(sql)?;
    if source_fts != expected_source {
        return Err("source FTS fingerprint is stale".to_owned());
    }
    let invalid_embeddings: u64 = connection
        .query_row(
            "SELECT count(*) FROM memory_embeddings e
             JOIN memory_records r ON r.record_id=e.record_id
             JOIN embedding_registrations g ON g.registration_id=e.registration_id
             WHERE r.owner_scope_digest=?1 AND (
                e.revision_digest<>r.current_revision_digest OR e.dimensions<>g.dimensions OR
                g.owner_scope_digest<>r.owner_scope_digest OR g.state<>'active')",
            [owner],
            |row| row.get(0),
        )
        .map_err(sql)?;
    if invalid_embeddings != 0 {
        return Err("memory embedding fingerprint is stale".to_owned());
    }
    let invalid_sharing_judgments: u64 = connection
        .query_row(
            "SELECT count(*) FROM memory_sharing_judgments j
             JOIN memory_records r ON r.record_id=j.record_id
             WHERE j.owner_scope_digest=?1 AND (
                r.owner_scope_digest<>j.owner_scope_digest OR
                (j.invalidated_at_ms IS NULL AND j.revision_digest<>r.current_revision_digest))",
            [owner],
            |row| row.get(0),
        )
        .map_err(sql)?;
    if invalid_sharing_judgments != 0 {
        return Err("memory sharing judgment fingerprint is stale".to_owned());
    }
    validate_registration_fingerprints(connection, owner)?;
    let embedding_count: u64 = connection
        .query_row(
            "SELECT count(*) FROM memory_embeddings e JOIN memory_records r ON r.record_id=e.record_id
             WHERE r.owner_scope_digest=?1",
            [owner],
            |row| row.get(0),
        )
        .map_err(sql)?;
    Ok((memory_fts, source_fts, embedding_count))
}

fn derivative_counts(connection: &Connection, owner: &str) -> Result<(u64, u64, u64), String> {
    let memory_fts = connection
        .query_row(
            "SELECT count(*) FROM memory_fts_rows f
             JOIN memory_records r ON r.record_id=f.record_id WHERE r.owner_scope_digest=?1",
            [owner],
            |row| row.get(0),
        )
        .map_err(sql)?;
    let source_fts = connection
        .query_row(
            "SELECT count(*) FROM source_fts_rows f
             JOIN source_index_documents d ON d.document_id=f.document_id
             WHERE d.owner_scope_digest=?1",
            [owner],
            |row| row.get(0),
        )
        .map_err(sql)?;
    let embeddings = connection
        .query_row(
            "SELECT count(*) FROM memory_embeddings e
             JOIN memory_records r ON r.record_id=e.record_id WHERE r.owner_scope_digest=?1",
            [owner],
            |row| row.get(0),
        )
        .map_err(sql)?;
    Ok((memory_fts, source_fts, embeddings))
}

fn validate_registration_fingerprints(connection: &Connection, owner: &str) -> Result<(), String> {
    let mut statement = connection
        .prepare(
            "SELECT mode,provider_identity,model_id,dimensions,normalized,fingerprint
             FROM embedding_registrations WHERE owner_scope_digest=?1",
        )
        .map_err(sql)?;
    let rows = statement
        .query_map([owner], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, usize>(3)?,
                row.get::<_, bool>(4)?,
                row.get::<_, String>(5)?,
            ))
        })
        .map_err(sql)?
        .collect::<Result<Vec<_>, _>>()
        .map_err(sql)?;
    for (mode, provider, model, dimensions, normalized, fingerprint) in rows {
        let mode = match mode.as_str() {
            "off" => EmbeddingMode::Off,
            "local" => EmbeddingMode::Local,
            "remote-compatible" => EmbeddingMode::RemoteCompatible,
            "managed-service" => EmbeddingMode::ManagedService,
            _ => return Err("embedding registration mode is invalid".to_owned()),
        };
        if registration_fingerprint(mode, &provider, &model, dimensions, normalized).to_hex()
            != fingerprint
        {
            return Err("embedding registration fingerprint is invalid".to_owned());
        }
    }
    Ok(())
}

pub fn rebuild_derivatives(
    transaction: &Transaction<'_>,
    scope: &Scope,
) -> Result<DerivativeRebuildReceipt, String> {
    let owner = scope_digest(scope).to_hex();
    transaction
        .execute(
            "DELETE FROM memory_sharing_judgments WHERE owner_scope_digest=?1",
            [&owner],
        )
        .map_err(sql)?;
    let cleared_embeddings = transaction
        .execute(
            "DELETE FROM memory_embeddings WHERE record_id IN (
                SELECT record_id FROM memory_records WHERE owner_scope_digest=?1)",
            [&owner],
        )
        .map_err(sql)? as u64;
    transaction
        .execute(
            "DELETE FROM embedding_jobs WHERE registration_id IN (
                SELECT registration_id FROM embedding_registrations WHERE owner_scope_digest=?1)",
            [&owner],
        )
        .map_err(sql)?;
    transaction
        .execute(
            "DELETE FROM embedding_registrations WHERE owner_scope_digest=?1",
            [&owner],
        )
        .map_err(sql)?;
    transaction
        .execute(
            "INSERT INTO memory_fts(memory_fts) VALUES('delete-all')",
            [],
        )
        .map_err(sql)?;
    transaction
        .execute("DELETE FROM memory_fts_rows", [])
        .map_err(sql)?;
    let mut statement = transaction
        .prepare(
            "SELECT r.record_id,r.owner_scope_digest,r.category,v.content,r.current_revision_digest
             FROM memory_records r JOIN memory_revisions v
               ON v.record_id=r.record_id AND v.revision=r.current_revision
             WHERE r.status IN ('active','stale')
             ORDER BY r.record_id",
        )
        .map_err(sql)?;
    let records = statement
        .query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, String>(4)?,
            ))
        })
        .map_err(sql)?
        .collect::<Result<Vec<_>, _>>()
        .map_err(sql)?;
    drop(statement);
    for (record_id, record_owner, category, content, revision_digest) in &records {
        index_memory_record(
            transaction,
            record_id,
            record_owner,
            category,
            content,
            revision_digest,
        )
        .map_err(|error| error.to_string())?;
    }
    transaction
        .execute(
            "INSERT INTO source_fts(source_fts) VALUES('delete-all')",
            [],
        )
        .map_err(sql)?;
    transaction
        .execute("DELETE FROM source_fts_rows", [])
        .map_err(sql)?;
    let mut statement = transaction
        .prepare(
            "SELECT document_id,owner_scope_digest,source_kind,content,content_digest
             FROM source_index_documents
             WHERE tombstoned_at_ms IS NULL ORDER BY document_id",
        )
        .map_err(sql)?;
    let documents = statement
        .query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, String>(4)?,
            ))
        })
        .map_err(sql)?
        .collect::<Result<Vec<_>, _>>()
        .map_err(sql)?;
    drop(statement);
    for (document_id, document_owner, source_kind, content, content_digest) in &documents {
        transaction
            .execute(
                "INSERT INTO source_fts(document_id,owner_scope_digest,source_kind,content)
                 VALUES (?1,?2,?3,?4)",
                params![document_id, document_owner, source_kind, content],
            )
            .map_err(sql)?;
        transaction
            .execute(
                "INSERT INTO source_fts_rows(rowid,document_id,content_digest) VALUES (?1,?2,?3)",
                params![transaction.last_insert_rowid(), document_id, content_digest],
            )
            .map_err(sql)?;
    }
    Ok(DerivativeRebuildReceipt {
        memory_fts_rows: records.len() as u64,
        source_fts_rows: documents.len() as u64,
        cleared_embeddings,
    })
}

pub(crate) fn authoritative_digest(
    connection: &Connection,
    owner: &str,
    cursor: Cursor,
) -> Result<Digest, String> {
    let mut hasher = Sha256::new();
    hasher.update(b"hypermid.memory.authoritative.v1\0");
    hasher.update(owner.as_bytes());
    hasher.update(cursor.epoch.to_be_bytes());
    hasher.update(cursor.sequence.to_be_bytes());
    for (label, query) in [
        (
            "memory_scopes",
            "SELECT * FROM memory_scopes WHERE scope_digest=?1 ORDER BY scope_digest",
        ),
        (
            "memory_share_grants",
            "SELECT * FROM memory_share_grants WHERE owner_scope_digest=?1 ORDER BY grant_id",
        ),
        (
            "memory_records",
            "SELECT * FROM memory_records WHERE owner_scope_digest=?1 ORDER BY record_id",
        ),
        (
            "memory_revisions",
            "SELECT v.* FROM memory_revisions v JOIN memory_records r ON r.record_id=v.record_id
             WHERE r.owner_scope_digest=?1 ORDER BY v.record_id,v.revision",
        ),
        (
            "episode_details",
            "SELECT d.* FROM episode_details d JOIN memory_records r ON r.record_id=d.record_id
             WHERE r.owner_scope_digest=?1 ORDER BY d.record_id",
        ),
        (
            "smart_note_details",
            "SELECT d.* FROM smart_note_details d JOIN memory_records r ON r.record_id=d.record_id
             WHERE r.owner_scope_digest=?1 ORDER BY d.record_id",
        ),
        (
            "summary_details",
            "SELECT d.* FROM summary_details d JOIN memory_records r ON r.record_id=d.record_id
             WHERE r.owner_scope_digest=?1 ORDER BY d.record_id",
        ),
        (
            "memory_sources",
            "SELECT * FROM memory_sources WHERE owner_scope_digest=?1 ORDER BY source_id",
        ),
        (
            "memory_provenance",
            "SELECT p.* FROM memory_provenance p JOIN memory_records r ON r.record_id=p.record_id
             WHERE r.owner_scope_digest=?1 ORDER BY p.record_id,p.revision,p.source_id,p.span_start",
        ),
        (
            "memory_lineage",
            "SELECT l.* FROM memory_lineage l JOIN memory_records r ON r.record_id=l.child_record_id
             WHERE r.owner_scope_digest=?1
             ORDER BY l.child_record_id,l.child_revision,l.parent_record_id,l.relation",
        ),
        (
            "memory_verification_events",
            "SELECT e.* FROM memory_verification_events e JOIN memory_records r ON r.record_id=e.record_id
             WHERE r.owner_scope_digest=?1 ORDER BY e.created_at_ms,e.event_id",
        ),
        (
            "memory_mutation_events",
            "SELECT * FROM memory_mutation_events WHERE owner_scope_digest=?1
             ORDER BY epoch,sequence,event_id",
        ),
    ] {
        hash_rows(connection, &mut hasher, label, query, owner)?;
    }
    Ok(Digest::from_bytes(hasher.finalize().into()))
}

fn hash_rows(
    connection: &Connection,
    hasher: &mut Sha256,
    label: &str,
    query: &str,
    owner: &str,
) -> Result<(), String> {
    use rusqlite::types::ValueRef;
    hash_part(hasher, label.as_bytes());
    let mut statement = connection.prepare(query).map_err(sql)?;
    let columns = statement.column_count();
    let mut rows = statement.query([owner]).map_err(sql)?;
    while let Some(row) = rows.next().map_err(sql)? {
        for index in 0..columns {
            match row.get_ref(index).map_err(sql)? {
                ValueRef::Null => hasher.update([0]),
                ValueRef::Integer(value) => {
                    hasher.update([1]);
                    hasher.update(value.to_be_bytes());
                }
                ValueRef::Real(value) => {
                    hasher.update([2]);
                    hasher.update(value.to_bits().to_be_bytes());
                }
                ValueRef::Text(value) => {
                    hasher.update([3]);
                    hash_part(hasher, value);
                }
                ValueRef::Blob(value) => {
                    hasher.update([4]);
                    hash_part(hasher, value);
                }
            }
        }
    }
    Ok(())
}

fn normalized_content(content: &str) -> Digest {
    Digest::sha256(
        content
            .split_whitespace()
            .collect::<Vec<_>>()
            .join(" ")
            .to_lowercase()
            .as_bytes(),
    )
}

fn revision_digest(
    record_id: &crate::Id,
    revision: u64,
    parent: Option<Digest>,
    content: Digest,
    metadata: &[u8],
    smart_predicate: Option<&[u8]>,
    author: Digest,
    authored_at_ms: u64,
) -> Digest {
    let mut hasher = Sha256::new();
    hasher.update(b"hypermid.memory.revision.v1\0");
    hash_part(&mut hasher, record_id.as_str().as_bytes());
    hasher.update(revision.to_be_bytes());
    match parent {
        Some(value) => {
            hasher.update([1]);
            hasher.update(value.as_bytes());
        }
        None => hasher.update([0]),
    }
    hasher.update(content.as_bytes());
    hash_part(&mut hasher, metadata);
    match smart_predicate {
        Some(value) => {
            hasher.update([1]);
            hash_part(&mut hasher, value);
        }
        None => hasher.update([0]),
    }
    hasher.update(author.as_bytes());
    hasher.update(authored_at_ms.to_be_bytes());
    Digest::from_bytes(hasher.finalize().into())
}

fn hash_part(hasher: &mut Sha256, value: &[u8]) {
    hasher.update((value.len() as u64).to_be_bytes());
    hasher.update(value);
}

fn sql(error: rusqlite::Error) -> String {
    format!("SQLite recovery validation failed: {error}")
}

fn domain_error(message: String) -> hypermid_contracts::Error {
    error(
        "RECOVERY_INTEGRITY_FAILED",
        message,
        EffectState::NotStarted,
    )
}

fn lifecycle_error(error: hypermid_store::backup::LifecycleError) -> hypermid_contracts::Error {
    crate::error(
        "RECOVERY_FAILED",
        format!("memory recovery failed: {error}"),
        EffectState::NotStarted,
    )
}

#[cfg(test)]
mod recovery_tests {
    use super::*;
    use crate::records::{RecordDraft, RecordKind};
    use crate::snapshot::create_memory_snapshot;
    use crate::{
        capability_operation, AuthContext, AuthenticatedPrincipal, AuthorizationRequest,
        CapabilityGrant, CapabilityOperation, Id, MutationRequest, Operation, PrincipalKind,
        RevisionPrecondition, Trace,
    };
    use hypermid_store::authorization::put_grant;
    use serde_json::Map;
    use sha2::Sha256;
    use std::collections::BTreeSet;
    use std::fs::File;
    use std::io::Read;

    fn id(value: &str) -> Id {
        Id::new(value).unwrap()
    }

    fn scope() -> Scope {
        Scope::new(id("owner-recovery"), id("project-recovery"), None)
    }

    fn principal() -> AuthenticatedPrincipal {
        AuthenticatedPrincipal {
            principal_id: id("principal-recovery"),
            owner_id: id("owner-recovery"),
            kind: PrincipalKind::Foreground,
        }
    }

    fn install_create_capability(store: &mut MemoryStore, target: &Scope) {
        let grant = CapabilityGrant {
            capability_id: id("cap-recovery"),
            issuer_owner_id: target.owner_id.clone(),
            principal_id: principal().principal_id,
            claimed_scope: target.clone(),
            target_scope: target.clone(),
            operations: BTreeSet::from([CapabilityOperation::Append]),
            resources: BTreeSet::from([id("record-recovery")]),
            expires_at_ms: 10_000,
        };
        store
            .immediate(|transaction| {
                put_grant(transaction.raw(), &principal(), &grant).unwrap();
                Ok(())
            })
            .unwrap();
    }

    fn create_record(store: &mut MemoryStore, target: &Scope) {
        let request = MutationRequest {
            operation: Operation::Create,
            actor_scope: target.clone(),
            target_scope: target.clone(),
            record_id: Some(id("record-recovery")),
            category: Some("project_fact".to_owned()),
            revision: RevisionPrecondition::MustNotExist,
            trace: Trace::new(id("trace-recovery"), id("request-recovery")),
        };
        let context = AuthContext {
            principal: principal(),
            request: AuthorizationRequest {
                claimed_scope: target.clone(),
                target_scope: target.clone(),
                operation: capability_operation(Operation::Create),
                resource_id: id("record-recovery"),
                now_ms: 100,
            },
            capability_id: id("cap-recovery"),
        };
        store
            .create_record(
                &context,
                &request,
                &RecordDraft {
                    id: id("record-recovery"),
                    scope: target.clone(),
                    kind: RecordKind::Fact,
                    category: "project_fact".to_owned(),
                    content: "recover this authoritative revision".to_owned(),
                    metadata: Map::new(),
                    smart_predicate: None,
                    importance: 0.8,
                    confidence: 0.9,
                    expires_at_ms: None,
                    retention_until_ms: None,
                    provenance: Vec::new(),
                    lineage: Vec::new(),
                    summary: None,
                },
                &[],
                100,
            )
            .unwrap();
    }

    fn file_digest(path: &Path) -> Digest {
        let mut file = File::open(path).unwrap();
        let mut hasher = Sha256::new();
        let mut buffer = [0_u8; 8192];
        loop {
            let count = file.read(&mut buffer).unwrap();
            if count == 0 {
                break;
            }
            hasher.update(&buffer[..count]);
        }
        Digest::from_bytes(hasher.finalize().into())
    }

    #[test]
    fn migration_recovery_rebuilds_copied_indexes_and_refuses_lineage_cycles() {
        let directory = tempfile::tempdir().unwrap();
        let active_path = directory.path().join("memory.sqlite3");
        let target = scope();
        let mut store = MemoryStore::open(&active_path).unwrap();
        install_create_capability(&mut store, &target);
        create_record(&mut store, &target);
        let original = store
            .read(|connection| inspect_store(connection, &target))
            .unwrap();
        assert_eq!((original.record_count, original.revision_count), (1, 1));

        store
            .immediate(|transaction| {
                transaction
                    .raw()
                    .execute("DELETE FROM memory_fts_rows", [])
                    .unwrap();
                Ok(())
            })
            .unwrap();
        assert_eq!(
            store
                .read(|connection| inspect_store(connection, &target))
                .unwrap_err()
                .code,
            "RECOVERY_INTEGRITY_FAILED"
        );
        let staged = stage_repaired_copy(&store, &active_path, target.clone()).unwrap();
        drop(store);
        let expected_active = file_digest(&active_path);
        staged.activate(Some(expected_active)).unwrap();

        let restored = MemoryStore::open(&active_path).unwrap();
        let repaired = restored
            .read(|connection| inspect_store(connection, &target))
            .unwrap();
        assert_eq!(repaired.authoritative_digest, original.authoritative_digest);
        assert_eq!((repaired.record_count, repaired.revision_count), (1, 1));
        assert_eq!(repaired.memory_fts_count, 1);

        let artifact = directory.path().join("snapshot");
        let snapshot = create_memory_snapshot(&restored, &artifact, target.clone()).unwrap();
        drop(restored);
        let restored_path = directory.path().join("restored.sqlite3");
        let staged_restore =
            stage_verified_restore(&restored_path, &artifact, snapshot.manifest_digest, &target)
                .unwrap();
        staged_restore.activate(None).unwrap();
        let mut restored = MemoryStore::open(&restored_path).unwrap();
        let snapshot_report = restored
            .read(|connection| inspect_store(connection, &target))
            .unwrap();
        assert_eq!(
            snapshot_report.authoritative_digest,
            original.authoritative_digest
        );

        restored
            .immediate(|transaction| {
                let revision: String = transaction
                    .raw()
                    .query_row(
                        "SELECT current_revision_digest FROM memory_records WHERE record_id='record-recovery'",
                        [],
                        |row| row.get(0),
                    )
                    .unwrap();
                transaction
                    .raw()
                    .execute(
                        "INSERT INTO memory_lineage(
                            child_record_id,child_revision,parent_record_id,
                            parent_revision_digest,relation,created_at_ms)
                         VALUES ('record-recovery',1,'record-recovery',?1,'cites',200)",
                        [revision],
                    )
                    .unwrap();
                Ok(())
            })
            .unwrap();
        let recovery_failure = match stage_repaired_copy(&restored, &restored_path, target) {
            Err(error) => error,
            Ok(_) => panic!("lineage cycle must refuse staged recovery"),
        };
        assert_eq!(recovery_failure.code, "RECOVERY_INTEGRITY_FAILED");
        assert_eq!(
            restored
                .read(|connection| {
                    connection
                        .query_row("SELECT count(*) FROM memory_lineage", [], |row| {
                            row.get::<_, u64>(0)
                        })
                        .map_err(|source| {
                            error("TEST_READ_FAILED", source.to_string(), EffectState::Unknown)
                        })
                })
                .unwrap(),
            1
        );
    }
}
