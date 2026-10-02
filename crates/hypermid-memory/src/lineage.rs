use std::collections::{BTreeSet, VecDeque};

use hypermid_contracts::{Digest, Id};
use rusqlite::{params, OptionalExtension, Transaction};
use serde::{Deserialize, Serialize};

use crate::provenance::{error, sql_error};
use crate::MemoryResult;

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum LineageRelation {
    DerivedFrom,
    Cites,
    Supersedes,
    Contradicts,
    MergedFrom,
    SplitFrom,
    ImportedFrom,
    Verifies,
}

impl LineageRelation {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::DerivedFrom => "derived_from",
            Self::Cites => "cites",
            Self::Supersedes => "supersedes",
            Self::Contradicts => "contradicts",
            Self::MergedFrom => "merged_from",
            Self::SplitFrom => "split_from",
            Self::ImportedFrom => "imported_from",
            Self::Verifies => "verifies",
        }
    }

    pub const fn is_acyclic(self) -> bool {
        matches!(
            self,
            Self::DerivedFrom | Self::Supersedes | Self::MergedFrom | Self::SplitFrom
        )
    }

    pub const fn invalidates_descendants(self) -> bool {
        matches!(self, Self::DerivedFrom | Self::MergedFrom | Self::SplitFrom)
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct LineageEdge {
    pub parent_id: Id,
    pub relation: LineageRelation,
    pub parent_revision_digest: Digest,
}

pub(crate) fn attach_edges(
    transaction: &Transaction<'_>,
    child_id: &Id,
    child_revision: u64,
    edges: &[LineageEdge],
    created_at_ms: u64,
) -> MemoryResult<()> {
    if edges.len() > 128 {
        return Err(error("INVALID_LINEAGE", "too many lineage edges"));
    }
    for edge in edges {
        let parent_exists: bool = transaction
            .query_row(
                "SELECT EXISTS(
                    SELECT 1 FROM memory_revisions
                    WHERE record_id=?1 AND revision_digest=?2
                 )",
                params![
                    edge.parent_id.as_str(),
                    edge.parent_revision_digest.to_string()
                ],
                |row| row.get(0),
            )
            .map_err(sql_error)?;
        if !parent_exists {
            return Err(error(
                "LINEAGE_PARENT_MISMATCH",
                "lineage parent revision does not exist",
            ));
        }
        if edge.is_cycle(transaction, child_id)? {
            return Err(error("LINEAGE_CYCLE", "lineage would create a cycle"));
        }
        transaction
            .execute(
                "INSERT INTO memory_lineage(
                    child_record_id, child_revision, parent_record_id,
                    parent_revision_digest, relation, created_at_ms
                 ) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                params![
                    child_id.as_str(),
                    child_revision,
                    edge.parent_id.as_str(),
                    edge.parent_revision_digest.to_string(),
                    edge.relation.as_str(),
                    created_at_ms
                ],
            )
            .map_err(sql_error)?;
    }
    Ok(())
}

impl LineageEdge {
    fn is_cycle(&self, transaction: &Transaction<'_>, child_id: &Id) -> MemoryResult<bool> {
        if !self.relation.is_acyclic() {
            return Ok(false);
        }
        if &self.parent_id == child_id {
            return Ok(true);
        }
        let found: Option<i64> = transaction
            .query_row(
                "WITH RECURSIVE ancestors(record_id) AS (
                    SELECT parent_record_id FROM memory_lineage
                    WHERE child_record_id=?1
                      AND relation IN ('derived_from','supersedes','merged_from','split_from')
                    UNION
                    SELECT l.parent_record_id FROM memory_lineage l
                    JOIN ancestors a ON l.child_record_id=a.record_id
                    WHERE l.relation IN ('derived_from','supersedes','merged_from','split_from')
                 ) SELECT 1 FROM ancestors WHERE record_id=?2 LIMIT 1",
                params![self.parent_id.as_str(), child_id.as_str()],
                |row| row.get(0),
            )
            .optional()
            .map_err(sql_error)?;
        Ok(found.is_some())
    }
}

pub(crate) fn dependent_descendants(
    transaction: &Transaction<'_>,
    roots: &[Id],
) -> MemoryResult<Vec<Id>> {
    let mut found = BTreeSet::new();
    let mut queue: VecDeque<Id> = roots.iter().cloned().collect();
    while let Some(parent) = queue.pop_front() {
        let mut statement = transaction
            .prepare(
                "SELECT child_record_id FROM memory_lineage
                 WHERE parent_record_id=?1
                   AND relation IN ('derived_from','merged_from','split_from')
                 ORDER BY child_record_id",
            )
            .map_err(sql_error)?;
        let children = statement
            .query_map([parent.as_str()], |row| row.get::<_, String>(0))
            .map_err(sql_error)?
            .collect::<Result<Vec<_>, _>>()
            .map_err(sql_error)?;
        for child in children {
            let child = Id::new(child)
                .map_err(|_| error("CORRUPT_STORE", "invalid descendant identifier"))?;
            if found.insert(child.clone()) {
                queue.push_back(child);
            }
        }
    }
    Ok(found.into_iter().collect())
}

#[cfg(test)]
mod lineage_tests {
    use super::*;

    #[test]
    fn lineage_relation_identifies_cycle_and_invalidation_edges() {
        assert!(LineageRelation::DerivedFrom.is_acyclic());
        assert!(LineageRelation::DerivedFrom.invalidates_descendants());
        assert!(LineageRelation::Supersedes.is_acyclic());
        assert!(!LineageRelation::Supersedes.invalidates_descendants());
        assert!(!LineageRelation::Contradicts.is_acyclic());
    }
}
