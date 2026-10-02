use crate::{error, MemoryResult};
use hypermid_contracts::{Cursor, EffectState};
use rusqlite::{params, OptionalExtension, Transaction};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SourceKind {
    Message,
    File,
    GitCommit,
}

impl SourceKind {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Message => "message",
            Self::File => "file",
            Self::GitCommit => "git_commit",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SourceDocument {
    pub document_id: String,
    pub source_kind: SourceKind,
    pub source_key: String,
    pub content: String,
    pub content_digest: String,
    pub source_time_ms: Option<i64>,
    pub metadata_json: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SourceIndexState {
    pub cursor: Option<Cursor>,
    pub dirty_floor_sequence: Option<u64>,
    pub repository_identity_digest: Option<String>,
    pub refs_digest: Option<String>,
    pub next_probe_at_ms: Option<i64>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct FilePredicateAudit<'a> {
    pub decision_id: &'a str,
    pub canonical_path_digest: &'a str,
    pub root_digest: &'a str,
    pub content_digest: Option<&'a str>,
    pub allowed: bool,
    pub reason: &'a str,
    pub policy_digest: &'a str,
    pub decided_at_ms: i64,
}

pub fn record_file_predicate(
    transaction: &Transaction<'_>,
    scope_digest: &str,
    audit: &FilePredicateAudit<'_>,
) -> MemoryResult<()> {
    transaction.execute(
        r#"INSERT INTO file_predicate_decisions(decision_id, owner_scope_digest, canonical_path_digest, root_digest, content_digest, decision, reason, policy_digest, decided_at_ms)
            VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)"#,
        params![audit.decision_id, scope_digest, audit.canonical_path_digest, audit.root_digest,
                audit.content_digest, if audit.allowed { "allow" } else { "deny" }, audit.reason,
                audit.policy_digest, audit.decided_at_ms],
    ).map_err(sql_error)?;
    Ok(())
}

pub fn publish_document(
    transaction: &Transaction<'_>,
    scope_digest: &str,
    document: &SourceDocument,
    indexed_at_ms: i64,
) -> MemoryResult<()> {
    let existing: Option<(i64, String, String, String)> = transaction
        .query_row(
            r#"SELECT f.rowid, d.document_id, d.owner_scope_digest, d.content
               FROM source_index_documents d JOIN source_fts_rows f ON f.document_id = d.document_id
               WHERE d.owner_scope_digest = ?1 AND d.source_kind = ?2 AND d.source_key = ?3"#,
            params![
                scope_digest,
                document.source_kind.as_str(),
                document.source_key
            ],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
        )
        .optional()
        .map_err(sql_error)?;
    if let Some((rowid, document_id, owner, content)) = existing {
        transaction
            .execute(
                r#"INSERT INTO source_fts(source_fts, rowid, document_id, owner_scope_digest, source_kind, content)
                    VALUES ('delete', ?1, ?2, ?3, ?4, ?5)"#,
                params![rowid, document_id, owner, document.source_kind.as_str(), content],
            )
            .map_err(sql_error)?;
        transaction
            .execute("DELETE FROM source_fts_rows WHERE rowid = ?1", [rowid])
            .map_err(sql_error)?;
    }
    transaction
        .execute(
            r#"INSERT INTO source_index_documents(document_id, owner_scope_digest, source_kind, source_key, content, content_digest, source_time_ms, metadata_json, indexed_at_ms, tombstoned_at_ms)
               VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, NULL)
               ON CONFLICT(owner_scope_digest, source_kind, source_key) DO UPDATE SET
                 document_id=excluded.document_id, content=excluded.content, content_digest=excluded.content_digest,
                 source_time_ms=excluded.source_time_ms, metadata_json=excluded.metadata_json,
                 indexed_at_ms=excluded.indexed_at_ms, tombstoned_at_ms=NULL"#,
            params![document.document_id, scope_digest, document.source_kind.as_str(), document.source_key,
                    document.content, document.content_digest, document.source_time_ms, document.metadata_json, indexed_at_ms],
        )
        .map_err(sql_error)?;
    transaction
        .execute(
            "INSERT INTO source_fts(document_id, owner_scope_digest, source_kind, content) VALUES (?1, ?2, ?3, ?4)",
            params![document.document_id, scope_digest, document.source_kind.as_str(), document.content],
        )
        .map_err(sql_error)?;
    transaction
        .execute(
            "INSERT INTO source_fts_rows(rowid, document_id, content_digest) VALUES (?1, ?2, ?3)",
            params![
                transaction.last_insert_rowid(),
                document.document_id,
                document.content_digest
            ],
        )
        .map_err(sql_error)?;
    Ok(())
}

