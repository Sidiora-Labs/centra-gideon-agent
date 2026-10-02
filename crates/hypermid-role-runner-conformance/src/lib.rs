use hypermid_contracts::{EffectState, Id};
use hypermid_role_harness::{CrashDriver, ProviderRoute, RequestEnvelope};
use hypermid_role_runner::{
    CapabilityGroup, DeliveryMode, RunResult, RunnerDescriptor, SubscriptionOrigin,
    ToolDispatchRecord, TranscriptPage, RUNNER_ROLE_V1,
};
use std::collections::BTreeSet;

pub const REQUIRED_CASES: [&str; 9] = [
    "grouped_capabilities",
    "transcript",
    "model_view",
    "run_result",
    "resumable_events",
    "delivery_modes",
    "session_changes",
    "tool_attribution",
    "crash_guarantees",
];

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RunnerConformanceReport {
    passed: BTreeSet<&'static str>,
}

impl RunnerConformanceReport {
    pub fn new() -> Self {
        Self {
            passed: BTreeSet::new(),
        }
    }
    pub fn passed(&self) -> &BTreeSet<&'static str> {
        &self.passed
    }
    pub fn mark(&mut self, case: &'static str) -> Result<(), RunnerConformanceFailure> {
        if !REQUIRED_CASES.contains(&case) {
            return Err(RunnerConformanceFailure::UnknownCase);
        }
        self.passed.insert(case);
        Ok(())
    }
    pub fn require_complete(&self) -> Result<(), RunnerConformanceFailure> {
        for case in REQUIRED_CASES {
            if !self.passed.contains(case) {
                return Err(RunnerConformanceFailure::MissingCase(case));
            }
        }
        Ok(())
    }
}

impl Default for RunnerConformanceReport {
    fn default() -> Self {
        Self::new()
    }
}

pub async fn check_live_transcript(
    route: &dyn ProviderRoute,
    request: RequestEnvelope,
) -> Result<TranscriptPage, RunnerConformanceFailure> {
    if request.role_version != RUNNER_ROLE_V1 || request.method != "transcript" {
        return Err(RunnerConformanceFailure::WrongRoute);
    }
    let response = route.request(request).await?;
    response.validate()?;
    let page: TranscriptPage = serde_json::from_value(
        response
            .result
            .ok_or(RunnerConformanceFailure::MalformedResult)?,
    )
    .map_err(|_| RunnerConformanceFailure::MalformedResult)?;
    page.validate()
        .map_err(|_| RunnerConformanceFailure::MalformedResult)?;
    Ok(page)
}

pub fn check_capabilities(descriptor: &RunnerDescriptor) -> Result<(), RunnerConformanceFailure> {
    for capability in [
        CapabilityGroup::TranscriptReads,
        CapabilityGroup::DispatchAttribution,
        CapabilityGroup::RunResults,
        CapabilityGroup::Streaming,
        CapabilityGroup::ModelView,
        CapabilityGroup::Queue,
        CapabilityGroup::Steer,
        CapabilityGroup::Interrupt,
        CapabilityGroup::Compaction,
        CapabilityGroup::SessionChange,
    ] {
        descriptor
            .require(capability)
            .map_err(|_| RunnerConformanceFailure::CapabilityNotExercised(capability))?;
    }
    Ok(())
}

pub fn check_crash_guarantees(
    driver: &CrashDriver,
    terminal_results: &[RunResult],
    dispatches: &[ToolDispatchRecord],
) -> Result<(), RunnerConformanceFailure> {
    if !driver.exercised_real_process() {
        return Err(RunnerConformanceFailure::NoRealCrash);
    }
    let mut run_ids = BTreeSet::new();
    if terminal_results
        .iter()
        .any(|result| !run_ids.insert(result.run_id.clone()))
    {
        return Err(RunnerConformanceFailure::MultipleTerminalResults);
    }
    let mut effects = BTreeSet::new();
    for dispatch in dispatches {
        if !effects.insert((dispatch.provider_id.clone(), dispatch.call_key.clone())) {
            return Err(RunnerConformanceFailure::DuplicateDispatch);
        }
        if dispatch.effect_state == EffectState::Unknown && dispatch.may_redispatch() {
            return Err(RunnerConformanceFailure::RedispatchedUnknownEffect);
        }
    }
    Ok(())
}

pub fn delivery_capability(mode: DeliveryMode) -> CapabilityGroup {
    mode.capability()
}
pub fn resume_is_explicit(origin: &SubscriptionOrigin) -> bool {
    matches!(
        origin,
        SubscriptionOrigin::Start | SubscriptionOrigin::Cursor(_) | SubscriptionOrigin::Head(_)
    )
}
pub fn unique_send_ids(ids: impl IntoIterator<Item = Id>) -> bool {
    let mut seen = BTreeSet::new();
    ids.into_iter().all(|id| seen.insert(id))
}

#[derive(Debug, thiserror::Error)]
pub enum RunnerConformanceFailure {
    #[error("request did not target the runner transcript route")]
    WrongRoute,
    #[error("runner returned malformed role data")]
    MalformedResult,
    #[error("runner capability was not exercised: {0:?}")]
    CapabilityNotExercised(CapabilityGroup),
    #[error("crash suite did not terminate a real process")]
    NoRealCrash,
    #[error("a run acquired more than one terminal result")]
    MultipleTerminalResults,
    #[error("a tool effect was dispatched more than once")]
    DuplicateDispatch,
    #[error("an indeterminate tool effect was eligible for redispatch")]
    RedispatchedUnknownEffect,
    #[error("unknown conformance case")]
    UnknownCase,
    #[error("required case did not pass: {0}")]
    MissingCase(&'static str),
    #[error(transparent)]
    Harness(#[from] hypermid_role_harness::HarnessError),
}
