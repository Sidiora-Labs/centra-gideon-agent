use hypermid_contracts::{Cursor, Digest, EffectState, Id};
use hypermid_role_harness::{RoleDescriptor, RoleMajor, RoleMessage, Stability, ToolAttribution};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeSet;

pub const RUNNER_ROLE_V1: &str = "hypermid.runner/v1";
pub const RUNNER_ROLE_OPERATIONS: [&str; 9] = [
    "describe",
    "admit",
    "transcript",
    "model_view",
    "run_result",
    "subscribe",
    "send",
    "change_session",
    "dispatch_tool",
];

pub fn descriptor(
    implementation_version: impl Into<String>,
    capabilities: BTreeSet<CapabilityGroup>,
) -> RunnerDescriptor {
    RunnerDescriptor {
        role: RoleDescriptor::new(
            implementation_version,
            vec![
                RoleMajor::new(RUNNER_ROLE_V1, RUNNER_ROLE_OPERATIONS, Stability::Alpha)
                    .expect("static runner role"),
            ],
        )
        .expect("static runner descriptor"),
        capabilities,
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CapabilityGroup {
    TranscriptReads,
    DispatchAttribution,
    RunResults,
    Streaming,
    ModelView,
    Queue,
    Steer,
    Interrupt,
    Compaction,
    SessionChange,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RunnerDescriptor {
    pub role: RoleDescriptor,
    pub capabilities: BTreeSet<CapabilityGroup>,
}

impl RunnerDescriptor {
    pub fn require(&self, capability: CapabilityGroup) -> Result<(), RunnerViolation> {
        self.capabilities
            .contains(&capability)
            .then_some(())
            .ok_or(RunnerViolation::UnsupportedCapability(capability))
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct TranscriptQuery {
    pub session_id: Id,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub lineage_id: Option<Id>,
    pub from: TranscriptOrigin,
    pub max_count: u32,
    pub max_bytes: u64,
    pub include_original: bool,
}

impl TranscriptQuery {
    pub fn validate(&self) -> Result<(), RunnerViolation> {
        if self.max_count == 0
            || self.max_count > 10_000
            || self.max_bytes == 0
            || self.max_bytes > 8 * 1024 * 1024
        {
            return Err(RunnerViolation::InvalidPageBounds);
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case", tag = "kind", content = "value")]
pub enum TranscriptOrigin {
    Ordinal(u64),
    Message(Id),
    Head,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct TranscriptHead {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub lineage_id: Option<Id>,
    pub next_ordinal: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub last_message_id: Option<Id>,
    pub digest: Digest,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct TranscriptPage {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub lineage_id: Option<Id>,
    pub messages: Vec<RoleMessage>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub next_ordinal: Option<u64>,
    pub cursor: Cursor,
    pub head: TranscriptHead,
}

impl TranscriptPage {
    pub fn validate(&self) -> Result<(), RunnerViolation> {
        let mut expected = self
            .messages
            .first()
            .map(|message| message.ordinal)
            .unwrap_or(self.head.next_ordinal);
        let mut ids = BTreeSet::new();
        for message in &self.messages {
            if message.ordinal != expected || !ids.insert(message.message_id.clone()) {
                return Err(RunnerViolation::NonImmutableTranscriptPage);
            }
            expected += 1;
        }
        if self.lineage_id != self.head.lineage_id {
            return Err(RunnerViolation::StaleLineage);
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "snake_case", tag = "kind")]
pub enum ModelViewItem {
    Raw {
        message: RoleMessage,
    },
    Replacement {
        compaction_id: Id,
        version: u64,
        from_ordinal: u64,
        to_ordinal: u64,
        messages: Vec<RoleMessage>,
    },
}

impl ModelViewItem {
    pub fn validate(&self) -> Result<(), RunnerViolation> {
        if let Self::Replacement {
            version,
            from_ordinal,
            to_ordinal,
            ..
        } = self
        {
            if *version == 0 || from_ordinal > to_ordinal {
                return Err(RunnerViolation::InvalidReplacementRange);
            }
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RunTerminalState {
    Completed,
    Failed,
    Cancelled,
    Refused,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RunResult {
    pub run_id: Id,
    pub state: RunTerminalState,
    pub final_assistant_text: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error_code: Option<String>,
    pub cursor: Cursor,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RunEvent {
    pub event_id: Id,
    pub cursor: Cursor,
    pub run_id: Id,
    pub kind: String,
    pub payload: Value,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case", tag = "kind", content = "cursor")]
pub enum SubscriptionOrigin {
    Start,
    Cursor(Cursor),
    Head(Cursor),
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum DeliveryMode {
    Queue,
    Steer,
    Interrupt,
}

impl DeliveryMode {
    pub fn capability(self) -> CapabilityGroup {
        match self {
            Self::Queue => CapabilityGroup::Queue,
            Self::Steer => CapabilityGroup::Steer,
            Self::Interrupt => CapabilityGroup::Interrupt,
        }
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct OwnerSend {
    pub send_id: Id,
    pub session_id: Id,
    pub mode: DeliveryMode,
    pub content: Value,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub runner_parameters: Option<Value>,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SendOutcome {
    Accepted,
    Duplicate,
    Conflict,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SessionBaseline {
    pub session_id: Id,
    pub lineage_id: Id,
    pub generation: u64,
    pub transcript_digest: Digest,
    pub plan_digest: Digest,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SessionChangeKind {
    Refresh,
    PolicyRefresh,
    PrefixFlush,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SessionChange {
    pub kind: SessionChangeKind,
    pub generation: u64,
    pub expected_generation: u64,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SessionChangeOutcome {
    Applied,
    Superseded,
    AlreadyApplied,
    UnsupportedRung,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ToolDispatchRecord {
    pub run_id: Id,
    pub provider_id: Id,
    pub call_key: String,
    pub schema_pin: String,
    pub effect_state: EffectState,
    pub cursor: Cursor,
}

impl ToolDispatchRecord {
    pub fn attribution(&self) -> ToolAttribution {
        ToolAttribution {
            provider_id: self.provider_id.clone(),
            call_key: self.call_key.clone(),
            schema_pin: self.schema_pin.clone(),
        }
    }

    pub fn may_redispatch(&self) -> bool {
        self.effect_state == EffectState::NotStarted
    }
}

#[derive(Debug, thiserror::Error)]
pub enum RunnerViolation {
    #[error("runner does not support capability {0:?}")]
    UnsupportedCapability(CapabilityGroup),
    #[error("transcript page bounds are invalid")]
    InvalidPageBounds,
    #[error("transcript page is not ordinal and identity stable")]
    NonImmutableTranscriptPage,
    #[error("transcript lineage is stale")]
    StaleLineage,
    #[error("model-view replacement range is invalid")]
    InvalidReplacementRange,
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn indeterminate_tool_effect_is_never_redispatched() {
        let record = ToolDispatchRecord {
            run_id: Id::new("run-1").unwrap(),
            provider_id: Id::new("provider-1").unwrap(),
            call_key: "call-1".into(),
            schema_pin: "pin".into(),
            effect_state: EffectState::Unknown,
            cursor: Cursor::new(1, 4).unwrap(),
        };
        assert!(!record.may_redispatch());
    }
}