pub fn lower_message_dirty_floor(
    transaction: &Transaction<'_>,
    scope_digest: &str,
    failed_sequence: u64,
    now_ms: i64,
) -> MemoryResult<()> {
    transaction
        .execute(
            r#"INSERT INTO source_index_state(owner_scope_digest, source_kind, dirty_floor_sequence, updated_at_ms)
               VALUES (?1, 'message', ?2, ?3)
               ON CONFLICT(owner_scope_digest, source_kind) DO UPDATE SET
                 dirty_floor_sequence = CASE
                   WHEN source_index_state.dirty_floor_sequence IS NULL THEN excluded.dirty_floor_sequence
                   ELSE min(source_index_state.dirty_floor_sequence, excluded.dirty_floor_sequence) END,
                 updated_at_ms = excluded.updated_at_ms"#,
            params![scope_digest, failed_sequence, now_ms],
        )
        .map_err(sql_error)?;
    Ok(())
}

pub fn message_reconcile_from(
    transaction: &Transaction<'_>,
    scope_digest: &str,
) -> MemoryResult<u64> {
    let state = read_state(transaction, scope_digest, SourceKind::Message)?;
    Ok(state
        .as_ref()
        .and_then(|state| state.dirty_floor_sequence)
        .or_else(|| state.and_then(|state| state.cursor.map(|cursor| cursor.sequence + 1)))
        .unwrap_or(0))
}

pub fn publish_message_cursor(
    transaction: &Transaction<'_>,
    scope_digest: &str,
    started_at_sequence: u64,
    cursor: Cursor,
    now_ms: i64,
) -> MemoryResult<()> {
    let expected = message_reconcile_from(transaction, scope_digest)?;
    let current = read_state(transaction, scope_digest, SourceKind::Message)?
        .and_then(|state| state.cursor)
        .map_or(0, |cursor| cursor.sequence);
    if started_at_sequence > expected || cursor.sequence < expected || cursor.sequence < current {
        return Err(error(
            "INDEX_GAP",
            "message reconciliation did not cover the earliest dirty cursor or moved backwards",
            EffectState::NotStarted,
        ));
    }
    transaction
        .execute(
            r#"INSERT INTO source_index_state(owner_scope_digest, source_kind, cursor_json, dirty_floor_sequence, updated_at_ms)
               VALUES (?1, 'message', ?2, NULL, ?3)
               ON CONFLICT(owner_scope_digest, source_kind) DO UPDATE SET
                 cursor_json=excluded.cursor_json, dirty_floor_sequence=NULL, updated_at_ms=excluded.updated_at_ms"#,
            params![scope_digest, serde_json::to_string(&cursor).map_err(json_error)?, now_ms],
        )
        .map_err(sql_error)?;
    Ok(())
}

