use std::{path::Path, sync::Mutex};

use hypermid_bus::{
    AuthoritativeEffectOutcome, BusError, DurableEffectLedger, EffectRecord, EffectReviewPlan,
    EffectStatus, ProviderEffectProof,
};
use hypermid_contracts::{Digest, EffectState, Error, Id, Scope};
use hypermid_protocol::{Envelope, PrincipalKind};
use hypermid_transport::AuthenticatedSession;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

pub const EFFECT_OPERATIONS: &[&str] = &[
    "effects.list",
    "effects.status",
    "effects.review",
    "effects.reconcile",
];

#[derive(Clone, Debug)]
pub struct EffectRouteResponse {
    pub payload: Option<Value>,
    pub error: Option<Error>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct EffectSnapshot {
    pub effect_id: Id,
    pub module_id: Id,
    pub operation: String,
    pub principal_id: Id,
    pub scope: Scope,
    pub input_digest: Digest,
    pub created_ms: u64,
    pub state: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result_digest: Option<Digest>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub settled_ms: Option<u64>,
    pub reviewable: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub review_plan: Option<EffectReviewPlan>,
}

pub struct EffectRoutes {
    ledger: Mutex<DurableEffectLedger>,
}

impl EffectRoutes {
    pub fn open(path: impl AsRef<Path>, recovered_at_ms: u64) -> Result<Self, BusError> {
        Ok(Self {
            ledger: Mutex::new(DurableEffectLedger::open(path, recovered_at_ms)?),
        })
    }

    pub fn handles(operation: &str) -> bool {
        EFFECT_OPERATIONS.contains(&operation)
    }

    pub fn dispatch(
        &self,
        session: &AuthenticatedSession,
        envelope: &Envelope,
        now_ms: u64,
    ) -> EffectRouteResponse {
        if envelope.scope.as_ref() != Some(&session.bound_scope) {
            return failure(route_error(
                "SCOPE_DENIED",
                "request scope does not match the authenticated session scope",
                false,
                EffectState::NotStarted,
            ));
        }
        let Some(operation) = envelope.operation.as_deref() else {
            return failure(invalid("effect operation is required"));
        };
        if !Self::handles(operation) {
            return failure(route_error(
                "UNKNOWN_OPERATION",
                "effect operation is not available",
                false,
                EffectState::NotStarted,
            ));
        }
        let payload = envelope.payload.clone().unwrap_or_else(|| json!({}));
        match self.execute(&session.bound_scope, operation, payload, now_ms) {
            Ok(payload) => EffectRouteResponse {
                payload: Some(payload),
                error: None,
            },
            Err(RouteFailure::Invalid(message)) => failure(invalid(message)),
            Err(RouteFailure::Ledger(error)) => failure(ledger_error(error)),
            Err(RouteFailure::Unavailable) => failure(route_error(
                "EFFECT_STATE_UNAVAILABLE",
                "durable effect state is unavailable",
                true,
                EffectState::Unknown,
            )),
        }
    }

    pub fn record_provider_proof(
        &self,
        provider: &AuthenticatedSession,
        proof_id: Id,
        effect_id: &Id,
        outcome: AuthoritativeEffectOutcome,
        observed_ms: u64,
    ) -> Result<ProviderEffectProof, BusError> {
        if provider.accepted.principal.kind != PrincipalKind::SupervisedModule {
            return Err(BusError::ProviderIdentityMismatch);
        }
        let provider_id = provider
            .accepted
            .principal
            .module_id
            .clone()
            .ok_or(BusError::ProviderIdentityMismatch)?;
        let mut ledger = self
            .ledger
            .lock()
            .map_err(|_| BusError::ProviderProofMismatch)?;
        let effect = ledger
            .status_scoped(effect_id, &provider.bound_scope)
            .ok_or(BusError::UnknownEffect)?;
        if effect.intent.module_id != provider_id {
            return Err(BusError::ProviderIdentityMismatch);
        }
        ledger.record_provider_proof(proof_id, effect_id, provider_id, outcome, observed_ms)
    }

