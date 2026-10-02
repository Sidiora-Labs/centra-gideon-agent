use hypermid_contracts::{Id, Scope, Trace};
use hypermid_role_harness::{RoleDescriptor, RoleMajor, Stability};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};

pub const TRANSFORM_ROLE_V1: &str = "hypermid.transform/v1";
pub const TRANSFORM_OPERATIONS: [&str; 3] = ["describe", "declare", "hook"];
pub const MAX_BUDGET_MS: u64 = 60_000;
pub const MAX_QUESTION_BYTES: usize = 2_048;

pub fn descriptor(implementation_version: impl Into<String>) -> RoleDescriptor {
    RoleDescriptor::new(
        implementation_version,
        vec![
            RoleMajor::new(TRANSFORM_ROLE_V1, TRANSFORM_OPERATIONS, Stability::Alpha)
                .expect("static transform role"),
        ],
    )
    .expect("static transform descriptor")
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct DeclareRequest {
    pub preset: String,
    #[serde(default)]
    pub parameters: BTreeMap<String, Value>,
    pub composition: Value,
    pub configuration: Value,
}

impl DeclareRequest {
    pub fn validate(&self) -> Result<(), TransformViolation> {
        if self.preset.is_empty()
            || self.preset.len() > 160
            || !valid_json_depth(&self.composition)
            || !valid_json_depth(&self.configuration)
            || !self.parameters.values().all(valid_json_depth)
        {
            return Err(TransformViolation::InvalidDeclaration);
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Hook {
    PreUser,
    PostAssistant,
    PreTool,
    PostTool,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Phase {
    Mutate,
    Validate,
    Approve,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum TextOperation {
    Prepend,
    Append,
    Replace,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum UnavailablePolicy {
    Continue,
    FailStep,
    FailRun,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct TransformSubscription {
    pub hook: Hook,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub phase: Option<Phase>,
    #[serde(default)]
    pub tools: BTreeSet<String>,
    pub ops: BTreeSet<TextOperation>,
    pub on_unavailable: UnavailablePolicy,
    pub budget_ms: u64,
}

impl TransformSubscription {
    pub fn validate(&self) -> Result<(), TransformViolation> {
        if self.budget_ms == 0
            || self.budget_ms > MAX_BUDGET_MS
            || self.ops.is_empty()
            || self
                .tools
                .iter()
                .any(|tool| tool.is_empty() || tool.len() > 160)
        {
            return Err(TransformViolation::InvalidSubscription);
        }
        if self.hook == Hook::PreTool {
            if self.phase.is_none() {
                return Err(TransformViolation::MissingPreToolPhase);
            }
        } else if self.phase.is_some() || !self.tools.is_empty() {
            return Err(TransformViolation::PhaseOutsidePreTool);
        }
        if self.phase == Some(Phase::Validate)
            && self
                .ops
                .iter()
                .any(|operation| *operation != TextOperation::Append)
        {
            return Err(TransformViolation::InvalidSubscription);
        }
        Ok(())
    }

    pub fn permits(&self, planned: &Self) -> bool {
        self.hook == planned.hook
            && self.phase == planned.phase
            && (self.tools.is_empty() || planned.tools.is_subset(&self.tools))
            && planned.ops.is_subset(&self.ops)
            && planned.budget_ms <= self.budget_ms
            && planned.on_unavailable == self.on_unavailable
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct TransformDeclaration {
    pub declaration_id: Id,
    pub pure: bool,
    pub subscriptions: Vec<TransformSubscription>,
}

impl TransformDeclaration {
    pub fn validate(&self) -> Result<(), TransformViolation> {
        if self.subscriptions.is_empty() {
            return Err(TransformViolation::InvalidDeclaration);
        }
        let mut keys = BTreeSet::new();
        for subscription in &self.subscriptions {
            subscription.validate()?;
            if !keys.insert((subscription.hook, subscription.phase)) {
                return Err(TransformViolation::DuplicateSubscription);
            }
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PlannedTransform {
    pub provider_id: Id,
    pub declaration_id: Id,
    pub subscription: TransformSubscription,
    pub order: u32,
    pub reduction_owner: bool,
    pub post_tool_replace_grant: bool,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct TransformPlan {
    pub transforms: Vec<PlannedTransform>,
}

impl TransformPlan {
    pub fn admit(
        transforms: Vec<PlannedTransform>,
        declarations: &BTreeMap<Id, TransformDeclaration>,
    ) -> Result<Self, TransformViolation> {
        if transforms.is_empty() {
            return Err(TransformViolation::InvalidPlan);
        }
        let mut previous_order = None;
        let mut replacement_owner = None;
        for (index, planned) in transforms.iter().enumerate() {
            planned.subscription.validate()?;
            if previous_order.is_some_and(|order| planned.order <= order) {
                return Err(TransformViolation::InvalidOrder);
            }
            previous_order = Some(planned.order);
            let declaration = declarations
                .get(&planned.provider_id)
                .filter(|declaration| declaration.declaration_id == planned.declaration_id)
                .ok_or(TransformViolation::StaleDeclaration)?;
            let declared = declaration
                .subscriptions
                .iter()
                .find(|declared| declared.permits(&planned.subscription))
                .ok_or(TransformViolation::PlanWidensDeclaration)?;
            if planned.subscription.ops.contains(&TextOperation::Replace) {
                if !planned.reduction_owner || replacement_owner.replace(index).is_some() {
                    return Err(TransformViolation::MultipleReductionOwners);
                }
                if planned.subscription.hook == Hook::PostTool && !planned.post_tool_replace_grant {
                    return Err(TransformViolation::MissingOwnerGrant);
                }
            } else if planned.reduction_owner || planned.post_tool_replace_grant {
                return Err(TransformViolation::InvalidPlan);
            }
            if declared.hook != planned.subscription.hook {
                return Err(TransformViolation::PlanWidensDeclaration);
            }
        }
        if replacement_owner.is_some_and(|index| index != 0) {
            return Err(TransformViolation::ReductionOwnerMustRunFirst);
        }
        Ok(Self { transforms })
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct HookRequest {
    pub call_id: Id,
    pub declaration_id: Id,
    pub scope: Scope,
    pub hook: Hook,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub phase: Option<Phase>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_name: Option<String>,
    pub subject: String,
    pub now_ms: u64,
    pub deadline_ms: u64,
    pub trace: Trace,
}

impl HookRequest {
    pub fn validate(&self) -> Result<(), TransformViolation> {
        if self.deadline_ms <= self.now_ms
            || self.subject.len() > 1_048_576
            || self
                .tool_name
                .as_ref()
                .is_some_and(|tool| tool.is_empty() || tool.len() > 160)
        {
            return Err(TransformViolation::InvalidHook);
        }
        if self.hook == Hook::PreTool {
            if self.phase.is_none() || self.tool_name.is_none() {
                return Err(TransformViolation::InvalidHook);
            }
        } else if self.phase.is_some() || self.tool_name.is_some() {
            return Err(TransformViolation::InvalidHook);
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ExpiryBehavior {
    Continue,
    FailStep,
    FailRun,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum HookAnswer {
    Pass,
    Text {
        operation: TextOperation,
        text: String,
    },
    Deny {
        reason: String,
    },
    Ask {
        question: String,
        expires_at_ms: u64,
        on_expiry: ExpiryBehavior,
    },
}

impl HookAnswer {
    pub fn validate_for(
        &self,
        request: &HookRequest,
        planned: &PlannedTransform,
    ) -> Result<(), TransformViolation> {
        request.validate()?;
        let subscription = &planned.subscription;
        if request.hook != subscription.hook
            || request.declaration_id != planned.declaration_id
            || request.phase != subscription.phase
            || (!subscription.tools.is_empty()
                && request
                    .tool_name
                    .as_ref()
                    .is_none_or(|tool| !subscription.tools.contains(tool)))
            || request.deadline_ms.saturating_sub(request.now_ms) > subscription.budget_ms
        {
            return Err(TransformViolation::AnswerOutsideDeclaration);
        }
        match self {
            Self::Pass => Ok(()),
            Self::Text { operation, text } => {
                if !subscription.ops.contains(operation)
                    || text.len() > 1_048_576
                    || (*operation == TextOperation::Replace
                        && subscription.hook == Hook::PostTool
                        && !planned.post_tool_replace_grant)
                {
                    return Err(TransformViolation::AnswerOutsideDeclaration);
                }
                Ok(())
            }
            Self::Deny { reason } => {
                if request.phase != Some(Phase::Validate)
                    || reason.is_empty()
                    || reason.len() > MAX_QUESTION_BYTES
                {
                    return Err(TransformViolation::IllegalAnswerPhase);
                }
                Ok(())
            }
            Self::Ask {
                question,
                expires_at_ms,
                ..
            } => {
                if request.phase != Some(Phase::Approve)
                    || question.is_empty()
                    || question.len() > MAX_QUESTION_BYTES
                    || *expires_at_ms <= request.now_ms
                {
                    return Err(TransformViolation::IllegalAnswerPhase);
                }
                Ok(())
            }
        }
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct DurableHookState {
    pub call_id: Id,
    pub provider_id: Id,
    pub point: HookDurablePoint,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub answer: Option<HookAnswer>,
}

impl DurableHookState {
    pub fn begin(call_id: Id, provider_id: Id) -> Self {
        Self {
            call_id,
            provider_id,
            point: HookDurablePoint::RequestRecorded,
            answer: None,
        }
    }

    pub fn record_answer(&mut self, answer: HookAnswer) {
        self.answer = Some(answer);
        self.point = HookDurablePoint::AnswerRecorded;
    }

    pub fn mark_applied(&mut self) -> Result<(), TransformViolation> {
        if self.point != HookDurablePoint::AnswerRecorded {
            return Err(TransformViolation::AnswerNotDurable);
        }
        self.point = HookDurablePoint::Applied;
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum HookDurablePoint {
    RequestRecorded,
    AnswerRecorded,
    Applied,
}

pub fn unavailable_outcome(policy: UnavailablePolicy) -> UnavailableOutcome {
    match policy {
        UnavailablePolicy::Continue => UnavailableOutcome::Continue,
        UnavailablePolicy::FailStep => UnavailableOutcome::FailStep,
        UnavailablePolicy::FailRun => UnavailableOutcome::FailRun,
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum UnavailableOutcome {
    Continue,
    FailStep,
    FailRun,
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
pub enum TransformViolation {
    #[error("transform declaration is invalid")]
    InvalidDeclaration,
    #[error("transform subscription is invalid")]
    InvalidSubscription,
    #[error("pre-tool subscriptions require a phase")]
    MissingPreToolPhase,
    #[error("phases and tool filters are only legal for pre-tool hooks")]
    PhaseOutsidePreTool,
    #[error("declaration contains duplicate hook and phase bounds")]
    DuplicateSubscription,
    #[error("transform plan is invalid")]
    InvalidPlan,
    #[error("transform plan order is not strictly increasing")]
    InvalidOrder,
    #[error("transform plan references a stale declaration")]
    StaleDeclaration,
    #[error("transform plan widens its declaration")]
    PlanWidensDeclaration,
    #[error("only one transform may own replacement")]
    MultipleReductionOwners,
    #[error("the reduction owner must run first")]
    ReductionOwnerMustRunFirst,
    #[error("post-tool replacement requires an explicit owner grant")]
    MissingOwnerGrant,
    #[error("hook request is invalid")]
    InvalidHook,
    #[error("provider answer exceeds the admitted declaration")]
    AnswerOutsideDeclaration,
    #[error("provider answer is illegal in this hook phase")]
    IllegalAnswerPhase,
    #[error("hook answer must be durable before application")]
    AnswerNotDurable,
}
