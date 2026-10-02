use hypermid_contracts::{Cursor, Id, Scope};
use hypermid_core::protocol::WriterLease;
use serde_json::Value;
use std::collections::HashMap;

const MAX_TIMESTAMP_MS: u64 = 253_402_300_799_999;

#[derive(Clone, Debug, PartialEq)]
pub struct MutationOutcome {
    pub operation: String,
    pub cursor: Cursor,
    pub result: Value,
    pub replayed: bool,
}

#[derive(Clone, Debug)]
struct StoredMutation {
    operation: String,
    cursor: Cursor,
    result: Value,
}

#[derive(Clone, Debug)]
struct ActiveWriter {
    lease: WriterLease,
    expires_at_ms: u64,
}

#[derive(Clone, Debug)]
pub struct SessionState {
    pub session_id: Id,
    pub scope: Scope,
    pub cursor: Cursor,
    pub fence_epoch: u64,
    pub active_lease: Option<WriterLease>,
    mutations: HashMap<Id, StoredMutation>,
    writer: Option<ActiveWriter>,
}

#[derive(Default)]
pub struct ContextEngine {
    sessions: HashMap<Id, SessionState>,
}

impl ContextEngine {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn bind(&mut self, session_id: Id, scope: Scope) -> Result<SessionState, EngineError> {
        if let Some(state) = self.sessions.get(&session_id) {
            require_scope(state, &scope)?;
            return Ok(state.clone());
        }
        let state = SessionState {
            session_id: session_id.clone(),
            scope,
            cursor: Cursor::new(1, 0).map_err(|_| EngineError::CursorOverflow)?,
            fence_epoch: 0,
            active_lease: None,
            mutations: HashMap::new(),
            writer: None,
        };
        self.sessions.insert(session_id, state.clone());
        Ok(state)
    }

    pub fn state(&self, session_id: &Id, scope: &Scope) -> Result<SessionState, EngineError> {
        let state = self
            .sessions
            .get(session_id)
            .ok_or(EngineError::SessionNotBound)?;
        require_scope(state, scope)?;
        Ok(state.clone())
    }

    pub fn acquire_writer(
        &mut self,
        session_id: &Id,
        scope: &Scope,
        lease_id: Id,
        now_ms: u64,
        ttl_ms: u64,
    ) -> Result<WriterLease, EngineError> {
        if ttl_ms == 0 {
            return Err(EngineError::InvalidLease);
        }
        let expires_at_ms = now_ms
            .checked_add(ttl_ms)
            .filter(|value| *value <= MAX_TIMESTAMP_MS)
            .ok_or(EngineError::InvalidLease)?;
        let state = self
            .sessions
            .get_mut(session_id)
            .ok_or(EngineError::SessionNotBound)?;
        require_scope(state, scope)?;
        state.fence_epoch = state
            .fence_epoch
            .checked_add(1)
            .ok_or(EngineError::FenceOverflow)?;
        let fence_token = Id::new(format!("fence:{}", state.fence_epoch))
            .map_err(|_| EngineError::FenceOverflow)?;
        let lease = WriterLease {
            lease_id,
            session_id: session_id.clone(),
            scope: scope.clone(),
            fence_token,
            acquired_at: format_timestamp(now_ms)?,
            expires_at: format_timestamp(expires_at_ms)?,
            cursor: state.cursor,
        };
        state.active_lease = Some(lease.clone());
        state.writer = Some(ActiveWriter {
            lease: lease.clone(),
            expires_at_ms,
        });
        Ok(lease)
    }

    #[allow(clippy::too_many_arguments)]
    pub fn commit(
        &mut self,
        session_id: &Id,
        scope: &Scope,
        expected_cursor: Cursor,
        lease: &WriterLease,
        idempotency_key: Id,
        operation: impl Into<String>,
        result: Value,
        now_ms: u64,
    ) -> Result<MutationOutcome, EngineError> {
        let operation = operation.into();
        let state = self
            .sessions
            .get_mut(session_id)
            .ok_or(EngineError::SessionNotBound)?;
        require_scope(state, scope)?;
        if let Some(previous) = state.mutations.get(&idempotency_key) {
            if previous.operation != operation {
                return Err(EngineError::IdempotencyConflict);
            }
            return Ok(MutationOutcome {
                operation: previous.operation.clone(),
                cursor: previous.cursor,
                result: previous.result.clone(),
                replayed: true,
            });
        }
        require_writer(state, lease, now_ms)?;
        if expected_cursor != state.cursor {
            return Err(EngineError::StaleCursor);
        }
        let next_cursor = state
            .cursor
            .next()
            .map_err(|_| EngineError::CursorOverflow)?;
        let stored = StoredMutation {
            operation: operation.clone(),
            cursor: next_cursor,
            result: result.clone(),
        };
        state.cursor = next_cursor;
        state.mutations.insert(idempotency_key, stored);
        if let Some(writer) = state.writer.as_mut() {
            writer.lease.cursor = next_cursor;
            state.active_lease = Some(writer.lease.clone());
        }
        Ok(MutationOutcome {
            operation,
            cursor: next_cursor,
            result,
            replayed: false,
        })
    }

