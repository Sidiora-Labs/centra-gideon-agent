use hypermid_contracts::{Cursor, Id, Scope};
use hypermid_core::reduction::{
    evaluate_reduction, BoundaryKind, ReductionBoundary, ReductionItem, ReductionRequest,
    ReductionResult, ReductionStatus, ReductionViolation,
};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet, HashMap};

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ReductionRecord {
    pub request: ReductionRequest,
    pub result: ReductionResult,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ReductionState {
    pub schema_version: u32,
    pub records: Vec<ReductionRecord>,
}

#[derive(Clone, Debug, Default)]
pub struct ReductionStore {
    records: HashMap<(Scope, Id, Id), ReductionRecord>,
    applied: HashMap<(Scope, Id), BTreeSet<u64>>,
}

impl ReductionStore {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn submit(
        &mut self,
        request: ReductionRequest,
        committed_cursor: Cursor,
        items: &[ReductionItem],
        boundary: ReductionBoundary,
    ) -> Result<ReductionResult, ReductionStoreError> {
        let key = (
            request.scope.clone(),
            request.session_id.clone(),
            request.idempotency_key.clone(),
        );
        if let Some(existing) = self.records.get(&key) {
            if existing.request != request {
                return Err(ReductionStoreError::IdempotencyConflict);
            }
            return Ok(existing.result.clone());
        }
        if committed_cursor.epoch != request.expected_cursor.epoch
            || committed_cursor.sequence != request.expected_cursor.sequence.saturating_add(1)
        {
            return Err(ReductionStoreError::InvalidCommitCursor);
        }
        let stream = (request.scope.clone(), request.session_id.clone());
        let already_applied = self.applied.entry(stream.clone()).or_default();
        let outcomes = evaluate_reduction(&request, items, &boundary, already_applied)?;
        for outcome in &outcomes {
            if outcome.status == ReductionStatus::Applied {
                already_applied.insert(outcome.tag);
            }
        }
        let result = ReductionResult {
            scope: request.scope.clone(),
            session_id: request.session_id.clone(),
            cursor: committed_cursor,
            idempotency_key: request.idempotency_key.clone(),
            outcomes,
            trace: request.trace.clone(),
            boundary,
        };
        self.records.insert(
            key,
            ReductionRecord {
                request,
                result: result.clone(),
            },
        );
        Ok(result)
    }

    pub fn apply_queued(
        &mut self,
        scope: &Scope,
        session_id: &Id,
        items: &[ReductionItem],
        boundary: ReductionBoundary,
    ) -> Result<Vec<ReductionResult>, ReductionStoreError> {
        if boundary.kind != BoundaryKind::HardBoundary && boundary.kind != BoundaryKind::TailSafe {
            return Err(ReductionStoreError::IncompatibleBoundary);
        }
        let stream = (scope.clone(), session_id.clone());
        let mut applied = self.applied.get(&stream).cloned().unwrap_or_default();
        let mut changed = Vec::new();
        for ((record_scope, record_session, _), record) in self.records.iter_mut() {
            if record_scope != scope || record_session != session_id {
                continue;
            }
            if !record
                .result
                .outcomes
                .iter()
                .any(|outcome| outcome.status == ReductionStatus::Queued)
            {
                continue;
            }
            let reevaluated = evaluate_reduction(&record.request, items, &boundary, &applied)?;
            let mut next_by_tag = BTreeMap::new();
            for outcome in reevaluated {
                next_by_tag.insert(outcome.tag, outcome);
            }
            for current in &mut record.result.outcomes {
                if current.status != ReductionStatus::Queued {
                    continue;
                }
                if let Some(next) = next_by_tag.remove(&current.tag) {
                    if next.status == ReductionStatus::Applied {
                        applied.insert(next.tag);
                    }
                    *current = next;
                }
            }
            record.result.boundary = boundary.clone();
            changed.push(record.result.clone());
        }
        self.applied.insert(stream, applied);
        Ok(changed)
    }

    pub fn state(&self) -> ReductionState {
        let mut records = self.records.values().cloned().collect::<Vec<_>>();
        records.sort_by(|left, right| {
            let left_scope = &left.request.scope;
            let right_scope = &right.request.scope;
            left_scope
                .owner_id
                .cmp(&right_scope.owner_id)
                .then_with(|| left_scope.project_id.cmp(&right_scope.project_id))
                .then_with(|| left_scope.workspace_id.cmp(&right_scope.workspace_id))
                .then_with(|| left.request.session_id.cmp(&right.request.session_id))
                .then_with(|| {
                    left.request
                        .idempotency_key
                        .cmp(&right.request.idempotency_key)
                })
        });
        ReductionState {
            schema_version: 1,
            records,
        }
    }

    pub fn from_state(state: ReductionState) -> Result<Self, ReductionStoreError> {
        if state.schema_version != 1 {
            return Err(ReductionStoreError::UnsupportedState);
        }
        let mut store = Self::new();
        for record in state.records {
            if record.request.scope != record.result.scope
                || record.request.session_id != record.result.session_id
                || record.request.idempotency_key != record.result.idempotency_key
                || record.request.trace != record.result.trace
            {
                return Err(ReductionStoreError::CorruptState);
            }
            record.request.expanded_tags()?;
            let key = (
                record.request.scope.clone(),
                record.request.session_id.clone(),
                record.request.idempotency_key.clone(),
            );
            if store.records.contains_key(&key) {
                return Err(ReductionStoreError::CorruptState);
            }
            for outcome in &record.result.outcomes {
                if outcome.status == ReductionStatus::Applied
                    || outcome.status == ReductionStatus::AlreadyApplied
                {
                    store
                        .applied
                        .entry((
                            record.request.scope.clone(),
                            record.request.session_id.clone(),
                        ))
                        .or_default()
                        .insert(outcome.tag);
                }
            }
            store.records.insert(key, record);
        }
        Ok(store)
    }
}

#[derive(Debug, thiserror::Error)]
pub enum ReductionStoreError {
    #[error("reduction request is invalid: {0}")]
    Reduction(#[from] ReductionViolation),
    #[error("idempotency key was reused for a different reduction")]
    IdempotencyConflict,
    #[error("committed cursor does not immediately follow the expected cursor")]
    InvalidCommitCursor,
    #[error("queued reductions require a compatible cache boundary")]
    IncompatibleBoundary,
    #[error("reduction state version is unsupported")]
    UnsupportedState,
    #[error("reduction state is internally inconsistent")]
    CorruptState,
}
