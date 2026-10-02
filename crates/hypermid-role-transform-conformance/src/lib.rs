use hypermid_contracts::{Id, Trace};
use hypermid_role_harness::{
    CrashDriver, DurablePoint, HarnessError, KillMechanism, ProcessRoute, ProviderRoute,
    RequestEnvelope, RoleDescriptor,
};
use hypermid_role_transform::{
    DeclareRequest, HookAnswer, HookRequest, PlannedTransform, TransformDeclaration,
    TransformViolation, TRANSFORM_OPERATIONS, TRANSFORM_ROLE_V1,
};
use serde::{Deserialize, Serialize};
use std::time::Duration;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ConformanceReport {
    pub route_exercised: bool,
    pub declaration_pure: bool,
    pub answer_bounded: bool,
    pub crash_recovered: bool,
}

impl ConformanceReport {
    pub fn passed(&self) -> bool {
        self.route_exercised && self.declaration_pure && self.answer_bounded && self.crash_recovered
    }
}

pub async fn run_live_route(
    route: &dyn ProviderRoute,
    declaration_request: DeclareRequest,
    hook_request: HookRequest,
    planned: PlannedTransform,
) -> Result<ConformanceReport, ConformanceError> {
    declaration_request.validate()?;
    hook_request.validate()?;

    let description = request(
        route,
        "describe",
        serde_json::json!({}),
        "transform-describe",
    )
    .await?;
    let descriptor: RoleDescriptor = serde_json::from_value(description)?;
    for operation in TRANSFORM_OPERATIONS {
        descriptor.require(TRANSFORM_ROLE_V1, operation)?;
    }

    let declaration_value = serde_json::to_value(&declaration_request)?;
    let first = request(
        route,
        "declare",
        declaration_value.clone(),
        "transform-declare-1",
    )
    .await?;
    let second = request(route, "declare", declaration_value, "transform-declare-2").await?;
    let declaration: TransformDeclaration = serde_json::from_value(first.clone())?;
    declaration.validate()?;
    if first != second || declaration.declaration_id != planned.declaration_id {
        return Err(ConformanceError::DeclarationImpure);
    }

    let answer_value = request(
        route,
        "hook",
        serde_json::to_value(&hook_request)?,
        "transform-hook",
    )
    .await?;
    let answer: HookAnswer = serde_json::from_value(answer_value)?;
    answer.validate_for(&hook_request, &planned)?;

    Ok(ConformanceReport {
        route_exercised: true,
        declaration_pure: true,
        answer_bounded: true,
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
        "transform-after-crash",
    )
    .await?;
    let descriptor: RoleDescriptor = serde_json::from_value(value)?;
    descriptor.require(TRANSFORM_ROLE_V1, "hook")?;
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
        TRANSFORM_ROLE_V1,
        method,
        params,
        Trace::new(Id::new("transform-conformance")?, request_id.clone()),
        None,
    )?;
    let response = route.request(envelope).await?;
    response.validate()?;
    response.result.ok_or(ConformanceError::ProviderFailure)
}

#[derive(Debug, thiserror::Error)]
pub enum ConformanceError {
    #[error("declaration output changed for identical declared inputs")]
    DeclarationImpure,
    #[error("provider returned an error response")]
    ProviderFailure,
    #[error("crash suite did not kill a real process")]
    NoRealCrash,
    #[error(transparent)]
    Contract(#[from] hypermid_contracts::ContractViolation),
    #[error(transparent)]
    Harness(#[from] HarnessError),
    #[error(transparent)]
    Protocol(#[from] TransformViolation),
    #[error(transparent)]
    Json(#[from] serde_json::Error),
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use hypermid_contracts::Scope;
    use hypermid_role_harness::LiveProcessHarness;
    use hypermid_role_transform::{
        Hook, Phase, TextOperation, TransformSubscription, UnavailablePolicy,
    };
    use std::collections::BTreeSet;

    fn subscription() -> TransformSubscription {
        TransformSubscription {
            hook: Hook::PreTool,
            phase: Some(Phase::Mutate),
            tools: BTreeSet::from(["lookup".into()]),
            ops: BTreeSet::from([TextOperation::Append]),
            on_unavailable: UnavailablePolicy::FailStep,
            budget_ms: 500,
        }
    }

    fn process_route(crash_at: Option<&str>) -> ProcessRoute {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.keep().join("transform-state");
        let runtime = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../..")
            .canonicalize()
            .unwrap()
            .join("runtime");
        let runtime = format!("'{}'", runtime.display().to_string().replace('\'', "'\\''"));
        let command = match crash_at {
            Some(point) => format!(
                "PYTHONPATH={runtime} exec python3 -m gideon.hypermid.transforms --serve --crash-at {point}"
            ),
            None => format!(
                "PYTHONPATH={runtime} exec python3 -m gideon.hypermid.transforms --serve"
            ),
        };
        ProcessRoute::new(
            LiveProcessHarness::new("/bin/sh", vec!["-c".into(), command], root).unwrap(),
        )
        .unwrap()
    }

    #[tokio::test]
    async fn production_adapter_passes_live_route_and_real_crash_checks() {
        let declare = DeclareRequest {
            preset: "safe-append".into(),
            parameters: Default::default(),
            composition: serde_json::json!({"provider":"local"}),
            configuration: serde_json::json!({"suffix":" [checked]"}),
        };
        let declaration_id = Id::new("trf-2816ef63f185a8850591977c").unwrap();
        let planned = PlannedTransform {
            provider_id: Id::new("transform-provider").unwrap(),
            declaration_id,
            subscription: subscription(),
            order: 0,
            reduction_owner: false,
            post_tool_replace_grant: false,
        };
        let hook = HookRequest {
            call_id: Id::new("hook-1").unwrap(),
            declaration_id: planned.declaration_id.clone(),
            scope: Scope::new(
                Id::new("owner-1").unwrap(),
                Id::new("project-1").unwrap(),
                None,
            ),
            hook: Hook::PreTool,
            phase: Some(Phase::Mutate),
            tool_name: Some("lookup".into()),
            subject: "query".into(),
            now_ms: 1_000,
            deadline_ms: 1_500,
            trace: Trace::new(Id::new("trace-1").unwrap(), Id::new("request-1").unwrap()),
        };
        let route = process_route(None);
        let report = run_live_route(&route, declare, hook, planned)
            .await
            .unwrap();
        assert!(report.route_exercised && report.declaration_pure && report.answer_bounded);

        let crash_route = process_route(Some("answer_recorded"));
        prove_crash_recovery(&crash_route, "answer_recorded")
            .await
            .unwrap();
    }

    #[test]
    fn independent_vectors_pin_phase_and_grant_choices() {
        let vectors: serde_json::Value = serde_json::from_str(include_str!(
            "../../hypermid-role-vectors/transform-v1/cases.json"
        ))
        .unwrap();
        for case in vectors["subscriptions"].as_array().unwrap() {
            let parsed = serde_json::from_value::<TransformSubscription>(case["value"].clone())
                .map(|subscription| subscription.validate().is_ok())
                .unwrap_or(false);
            assert_eq!(parsed, case["valid"].as_bool().unwrap());
        }
    }
}
