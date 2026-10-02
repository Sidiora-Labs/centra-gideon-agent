use hypermid_contracts::{Cursor, Digest, Id, Scope, Trace, MAX_SAFE_INTEGER};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

pub const MAX_RECENT_OUTCOMES: usize = 256;
pub const MAX_DIAGNOSTIC_IDENTITIES: usize = 100_000;
pub const MAX_TOKEN_COUNT: u64 = 1_000_000_000;
pub const MAX_QUEUE_DEPTH: u64 = 1_000_000;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ContextMode {
    Off,
    PassThrough,
    Shadow,
    Primary,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum OverflowPolicy {
    ReclaimThenRefuse,
    RefuseImmediately,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RefusalPolicy {
    Refuse,
    CompatibleLastKnownGood,
    HostPassthrough,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PressureBand {
    Normal,
    Advisory,
    Action,
    Emergency,
    HardWall,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum BudgetConfidence {
    Measured,
    Calibrated,
    Conservative,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RegionKind {
    Baseline,
    Delta,
    Tail,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum UsageState {
    NoCall,
    Missing,
    ReportedZero,
    Reported,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct UsageAccounting {
    pub state: UsageState,
    pub input_tokens: Option<u64>,
    pub output_tokens: Option<u64>,
    pub cache_read_tokens: Option<u64>,
    pub cache_write_tokens: Option<u64>,
}

impl UsageAccounting {
    pub fn no_call() -> Self {
        Self::empty(UsageState::NoCall)
    }

    pub fn missing() -> Self {
        Self::empty(UsageState::Missing)
    }

    pub fn reported_zero() -> Self {
        Self {
            state: UsageState::ReportedZero,
            input_tokens: Some(0),
            output_tokens: Some(0),
            cache_read_tokens: Some(0),
            cache_write_tokens: Some(0),
        }
    }

    pub fn reported(
        input_tokens: u64,
        output_tokens: u64,
        cache_read_tokens: u64,
        cache_write_tokens: u64,
    ) -> Result<Self, DiagnosticViolation> {
        let value = Self {
            state: UsageState::Reported,
            input_tokens: Some(input_tokens),
            output_tokens: Some(output_tokens),
            cache_read_tokens: Some(cache_read_tokens),
            cache_write_tokens: Some(cache_write_tokens),
        };
        value.validate()?;
        if input_tokens == 0
            && output_tokens == 0
            && cache_read_tokens == 0
            && cache_write_tokens == 0
        {
            return Err(DiagnosticViolation::UsageStateMismatch);
        }
        Ok(value)
    }

    pub fn validate(&self) -> Result<(), DiagnosticViolation> {
        let values = [
            self.input_tokens,
            self.output_tokens,
            self.cache_read_tokens,
            self.cache_write_tokens,
        ];
        if values
            .iter()
            .flatten()
            .any(|value| *value > MAX_TOKEN_COUNT)
        {
            return Err(DiagnosticViolation::TokenCountTooLarge);
        }
        match self.state {
            UsageState::NoCall | UsageState::Missing if values.iter().all(Option::is_none) => {
                Ok(())
            }
            UsageState::ReportedZero if values.iter().all(|value| *value == Some(0)) => Ok(()),
            UsageState::Reported
                if values.iter().all(Option::is_some)
                    && values.iter().flatten().any(|value| *value > 0) =>
            {
                Ok(())
            }
            _ => Err(DiagnosticViolation::UsageStateMismatch),
        }
    }

    fn empty(state: UsageState) -> Self {
        Self {
            state,
            input_tokens: None,
            output_tokens: None,
            cache_read_tokens: None,
            cache_write_tokens: None,
        }
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ModelBudgetStatus {
    pub context_window_tokens: u64,
    pub reserved_output_tokens: u64,
    pub max_input_tokens: u64,
    pub max_items: u64,
    pub max_images: u64,
    pub baseline_tokens: u64,
    pub delta_tokens: u64,
    pub tail_tokens: u64,
    pub confidence: BudgetConfidence,
}

impl ModelBudgetStatus {
    pub fn validate(&self) -> Result<(), DiagnosticViolation> {
        let tokens = [
            self.context_window_tokens,
            self.reserved_output_tokens,
            self.max_input_tokens,
            self.baseline_tokens,
            self.delta_tokens,
            self.tail_tokens,
        ];
        if self.context_window_tokens == 0
            || self.max_items == 0
            || self.max_items > MAX_QUEUE_DEPTH
            || self.max_images > 4_096
            || tokens.iter().any(|value| *value > MAX_TOKEN_COUNT)
            || self.reserved_output_tokens > self.context_window_tokens
            || self.max_input_tokens > self.context_window_tokens - self.reserved_output_tokens
            || self.baseline_tokens + self.delta_tokens + self.tail_tokens > self.max_input_tokens
        {
            return Err(DiagnosticViolation::InvalidBudget);
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ProjectionRegionStatus {
    pub kind: RegionKind,
    pub digest: Digest,
    pub item_ids: Vec<Id>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub summary_ids: Vec<Id>,
    pub token_mass: u64,
}

impl ProjectionRegionStatus {
    fn validate(&self, expected: RegionKind) -> Result<(), DiagnosticViolation> {
        if self.kind != expected
            || self.item_ids.len() > MAX_DIAGNOSTIC_IDENTITIES
            || self.summary_ids.len() > MAX_DIAGNOSTIC_IDENTITIES
            || self.token_mass > MAX_TOKEN_COUNT
        {
            return Err(DiagnosticViolation::InvalidRegion);
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct WriterLeaseStatus {
    pub lease_id: Id,
    pub session_id: Id,
    pub scope: Scope,
    pub fence_token: Id,
    pub acquired_at: String,
    pub expires_at: String,
    pub cursor: Cursor,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct DiagnosticOutcome {
    pub at: String,
    pub reason_code: String,
    pub trace: Trace,
    pub message: String,
}

impl DiagnosticOutcome {
    pub fn redacted(
        at: impl Into<String>,
        reason_code: impl Into<String>,
        trace: Trace,
        message: impl AsRef<str>,
    ) -> Result<Self, DiagnosticViolation> {
        let reason_code = reason_code.into();
        validate_reason_code(&reason_code)?;
        Ok(Self {
            at: bounded_timestamp(at.into())?,
            reason_code,
            trace,
            message: redact_message(message.as_ref()),
        })
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ContextDiagnostics {
    pub scope: Scope,
    pub session_id: Id,
    pub cursor: Cursor,
    pub generation: u64,
    pub mode: ContextMode,
    pub render_mode: RenderMode,
    pub overflow_policy: OverflowPolicy,
    pub refusal_policy: RefusalPolicy,
    pub policy_revision: u64,
    pub provider_profile_digest: Digest,
    pub journal_items: u64,
    pub journal_bytes: u64,
    pub model_budget: ModelBudgetStatus,
    pub pressure_band: PressureBand,
    pub baseline: ProjectionRegionStatus,
    pub delta: ProjectionRegionStatus,
    pub tail: ProjectionRegionStatus,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub protected_item_ids: Vec<Id>,
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub selected_tiers: BTreeMap<Id, u8>,
    pub pending_reductions: u64,
    pub summary_jobs: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub writer_lease: Option<WriterLeaseStatus>,
    pub last_known_good_eligible: bool,
    pub last_reason_code: String,
    pub foreground_usage: UsageAccounting,
    pub summary_usage: UsageAccounting,
    pub subagent_usage: UsageAccounting,
    pub recent_outcomes: Vec<DiagnosticOutcome>,
    pub generated_at: String,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub enum RenderMode {
    #[serde(rename = "host_serialized")]
    HostSerialized,
}

impl ContextDiagnostics {
    pub fn validate(&self) -> Result<(), DiagnosticViolation> {
        if self.generation == 0
            || self.generation > MAX_SAFE_INTEGER
            || self.policy_revision == 0
            || self.policy_revision > MAX_SAFE_INTEGER
            || self.journal_items > MAX_SAFE_INTEGER
            || self.journal_bytes > MAX_SAFE_INTEGER
            || self.pending_reductions > MAX_QUEUE_DEPTH
            || self.summary_jobs > MAX_QUEUE_DEPTH
        {
            return Err(DiagnosticViolation::CounterOutsideRange);
        }
        self.model_budget.validate()?;
        self.baseline.validate(RegionKind::Baseline)?;
        self.delta.validate(RegionKind::Delta)?;
        self.tail.validate(RegionKind::Tail)?;
        self.foreground_usage.validate()?;
        self.summary_usage.validate()?;
        self.subagent_usage.validate()?;
        if self.protected_item_ids.len() > MAX_DIAGNOSTIC_IDENTITIES
            || self.selected_tiers.len() > MAX_DIAGNOSTIC_IDENTITIES
            || self.selected_tiers.values().any(|tier| *tier > 3)
            || self.recent_outcomes.len() > MAX_RECENT_OUTCOMES
        {
            return Err(DiagnosticViolation::CollectionTooLarge);
        }
        validate_reason_code(&self.last_reason_code)?;
        bounded_timestamp(self.generated_at.clone())?;
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum EngineDisposition {
    Active,
    Degraded,
    Parked,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum EvidenceState {
    Available,
    Unavailable,
    Unsupported,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct DiagnosticFailure {
    pub reason_code: String,
    pub message: String,
    pub retryable: bool,
    pub failed_at_ms: u64,
}

impl DiagnosticFailure {
    pub fn redacted(
        reason_code: impl Into<String>,
        message: impl AsRef<str>,
        retryable: bool,
        failed_at_ms: u64,
    ) -> Result<Self, DiagnosticViolation> {
        let reason_code = reason_code.into();
        validate_reason_code(&reason_code)?;
        Ok(Self {
            reason_code,
            message: redact_message(message.as_ref()),
            retryable,
            failed_at_ms,
        })
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ContextDiagnosticsSnapshot {
    pub state: EvidenceState,
    pub observed_at_ms: u64,
    pub disposition: EngineDisposition,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub value: Option<ContextDiagnostics>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub current_error: Option<DiagnosticFailure>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub last_good: Option<ContextDiagnostics>,
}

impl ContextDiagnosticsSnapshot {
    pub fn available(
        observed_at_ms: u64,
        value: ContextDiagnostics,
    ) -> Result<Self, DiagnosticViolation> {
        value.validate()?;
        Ok(Self {
            state: EvidenceState::Available,
            observed_at_ms,
            disposition: EngineDisposition::Active,
            last_good: Some(value.clone()),
            value: Some(value),
            current_error: None,
        })
    }

    pub fn unavailable(
        observed_at_ms: u64,
        failure: DiagnosticFailure,
        last_good: Option<ContextDiagnostics>,
        parked: bool,
    ) -> Result<Self, DiagnosticViolation> {
        if let Some(last_good) = &last_good {
            last_good.validate()?;
        }
        Ok(Self {
            state: EvidenceState::Unavailable,
            observed_at_ms,
            disposition: if parked {
                EngineDisposition::Parked
            } else {
                EngineDisposition::Degraded
            },
            value: None,
            current_error: Some(failure),
            last_good,
        })
    }

    pub fn unsupported(observed_at_ms: u64) -> Self {
        Self {
            state: EvidenceState::Unsupported,
            observed_at_ms,
            disposition: EngineDisposition::Parked,
            value: None,
            current_error: None,
            last_good: None,
        }
    }
}

pub fn redact_message(value: &str) -> String {
    let normalized: String = value
        .chars()
        .filter(|character| !character.is_control() || *character == ' ')
        .take(2_048)
        .collect();
    let lowercase = normalized.to_ascii_lowercase();
    if [
        "authorization:",
        "bearer ",
        "api_key",
        "api-key",
        "password",
        "secret",
        "token=",
    ]
    .iter()
    .any(|marker| lowercase.contains(marker))
    {
        "diagnostic detail redacted".to_owned()
    } else {
        normalized
    }
}

fn validate_reason_code(value: &str) -> Result<(), DiagnosticViolation> {
    let bytes = value.as_bytes();
    if bytes.len() < 2
        || bytes.len() > 64
        || !bytes[0].is_ascii_lowercase()
        || !bytes[1..]
            .iter()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || *byte == b'_')
    {
        return Err(DiagnosticViolation::InvalidReasonCode);
    }
    Ok(())
}

fn bounded_timestamp(value: String) -> Result<String, DiagnosticViolation> {
    if value.is_empty() || value.len() > 64 || value.chars().any(char::is_control) {
        return Err(DiagnosticViolation::InvalidTimestamp);
    }
    Ok(value)
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, thiserror::Error)]
pub enum DiagnosticViolation {
    #[error("diagnostic counter is outside the supported range")]
    CounterOutsideRange,
    #[error("diagnostic collection exceeds its bound")]
    CollectionTooLarge,
    #[error("diagnostic reason code is invalid")]
    InvalidReasonCode,
    #[error("diagnostic timestamp is invalid")]
    InvalidTimestamp,
    #[error("model budget is inconsistent")]
    InvalidBudget,
    #[error("projection region is inconsistent")]
    InvalidRegion,
    #[error("usage state does not match its values")]
    UsageStateMismatch,
    #[error("token count exceeds the supported range")]
    TokenCountTooLarge,
}
