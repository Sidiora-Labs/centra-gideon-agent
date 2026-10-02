use hypermid_contracts::{
    storage::{BackendKind, LeaseKey},
    Cursor, Digest, Id, Scope, MAX_SAFE_INTEGER,
};
use hypermid_store::{Migration, SQLiteStore, StoreError};
use rusqlite::{params, OptionalExtension, Transaction};
use serde::{de::DeserializeOwned, Deserialize, Serialize};
use std::path::Path;
use std::sync::Mutex;
use thiserror::Error;

const AUTHORITY_SCHEMA: &str = r#"
CREATE TABLE writer_scopes (
    scope_key TEXT PRIMARY KEY,
    scope_json TEXT NOT NULL,
    authority TEXT NOT NULL CHECK(authority IN ('gideon', 'hypermid_pending', 'hypermid')),
    authority_epoch INTEGER NOT NULL CHECK(authority_epoch > 0),
    cursor_epoch INTEGER NOT NULL CHECK(cursor_epoch > 0),
    cursor_sequence INTEGER NOT NULL CHECK(cursor_sequence >= 0),
    active_lease_json TEXT,
    journal_digest TEXT
);
CREATE TABLE writer_requests (
    request_id TEXT PRIMARY KEY,
    operation TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    result_json TEXT NOT NULL
);
"#;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ScopedWriterLease {
    pub lease_id: Id,
    pub scope: Scope,
    pub fence_epoch: u64,
    pub fence_token: Id,
    pub cursor: Cursor,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum WriterAuthorityKind {
    Gideon,
    HypermidPending,
    Hypermid,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct QuiescentJournalBarrier {
    pub quiescent: bool,
    pub before_digest: Digest,
    pub after_digest: Digest,
    pub cursor: Cursor,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct CutoverReceipt {
    pub request_id: Id,
    pub lease: ScopedWriterLease,
    pub journal_digest: Digest,
    pub cursor: Cursor,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct GideonRestoreReceipt {
    pub request_id: Id,
    pub scope: Scope,
    pub prior_lease_id: Id,
    pub prior_fence_epoch: u64,
    pub prior_fence_token: Id,
    pub gideon_epoch: u64,
    pub cursor: Cursor,
    pub journal_digest: Digest,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(tag = "operation", content = "result", rename_all = "snake_case")]
pub enum WriterReconciliation {
    Acquire(ScopedWriterLease),
    Cutover(CutoverReceipt),
    Release(GideonRestoreReceipt),
    Restore(GideonRestoreReceipt),
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct WriterAuthorityStatus {
    pub scope: Scope,
    pub authority: WriterAuthorityKind,
    pub authority_epoch: u64,
    pub cursor: Cursor,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub active_lease: Option<ScopedWriterLease>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub journal_digest: Option<Digest>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ValidatedWriterLease {
    pub lease: ScopedWriterLease,
    pub journal_digest: Digest,
}

pub struct DurableWriterAuthority {
    store: Mutex<SQLiteStore>,
}

impl DurableWriterAuthority {
    pub fn open(path: impl AsRef<Path>) -> Result<Self, DurableWriterError> {
        let lease_key = LeaseKey {
            module_id: Id::new("context-writer-authority")?,
            backend: BackendKind::Sqlite,
            scope_key: Id::new("all-scopes")?,
        };
        let migrations = [Migration::new(1, "writer_authority", AUTHORITY_SCHEMA)];
        let store = SQLiteStore::open(path, lease_key, 1, &migrations)?;
        Ok(Self {
            store: Mutex::new(store),
        })
    }

    pub fn acquire(
        &self,
        scope: &Scope,
        request_id: Id,
        minimum_fence_epoch: u64,
    ) -> Result<ScopedWriterLease, DurableWriterError> {
        if minimum_fence_epoch == 0 || minimum_fence_epoch > MAX_SAFE_INTEGER {
            return Err(DurableWriterError::InvalidFenceEpoch);
        }
        let scope_key = scope_key(scope)?;
        let request_digest = digest_request(&("acquire", scope, &request_id, minimum_fence_epoch))?;
        self.transaction(|transaction| {
            if let Some(effect) = replay_request::<WriterReconciliation>(
                transaction,
                &request_id,
                "acquire",
                &scope_key,
                request_digest,
            )? {
                return match effect {
                    WriterReconciliation::Acquire(lease) => Ok(lease),
                    _ => Err(DurableWriterError::IdempotencyConflict),
                };
            }
            let state = load_or_create_scope(transaction, &scope_key, scope)?;
            if state.active_lease.is_some() || state.authority != WriterAuthorityKind::Gideon {
                return Err(DurableWriterError::Contended);
            }
            let next_epoch = state
                .authority_epoch
                .checked_add(1)
                .ok_or(DurableWriterError::FenceExhausted)?
                .max(minimum_fence_epoch);
            if next_epoch > MAX_SAFE_INTEGER {
                return Err(DurableWriterError::FenceExhausted);
            }
            let lease_material = serde_json::to_vec(&(
                scope,
                &request_id,
                next_epoch,
                state.cursor,
            ))?;
            let lease_digest = Digest::sha256(lease_material);
            let lease = ScopedWriterLease {
                lease_id: Id::new(format!("writer-lease:{lease_digest}"))?,
                scope: scope.clone(),
                fence_epoch: next_epoch,
                fence_token: Id::new(format!("writer-fence:{lease_digest}"))?,
                cursor: state.cursor,
            };
            let lease_json = serde_json::to_string(&lease)?;
            transaction.execute(
                "UPDATE writer_scopes SET authority='hypermid_pending', authority_epoch=?2, active_lease_json=?3, journal_digest=NULL WHERE scope_key=?1",
                params![scope_key.as_str(), next_epoch, lease_json],
            )?;
            let effect = WriterReconciliation::Acquire(lease.clone());
            record_request(
                transaction,
                &request_id,
                "acquire",
                &scope_key,
                request_digest,
                &effect,
            )?;
            Ok(lease)
        })
    }

    pub fn cutover(
        &self,
        scope: &Scope,
        request_id: Id,
        lease: &ScopedWriterLease,
        barrier: &QuiescentJournalBarrier,
    ) -> Result<CutoverReceipt, DurableWriterError> {
        validate_barrier(barrier)?;
        require_scope(scope, lease)?;
        let scope_key = scope_key(scope)?;
        let request_digest = digest_request(&("cutover", scope, &request_id, lease, barrier))?;
        self.transaction(|transaction| {
            if let Some(effect) = replay_request::<WriterReconciliation>(
                transaction,
                &request_id,
                "cutover",
                &scope_key,
                request_digest,
            )? {
                return match effect {
                    WriterReconciliation::Cutover(receipt) => Ok(receipt),
                    _ => Err(DurableWriterError::IdempotencyConflict),
                };
            }
            let state = load_scope(transaction, &scope_key, scope)?
                .ok_or(DurableWriterError::StaleLease)?;
            if state.authority != WriterAuthorityKind::HypermidPending {
                return Err(DurableWriterError::CutoverState);
            }
            require_active(&state, lease)?;
            if barrier.cursor < lease.cursor {
                return Err(DurableWriterError::StaleCursor);
            }
            let mut activated = lease.clone();
            activated.cursor = barrier.cursor;
            transaction.execute(
                "UPDATE writer_scopes SET authority='hypermid', cursor_epoch=?2, cursor_sequence=?3, active_lease_json=?4, journal_digest=?5 WHERE scope_key=?1",
                params![
                    scope_key.as_str(),
                    barrier.cursor.epoch,
                    barrier.cursor.sequence,
                    serde_json::to_string(&activated)?,
                    barrier.after_digest.to_string(),
                ],
            )?;
            let receipt = CutoverReceipt {
                request_id: request_id.clone(),
                lease: activated,
                journal_digest: barrier.after_digest,
                cursor: barrier.cursor,
            };
            record_request(
                transaction,
                &request_id,
                "cutover",
                &scope_key,
                request_digest,
                &WriterReconciliation::Cutover(receipt.clone()),
            )?;
            Ok(receipt)
        })
    }

    pub fn release(
        &self,
        scope: &Scope,
        request_id: Id,
        lease: &ScopedWriterLease,
        barrier: &QuiescentJournalBarrier,
    ) -> Result<GideonRestoreReceipt, DurableWriterError> {
        self.restore_operation("release", scope, request_id, lease, barrier)
    }

    pub fn restore(
        &self,
        scope: &Scope,
        request_id: Id,
        lease: &ScopedWriterLease,
        barrier: &QuiescentJournalBarrier,
    ) -> Result<GideonRestoreReceipt, DurableWriterError> {
        self.restore_operation("restore", scope, request_id, lease, barrier)
    }

    pub fn validate(
        &self,
        scope: &Scope,
        lease: &ScopedWriterLease,
        _now_ms: u64,
    ) -> Result<ValidatedWriterLease, DurableWriterError> {
        require_scope(scope, lease)?;
        let scope_key = scope_key(scope)?;
        self.transaction(|transaction| {
            let state = load_scope(transaction, &scope_key, scope)?
                .ok_or(DurableWriterError::StaleLease)?;
            if state.authority != WriterAuthorityKind::Hypermid {
                return Err(DurableWriterError::CutoverRequired);
            }
            require_active(&state, lease)?;
            let journal_digest = state
                .journal_digest
                .ok_or(DurableWriterError::CutoverRequired)?;
            Ok(ValidatedWriterLease {
                lease: lease.clone(),
                journal_digest,
            })
        })
    }

    pub fn status(&self, scope: &Scope) -> Result<WriterAuthorityStatus, DurableWriterError> {
        let scope_key = scope_key(scope)?;
        self.transaction(|transaction| {
            Ok(load_scope(transaction, &scope_key, scope)?
                .map(StoredScope::status)
                .unwrap_or_else(|| initial_status(scope.clone())))
        })
    }

    pub fn reconcile(
        &self,
        request_id: &Id,
    ) -> Result<Option<WriterReconciliation>, DurableWriterError> {
        self.transaction(|transaction| {
            transaction
                .query_row(
                    "SELECT result_json FROM writer_requests WHERE request_id=?1",
                    params![request_id.as_str()],
                    |row| row.get::<_, String>(0),
                )
                .optional()?
                .map(|encoded| serde_json::from_str(&encoded).map_err(DurableWriterError::Json))
                .transpose()
        })
    }

    fn restore_operation(
        &self,
        operation: &'static str,
        scope: &Scope,
        request_id: Id,
        lease: &ScopedWriterLease,
        barrier: &QuiescentJournalBarrier,
    ) -> Result<GideonRestoreReceipt, DurableWriterError> {
        validate_barrier(barrier)?;
        require_scope(scope, lease)?;
        let scope_key = scope_key(scope)?;
        let request_digest = digest_request(&(operation, scope, &request_id, lease, barrier))?;
        self.transaction(|transaction| {
            if let Some(effect) = replay_request::<WriterReconciliation>(
                transaction,
                &request_id,
                operation,
                &scope_key,
                request_digest,
            )? {
                return match (operation, effect) {
                    ("release", WriterReconciliation::Release(receipt))
                    | ("restore", WriterReconciliation::Restore(receipt)) => Ok(receipt),
                    _ => Err(DurableWriterError::IdempotencyConflict),
                };
            }
            let state = load_scope(transaction, &scope_key, scope)?
                .ok_or(DurableWriterError::StaleLease)?;
            if !matches!(
                state.authority,
                WriterAuthorityKind::Hypermid | WriterAuthorityKind::HypermidPending
            ) {
                return Err(DurableWriterError::StaleLease);
            }
            require_active(&state, lease)?;
            if barrier.cursor < lease.cursor {
                return Err(DurableWriterError::StaleCursor);
            }
            let gideon_epoch = state
                .authority_epoch
                .checked_add(1)
                .filter(|epoch| *epoch <= MAX_SAFE_INTEGER)
                .ok_or(DurableWriterError::FenceExhausted)?;
            transaction.execute(
                "UPDATE writer_scopes SET authority='gideon', authority_epoch=?2, cursor_epoch=?3, cursor_sequence=?4, active_lease_json=NULL, journal_digest=?5 WHERE scope_key=?1",
                params![
                    scope_key.as_str(),
                    gideon_epoch,
                    barrier.cursor.epoch,
                    barrier.cursor.sequence,
                    barrier.after_digest.to_string(),
                ],
            )?;
            let receipt = GideonRestoreReceipt {
                request_id: request_id.clone(),
                scope: scope.clone(),
                prior_lease_id: lease.lease_id.clone(),
                prior_fence_epoch: lease.fence_epoch,
                prior_fence_token: lease.fence_token.clone(),
                gideon_epoch,
                cursor: barrier.cursor,
                journal_digest: barrier.after_digest,
            };
            let effect = if operation == "release" {
                WriterReconciliation::Release(receipt.clone())
            } else {
                WriterReconciliation::Restore(receipt.clone())
            };
            record_request(
                transaction,
                &request_id,
                operation,
                &scope_key,
                request_digest,
                &effect,
            )?;
            Ok(receipt)
        })
    }

    fn transaction<T>(
        &self,
        operation: impl FnOnce(&Transaction<'_>) -> Result<T, DurableWriterError>,
    ) -> Result<T, DurableWriterError> {
        let mut store = self
            .store
            .lock()
            .map_err(|_| DurableWriterError::Poisoned)?;
        let fence = store.current_fence().clone();
        store
            .with_fenced_transaction(&fence, operation)
            .map_err(DurableWriterError::Store)?
    }
}

#[derive(Clone)]
struct StoredScope {
    scope: Scope,
    authority: WriterAuthorityKind,
    authority_epoch: u64,
    cursor: Cursor,
    active_lease: Option<ScopedWriterLease>,
    journal_digest: Option<Digest>,
}

impl StoredScope {
    fn status(self) -> WriterAuthorityStatus {
        WriterAuthorityStatus {
            scope: self.scope,
            authority: self.authority,
            authority_epoch: self.authority_epoch,
            cursor: self.cursor,
            active_lease: self.active_lease,
            journal_digest: self.journal_digest,
        }
    }
}

fn load_or_create_scope(
    transaction: &Transaction<'_>,
    scope_key: &Id,
    scope: &Scope,
) -> Result<StoredScope, DurableWriterError> {
    if let Some(state) = load_scope(transaction, scope_key, scope)? {
        return Ok(state);
    }
    let cursor = Cursor::new(1, 0)?;
    transaction.execute(
        "INSERT INTO writer_scopes(scope_key,scope_json,authority,authority_epoch,cursor_epoch,cursor_sequence) VALUES (?1,?2,'gideon',1,?3,?4)",
        params![
            scope_key.as_str(),
            serde_json::to_string(scope)?,
            cursor.epoch,
            cursor.sequence,
        ],
    )?;
    Ok(StoredScope {
        scope: scope.clone(),
        authority: WriterAuthorityKind::Gideon,
        authority_epoch: 1,
        cursor,
        active_lease: None,
        journal_digest: None,
    })
}

fn load_scope(
    transaction: &Transaction<'_>,
    scope_key: &Id,
    scope: &Scope,
) -> Result<Option<StoredScope>, DurableWriterError> {
    load_scope_query(transaction, scope_key, scope)
}

fn load_scope_query(
    connection: &rusqlite::Connection,
    scope_key: &Id,
    scope: &Scope,
) -> Result<Option<StoredScope>, DurableWriterError> {
    let row = connection
        .query_row(
            "SELECT scope_json,authority,authority_epoch,cursor_epoch,cursor_sequence,active_lease_json,journal_digest FROM writer_scopes WHERE scope_key=?1",
            params![scope_key.as_str()],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, u64>(2)?,
                    row.get::<_, u64>(3)?,
                    row.get::<_, u64>(4)?,
                    row.get::<_, Option<String>>(5)?,
                    row.get::<_, Option<String>>(6)?,
                ))
            },
        )
        .optional()?;
    let Some((
        scope_json,
        authority,
        authority_epoch,
        cursor_epoch,
        cursor_sequence,
        lease,
        digest,
    )) = row
    else {
        return Ok(None);
    };
    let stored_scope: Scope = serde_json::from_str(&scope_json)?;
    if stored_scope != *scope {
        return Err(DurableWriterError::ScopeMismatch);
    }
    Ok(Some(StoredScope {
        scope: stored_scope,
        authority: parse_authority(&authority)?,
        authority_epoch,
        cursor: Cursor::new(cursor_epoch, cursor_sequence)?,
        active_lease: lease
            .map(|value| serde_json::from_str(&value))
            .transpose()?,
        journal_digest: digest.map(|value| value.parse()).transpose()?,
    }))
}

fn require_active(
    state: &StoredScope,
    supplied: &ScopedWriterLease,
) -> Result<(), DurableWriterError> {
    if state.active_lease.as_ref() != Some(supplied)
        || state.authority_epoch != supplied.fence_epoch
    {
        return Err(DurableWriterError::StaleLease);
    }
    Ok(())
}

fn require_scope(scope: &Scope, lease: &ScopedWriterLease) -> Result<(), DurableWriterError> {
    if &lease.scope != scope {
        return Err(DurableWriterError::ScopeMismatch);
    }
    Ok(())
}

fn validate_barrier(barrier: &QuiescentJournalBarrier) -> Result<(), DurableWriterError> {
    if !barrier.quiescent {
        return Err(DurableWriterError::NotQuiescent);
    }
    if barrier.before_digest != barrier.after_digest {
        return Err(DurableWriterError::JournalDigestMismatch);
    }
    Ok(())
}

fn replay_request<T: DeserializeOwned>(
    transaction: &Transaction<'_>,
    request_id: &Id,
    operation: &str,
    scope_key: &Id,
    request_digest: Digest,
) -> Result<Option<T>, DurableWriterError> {
    let recorded = transaction
        .query_row(
            "SELECT operation,scope_key,request_digest,result_json FROM writer_requests WHERE request_id=?1",
            params![request_id.as_str()],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?,
                ))
            },
        )
        .optional()?;
    let Some((stored_operation, stored_scope, stored_digest, result)) = recorded else {
        return Ok(None);
    };
    if stored_operation != operation
        || stored_scope != scope_key.as_str()
        || stored_digest != request_digest.to_string()
    {
        return Err(DurableWriterError::IdempotencyConflict);
    }
    Ok(Some(serde_json::from_str(&result)?))
}

