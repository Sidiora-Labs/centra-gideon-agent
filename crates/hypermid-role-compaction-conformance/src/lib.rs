use hypermid_contracts::{Id, Trace};
use hypermid_role_compaction::{
    AnswerDisposition, CompactionAnswer, CompactionState, SetupRequest, SetupResponse, StepRequest,
    COMPACTION_OPERATIONS, COMPACTION_ROLE_V1,
};
use hypermid_role_harness::{
    CrashDriver, DurablePoint, HarnessError, KillMechanism, ProcessRoute, ProviderRoute,
    RequestEnvelope, RoleDescriptor,
};
use serde::{Deserialize, Serialize};
use std::time::Duration;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ConformanceReport {
    pub route_exercised: bool,
    pub setup_pure: bool,
    pub step_fenced: bool,
    pub crash_recovered: bool,
}

impl ConformanceReport {
    pub fn passed(&self) -> bool {
        self.route_exercised && self.setup_pure && self.step_fenced && self.crash_recovered
    }
}

pub async fn run_live_route(
    route: &dyn ProviderRoute,
    setup: SetupRequest,
    mut step: StepRequest,
) -> Result<ConformanceReport, ConformanceError> {
    setup.validate()?;
    step.validate()?;

    let description = request(
        route,
        "describe",
        serde_json::json!({}),
        "compaction-describe",
    )
    .await?;
    let descriptor: RoleDescriptor = serde_json::from_value(description)?;
    for operation in COMPACTION_OPERATIONS {
        descriptor.require(COMPACTION_ROLE_V1, operation)?;
    }

    let setup_value = serde_json::to_value(&setup)?;
    let first = request(route, "setup", setup_value.clone(), "compaction-setup-1").await?;
    let second = request(route, "setup", setup_value, "compaction-setup-2").await?;
    let first_setup: SetupResponse = serde_json::from_value(first.clone())?;
    first_setup.validate()?;
    let second_setup: SetupResponse = serde_json::from_value(second.clone())?;
    second_setup.validate()?;
    if first != second {
        return Err(ConformanceError::SetupImpure);
    }
    step.session_handle = first_setup.session_handle;

    let step_value = request(
        route,
        "step",
        serde_json::to_value(&step)?,
        "compaction-step",
    )
    .await?;
    let answer: CompactionAnswer = serde_json::from_value(step_value)?;
    let mut state = CompactionState::default();
    state.begin(&step)?;
    let transcript = step
        .delta
        .messages
        .iter()
        .map(|message| message.message.clone())
        .collect::<Vec<_>>();
    let disposition = state.observe(&answer, step.now_ms + 1, &transcript)?;
    if !matches!(
        disposition,
        AnswerDisposition::Applied | AnswerDisposition::Waiting | AnswerDisposition::Refused
    ) {
        return Err(ConformanceError::StepFenceFailed);
    }

    Ok(ConformanceReport {
        route_exercised: true,
        setup_pure: true,
        step_fenced: true,
        crash_recovered: false,
    })
}

pub async fn prove_crash_recovery(
    route: &ProcessRoute,
    point_name: &str,
) -> Result<(), ConformanceError> {
    let point = DurablePoint::new(point_name, [KillMechanism::Kill])?;
    route.with_harness(|harness| harness.wait_for_point(&point, Duration::from_secs(5)))?;
    let mut driver = CrashDriver::default();
    route.with_harness(|harness| driver.cut(harness, &point, KillMechanism::Kill))?;
    if !driver.exercised_real_process() {
        return Err(ConformanceError::NoRealCrash);
    }
    route.with_harness(|harness| harness.restart())?;
    let value = request(
        route,
        "describe",
        serde_json::json!({}),
        "compaction-after-crash",
    )
    .await?;
    let descriptor: RoleDescriptor = serde_json::from_value(value)?;
    descriptor.require(COMPACTION_ROLE_V1, "step")?;
    Ok(())
}

async fn request(
    route: &dyn ProviderRoute,
    method: &str,
    params: serde_json::Value,
    request_id: &str,
) -> Result<serde_json::Value, ConformanceError> {
    let request_id = Id::new(request_id)?;
    let envelope = RequestEnvelope::new(
        COMPACTION_ROLE_V1,
        method,
        params,
        Trace::new(Id::new("compaction-conformance")?, request_id.clone()),
        None,
    )?;
    let response = route.request(envelope).await?;
    response.validate()?;
    response.result.ok_or(ConformanceError::ProviderFailure)
}