pub fn publish_git_state(
    transaction: &Transaction<'_>,
    scope_digest: &str,
    repository_identity_digest: &str,
    refs_digest: &str,
    now_ms: i64,
) -> MemoryResult<()> {
    transaction.execute(
        r#"INSERT INTO source_index_state(owner_scope_digest, source_kind, repository_identity_digest, refs_digest, next_probe_at_ms, updated_at_ms)
            VALUES (?1, 'git_commit', ?2, ?3, NULL, ?4)
            ON CONFLICT(owner_scope_digest, source_kind) DO UPDATE SET
              repository_identity_digest=excluded.repository_identity_digest, refs_digest=excluded.refs_digest,
              next_probe_at_ms=NULL, updated_at_ms=excluded.updated_at_ms"#,
        params![scope_digest, repository_identity_digest, refs_digest, now_ms],
    ).map_err(sql_error)?;
    Ok(())
}

pub fn record_git_cooldown(
    transaction: &Transaction<'_>,
    scope_digest: &str,
    next_probe_at_ms: i64,
    now_ms: i64,
) -> MemoryResult<()> {
    transaction.execute(
        r#"INSERT INTO source_index_state(owner_scope_digest, source_kind, next_probe_at_ms, updated_at_ms)
            VALUES (?1, 'git_commit', ?2, ?3)
            ON CONFLICT(owner_scope_digest, source_kind) DO UPDATE SET
              next_probe_at_ms=max(COALESCE(source_index_state.next_probe_at_ms, 0), excluded.next_probe_at_ms),
              updated_at_ms=excluded.updated_at_ms"#,
        params![scope_digest, next_probe_at_ms, now_ms],
    ).map_err(sql_error)?;
    Ok(())
}

pub fn read_state(
    transaction: &Transaction<'_>,
    scope_digest: &str,
    kind: SourceKind,
) -> MemoryResult<Option<SourceIndexState>> {
    transaction.query_row(
        r#"SELECT cursor_json, dirty_floor_sequence, repository_identity_digest, refs_digest, next_probe_at_ms
            FROM source_index_state WHERE owner_scope_digest=?1 AND source_kind=?2"#,
        params![scope_digest, kind.as_str()],
        |row| {
            let cursor_json: Option<String> = row.get(0)?;
            Ok(SourceIndexState {
                cursor: cursor_json.map(|value| serde_json::from_str(&value)).transpose().map_err(|error| {
                    rusqlite::Error::FromSqlConversionFailure(0, rusqlite::types::Type::Text, Box::new(error))
                })?,
                dirty_floor_sequence: row.get(1)?, repository_identity_digest: row.get(2)?,
                refs_digest: row.get(3)?, next_probe_at_ms: row.get(4)?,
            })
        },
    ).optional().map_err(sql_error)
}

fn json_error(source: serde_json::Error) -> hypermid_contracts::Error {
    error(
        "INDEX_STATE_FAILED",
        format!("index cursor serialization failed: {source}"),
        EffectState::NotStarted,
    )
}

