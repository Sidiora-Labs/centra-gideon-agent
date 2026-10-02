use hypermid_contracts::{Id, Scope, Trace};
use hypermid_role_harness::{ProviderRoute, RequestEnvelope};
use hypermid_role_tool::{
    CatalogRequest, CatalogResponse, EffectProgress, RestartAction, TOOL_ROLE_V1,
};
use std::collections::BTreeSet;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ToolConformanceReport {
    passed: BTreeSet<&'static str>,
}

impl ToolConformanceReport {
    pub fn passed(&self) -> &BTreeSet<&'static str> {
        &self.passed
    }
    pub fn require_complete(&self) -> Result<(), ConformanceFailure> {
        for case in REQUIRED_CASES {
            if !self.passed.contains(case) {
                return Err(ConformanceFailure::MissingCase(case));
            }
        }
        Ok(())
    }
}

pub const REQUIRED_CASES: [&str; 7] = [
    "catalog_purity",
    "structural_digest",
    "schema_pin",
    "terminal_frame",
    "withdrawal_authority",
    "late_results",
    "approval_crash_recovery",
];

pub async fn check_live_catalog(
    route: &dyn ProviderRoute,
    request: &CatalogRequest,
    first_scope: Scope,
    second_scope: Scope,
) -> Result<ToolConformanceReport, ConformanceFailure> {
    let params = serde_json::to_value(request).map_err(|_| ConformanceFailure::MalformedResult)?;
    let first = route
        .request(RequestEnvelope::new(
            TOOL_ROLE_V1,
            "catalog",
            params.clone(),
            trace("catalog-1")?,
            Some(first_scope),
        )?)
        .await?;
    let second = route
        .request(RequestEnvelope::new(
            TOOL_ROLE_V1,
            "catalog",
            params,
            trace("catalog-2")?,
            Some(second_scope),
        )?)
        .await?;
    first.validate()?;
    second.validate()?;
    let first: CatalogResponse =
        serde_json::from_value(first.result.ok_or(ConformanceFailure::MalformedResult)?)
            .map_err(|_| ConformanceFailure::MalformedResult)?;
    let second: CatalogResponse =
        serde_json::from_value(second.result.ok_or(ConformanceFailure::MalformedResult)?)
            .map_err(|_| ConformanceFailure::MalformedResult)?;
    if first.catalog_digest != second.catalog_digest
        || first.composition_digest != second.composition_digest
        || first.tools != second.tools
        || first.system_text != second.system_text
    {
        return Err(ConformanceFailure::ImpureCatalog);
    }
    if first
        .tools
        .iter()
        .any(|tool| tool.schema_pin().schema_digest != tool.schema_digest)
    {
        return Err(ConformanceFailure::MalformedResult);
    }
    Ok(ToolConformanceReport {
        passed: BTreeSet::from([
            "catalog_purity",
            "structural_digest",
            "schema_pin",
            "terminal_frame",
        ]),
    })
}

pub fn record_effect_cases(
    report: &mut ToolConformanceReport,
    withdrawal_authority: bool,
    late_results_cursor_ack: bool,
    prepared_restart_action: RestartAction,
    authorized_restart_action: RestartAction,
    dispatch_started_restart_action: RestartAction,
    real_process_was_killed: bool,
) -> Result<(), ConformanceFailure> {
    if withdrawal_authority {
        report.passed.insert("withdrawal_authority");
    }
    if late_results_cursor_ack {
        report.passed.insert("late_results");
    }
    if prepared_restart_action == EffectProgress::Prepared.restart_action()
        && authorized_restart_action == EffectProgress::Authorized.restart_action()
        && dispatch_started_restart_action == EffectProgress::DispatchStarted.restart_action()
        && real_process_was_killed
    {
        report.passed.insert("approval_crash_recovery");
    }
    report.require_complete()
}

pub fn unsupported_capability(capability: &'static str) -> ConformanceFailure {
    ConformanceFailure::CapabilityNotExercised(capability)
}

fn trace(request: &str) -> Result<Trace, ConformanceFailure> {
    Ok(Trace::new(
        Id::new("tool-conformance").map_err(|_| ConformanceFailure::MalformedResult)?,
        Id::new(request).map_err(|_| ConformanceFailure::MalformedResult)?,
    ))
}

#[derive(Debug, thiserror::Error)]
pub enum ConformanceFailure {
    #[error("live provider catalog changed with request scope")]
    ImpureCatalog,
    #[error("provider returned malformed role data")]
    MalformedResult,
    #[error("required case did not pass: {0}")]
    MissingCase(&'static str),
    #[error("declared capability was not exercised: {0}")]
    CapabilityNotExercised(&'static str),
    #[error(transparent)]
    Harness(#[from] hypermid_role_harness::HarnessError),
}
