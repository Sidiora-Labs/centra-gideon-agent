use crate::{error, MemoryResult};
use hypermid_contracts::EffectState;
use rusqlite::{params, Connection, OptionalExtension, Transaction};

#[derive(Clone, Debug, PartialEq)]
pub struct LexicalMatch {
    pub rowid: i64,
    pub score: f64,
}

pub(crate) fn index_memory_record(
    transaction: &Transaction<'_>,
    record_id: &str,
    scope_digest: &str,
    category: &str,
    content: &str,
    revision_digest: &str,
) -> MemoryResult<()> {
    let existing: Option<i64> = transaction
        .query_row(
            "SELECT rowid FROM memory_fts_rows WHERE record_id = ?1",
            [record_id],
            |row| row.get(0),
        )
        .optional()
        .map_err(sql_error)?;
    if existing.is_some() {
        remove_memory_record(transaction, record_id, scope_digest, category, content)?;
    }
    transaction
        .execute(
            "INSERT INTO memory_fts(record_id, owner_scope_digest, category, content) VALUES (?1, ?2, ?3, ?4)",
            params![record_id, scope_digest, category, content],
        )
        .map_err(sql_error)?;
    let rowid = transaction.last_insert_rowid();
    transaction
        .execute(
            "INSERT INTO memory_fts_rows(rowid, record_id, revision_digest) VALUES (?1, ?2, ?3)",
            params![rowid, record_id, revision_digest],
        )
        .map_err(sql_error)?;
    Ok(())
}

pub(crate) fn remove_memory_record(
    transaction: &Transaction<'_>,
    record_id: &str,
    _scope_digest: &str,
    _category: &str,
    _content: &str,
) -> MemoryResult<()> {
    let indexed: Option<(i64, String, String, String)> = transaction
        .query_row(
            r#"SELECT f.rowid, r.owner_scope_digest, r.category, v.content
               FROM memory_fts_rows f
               JOIN memory_records r ON r.record_id = f.record_id
               JOIN memory_revisions v
                 ON v.record_id = r.record_id AND v.revision_digest = f.revision_digest
               WHERE f.record_id = ?1"#,
            [record_id],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
        )
        .optional()
        .map_err(sql_error)?;
    if let Some((rowid, scope_digest, category, content)) = indexed {
        transaction
            .execute(
                "INSERT INTO memory_fts(memory_fts, rowid, record_id, owner_scope_digest, category, content) VALUES ('delete', ?1, ?2, ?3, ?4, ?5)",
                params![rowid, record_id, scope_digest, category, content],
            )
            .map_err(sql_error)?;
        transaction
            .execute("DELETE FROM memory_fts_rows WHERE rowid = ?1", [rowid])
            .map_err(sql_error)?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn search_fts5_indexes_and_updates_real_content() {
        let mut connection = Connection::open_in_memory().unwrap();
        connection.execute_batch(
            "CREATE TABLE memory_records(record_id TEXT PRIMARY KEY, owner_scope_digest TEXT NOT NULL, category TEXT NOT NULL);\
             CREATE TABLE memory_revisions(record_id TEXT NOT NULL, revision_digest TEXT NOT NULL, content TEXT NOT NULL);\
             CREATE VIRTUAL TABLE memory_fts USING fts5(record_id UNINDEXED, owner_scope_digest UNINDEXED, category, content, content='');\
             CREATE TABLE memory_fts_rows(rowid INTEGER PRIMARY KEY, record_id TEXT NOT NULL UNIQUE, revision_digest TEXT NOT NULL);\
             INSERT INTO memory_records VALUES ('record', 'scope', 'general');\
             INSERT INTO memory_revisions VALUES ('record', 'rev-1', 'launch checklist');",
        ).unwrap();
        let transaction = connection.transaction().unwrap();
        index_memory_record(
            &transaction,
            "record",
            "scope",
            "general",
            "launch checklist",
            "rev-1",
        )
        .unwrap();
        assert_eq!(
            lexical_matches(&transaction, "launch", "scope", 10)
                .unwrap()
                .len(),
            1
        );
        transaction
            .execute(
                "INSERT INTO memory_revisions VALUES ('record', 'rev-2', 'release runbook')",
                [],
            )
            .unwrap();
        index_memory_record(
            &transaction,
            "record",
            "scope",
            "general",
            "release runbook",
            "rev-2",
        )
        .unwrap();
        assert!(lexical_matches(&transaction, "launch", "scope", 10)
            .unwrap()
            .is_empty());
        assert_eq!(
            lexical_matches(&transaction, "release", "scope", 10)
                .unwrap()
                .len(),
            1
        );
    }
}

pub(crate) fn lexical_matches(
    connection: &Connection,
    query: &str,
    scope_digest: &str,
    limit: usize,
) -> MemoryResult<Vec<LexicalMatch>> {
    let query = fts_query(query);
    if query.is_empty() || limit == 0 {
        return Ok(Vec::new());
    }
    let mut statement = connection
        .prepare(
            r#"SELECT memory_fts.rowid,
                      -bm25(memory_fts, 0.0, 0.0, 0.35, 1.0) AS score
               FROM memory_fts
               JOIN memory_fts_rows f ON f.rowid=memory_fts.rowid
               JOIN memory_records r ON r.record_id=f.record_id
               WHERE memory_fts MATCH ?1 AND r.owner_scope_digest=?2
               ORDER BY score DESC, memory_fts.rowid ASC LIMIT ?3"#,
        )
        .map_err(sql_error)?;
    let rows = statement
        .query_map(params![query, scope_digest, limit as i64], |row| {
            Ok(LexicalMatch {
                rowid: row.get(0)?,
                score: row.get(1)?,
            })
        })
        .map_err(sql_error)?
        .collect::<Result<Vec<_>, _>>()
        .map_err(sql_error)?;
    Ok(rows)
}

pub(crate) fn fts_query(query: &str) -> String {
    query
        .split(|character: char| !character.is_alphanumeric() && character != '_')
        .filter(|term| !term.is_empty())
        .take(64)
        .map(|term| format!("\"{}\"", term.replace('"', "\"\"")))
        .collect::<Vec<_>>()
        .join(" ")
}

fn sql_error(source: rusqlite::Error) -> hypermid_contracts::Error {
    error(
        "SEARCH_INDEX_FAILED",
        format!("full-text index operation failed: {source}"),
        EffectState::Unknown,
    )
}
