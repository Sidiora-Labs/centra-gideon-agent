use hypermid_contracts::{Cursor, Error, Id};
use hypermid_role_harness::{RoleDescriptor, RoleMajor, RoleMessage, Stability};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};

pub const COMPACTION_ROLE_V1: &str = "hypermid.compaction/v1";
pub const COMPACTION_OPERATIONS: [&str; 4] = ["describe", "setup", "step", "ready"];
pub const MAX_DELTA_BYTES: usize = 1_048_576;
pub const MAX_REPLACEMENT_MESSAGES: usize = 4_096;
pub const MAX_WAIT_MS: u64 = 60_000;

pub fn descriptor(implementation_version: impl Into<String>) -> RoleDescriptor {
    RoleDescriptor::new(
        implementation_version,
        vec![
            RoleMajor::new(COMPACTION_ROLE_V1, COMPACTION_OPERATIONS, Stability::Alpha)
                .expect("static compaction role"),
        ],
    )
    .expect("static compaction descriptor")
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SetupRequest {
    pub preset: String,
    #[serde(default)]
    pub parameters: BTreeMap<String, Value>,
    pub composition: Value,
    pub configuration: Value,
}

impl SetupRequest {
    pub fn validate(&self) -> Result<(), CompactionViolation> {
        if self.preset.is_empty()
            || self.preset.len() > 160
            || !self.parameters.values().all(valid_json_depth)
            || !valid_json_depth(&self.composition)
            || !valid_json_depth(&self.configuration)
        {
            return Err(CompactionViolation::InvalidSetup);
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct CallCondition {
    pub kind: String,
    #[serde(default)]
    pub parameters: BTreeMap<String, Value>,
}

impl CallCondition {
    pub fn should_call(&self, known_result: Option<bool>) -> bool {
        known_result.unwrap_or(true)
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct StabilityRank {
    pub name: String,
    pub rank: u32,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SetupResponse {
    pub session_handle: Id,
    pub stability_ranks: Vec<StabilityRank>,
    #[serde(default)]
    pub call_conditions: Vec<CallCondition>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub initial_replacement: Option<CompactionDirective>,
}

impl SetupResponse {
    pub fn validate(&self) -> Result<(), CompactionViolation> {
        let ranks = self
            .stability_ranks
            .iter()
            .map(|rank| (&rank.name, rank.rank))
            .collect::<BTreeSet<_>>();
        if self.stability_ranks.is_empty()
            || ranks.len() != self.stability_ranks.len()
            || self
                .stability_ranks
                .iter()
                .any(|rank| rank.name.is_empty() || rank.name.len() > 64)
        {
            return Err(CompactionViolation::InvalidSetup);
        }
        if let Some(replacement) = &self.initial_replacement {
            replacement.validate(None)?;
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ModelGeometry {
    pub context_window: u64,
    pub output_limit: u64,
}

impl ModelGeometry {
    fn validate(&self) -> Result<(), CompactionViolation> {
        if self.context_window == 0
            || self.output_limit == 0
            || self.output_limit > self.context_window
        {
            return Err(CompactionViolation::InvalidStep);
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RequestEstimate {
    pub input_bytes: u64,
    pub input_tokens: u64,
    pub reserved_output_tokens: u64,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct DeltaMessage {
    pub cursor: Cursor,
    pub message: RoleMessage,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct MessageDelta {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub after: Option<Cursor>,
    pub messages: Vec<DeltaMessage>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub next: Option<Cursor>,
    pub byte_cap: u64,
    pub delivered_bytes: u64,
    pub truncated: bool,
}

impl MessageDelta {
    pub fn validate(&self) -> Result<(), CompactionViolation> {
        if self.byte_cap == 0
            || self.byte_cap as usize > MAX_DELTA_BYTES
            || self.delivered_bytes > self.byte_cap
        {
            return Err(CompactionViolation::DeltaBounds);
        }
        let mut previous = self.after;
        let mut ids = BTreeSet::new();
        for entry in &self.messages {
            if previous.is_some_and(|cursor| entry.cursor <= cursor)
                || !ids.insert(&entry.message.message_id)
            {
                return Err(CompactionViolation::DeltaOrder);
            }
            if let Some(cursor) = previous {
                if cursor.epoch != entry.cursor.epoch {
                    return Err(CompactionViolation::DeltaOrder);
                }
            }
            previous = Some(entry.cursor);
        }
        if self.next != previous {
            return Err(CompactionViolation::DeltaCursor);
        }
        let actual = serde_json::to_vec(&self.messages)
            .map_err(|_| CompactionViolation::DeltaBounds)?
            .len() as u64;
        if actual != self.delivered_bytes {
            return Err(CompactionViolation::DeltaBounds);
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct AppliedCompaction {
    pub compaction_id: Id,
    pub version: u64,
    pub from_ordinal: u64,
    pub to_ordinal: u64,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RejectedCompaction {
    pub compaction_id: Id,
    pub version: u64,
    pub reason: String,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct StepRequest {
    pub session_handle: Id,
    pub request_id: Id,
    pub lineage_id: Id,
    pub step_id: Id,
    pub step_kind: String,
    pub geometry: ModelGeometry,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub previous_usage: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub provider_failure: Option<Error>,
    pub estimate: RequestEstimate,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub rebuild_reason: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub newest_message: Option<RoleMessage>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub last_applied: Option<AppliedCompaction>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub last_rejected: Option<RejectedCompaction>,
    pub delta: MessageDelta,
    pub now_ms: u64,
    pub deadline_ms: u64,
}

impl StepRequest {
    pub fn validate(&self) -> Result<(), CompactionViolation> {
        if self.step_kind.is_empty()
            || self.step_kind.len() > 64
            || self.deadline_ms <= self.now_ms
            || self
                .rebuild_reason
                .as_ref()
                .is_some_and(|reason| reason.len() > 512)
        {
            return Err(CompactionViolation::InvalidStep);
        }
        self.geometry.validate()?;
        self.delta.validate()
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct CompactionDirective {
    pub request_id: Id,
    pub compaction_id: Id,
    pub version: u64,
    pub from_ordinal: u64,
    pub to_ordinal: u64,
    pub replacement: Vec<RoleMessage>,
}

impl CompactionDirective {
    pub fn validate(&self, transcript: Option<&[RoleMessage]>) -> Result<(), CompactionViolation> {
        if self.version == 0
            || self.from_ordinal > self.to_ordinal
            || self.replacement.len() > MAX_REPLACEMENT_MESSAGES
            || self
                .replacement
                .windows(2)
                .any(|pair| pair[0].ordinal >= pair[1].ordinal)
        {
            return Err(CompactionViolation::InvalidReplacement);
        }
        if let Some(messages) = transcript {
            validate_tool_boundaries(messages, self.from_ordinal, self.to_ordinal)?;
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum CompactionAnswer {
    NoChange {
        request_id: Id,
        cursor: Cursor,
    },
    Replacement {
        request_id: Id,
        cursor: Cursor,
        directive: CompactionDirective,
    },
    Wait {
        request_id: Id,
        cursor: Cursor,
        wait_id: Id,
        ready_deadline_ms: u64,
    },
    Refusal {
        request_id: Id,
        cursor: Cursor,
        error: Error,
    },
}

impl CompactionAnswer {
    pub fn request_id(&self) -> &Id {
        match self {
            Self::NoChange { request_id, .. }
            | Self::Replacement { request_id, .. }
            | Self::Wait { request_id, .. }
            | Self::Refusal { request_id, .. } => request_id,
        }
    }

    pub fn cursor(&self) -> Cursor {
        match self {
            Self::NoChange { cursor, .. }
            | Self::Replacement { cursor, .. }
            | Self::Wait { cursor, .. }
            | Self::Refusal { cursor, .. } => *cursor,
        }
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ReadyRequest {
    pub session_handle: Id,
    pub request_id: Id,
    pub wait_id: Id,
    pub now_ms: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AnswerDisposition {
    Applied,
    ObservedSuperseded,
    ObservedLate,
    Waiting,
    Refused,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct CompactionState {
    #[serde(skip_serializing_if = "Option::is_none")]
    session_handle: Option<Id>,
    #[serde(skip_serializing_if = "Option::is_none")]
    newest_request_id: Option<Id>,
    deadline_ms: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    last_provider_cursor: Option<Cursor>,
    #[serde(skip_serializing_if = "Option::is_none")]
    last_applied: Option<AppliedCompaction>,
    #[serde(skip_serializing_if = "Option::is_none")]
    waiting: Option<WaitState>,
    #[serde(default)]
    observations: Vec<ObservedAnswer>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ObservedAnswer {
    pub request_id: Id,
    pub cursor: Cursor,
    pub disposition: ObservedDisposition,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ObservedDisposition {
    Superseded,
    Late,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
struct WaitState {
    request_id: Id,
    wait_id: Id,
    ready_deadline_ms: u64,
}

impl Default for CompactionState {
    fn default() -> Self {
        Self {
            session_handle: None,
            newest_request_id: None,
            deadline_ms: 0,
            last_provider_cursor: None,
            last_applied: None,
            waiting: None,
            observations: Vec::new(),
        }
    }
}

impl CompactionState {
    pub fn begin(&mut self, request: &StepRequest) -> Result<(), CompactionViolation> {
        request.validate()?;
        self.session_handle = Some(request.session_handle.clone());
        self.newest_request_id = Some(request.request_id.clone());
        self.deadline_ms = request.deadline_ms;
        self.waiting = None;
        Ok(())
    }

    pub fn observe(
        &mut self,
        answer: &CompactionAnswer,
        arrived_at_ms: u64,
        transcript: &[RoleMessage],
    ) -> Result<AnswerDisposition, CompactionViolation> {
        if self.newest_request_id.as_ref() != Some(answer.request_id()) {
            self.record_observation(answer, ObservedDisposition::Superseded);
            return Ok(AnswerDisposition::ObservedSuperseded);
        }
        if arrived_at_ms >= self.deadline_ms {
            self.record_observation(answer, ObservedDisposition::Late);
            return Ok(AnswerDisposition::ObservedLate);
        }
        if self
            .last_provider_cursor
            .is_some_and(|cursor| answer.cursor() < cursor)
        {
            return Err(CompactionViolation::DeltaCursor);
        }
        self.last_provider_cursor = Some(answer.cursor());
        match answer {
            CompactionAnswer::NoChange { .. } => Ok(AnswerDisposition::Applied),
            CompactionAnswer::Replacement { directive, .. } => {
                directive.validate(Some(transcript))?;
                if directive.request_id != *answer.request_id()
                    || self
                        .last_applied
                        .as_ref()
                        .is_some_and(|last| directive.version <= last.version)
                {
                    return Err(CompactionViolation::NonRisingVersion);
                }
                self.last_applied = Some(AppliedCompaction {
                    compaction_id: directive.compaction_id.clone(),
                    version: directive.version,
                    from_ordinal: directive.from_ordinal,
                    to_ordinal: directive.to_ordinal,
                });
                Ok(AnswerDisposition::Applied)
            }
            CompactionAnswer::Wait {
                request_id,
                wait_id,
                ready_deadline_ms,
                ..
            } => {
                if *ready_deadline_ms <= arrived_at_ms
                    || ready_deadline_ms.saturating_sub(arrived_at_ms) > MAX_WAIT_MS
                {
                    return Err(CompactionViolation::InvalidWait);
                }
                self.waiting = Some(WaitState {
                    request_id: request_id.clone(),
                    wait_id: wait_id.clone(),
                    ready_deadline_ms: *ready_deadline_ms,
                });
                Ok(AnswerDisposition::Waiting)
            }
            CompactionAnswer::Refusal { .. } => Ok(AnswerDisposition::Refused),
        }
    }

    pub fn ready(&mut self, request: &ReadyRequest) -> Result<bool, CompactionViolation> {
        let Some(waiting) = &self.waiting else {
            return Ok(false);
        };
        if self.session_handle.as_ref() != Some(&request.session_handle)
            || waiting.request_id != request.request_id
            || waiting.wait_id != request.wait_id
        {
            return Ok(false);
        }
        if request.now_ms >= waiting.ready_deadline_ms {
            self.waiting = None;
            return Err(CompactionViolation::WaitExpired);
        }
        self.waiting = None;
        Ok(true)
    }

    pub fn last_applied(&self) -> Option<&AppliedCompaction> {
        self.last_applied.as_ref()
    }

    pub fn observations(&self) -> &[ObservedAnswer] {
        &self.observations
    }

    fn record_observation(&mut self, answer: &CompactionAnswer, disposition: ObservedDisposition) {
        if self.observations.len() == 256 {
            self.observations.remove(0);
        }
        self.observations.push(ObservedAnswer {
            request_id: answer.request_id().clone(),
            cursor: answer.cursor(),
            disposition,
        });
    }
}

fn validate_tool_boundaries(
    messages: &[RoleMessage],
    from: u64,
    to: u64,
) -> Result<(), CompactionViolation> {
    let start = messages.iter().position(|message| message.ordinal == from);
    let end = messages.iter().position(|message| message.ordinal == to);
    if let Some(index) = start {
        if messages[index].role == hypermid_role_harness::MessageRole::Tool {
            return Err(CompactionViolation::SplitToolPair);
        }
    }
    if let Some(index) = end {
        if index > 0 && messages[index - 1].tool.is_some() {
            return Err(CompactionViolation::SplitToolPair);
        }
    }
    Ok(())
}

fn valid_json_depth(value: &Value) -> bool {
    fn check(value: &Value, depth: usize) -> bool {
        if depth > 32 {
            return false;
        }
        match value {
            Value::Array(items) => items.iter().all(|item| check(item, depth + 1)),
            Value::Object(items) => items.values().all(|item| check(item, depth + 1)),
            _ => true,
        }
    }
    check(value, 0)
}

#[derive(Debug, thiserror::Error)]
pub enum CompactionViolation {
    #[error("compaction setup is invalid")]
    InvalidSetup,
    #[error("compaction step is invalid")]
    InvalidStep,
    #[error("message delta exceeds its bounds")]
    DeltaBounds,
    #[error("message delta is unordered or duplicated")]
    DeltaOrder,
    #[error("message delta cursor does not cover exactly the delivered messages")]
    DeltaCursor,
    #[error("replacement range or contents are invalid")]
    InvalidReplacement,
    #[error("replacement range splits a tool call from its result")]
    SplitToolPair,
    #[error("compaction version must rise strictly")]
    NonRisingVersion,
    #[error("wait deadline is unbounded or already expired")]
    InvalidWait,
    #[error("wait expired before the provider became ready")]
    WaitExpired,
}