fn sql_error(source: rusqlite::Error) -> hypermid_contracts::Error {
    error(
        "INDEX_STATE_FAILED",
        format!("source index operation failed: {source}"),
        EffectState::Unknown,
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::Connection;

    #[test]
    fn source_index_dirty_floor_recovers_before_cursor_advances() {
        let mut connection = Connection::open_in_memory().unwrap();
        connection.execute_batch(
            "CREATE TABLE source_index_state(owner_scope_digest TEXT, source_kind TEXT, cursor_json TEXT, dirty_floor_sequence INTEGER, repository_identity_digest TEXT, refs_digest TEXT, next_probe_at_ms INTEGER, updated_at_ms INTEGER, PRIMARY KEY(owner_scope_digest, source_kind));"
        ).unwrap();
        let transaction = connection.transaction().unwrap();
        lower_message_dirty_floor(&transaction, "scope", 8, 1).unwrap();
        lower_message_dirty_floor(&transaction, "scope", 5, 2).unwrap();
        assert_eq!(message_reconcile_from(&transaction, "scope").unwrap(), 5);
        assert!(
            publish_message_cursor(&transaction, "scope", 6, Cursor::new(1, 9).unwrap(), 3)
                .is_err()
        );
        publish_message_cursor(&transaction, "scope", 5, Cursor::new(1, 9).unwrap(), 3).unwrap();
        assert_eq!(message_reconcile_from(&transaction, "scope").unwrap(), 10);
    }

    #[test]
    fn source_index_publishes_real_fts_rows_and_audits_predicates() {
        let mut connection = Connection::open_in_memory().unwrap();
        connection.execute_batch(
            "CREATE TABLE source_index_documents(document_id TEXT PRIMARY KEY, owner_scope_digest TEXT NOT NULL, source_kind TEXT NOT NULL, source_key TEXT NOT NULL, content TEXT NOT NULL, content_digest TEXT NOT NULL, source_time_ms INTEGER, metadata_json TEXT NOT NULL, indexed_at_ms INTEGER NOT NULL, tombstoned_at_ms INTEGER, UNIQUE(owner_scope_digest, source_kind, source_key));\
             CREATE VIRTUAL TABLE source_fts USING fts5(document_id UNINDEXED, owner_scope_digest UNINDEXED, source_kind UNINDEXED, content, content='');\
             CREATE TABLE source_fts_rows(rowid INTEGER PRIMARY KEY, document_id TEXT NOT NULL UNIQUE, content_digest TEXT NOT NULL);\
             CREATE TABLE file_predicate_decisions(decision_id TEXT PRIMARY KEY, owner_scope_digest TEXT NOT NULL, canonical_path_digest TEXT NOT NULL, root_digest TEXT NOT NULL, content_digest TEXT, decision TEXT NOT NULL, reason TEXT NOT NULL, policy_digest TEXT NOT NULL, decided_at_ms INTEGER NOT NULL);",
        ).unwrap();
        let transaction = connection.transaction().unwrap();
        let mut document = SourceDocument {
            document_id: "file-1".into(),
            source_kind: SourceKind::File,
            source_key: "src/lib.rs".into(),
            content: "launch checklist".into(),
            content_digest: "a".repeat(64),
            source_time_ms: Some(10),
            metadata_json: "{}".into(),
        };
        publish_document(&transaction, "scope", &document, 20).unwrap();
        assert_eq!(
            transaction
                .query_row(
                    "SELECT count(*) FROM source_fts WHERE source_fts MATCH 'launch'",
                    [],
                    |row| row.get::<_, i64>(0)
                )
                .unwrap(),
            1
        );
        document.document_id = "file-2".into();
        document.content = "release runbook".into();
        document.content_digest = "b".repeat(64);
        publish_document(&transaction, "scope", &document, 30).unwrap();
        assert_eq!(
            transaction
                .query_row(
                    "SELECT count(*) FROM source_fts WHERE source_fts MATCH 'launch'",
                    [],
                    |row| row.get::<_, i64>(0)
                )
                .unwrap(),
            0
        );
        assert_eq!(
            transaction
                .query_row(
                    "SELECT count(*) FROM source_fts WHERE source_fts MATCH 'release'",
                    [],
                    |row| row.get::<_, i64>(0)
                )
                .unwrap(),
            1
        );
        let canonical_digest = "c".repeat(64);
        let root_digest = "d".repeat(64);
        let policy_digest = "e".repeat(64);
        record_file_predicate(
            &transaction,
            "scope",
            &FilePredicateAudit {
                decision_id: "decision",
                canonical_path_digest: &canonical_digest,
                root_digest: &root_digest,
                content_digest: None,
                allowed: false,
                reason: "secret_policy",
                policy_digest: &policy_digest,
                decided_at_ms: 40,
            },
        )
        .unwrap();
        assert_eq!(
            transaction
                .query_row(
                    "SELECT decision || ':' || reason FROM file_predicate_decisions",
                    [],
                    |row| row.get::<_, String>(0)
                )
                .unwrap(),
            "deny:secret_policy"
        );
    }
}