fn record_request<T: Serialize>(
    transaction: &Transaction<'_>,
    request_id: &Id,
    operation: &str,
    scope_key: &Id,
    request_digest: Digest,
    result: &T,
) -> Result<(), DurableWriterError> {
    transaction.execute(
        "INSERT INTO writer_requests(request_id,operation,scope_key,request_digest,result_json) VALUES (?1,?2,?3,?4,?5)",
        params![
            request_id.as_str(),
            operation,
            scope_key.as_str(),
            request_digest.to_string(),
            serde_json::to_string(result)?,
        ],
    )?;
    Ok(())
}

fn initial_status(scope: Scope) -> WriterAuthorityStatus {
    WriterAuthorityStatus {
        scope,
        authority: WriterAuthorityKind::Gideon,
        authority_epoch: 1,
        cursor: Cursor::new(1, 0).expect("the initial cursor is valid"),
        active_lease: None,
        journal_digest: None,
    }
}

fn parse_authority(value: &str) -> Result<WriterAuthorityKind, DurableWriterError> {
    match value {
        "gideon" => Ok(WriterAuthorityKind::Gideon),
        "hypermid_pending" => Ok(WriterAuthorityKind::HypermidPending),
        "hypermid" => Ok(WriterAuthorityKind::Hypermid),
        _ => Err(DurableWriterError::CorruptState),
    }
}

