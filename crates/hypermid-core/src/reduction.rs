use crate::history::ContextItem;
use hypermid_contracts::{Cursor, Digest, Id, Scope, Trace, MAX_SAFE_INTEGER};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

pub const MAX_REDUCTION_TARGETS: usize = 100_000;

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ContextRegion {
    Baseline,
    Delta,
    Tail,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ContentClass {
    ProviderFraming,
    UserRequest,
    AssistantProse,
    Reasoning,
    ToolCall,
    ToolResult,
    Approval,
    Control,
    Attachment,
    HistoricalDetail,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ToolArcState {
    None,
    Open,
    Complete,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ReductionProtection {
    LatestUserRequest,
    ActiveToolArc,
    UnresolvedApproval,
    RequiredReasoning,
    ContextControl,
    ProtectedTail,
    ProviderFraming,
    AlreadySummarized,
    NotRecoverable,
}

impl ReductionProtection {
    pub fn reason_code(self) -> &'static str {
        match self {
            Self::LatestUserRequest => "latest_user_request",
            Self::ActiveToolArc => "active_tool_arc",
            Self::UnresolvedApproval => "unresolved_approval",
            Self::RequiredReasoning => "required_reasoning",
            Self::ContextControl => "context_control",
            Self::ProtectedTail => "protected_tail",
            Self::ProviderFraming => "provider_framing",
            Self::AlreadySummarized => "already_summarized",
            Self::NotRecoverable => "not_recoverable",
        }
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ReductionItem {
    pub item_id: Id,
    pub source_digest: Digest,
    pub scope: Scope,
    pub session_id: Id,
    pub cursor: Cursor,
    pub reclaim_tag: u64,
    pub content_class: ContentClass,
    pub region: ContextRegion,
    pub source_bytes: u64,
    pub estimated_tokens: u64,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub call_ids: Vec<Id>,
    pub tool_arc_state: ToolArcState,
    #[serde(default)]
    pub protections: BTreeSet<ReductionProtection>,
}

impl ReductionItem {
    pub fn from_history(
        item: &ContextItem,
        content_class: ContentClass,
        region: ContextRegion,
        source_bytes: u64,
        estimated_tokens: u64,
        tool_arc_state: ToolArcState,
        protections: BTreeSet<ReductionProtection>,
    ) -> Result<Self, ReductionViolation> {
        item.validate_shape()
            .map_err(|_| ReductionViolation::InvalidItem)?;
        let call_ids = item
            .parts
            .iter()
            .filter_map(|part| part.call_id.clone())
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect();
        let reduction_item = Self {
            item_id: item.item_id.clone(),
            source_digest: item.source_digest,
            scope: item.scope.clone(),
            session_id: item.session_id.clone(),
            cursor: item.cursor,
            reclaim_tag: item.reclaim_tag(),
            content_class,
            region,
            source_bytes,
            estimated_tokens,
            call_ids,
            tool_arc_state,
            protections,
        };
        reduction_item.validate()?;
        Ok(reduction_item)
    }

    pub fn validate(&self) -> Result<(), ReductionViolation> {
        if self.reclaim_tag == 0
            || self.reclaim_tag > MAX_SAFE_INTEGER
            || self.reclaim_tag != self.cursor.sequence
        {
            return Err(ReductionViolation::InvalidItem);
        }
        if self.tool_arc_state == ToolArcState::None && !self.call_ids.is_empty()
            || self.tool_arc_state != ToolArcState::None && self.call_ids.is_empty()
        {
            return Err(ReductionViolation::InvalidItem);
        }
        if matches!(
            self.content_class,
            ContentClass::ToolCall | ContentClass::ToolResult
        ) != !self.call_ids.is_empty()
        {
            return Err(ReductionViolation::InvalidItem);
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ReductionTarget {
    pub tag_start: u64,
    pub tag_end: u64,
}

impl ReductionTarget {
    pub fn new(tag_start: u64, tag_end: u64) -> Result<Self, ReductionViolation> {
        if tag_start == 0 || tag_start > tag_end || tag_end > MAX_SAFE_INTEGER {
            return Err(ReductionViolation::InvalidTarget);
        }
        Ok(Self { tag_start, tag_end })
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ReductionRequest {
    pub scope: Scope,
    pub session_id: Id,
    pub expected_cursor: Cursor,
    pub idempotency_key: Id,
    pub targets: Vec<ReductionTarget>,
    pub trace: Trace,
}

impl ReductionRequest {
    pub fn expanded_tags(&self) -> Result<BTreeSet<u64>, ReductionViolation> {
        if self.targets.is_empty() || self.targets.len() > 1_024 {
            return Err(ReductionViolation::InvalidTarget);
        }
        let mut tags = BTreeSet::new();
        for target in &self.targets {
            ReductionTarget::new(target.tag_start, target.tag_end)?;
            let span = target
                .tag_end
                .checked_sub(target.tag_start)
                .and_then(|value| value.checked_add(1))
                .ok_or(ReductionViolation::TooManyTargets)?;
            if span > MAX_REDUCTION_TARGETS as u64 {
                return Err(ReductionViolation::TooManyTargets);
            }
            for tag in target.tag_start..=target.tag_end {
                tags.insert(tag);
                if tags.len() > MAX_REDUCTION_TARGETS {
                    return Err(ReductionViolation::TooManyTargets);
                }
            }
        }
        Ok(tags)
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum BoundaryKind {
    TailSafe,
    HardBoundary,
    Deferred,
    PressureRefusal,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ReductionBoundary {
    pub kind: BoundaryKind,
    pub reason_code: String,
    pub cache_generation: u64,
}

impl ReductionBoundary {
    pub fn can_apply(&self, region: ContextRegion) -> bool {
        self.kind == BoundaryKind::HardBoundary
            || self.kind == BoundaryKind::TailSafe && region == ContextRegion::Tail
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ReductionStatus {
    Queued,
    Applied,
    Rejected,
    AlreadyApplied,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RecoveryInstruction {
    pub operation: String,
    pub item_id: Id,
    pub reclaim_tag: u64,
    pub cursor: Cursor,
    pub source_digest: Digest,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ReductionMarker {
    pub marker_id: Id,
    pub item_id: Id,
    pub source_digest: Digest,
    pub reclaim_tag: u64,
    pub content_class: ContentClass,
    pub source_bytes: u64,
    pub estimated_tokens: u64,
    pub recovery: RecoveryInstruction,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ReductionOutcome {
    pub tag: u64,
    pub status: ReductionStatus,
    pub reason_code: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub item_id: Option<Id>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub estimated_tokens: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub marker: Option<ReductionMarker>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ReductionResult {
    pub scope: Scope,
    pub session_id: Id,
    pub cursor: Cursor,
    pub idempotency_key: Id,
    pub outcomes: Vec<ReductionOutcome>,
    pub trace: Trace,
    pub boundary: ReductionBoundary,
}

pub fn evaluate_reduction(
    request: &ReductionRequest,
    items: &[ReductionItem],
    boundary: &ReductionBoundary,
    already_applied: &BTreeSet<u64>,
) -> Result<Vec<ReductionOutcome>, ReductionViolation> {
    let tags = request.expanded_tags()?;
    let requested_tags = tags.clone();
    let mut by_tag = BTreeMap::new();
    let mut arcs: BTreeMap<Id, BTreeSet<u64>> = BTreeMap::new();
    let mut open_arcs = BTreeSet::new();
    for item in items {
        item.validate()?;
        if by_tag.insert(item.reclaim_tag, item).is_some() {
            return Err(ReductionViolation::DuplicateTag);
        }
        for call_id in &item.call_ids {
            arcs.entry(call_id.clone())
                .or_default()
                .insert(item.reclaim_tag);
            if item.tool_arc_state == ToolArcState::Open {
                open_arcs.insert(call_id.clone());
            }
        }
    }

    let mut outcomes = Vec::with_capacity(tags.len());
    for tag in tags {
        if already_applied.contains(&tag) {
            outcomes.push(outcome(
                tag,
                ReductionStatus::AlreadyApplied,
                "already_applied",
                None,
            ));
            continue;
        }
        let Some(item) = by_tag.get(&tag).copied() else {
            outcomes.push(outcome(tag, ReductionStatus::Rejected, "unknown_tag", None));
            continue;
        };
        if item.scope != request.scope || item.session_id != request.session_id {
            outcomes.push(outcome(
                tag,
                ReductionStatus::Rejected,
                "foreign_scope",
                Some(item),
            ));
            continue;
        }
        if let Some(protection) = item.protections.iter().next().copied() {
            outcomes.push(outcome(
                tag,
                ReductionStatus::Rejected,
                protection.reason_code(),
                Some(item),
            ));
            continue;
        }
        if item
            .call_ids
            .iter()
            .any(|call_id| open_arcs.contains(call_id))
        {
            outcomes.push(outcome(
                tag,
                ReductionStatus::Rejected,
                "active_tool_arc",
                Some(item),
            ));
            continue;
        }
        if item.call_ids.iter().any(|call_id| {
            arcs.get(call_id)
                .is_some_and(|arc_tags| !arc_tags.is_subset(&requested_tags))
        }) {
            outcomes.push(outcome(
                tag,
                ReductionStatus::Rejected,
                "incomplete_tool_arc",
                Some(item),
            ));
            continue;
        }
        if boundary.kind == BoundaryKind::PressureRefusal {
            outcomes.push(outcome(
                tag,
                ReductionStatus::Rejected,
                "pressure_refusal",
                Some(item),
            ));
        } else if boundary.can_apply(item.region) {
            outcomes.push(applied_outcome(item));
        } else {
            outcomes.push(outcome(
                tag,
                ReductionStatus::Queued,
                "awaiting_cache_boundary",
                Some(item),
            ));
        }
    }
    Ok(outcomes)
}

fn outcome(
    tag: u64,
    status: ReductionStatus,
    reason_code: &str,
    item: Option<&ReductionItem>,
) -> ReductionOutcome {
    ReductionOutcome {
        tag,
        status,
        reason_code: reason_code.to_owned(),
        item_id: item.map(|value| value.item_id.clone()),
        estimated_tokens: item.map(|value| value.estimated_tokens),
        marker: None,
    }
}

fn applied_outcome(item: &ReductionItem) -> ReductionOutcome {
    let marker_id = Id::new(format!(
        "reduction:{}:{}",
        &item.source_digest.to_hex()[..24],
        item.reclaim_tag
    ))
    .expect("validated ids and numeric tag form a valid marker id");
    ReductionOutcome {
        tag: item.reclaim_tag,
        status: ReductionStatus::Applied,
        reason_code: "applied_at_compatible_boundary".to_owned(),
        item_id: Some(item.item_id.clone()),
        estimated_tokens: Some(item.estimated_tokens),
        marker: Some(ReductionMarker {
            marker_id,
            item_id: item.item_id.clone(),
            source_digest: item.source_digest,
            reclaim_tag: item.reclaim_tag,
            content_class: item.content_class,
            source_bytes: item.source_bytes,
            estimated_tokens: item.estimated_tokens,
            recovery: RecoveryInstruction {
                operation: "expand".to_owned(),
                item_id: item.item_id.clone(),
                reclaim_tag: item.reclaim_tag,
                cursor: item.cursor,
                source_digest: item.source_digest,
            },
        }),
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, thiserror::Error)]
pub enum ReductionViolation {
    #[error("reduction target is invalid")]
    InvalidTarget,
    #[error("reduction expands beyond the bounded target limit")]
    TooManyTargets,
    #[error("reduction item is structurally invalid")]
    InvalidItem,
    #[error("reclaim tags must be unique within a context stream")]
    DuplicateTag,
}
