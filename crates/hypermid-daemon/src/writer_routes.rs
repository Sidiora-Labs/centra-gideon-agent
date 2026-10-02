use hypermid_context::durable_writer::{
    DurableWriterAuthority, DurableWriterError, QuiescentJournalBarrier, ScopedWriterLease,
    WriterReconciliation,
};
use hypermid_contracts::{EffectState, Error, Id, Scope};
use hypermid_protocol::Envelope;
use hypermid_transport::AuthenticatedSession;
use serde::Deserialize;
use serde_json::{json, Value};
use std::sync::Arc;

pub const WRITER_OPERATIONS: &[&str] = &[
    "writer.acquire",
    "writer.cutover",
    "writer.release",
    "writer.restore",
    "writer.reconcile",
    "writer.status",
];

#[derive(Clone, Debug)]
pub struct WriterRouteResponse {
    pub payload: Option<Value>,
    pub error: Option<Error>,
}

pub struct WriterRoutes {
    authority: Arc<DurableWriterAuthority>,
}

impl WriterRoutes {
    pub fn new(authority: Arc<DurableWriterAuthority>) -> Self {
        Self { authority }
    }

    pub fn authority(&self) -> Arc<DurableWriterAuthority> {
        Arc::clone(&self.authority)
    }

    pub fn handles(operation: &str) -> bool {
        WRITER_OPERATIONS.contains(&operation)
    }

    pub fn dispatch(
        &self,
        session: &AuthenticatedSession,
        envelope: &Envelope,
        now_ms: u64,
    ) -> WriterRouteResponse {
        if envelope.scope.as_ref() != Some(&session.bound_scope) {
            return failure(route_error(
                "SCOPE_DENIED",
                "request scope does not match the authenticated session scope",
                false,
                EffectState::NotStarted,
            ));
        }
        let Some(operation) = envelope.operation.as_deref() else {
            return failure(invalid("writer operation is required"));
        };
        if !Self::handles(operation) {
            return failure(route_error(
                "UNKNOWN_OPERATION",
                "writer operation is not available",
                false,
                EffectState::NotStarted,
            ));
        }
        let payload = envelope.payload.clone().unwrap_or_else(|| json!({}));
        match self.execute(&session.bound_scope, operation, payload, now_ms) {
            Ok(payload) => WriterRouteResponse {
                payload: Some(payload),
                error: None,
            },
            Err(error) => failure(authority_error(error)),
        }
    }

    fn execute(
        &self,
        scope: &Scope,
        operation: &str,
        payload: Value,
        now_ms: u64,
    ) -> Result<Value, DurableWriterError> {
        match operation {
            "writer.acquire" => {
                let payload: AcquirePayload = parse(payload)?;
                encode(self.authority.acquire(
                    scope,
                    payload.request_id,
                    payload.minimum_fence_epoch,
                )?)
            }
            "writer.cutover" => {
                let payload: BarrierPayload = parse(payload)?;
                encode(self.authority.cutover(
                    scope,
                    payload.request_id,
                    &payload.lease,
                    &payload.barrier,
                )?)
            }
            "writer.release" => {
                let payload: BarrierPayload = parse(payload)?;
                encode(self.authority.release(
                    scope,
                    payload.request_id,
                    &payload.lease,
                    &payload.barrier,
                )?)
            }
            "writer.restore" => {
                let payload: BarrierPayload = parse(payload)?;
                encode(self.authority.restore(
                    scope,
                    payload.request_id,
                    &payload.lease,
                    &payload.barrier,
                )?)
            }
            "writer.reconcile" => {
                let payload: ReconcilePayload = parse(payload)?;
                let result = self.authority.reconcile(&payload.request_id)?;
                if result
                    .as_ref()
                    .is_some_and(|effect| effect_scope(effect) != scope)
                {
                    return Err(DurableWriterError::ScopeMismatch);
                }
                encode(result)
            }
            "writer.status" => {
                parse::<EmptyPayload>(payload)?;
                encode(self.authority.status(scope)?)
            }
            _ => unreachable!("writer operation catalog and dispatch remain aligned"),
        }
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct AcquirePayload {
    request_id: Id,
    minimum_fence_epoch: u64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct BarrierPayload {
    request_id: Id,
    lease: ScopedWriterLease,
    barrier: QuiescentJournalBarrier,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ReconcilePayload {
    request_id: Id,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct EmptyPayload {}

fn effect_scope(effect: &WriterReconciliation) -> &Scope {
    match effect {
        WriterReconciliation::Acquire(lease) => &lease.scope,
        WriterReconciliation::Cutover(receipt) => &receipt.lease.scope,
        WriterReconciliation::Release(receipt) | WriterReconciliation::Restore(receipt) => {
            &receipt.scope
        }
    }
}

fn parse<T: for<'de> Deserialize<'de>>(payload: Value) -> Result<T, DurableWriterError> {
    serde_json::from_value(payload).map_err(DurableWriterError::Json)
}

fn encode(value: impl serde::Serialize) -> Result<Value, DurableWriterError> {
    serde_json::to_value(value).map_err(DurableWriterError::Json)
}

fn authority_error(error: DurableWriterError) -> Error {
    match error {
        DurableWriterError::Contended => route_error(
            "WRITER_CONTENDED",
            "another writer holds the scoped authority",
            true,
            EffectState::NotStarted,
        ),
        DurableWriterError::StaleLease => route_error(
            "WRITER_FENCE_STALE",
            "writer lease is stale or superseded",
            false,
            EffectState::NotStarted,
        ),
        DurableWriterError::ScopeMismatch => route_error(
            "SCOPE_DENIED",
            "writer lease scope does not match the authenticated scope",
            false,
            EffectState::NotStarted,
        ),
        DurableWriterError::IdempotencyConflict => route_error(
            "IDEMPOTENCY_CONFLICT",
            "writer request id was reused with different input",
            false,
            EffectState::NotStarted,
        ),
        DurableWriterError::NotQuiescent
        | DurableWriterError::JournalDigestMismatch
        | DurableWriterError::StaleCursor
        | DurableWriterError::CutoverState
        | DurableWriterError::CutoverRequired => route_error(
            "WRITER_CUTOVER_REFUSED",
            "writer cutover barrier is not valid",
            false,
            EffectState::NotStarted,
        ),
        DurableWriterError::InvalidFenceEpoch | DurableWriterError::FenceExhausted => route_error(
            "WRITER_FENCE_INVALID",
            "writer fence epoch is invalid or exhausted",
            false,
            EffectState::NotStarted,
        ),
        DurableWriterError::Json(_) | DurableWriterError::Contract(_) => {
            invalid("writer request payload is invalid")
        }
        DurableWriterError::CorruptState
        | DurableWriterError::Poisoned
        | DurableWriterError::Store(_)
        | DurableWriterError::Sqlite(_) => route_error(
            "WRITER_AUTHORITY_FAILED",
            "writer authority is unavailable",
            true,
            EffectState::Unknown,
        ),
    }
}

fn invalid(message: &str) -> Error {
    route_error("INVALID_REQUEST", message, false, EffectState::NotStarted)
}

fn route_error(code: &str, message: &str, retryable: bool, effect_state: EffectState) -> Error {
    Error::new(code, message, retryable, None, Some(effect_state))
        .expect("static writer route errors satisfy the error contract")
}

fn failure(error: Error) -> WriterRouteResponse {
    WriterRouteResponse {
        payload: None,
        error: Some(error),
    }
}
