use crate::diagnostics::redact_message;
use hypermid_contracts::{Cursor, Digest, Id, Scope, Trace};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

pub const MAX_HOOK_METADATA: usize = 64;
pub const MAX_SYNTHETIC_BLOCKS: usize = 128;
pub const MAX_SYNTHETIC_TOKENS: u64 = 65_536;

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum HookKind {
    SessionBind,
    Ingest,
    Project,
    PressureChange,
    Reduction,
    Summary,
    Recovery,
    SubagentSnapshot,
    DiagnosticFault,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum HookPhase {
    Pre,
    Post,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum HookOutcome {
    Allow,
    Deny,
    Inject,
    Committed,
    Rejected,
    TimedOut,
    Failed,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(untagged)]
pub enum MetadataValue {
    Bool(bool),
    Integer(i64),
    String(String),
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct HookEvent {
    pub event_id: Id,
    pub kind: HookKind,
    pub scope: Scope,
    pub trace: Trace,
    pub session_id: Id,
    pub cursor: Cursor,
    pub policy_revision: u64,
    pub phase: HookPhase,
    pub metadata: BTreeMap<String, MetadataValue>,
    pub created_at: String,
}

impl HookEvent {
    #[allow(clippy::too_many_arguments)]
    pub fn redacted(
        event_id: Id,
        kind: HookKind,
        scope: Scope,
        trace: Trace,
        session_id: Id,
        cursor: Cursor,
        policy_revision: u64,
        phase: HookPhase,
        metadata: BTreeMap<String, MetadataValue>,
        created_at: impl Into<String>,
    ) -> Result<Self, HookViolation> {
        if policy_revision == 0 || metadata.len() > MAX_HOOK_METADATA {
            return Err(HookViolation::InvalidEvent);
        }
        let metadata = metadata
            .into_iter()
            .map(|(key, value)| {
                validate_metadata_key(&key)?;
                Ok((key.clone(), redact_metadata(&key, value)))
            })
            .collect::<Result<_, HookViolation>>()?;
        let created_at = created_at.into();
        if created_at.is_empty()
            || created_at.len() > 64
            || created_at.chars().any(char::is_control)
        {
            return Err(HookViolation::InvalidEvent);
        }
        Ok(Self {
            event_id,
            kind,
            scope,
            trace,
            session_id,
            cursor,
            policy_revision,
            phase,
            metadata,
            created_at,
        })
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SyntheticBlockRef {
    pub block_id: Id,
    pub content_digest: Digest,
    pub token_mass: u64,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct HookDecision {
    pub outcome: HookOutcome,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason_code: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub synthetic_blocks: Vec<SyntheticBlockRef>,
}

impl HookDecision {
    pub fn allow() -> Self {
        Self {
            outcome: HookOutcome::Allow,
            reason_code: None,
            synthetic_blocks: vec![],
        }
    }

    pub fn deny(reason_code: impl Into<String>) -> Result<Self, HookViolation> {
        let reason_code = reason_code.into();
        validate_reason_code(&reason_code)?;
        Ok(Self {
            outcome: HookOutcome::Deny,
            reason_code: Some(reason_code),
            synthetic_blocks: vec![],
        })
    }

    pub fn inject(blocks: Vec<SyntheticBlockRef>) -> Result<Self, HookViolation> {
        if blocks.is_empty()
            || blocks.len() > MAX_SYNTHETIC_BLOCKS
            || blocks.iter().map(|block| block.token_mass).sum::<u64>() > MAX_SYNTHETIC_TOKENS
        {
            return Err(HookViolation::SyntheticBlockLimit);
        }
        Ok(Self {
            outcome: HookOutcome::Inject,
            reason_code: None,
            synthetic_blocks: blocks,
        })
    }

    pub fn validate(&self) -> Result<(), HookViolation> {
        if let Some(reason_code) = &self.reason_code {
            validate_reason_code(reason_code)?;
        }
        match self.outcome {
            HookOutcome::Inject => {
                Self::inject(self.synthetic_blocks.clone())?;
            }
            HookOutcome::Deny if self.reason_code.is_none() => {
                return Err(HookViolation::InvalidDecision);
            }
            _ if !self.synthetic_blocks.is_empty() => {
                return Err(HookViolation::InvalidDecision);
            }
            _ => {}
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct HookOutcomeRecord {
    pub sequence: u64,
    pub event_id: Id,
    pub hook_id: Id,
    pub kind: HookKind,
    pub phase: HookPhase,
    pub trace: Trace,
    pub outcome: HookOutcome,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason_code: Option<String>,
    pub duration_ms: u64,
    pub synthetic_blocks: Vec<SyntheticBlockRef>,
    pub recorded_at_ms: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, thiserror::Error)]
#[error("{reason_code}: {message}")]
pub struct HookFailure {
    pub reason_code: String,
    pub message: String,
}

impl HookFailure {
    pub fn redacted(
        reason_code: impl Into<String>,
        message: impl AsRef<str>,
    ) -> Result<Self, HookViolation> {
        let reason_code = reason_code.into();
        validate_reason_code(&reason_code)?;
        Ok(Self {
            reason_code,
            message: redact_message(message.as_ref()),
        })
    }
}

fn redact_metadata(key: &str, value: MetadataValue) -> MetadataValue {
    let key = key.to_ascii_lowercase();
    if [
        "authorization",
        "credential",
        "password",
        "secret",
        "token",
        "content",
        "prompt",
    ]
    .iter()
    .any(|marker| key.contains(marker))
    {
        return MetadataValue::String("[redacted]".to_owned());
    }
    match value {
        MetadataValue::String(value) => {
            MetadataValue::String(redact_message(&value).chars().take(512).collect())
        }
        other => other,
    }
}

fn validate_metadata_key(value: &str) -> Result<(), HookViolation> {
    let bytes = value.as_bytes();
    if bytes.is_empty()
        || bytes.len() > 64
        || !bytes[0].is_ascii_lowercase()
        || !bytes
            .iter()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || *byte == b'_')
    {
        return Err(HookViolation::InvalidMetadataKey);
    }
    Ok(())
}

pub fn validate_reason_code(value: &str) -> Result<(), HookViolation> {
    let bytes = value.as_bytes();
    if bytes.len() < 2
        || bytes.len() > 64
        || !bytes[0].is_ascii_lowercase()
        || !bytes[1..]
            .iter()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || *byte == b'_')
    {
        return Err(HookViolation::InvalidReasonCode);
    }
    Ok(())
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, thiserror::Error)]
pub enum HookViolation {
    #[error("hook event is invalid")]
    InvalidEvent,
    #[error("hook metadata key is invalid")]
    InvalidMetadataKey,
    #[error("hook reason code is invalid")]
    InvalidReasonCode,
    #[error("hook decision is invalid")]
    InvalidDecision,
    #[error("synthetic block bound exceeded")]
    SyntheticBlockLimit,
}
