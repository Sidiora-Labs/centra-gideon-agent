use hypermid_contracts::{Cursor, Id, Scope};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ContextAuthority {
    Previous,
    Hypermid,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct MigrationCheckpoint {
    pub migration_id: Id,
    pub scope: Scope,
    pub session_id: Id,
    pub committed_cursor: Cursor,
    pub authority: ContextAuthority,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct BatchOutcome {
    expected_cursor: Cursor,
    next_cursor: Cursor,
    source_event_ids: Vec<Id>,
}

#[derive(Clone, Debug)]
struct MigrationRun {
    checkpoint: MigrationCheckpoint,
    source_event_ids: BTreeSet<Id>,
    batches: BTreeMap<Id, BatchOutcome>,
}

#[derive(Default)]
pub struct ContextMigrationCoordinator {
    runs: BTreeMap<Id, MigrationRun>,
}

impl ContextMigrationCoordinator {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn begin(
        &mut self,
        migration_id: Id,
        scope: Scope,
        session_id: Id,
        cursor: Cursor,
    ) -> Result<MigrationCheckpoint, ContextMigrationError> {
        if let Some(existing) = self.runs.get(&migration_id) {
            if existing.checkpoint.scope != scope
                || existing.checkpoint.session_id != session_id
                || existing.checkpoint.committed_cursor.epoch != cursor.epoch
                || cursor.sequence > existing.checkpoint.committed_cursor.sequence
            {
                return Err(ContextMigrationError::MigrationConflict);
            }
            return Ok(existing.checkpoint.clone());
        }
        let checkpoint = MigrationCheckpoint {
            migration_id: migration_id.clone(),
            scope,
            session_id,
            committed_cursor: cursor,
            authority: ContextAuthority::Previous,
        };
        self.runs.insert(
            migration_id,
            MigrationRun {
                checkpoint: checkpoint.clone(),
                source_event_ids: BTreeSet::new(),
                batches: BTreeMap::new(),
            },
        );
        Ok(checkpoint)
    }

    pub fn commit_batch(
        &mut self,
        migration_id: &Id,
        batch_id: Id,
        expected_cursor: Cursor,
        next_cursor: Cursor,
        source_event_ids: Vec<Id>,
    ) -> Result<MigrationCheckpoint, ContextMigrationError> {
        let run = self
            .runs
            .get_mut(migration_id)
            .ok_or(ContextMigrationError::MigrationNotFound)?;
        if let Some(existing) = run.batches.get(&batch_id) {
            if existing.expected_cursor != expected_cursor
                || existing.next_cursor != next_cursor
                || existing.source_event_ids != source_event_ids
            {
                return Err(ContextMigrationError::BatchConflict);
            }
            return Ok(run.checkpoint.clone());
        }
        if expected_cursor != run.checkpoint.committed_cursor
            || next_cursor.epoch != expected_cursor.epoch
            || next_cursor.sequence
                != expected_cursor
                    .sequence
                    .checked_add(source_event_ids.len() as u64)
                    .ok_or(ContextMigrationError::InvalidCursor)?
            || source_event_ids.is_empty()
        {
            return Err(ContextMigrationError::InvalidCursor);
        }
        let unique = source_event_ids.iter().cloned().collect::<BTreeSet<_>>();
        if unique.len() != source_event_ids.len()
            || unique
                .iter()
                .any(|source_id| run.source_event_ids.contains(source_id))
        {
            return Err(ContextMigrationError::DuplicateSourceIdentity);
        }
        run.source_event_ids.extend(unique);
        run.batches.insert(
            batch_id,
            BatchOutcome {
                expected_cursor,
                next_cursor,
                source_event_ids,
            },
        );
        run.checkpoint.committed_cursor = next_cursor;
        Ok(run.checkpoint.clone())
    }

    pub fn cutover(
        &mut self,
        migration_id: &Id,
        quiescent: bool,
        validation_passed: bool,
    ) -> Result<MigrationCheckpoint, ContextMigrationError> {
        let run = self
            .runs
            .get_mut(migration_id)
            .ok_or(ContextMigrationError::MigrationNotFound)?;
        if !quiescent {
            return Err(ContextMigrationError::NotQuiescent);
        }
        if !validation_passed {
            return Err(ContextMigrationError::ValidationFailed);
        }
        run.checkpoint.authority = ContextAuthority::Hypermid;
        Ok(run.checkpoint.clone())
    }

    pub fn rollback(
        &mut self,
        migration_id: &Id,
        quiescent: bool,
    ) -> Result<MigrationCheckpoint, ContextMigrationError> {
        let run = self
            .runs
            .get_mut(migration_id)
            .ok_or(ContextMigrationError::MigrationNotFound)?;
        if !quiescent {
            return Err(ContextMigrationError::NotQuiescent);
        }
        run.checkpoint.authority = ContextAuthority::Previous;
        Ok(run.checkpoint.clone())
    }

    pub fn checkpoint(
        &self,
        migration_id: &Id,
    ) -> Result<&MigrationCheckpoint, ContextMigrationError> {
        self.runs
            .get(migration_id)
            .map(|run| &run.checkpoint)
            .ok_or(ContextMigrationError::MigrationNotFound)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, thiserror::Error)]
pub enum ContextMigrationError {
    #[error("migration identity is already bound differently")]
    MigrationConflict,
    #[error("migration was not found")]
    MigrationNotFound,
    #[error("migration batch identity is already bound differently")]
    BatchConflict,
    #[error("migration cursor does not form the next committed range")]
    InvalidCursor,
    #[error("migration source identity is duplicated")]
    DuplicateSourceIdentity,
    #[error("context authority can change only at a quiescent boundary")]
    NotQuiescent,
    #[error("migration validation failed; previous authority remains active")]
    ValidationFailed,
}