fn scope_key(scope: &Scope) -> Result<Id, DurableWriterError> {
    Ok(Id::new(format!(
        "writer-scope:{}",
        Digest::sha256(serde_json::to_vec(scope)?)
    ))?)
}

fn digest_request(value: &impl Serialize) -> Result<Digest, DurableWriterError> {
    Ok(Digest::sha256(serde_json::to_vec(value)?))
}

#[derive(Debug, Error)]
pub enum DurableWriterError {
    #[error("writer authority is held by another live process or writer")]
    Contended,
    #[error("writer fence epoch is invalid")]
    InvalidFenceEpoch,
    #[error("writer fence epoch is exhausted")]
    FenceExhausted,
    #[error("writer lease is stale or superseded")]
    StaleLease,
    #[error("writer lease scope does not match")]
    ScopeMismatch,
    #[error("writer request id was reused with different input")]
    IdempotencyConflict,
    #[error("context authority cutover is not quiescent")]
    NotQuiescent,
    #[error("context authority journal digest changed during cutover")]
    JournalDigestMismatch,
    #[error("context authority cursor moved backwards")]
    StaleCursor,
    #[error("context authority is in the wrong cutover state")]
    CutoverState,
    #[error("context authority cutover has not committed")]
    CutoverRequired,
    #[error("writer authority state is corrupt")]
    CorruptState,
    #[error("writer authority lock is poisoned")]
    Poisoned,
    #[error(transparent)]
    Store(#[from] StoreError),
    #[error("writer authority SQLite operation failed: {0}")]
    Sqlite(#[from] rusqlite::Error),
    #[error("writer authority serialization failed: {0}")]
    Json(#[from] serde_json::Error),
    #[error("writer authority contract failed: {0}")]
    Contract(#[from] hypermid_contracts::ContractViolation),
}