    #[allow(clippy::too_many_arguments)]
    pub fn rebind_workspace(
        &mut self,
        session_id: &Id,
        previous_scope: &Scope,
        next_scope: Scope,
        expected_cursor: Cursor,
        lease: &WriterLease,
        idempotency_key: Id,
        now_ms: u64,
    ) -> Result<MutationOutcome, EngineError> {
        if previous_scope.owner_id != next_scope.owner_id
            || previous_scope.project_id != next_scope.project_id
        {
            return Err(EngineError::IdentityChangeRequiresNewSession);
        }
        if let Some(state) = self.sessions.get(session_id) {
            if let Some(previous) = state.mutations.get(&idempotency_key) {
                if previous.operation != "rebind" || state.scope != next_scope {
                    return Err(EngineError::IdempotencyConflict);
                }
                return Ok(MutationOutcome {
                    operation: previous.operation.clone(),
                    cursor: previous.cursor,
                    result: previous.result.clone(),
                    replayed: true,
                });
            }
        }
        let result = serde_json::json!({
            "previous_scope": previous_scope,
            "next_scope": &next_scope,
        });
        let outcome = self.commit(
            session_id,
            previous_scope,
            expected_cursor,
            lease,
            idempotency_key,
            "rebind",
            result,
            now_ms,
        )?;
        let state = self
            .sessions
            .get_mut(session_id)
            .ok_or(EngineError::SessionNotBound)?;
        state.scope = next_scope.clone();
        if let Some(writer) = state.writer.as_mut() {
            writer.lease.scope = next_scope;
            state.active_lease = Some(writer.lease.clone());
        }
        Ok(outcome)
    }
}

fn require_scope(state: &SessionState, scope: &Scope) -> Result<(), EngineError> {
    if &state.scope == scope {
        Ok(())
    } else {
        Err(EngineError::ScopeMismatch)
    }
}

fn require_writer(
    state: &SessionState,
    supplied: &WriterLease,
    now_ms: u64,
) -> Result<(), EngineError> {
    let active = state
        .writer
        .as_ref()
        .ok_or(EngineError::WriterLeaseRequired)?;
    if active.lease.lease_id != supplied.lease_id
        || active.lease.fence_token != supplied.fence_token
        || supplied.session_id != state.session_id
        || supplied.scope != state.scope
    {
        return Err(EngineError::StaleFence);
    }
    if now_ms >= active.expires_at_ms {
        return Err(EngineError::LeaseExpired);
    }
    Ok(())
}

fn format_timestamp(epoch_ms: u64) -> Result<String, EngineError> {
    if epoch_ms > MAX_TIMESTAMP_MS {
        return Err(EngineError::InvalidLease);
    }
    let seconds = epoch_ms / 1_000;
    let millis = epoch_ms % 1_000;
    let days = seconds / 86_400;
    let day_seconds = seconds % 86_400;
    let (year, month, day) = civil_from_days(days as i64);
    let hour = day_seconds / 3_600;
    let minute = (day_seconds % 3_600) / 60;
    let second = day_seconds % 60;
    Ok(format!(
        "{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}.{millis:03}Z"
    ))
}

fn civil_from_days(days_since_epoch: i64) -> (i64, i64, i64) {
    let z = days_since_epoch + 719_468;
    let era = z / 146_097;
    let day_of_era = z - era * 146_097;
    let year_of_era =
        (day_of_era - day_of_era / 1_460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let mut year = year_of_era + era * 400;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let month_prime = (5 * day_of_year + 2) / 153;
    let day = day_of_year - (153 * month_prime + 2) / 5 + 1;
    let month = month_prime + if month_prime < 10 { 3 } else { -9 };
    if month <= 2 {
        year += 1;
    }
    (year, month, day)
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, thiserror::Error)]
pub enum EngineError {
    #[error("session is not bound")]
    SessionNotBound,
    #[error("scope does not own this context session")]
    ScopeMismatch,
    #[error("writer lease is required")]
    WriterLeaseRequired,
    #[error("writer lease was superseded")]
    StaleFence,
    #[error("writer lease has expired")]
    LeaseExpired,
    #[error("expected cursor is stale")]
    StaleCursor,
    #[error("idempotency key was used for another operation")]
    IdempotencyConflict,
    #[error("owner or project changes require a new context identity")]
    IdentityChangeRequiresNewSession,
    #[error("writer lease is invalid")]
    InvalidLease,
    #[error("writer fence counter overflowed")]
    FenceOverflow,
    #[error("context cursor overflowed")]
    CursorOverflow,
}