#[derive(Debug, thiserror::Error)]
pub enum ConformanceError {
    #[error("setup output changed for identical declared inputs")]
    SetupImpure,
    #[error("step answer did not satisfy request fencing")]
    StepFenceFailed,
    #[error("provider returned an error response")]
    ProviderFailure,
    #[error("crash suite did not kill a real process")]
    NoRealCrash,
    #[error(transparent)]
    Contract(#[from] hypermid_contracts::ContractViolation),
    #[error(transparent)]
    Harness(#[from] HarnessError),
    #[error(transparent)]
    Protocol(#[from] hypermid_role_compaction::CompactionViolation),
    #[error(transparent)]
    Json(#[from] serde_json::Error),
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use hypermid_contracts::Cursor;
    use hypermid_role_compaction::{DeltaMessage, MessageDelta, ModelGeometry, RequestEstimate};
    use hypermid_role_harness::{LiveProcessHarness, MessageRole, RoleMessage};

    fn step_request() -> StepRequest {
        let message = RoleMessage {
            message_id: Id::new("message-1").unwrap(),
            ordinal: 0,
            role: MessageRole::User,
            content: "retain this".into(),
            original: None,
            run_id: None,
            tool: None,
        };
        let entries = vec![DeltaMessage {
            cursor: Cursor::new(1, 1).unwrap(),
            message,
        }];
        let delivered_bytes = serde_json::to_vec(&entries).unwrap().len() as u64;
        StepRequest {
            session_handle: Id::new("session-1").unwrap(),
            request_id: Id::new("request-1").unwrap(),
            lineage_id: Id::new("lineage-1").unwrap(),
            step_id: Id::new("step-1").unwrap(),
            step_kind: "model_request".into(),
            geometry: ModelGeometry {
                context_window: 16_384,
                output_limit: 2_048,
            },
            previous_usage: None,
            provider_failure: None,
            estimate: RequestEstimate {
                input_bytes: 100,
                input_tokens: 25,
                reserved_output_tokens: 128,
            },
            rebuild_reason: None,
            newest_message: None,
            last_applied: None,
            last_rejected: None,
            delta: MessageDelta {
                after: Some(Cursor::new(1, 0).unwrap()),
                messages: entries,
                next: Some(Cursor::new(1, 1).unwrap()),
                byte_cap: 65_536,
                delivered_bytes,
                truncated: false,
            },
            now_ms: 1_000,
            deadline_ms: 2_000,
        }
    }

    fn process_route(crash_at: Option<&str>) -> ProcessRoute {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.keep().join("compaction-state");
        let runtime = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../..")
            .canonicalize()
            .unwrap()
            .join("runtime");
        let runtime = format!("'{}'", runtime.display().to_string().replace('\'', "'\\''"));
        let command = match crash_at {
            Some(point) => format!(
                "PYTHONPATH={runtime} exec python3 -m gideon.hypermid.compaction --serve --crash-at {point}"
            ),
            None => format!(
                "PYTHONPATH={runtime} exec python3 -m gideon.hypermid.compaction --serve"
            ),
        };
        ProcessRoute::new(
            LiveProcessHarness::new("/bin/sh", vec!["-c".into(), command], root).unwrap(),
        )
        .unwrap()
    }

    #[tokio::test]
    async fn production_adapter_passes_live_route_and_real_crash_checks() {
        let route = process_route(None);
        let setup = SetupRequest {
            preset: "bounded".into(),
            parameters: Default::default(),
            composition: serde_json::json!({"summary":"v1"}),
            configuration: serde_json::json!({"max_messages":32}),
        };
        let report = run_live_route(&route, setup, step_request()).await.unwrap();
        assert!(report.route_exercised && report.setup_pure && report.step_fenced);

        let crash_route = process_route(Some("step_recorded"));
        prove_crash_recovery(&crash_route, "step_recorded")
            .await
            .unwrap();
    }

    #[test]
    fn independent_vectors_pin_replacement_choices() {
        let vectors: serde_json::Value = serde_json::from_str(include_str!(
            "../../hypermid-role-vectors/compaction-v1/cases.json"
        ))
        .unwrap();
        for case in vectors["directives"].as_array().unwrap() {
            let directive = serde_json::from_value(case["value"].clone());
            assert_eq!(
                directive
                    .map(|value: hypermid_role_compaction::CompactionDirective| value
                        .validate(None)
                        .is_ok())
                    .unwrap_or(false),
                case["valid"].as_bool().unwrap()
            );
        }
    }
}