    fn execute(
        &self,
        scope: &Scope,
        operation: &str,
        payload: Value,
        now_ms: u64,
    ) -> Result<Value, RouteFailure> {
        let mut ledger = self.ledger.lock().map_err(|_| RouteFailure::Unavailable)?;
        match operation {
            "effects.list" => {
                parse::<EmptyPayload>(payload)?;
                let effects = ledger
                    .list_scoped(scope)
                    .into_iter()
                    .filter(|effect| effect.status.is_unsettled())
                    .map(|effect| snapshot(&ledger, &effect, scope))
                    .collect::<Vec<_>>();
                encode(json!({"effects": effects}))
            }
            "effects.status" => {
                let request: EffectIdPayload = parse(payload)?;
                let effect = ledger
                    .status_scoped(&request.effect_id, scope)
                    .ok_or(BusError::UnknownEffect)?;
                encode(snapshot(&ledger, effect, scope))
            }
            "effects.review" => {
                let request: ReviewPayload = parse(payload)?;
                let plan = ledger.review_unknown_current(
                    request.review_id,
                    &request.effect_id,
                    scope,
                    now_ms,
                )?;
                encode(json!({"plan": plan}))
            }
            "effects.reconcile" => {
                let request: ReconcilePayload = parse(payload)?;
                let effect = ledger.reconcile_reviewed(
                    &request.effect_id,
                    scope,
                    &request.review_id,
                    &request.reviewed_plan_digest,
                )?;
                encode(snapshot(&ledger, &effect, scope))
            }
            _ => unreachable!("effect operation catalog and dispatch remain aligned"),
        }
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct EmptyPayload {}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct EffectIdPayload {
    effect_id: Id,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ReviewPayload {
    effect_id: Id,
    review_id: Id,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ReconcilePayload {
    effect_id: Id,
    review_id: Id,
    reviewed_plan_digest: Digest,
}

fn snapshot(ledger: &DurableEffectLedger, effect: &EffectRecord, scope: &Scope) -> EffectSnapshot {
    let (state, result_digest, reason, settled_ms) = match &effect.status {
        EffectStatus::Intent => ("intent", None, None, None),
        EffectStatus::Dispatched => ("dispatched", None, None, None),
        EffectStatus::Committed {
            result_digest,
            settled_ms,
            ..
        } => (
            "committed",
            Some(result_digest.clone()),
            None,
            Some(*settled_ms),
        ),
        EffectStatus::NotStarted { reason, settled_ms } => {
            ("not_started", None, Some(reason.clone()), Some(*settled_ms))
        }
        EffectStatus::Unknown { reason, settled_ms } => {
            ("unknown", None, Some(reason.clone()), Some(*settled_ms))
        }
    };
    EffectSnapshot {
        effect_id: effect.intent.effect_id.clone(),
        module_id: effect.intent.module_id.clone(),
        operation: effect.intent.operation.clone(),
        principal_id: effect.intent.principal.id.clone(),
        scope: effect.intent.scope.clone(),
        input_digest: effect.intent.input_digest.clone(),
        created_ms: effect.intent.created_ms,
        state: state.into(),
        result_digest,
        reason,
        settled_ms,
        reviewable: ledger.reviewable(&effect.intent.effect_id, scope),
        review_plan: ledger
            .review_for_effect(&effect.intent.effect_id, scope)
            .cloned(),
    }
}

#[derive(Debug)]
enum RouteFailure {
    Invalid(&'static str),
    Ledger(BusError),
    Unavailable,
}

impl From<BusError> for RouteFailure {
    fn from(error: BusError) -> Self {
        Self::Ledger(error)
    }
}

fn parse<T: for<'de> Deserialize<'de>>(payload: Value) -> Result<T, RouteFailure> {
    serde_json::from_value(payload).map_err(|_| RouteFailure::Invalid("effect request is invalid"))
}

fn encode(value: impl Serialize) -> Result<Value, RouteFailure> {
    serde_json::to_value(value).map_err(|_| RouteFailure::Unavailable)
}

fn ledger_error(error: BusError) -> Error {
    match error {
        BusError::UnknownEffect => route_error(
            "EFFECT_NOT_FOUND",
            "effect is unavailable in the authenticated scope",
            false,
            EffectState::NotStarted,
        ),
        BusError::EffectNotUnknown => route_error(
            "EFFECT_NOT_UNKNOWN",
            "effect is not awaiting authoritative reconciliation",
            false,
            EffectState::NotStarted,
        ),
        BusError::ProviderProofMissing | BusError::ProviderProofRequired => route_error(
            "PROVIDER_PROOF_NOT_FOUND",
            "authoritative provider status is not available",
            true,
            EffectState::Unknown,
        ),
        BusError::ProviderProofAmbiguous => route_error(
            "PROVIDER_PROOF_AMBIGUOUS",
            "authoritative provider status is conflicting",
            false,
            EffectState::Unknown,
        ),
        BusError::ReviewMissing => route_error(
            "REVIEW_NOT_FOUND",
            "review plan is unavailable",
            false,
            EffectState::Unknown,
        ),
        BusError::ReviewMismatch | BusError::ProviderProofMismatch => route_error(
            "REVIEW_MISMATCH",
            "reviewed plan does not match the durable effect and provider proof",
            false,
            EffectState::Unknown,
        ),
        BusError::DivergentReview
        | BusError::DivergentProviderProof
        | BusError::DivergentEffect => route_error(
            "IDEMPOTENCY_CONFLICT",
            "durable identity was reused with different content",
            false,
            EffectState::Unknown,
        ),
        BusError::ProviderIdentityMismatch => route_error(
            "PROVIDER_IDENTITY_MISMATCH",
            "provider identity does not own this effect",
            false,
            EffectState::Unknown,
        ),
        BusError::InvalidEffectTransition { .. } => route_error(
            "EFFECT_TRANSITION_REFUSED",
            "effect transition is not valid",
            false,
            EffectState::Unknown,
        ),
        BusError::Journal(_) => route_error(
            "EFFECT_STATE_UNAVAILABLE",
            "durable effect state is unavailable",
            true,
            EffectState::Unknown,
        ),
    }
}

fn invalid(message: &str) -> Error {
    route_error("INVALID_REQUEST", message, false, EffectState::NotStarted)
}

fn route_error(code: &str, message: &str, retryable: bool, state: EffectState) -> Error {
    Error::new(code, message, retryable, None, Some(state))
        .expect("static effect route errors satisfy the error contract")
}

fn failure(error: Error) -> EffectRouteResponse {
    EffectRouteResponse {
        payload: None,
        error: Some(error),
    }
}
