use hypermid_contracts::Id;
use rusqlite::{params, Transaction};

use crate::fts::remove_memory_record;
use crate::lineage::dependent_descendants;
use crate::provenance::sql_error;
use crate::MemoryResult;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum InvalidationReason {
    ContentEdited,
    Relocated,
    Deleted,
}

impl InvalidationReason {
    fn as_str(self) -> &'static str {
        match self {
            Self::ContentEdited => "content_edited",
            Self::Relocated => "relocated",
            Self::Deleted => "deleted",
        }
    }
}

pub(crate) fn invalidate_record_and_descendants(
    transaction: &Transaction<'_>,
    source_id: &Id,
    now_ms: u64,
    reason: InvalidationReason,
) -> MemoryResult<Vec<Id>> {
    let mut affected = vec![source_id.clone()];
    affected.extend(dependent_descendants(transaction, &[source_id.clone()])?);

    for record_id in &affected {
        remove_derivatives(transaction, record_id)?;
        transaction
            .execute(
                "UPDATE memory_sharing_judgments
                 SET invalidated_at_ms=?2, invalidation_reason=?3
                 WHERE record_id=?1 AND invalidated_at_ms IS NULL",
                params![record_id.as_str(), now_ms, reason.as_str()],
            )
            .map_err(sql_error)?;
        if record_id != source_id {
            transaction
                .execute(
                    "UPDATE memory_records SET status='stale', updated_at_ms=?2
                     WHERE record_id=?1 AND status NOT IN ('tombstoned','archived')",
                    params![record_id.as_str(), now_ms],
                )
                .map_err(sql_error)?;
        }
        transaction
            .execute(
                "UPDATE summary_details SET stale_at_ms=?2 WHERE record_id=?1",
                params![record_id.as_str(), now_ms],
            )
            .map_err(sql_error)?;
    }

    affected.sort();
    affected.dedup();
    Ok(affected)
}

fn remove_derivatives(transaction: &Transaction<'_>, record_id: &Id) -> MemoryResult<()> {
    transaction
        .execute(
            "DELETE FROM memory_embeddings WHERE record_id=?1",
            [record_id.as_str()],
        )
        .map_err(sql_error)?;
    let record: Result<(String, String, String), rusqlite::Error> = transaction.query_row(
        "SELECT r.owner_scope_digest, r.category, v.content
         FROM memory_records r
         JOIN memory_revisions v ON v.record_id=r.record_id AND v.revision=r.current_revision
         WHERE r.record_id=?1",
        [record_id.as_str()],
        |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
    );
    if let Ok((scope, category, content)) = record {
        remove_memory_record(transaction, record_id.as_str(), &scope, &category, &content)?;
    }
    Ok(())
}


pub(crate) fn invalidate_selected_record(tx:&Transaction<'_>,id:&Id,now_ms:u64)->MemoryResult<()> {
 remove_derivatives(tx,id)?;
 tx.execute("UPDATE memory_sharing_judgments SET invalidated_at_ms=?2,invalidation_reason='deleted' WHERE record_id=?1 AND invalidated_at_ms IS NULL",params![id.as_str(),now_ms]).map_err(sql_error)?;
 tx.execute("UPDATE summary_details SET stale_at_ms=?2 WHERE record_id=?1",params![id.as_str(),now_ms]).map_err(sql_error)?;
 Ok(())
}
