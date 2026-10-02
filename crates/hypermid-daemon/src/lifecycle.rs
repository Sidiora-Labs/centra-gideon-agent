use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Component, Path, PathBuf},
};

use hypermid_contracts::{Cursor, Digest, Scope};
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum LifecycleOperation {
    Install,
    Update,
    Uninstall,
    Migrate,
    Export,
    Restore,
    Rollback,
}

impl LifecycleOperation {
    pub fn wire_name(self) -> &'static str {
        match self {
            Self::Install => "lifecycle.install.apply",
            Self::Update => "lifecycle.update.apply",
            Self::Uninstall => "lifecycle.uninstall.apply",
            Self::Migrate => "lifecycle.migrate.apply",
            Self::Export => "lifecycle.export.apply",
            Self::Restore => "lifecycle.restore.apply",
            Self::Rollback => "lifecycle.rollback.apply",
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum DataDisposition {
    Retain,
    Export,
    Purge,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum StepEffect {
    Read,
    WriteDerivative,
    WriteAuthoritative,
    DeleteDerivative,
    DeleteAuthoritative,
    Restart,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum StepState {
    Planned,
    Running,
    Committed,
    Skipped,
    Failed,
    Cancelled,
    Unknown,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct LifecycleStep {
    pub id: String,
    pub title: String,
    pub effect: StepEffect,
    pub state: StepState,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub detail: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct LifecyclePlanInput {
    pub operation: LifecycleOperation,
    pub scope: Scope,
    pub created_at: String,
    pub expires_at: String,
    pub destructive: bool,
    pub restart_required: bool,
    pub steps: Vec<LifecycleStep>,
    pub blockers: Vec<String>,
    pub authority_digest: Digest,
    pub blocker_digest: Digest,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub current_version: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub target_version: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub data_disposition: Option<DataDisposition>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_digest: Option<Digest>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub destination: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub rollback_digest: Option<Digest>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub staging_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub resume_after: Option<Cursor>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub estimated_items: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub estimated_bytes: Option<u64>,
    #[serde(default)]
    pub inventory: BTreeMap<String, Vec<String>>,
    #[serde(default)]
    pub exclusions: BTreeSet<String>,
    #[serde(default)]
    pub checks: BTreeMap<String, bool>,
    #[serde(default)]
    pub params: BTreeMap<String, Value>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct LifecyclePlan {
    pub plan_id: String,
    pub plan_digest: Digest,
    #[serde(flatten)]
    pub input: LifecyclePlanInput,
}

impl LifecyclePlan {
    pub fn new(input: LifecyclePlanInput) -> Result<Self, LifecycleError> {
        validate_plan_input(&input)?;
        let bytes = serde_json::to_vec(&input)?;
        let plan_digest = Digest::sha256(bytes);
        let plan_id = format!("lifecycle-{}", &plan_digest.to_hex()[..24]);
        Ok(Self {
            plan_id,
            plan_digest,
            input,
        })
    }

    pub fn validate_apply(&self, evidence: &ApplyEvidence) -> Result<(), LifecycleError> {
        validate_apply(self, evidence)
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ApplyEvidence {
    pub reviewed_plan_digest: Digest,
    pub current_authority_digest: Digest,
    pub current_blocker_digest: Digest,
    pub observed_at: String,
    pub confirm_destructive: bool,
    pub confirm_purge: bool,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ExecutionState {
    Running,
    Committed,
    Failed,
    Cancelled,
    OutcomeUnknown,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ExecutionResult {
    pub job_id: String,
    pub state: ExecutionState,
    pub started_at: String,
    pub finished_at: Option<String>,
    pub cursor: Cursor,
    pub steps: Vec<LifecycleStep>,
    pub rollback_available: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub artifact_digest: Option<Digest>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub artifact_bytes: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub artifact_path: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error_code: Option<String>,
    #[serde(default)]
    pub resume_supported: bool,
    #[serde(default)]
    pub effect_started: bool,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct LifecycleReceipt {
    pub job_id: String,
    pub operation: String,
    pub scope: Scope,
    pub plan_digest: Digest,
    pub state: ExecutionState,
    pub started_at: String,
    pub finished_at: Option<String>,
    pub cursor: Cursor,
    pub steps: Vec<LifecycleStep>,
    pub rollback_available: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub artifact_digest: Option<Digest>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub artifact_bytes: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub artifact_path: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error_code: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
struct JournalRecord {
    receipt: LifecycleReceipt,
    resume_supported: bool,
    effect_started: bool,
    record_digest: Digest,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RecoveryState {
    Committed,
    Resumable,
    RollbackAvailable,
    Failed,
    Cancelled,
    OutcomeUnknown,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct RecoveredLifecycle {
    pub receipt: LifecycleReceipt,
    pub recovery_state: RecoveryState,
}

pub struct LifecycleJournal {
    root: PathBuf,
}

impl LifecycleJournal {
    pub fn open(root: impl AsRef<Path>) -> Result<Self, LifecycleError> {
        let root = root.as_ref().to_path_buf();
        fs::create_dir_all(&root)?;
        set_owner_only_directory(&root)?;
        Ok(Self { root })
    }

    pub fn commit(
        &self,
        plan: &LifecyclePlan,
        evidence: &ApplyEvidence,
        result: ExecutionResult,
    ) -> Result<LifecycleReceipt, LifecycleError> {
        validate_apply(plan, evidence)?;
        validate_execution(plan, &result)?;
        let receipt = LifecycleReceipt {
            job_id: result.job_id,
            operation: plan.input.operation.wire_name().to_owned(),
            scope: plan.input.scope.clone(),
            plan_digest: plan.plan_digest,
            state: result.state,
            started_at: result.started_at,
            finished_at: result.finished_at,
            cursor: result.cursor,
            steps: result.steps,
            rollback_available: result.rollback_available,
            artifact_digest: result.artifact_digest,
            artifact_bytes: result.artifact_bytes,
            artifact_path: result.artifact_path,
            error_code: result.error_code,
        };
        let unsigned = serde_json::to_vec(&receipt)?;
        let record = JournalRecord {
            receipt: receipt.clone(),
            resume_supported: result.resume_supported,
            effect_started: result.effect_started,
            record_digest: Digest::sha256(unsigned),
        };
        atomic_write_json(&self.root, &receipt.job_id, &record)?;
        Ok(receipt)
    }

    pub fn recover(&self, job_id: &str) -> Result<RecoveredLifecycle, LifecycleError> {
        validate_identifier(job_id, "job id")?;
        let path = self.root.join(format!("{job_id}.json"));
        let mut bytes = Vec::new();
        File::open(path)?.read_to_end(&mut bytes)?;
        let record: JournalRecord = serde_json::from_slice(&bytes)?;
        let expected = Digest::sha256(serde_json::to_vec(&record.receipt)?);
        if expected != record.record_digest {
            return Err(LifecycleError::JournalDigestMismatch);
        }
        let recovery_state = match record.receipt.state {
            ExecutionState::Running => RecoveryState::Resumable,
            ExecutionState::Committed => RecoveryState::Committed,
            ExecutionState::Failed if record.receipt.rollback_available => {
                RecoveryState::RollbackAvailable
            }
            ExecutionState::Failed if record.resume_supported => RecoveryState::Resumable,
            ExecutionState::Failed => RecoveryState::Failed,
            ExecutionState::Cancelled => RecoveryState::Cancelled,
            ExecutionState::OutcomeUnknown => RecoveryState::OutcomeUnknown,
        };
        Ok(RecoveredLifecycle {
            receipt: record.receipt,
            recovery_state,
        })
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct RestoreFile {
    pub path: String,
    pub bytes: u64,
    pub digest: Digest,
    #[serde(flatten)]
    pub extensions: BTreeMap<String, Value>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct RestoreManifestBody {
    pub schema_version: u64,
    pub scope: Scope,
    pub cursor: Cursor,
    pub files: Vec<RestoreFile>,
    #[serde(flatten)]
    pub extensions: BTreeMap<String, Value>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct RestoreManifest {
    #[serde(flatten)]
    pub body: RestoreManifestBody,
    pub artifact_digest: Digest,
}

impl RestoreManifest {
    pub fn verify(
        &self,
        stage_root: impl AsRef<Path>,
        expected_scope: &Scope,
    ) -> Result<(), LifecycleError> {
        if &self.body.scope != expected_scope {
            return Err(LifecycleError::RestoreScopeMismatch);
        }
        let body_digest = Digest::sha256(serde_json::to_vec(&self.body)?);
        if body_digest != self.artifact_digest {
            return Err(LifecycleError::RestoreManifestDigestMismatch);
        }
        let stage_root = stage_root.as_ref().canonicalize()?;
        let mut seen = BTreeSet::new();
        for entry in &self.body.files {
            let relative = safe_relative_path(&entry.path)?;
            if !seen.insert(relative.clone()) {
                return Err(LifecycleError::DuplicateRestorePath(entry.path.clone()));
            }
            let path = stage_root.join(relative);
            let metadata = fs::symlink_metadata(&path)?;
            if !metadata.file_type().is_file() || metadata.len() != entry.bytes {
                return Err(LifecycleError::RestoreFileMismatch(entry.path.clone()));
            }
            let canonical = path.canonicalize()?;
            if !canonical.starts_with(&stage_root) {
                return Err(LifecycleError::UnsafeRestorePath(entry.path.clone()));
            }
            let mut file = File::open(&canonical)?;
            let mut bytes = Vec::with_capacity(usize::try_from(entry.bytes).unwrap_or(0));
            file.read_to_end(&mut bytes)?;
            if Digest::sha256(bytes) != entry.digest {
                return Err(LifecycleError::RestoreFileMismatch(entry.path.clone()));
            }
        }
        Ok(())
    }
}

pub fn restore_atomically(
    staged: impl AsRef<Path>,
    active: impl AsRef<Path>,
    rollback: impl AsRef<Path>,
    manifest: &RestoreManifest,
    expected_scope: &Scope,
) -> Result<(), LifecycleError> {
    let staged = staged.as_ref();
    let active = active.as_ref();
    let rollback = rollback.as_ref();
    let stage_parent = staged.parent().ok_or(LifecycleError::RestoreLayout)?;
    if active.parent() != Some(stage_parent) || rollback.parent() != Some(stage_parent) {
        return Err(LifecycleError::RestoreLayout);
    }
    if rollback.exists() || !active.is_dir() || !staged.is_dir() {
        return Err(LifecycleError::RestoreLayout);
    }
    manifest.verify(staged, expected_scope)?;
    fs::rename(active, rollback)?;
    if let Err(error) = fs::rename(staged, active) {
        fs::rename(rollback, active).map_err(|rollback_error| {
            LifecycleError::RestoreRollbackFailed {
                apply: error.to_string(),
                rollback: rollback_error.to_string(),
            }
        })?;
        return Err(error.into());
    }
    sync_directory(stage_parent)?;
    Ok(())
}

fn validate_plan_input(input: &LifecyclePlanInput) -> Result<(), LifecycleError> {
    validate_timestamp(&input.created_at)?;
    validate_timestamp(&input.expires_at)?;
    if input.expires_at <= input.created_at {
        return Err(LifecycleError::InvalidPlan("expiry must follow creation"));
    }
    if input.steps.is_empty() || input.steps.len() > 256 || input.blockers.len() > 128 {
        return Err(LifecycleError::InvalidPlan(
            "steps or blockers are outside bounds",
        ));
    }
    for step in &input.steps {
        validate_identifier(&step.id, "step id")?;
        if step.title.is_empty() || step.title.len() > 240 {
            return Err(LifecycleError::InvalidPlan("step title is invalid"));
        }
    }
    match input.operation {
        LifecycleOperation::Install => {
            require_target(input)?;
            require_checks(
                input,
                &[
                    "platform_supported",
                    "directories_writable",
                    "connection_material_protected",
                    "ownership_available",
                ],
            )?;
        }
        LifecycleOperation::Update => {
            require_target(input)?;
            require_checks(
                input,
                &[
                    "candidate_verified",
                    "compatibility_floor_met",
                    "migration_validated",
                    "space_available",
                    "drain_complete",
                    "rollback_material_verified",
                ],
            )?;
            if input.rollback_digest.is_none() {
                return Err(LifecycleError::InvalidPlan(
                    "update requires rollback material",
                ));
            }
        }
        LifecycleOperation::Uninstall => {
            if input.data_disposition.is_none()
                || !input.inventory.contains_key("runtime")
                || !input.inventory.contains_key("user_data")
            {
                return Err(LifecycleError::InvalidPlan(
                    "uninstall requires separate runtime and user-data inventory",
                ));
            }
        }
        LifecycleOperation::Migrate => {
            if input.source_digest.is_none() || input.staging_id.is_none() {
                return Err(LifecycleError::InvalidPlan(
                    "migration requires a source digest and staging id",
                ));
            }
            require_checks(input, &["staged", "validated", "idempotency_checked"])?;
        }
        LifecycleOperation::Export => {
            if input.destination.as_deref().map_or(true, str::is_empty) {
                return Err(LifecycleError::InvalidPlan("export requires a destination"));
            }
            for required in [
                "credentials",
                "local_authentication_material",
                "vectors",
                "indexes",
                "leases",
                "jobs",
                "budget_reservations",
            ] {
                if !input.exclusions.contains(required) {
                    return Err(LifecycleError::InvalidPlan(
                        "export exclusions are incomplete",
                    ));
                }
            }
        }
        LifecycleOperation::Restore => {
            if input.source_digest.is_none() || input.staging_id.is_none() {
                return Err(LifecycleError::InvalidPlan(
                    "restore requires verified staged input",
                ));
            }
            require_checks(
                input,
                &[
                    "manifest_verified",
                    "scope_verified",
                    "cursor_verified",
                    "active_store_unchanged",
                ],
            )?;
        }
        LifecycleOperation::Rollback => {
            if input.rollback_digest.is_none() {
                return Err(LifecycleError::InvalidPlan("rollback material is required"));
            }
        }
    }
    Ok(())
}

fn validate_apply(plan: &LifecyclePlan, evidence: &ApplyEvidence) -> Result<(), LifecycleError> {
    validate_timestamp(&evidence.observed_at)?;
    if evidence.reviewed_plan_digest != plan.plan_digest {
        return Err(LifecycleError::PlanChanged);
    }
    if evidence.current_authority_digest != plan.input.authority_digest {
        return Err(LifecycleError::AuthorityChanged);
    }
    if evidence.current_blocker_digest != plan.input.blocker_digest
        || !plan.input.blockers.is_empty()
    {
        return Err(LifecycleError::BlockersChanged);
    }
    if evidence.observed_at > plan.input.expires_at {
        return Err(LifecycleError::PlanExpired);
    }
    if plan.input.destructive && !evidence.confirm_destructive {
        return Err(LifecycleError::ConfirmationRequired);
    }
    if plan.input.operation == LifecycleOperation::Uninstall {
        match plan.input.data_disposition {
            Some(DataDisposition::Purge) if !evidence.confirm_purge => {
                return Err(LifecycleError::PurgeConfirmationRequired)
            }
            Some(DataDisposition::Retain | DataDisposition::Export) if evidence.confirm_purge => {
                return Err(LifecycleError::PlanChanged)
            }
            _ => {}
        }
    }
    Ok(())
}

fn validate_execution(
    plan: &LifecyclePlan,
    result: &ExecutionResult,
) -> Result<(), LifecycleError> {
    validate_identifier(&result.job_id, "job id")?;
    validate_timestamp(&result.started_at)?;
    if let Some(finished_at) = result.finished_at.as_ref() {
        validate_timestamp(finished_at)?;
        if finished_at < &result.started_at {
            return Err(LifecycleError::InvalidExecution("finish precedes start"));
        }
    } else if result.state != ExecutionState::Running {
        return Err(LifecycleError::InvalidExecution(
            "terminal execution has no finish time",
        ));
    }
    if result.state == ExecutionState::Committed && result.error_code.is_some() {
        return Err(LifecycleError::InvalidExecution(
            "committed execution cannot contain an error",
        ));
    }
    if plan.input.operation == LifecycleOperation::Export
        && result.state == ExecutionState::Committed
        && (result.artifact_digest.is_none() || result.artifact_bytes.is_none())
    {
        return Err(LifecycleError::InvalidExecution(
            "committed export requires verified artifact evidence",
        ));
    }
    if plan.input.operation == LifecycleOperation::Restore
        && result.state == ExecutionState::Committed
        && result.artifact_digest != plan.input.source_digest
    {
        return Err(LifecycleError::InvalidExecution(
            "restore receipt does not bind the verified artifact",
        ));
    }
    Ok(())
}

fn require_target(input: &LifecyclePlanInput) -> Result<(), LifecycleError> {
    if input.target_version.as_deref().map_or(true, str::is_empty) {
        return Err(LifecycleError::InvalidPlan("target version is required"));
    }
    Ok(())
}

fn require_checks(input: &LifecyclePlanInput, required: &[&str]) -> Result<(), LifecycleError> {
    if required
        .iter()
        .any(|name| input.checks.get(*name).copied() != Some(true))
    {
        return Err(LifecycleError::InvalidPlan(
            "required validation did not pass",
        ));
    }
    Ok(())
}

fn validate_identifier(value: &str, name: &'static str) -> Result<(), LifecycleError> {
    if value.is_empty()
        || value.len() > 160
        || !value.as_bytes()[0].is_ascii_alphanumeric()
        || !value.as_bytes()[1..]
            .iter()
            .all(|byte| byte.is_ascii_alphanumeric() || b"._:-".contains(byte))
    {
        return Err(LifecycleError::InvalidIdentifier(name));
    }
    Ok(())
}

fn validate_timestamp(value: &str) -> Result<(), LifecycleError> {
    let bytes = value.as_bytes();
    if bytes.len() < 20
        || bytes[4] != b'-'
        || bytes[7] != b'-'
        || bytes[10] != b'T'
        || bytes[13] != b':'
        || bytes[16] != b':'
        || !value.ends_with('Z')
    {
        return Err(LifecycleError::InvalidTimestamp);
    }
    Ok(())
}

fn safe_relative_path(value: &str) -> Result<PathBuf, LifecycleError> {
    let path = Path::new(value);
    if value.is_empty()
        || path.is_absolute()
        || path
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err(LifecycleError::UnsafeRestorePath(value.to_owned()));
    }
    Ok(path.to_path_buf())
}

fn atomic_write_json(
    root: &Path,
    job_id: &str,
    value: &JournalRecord,
) -> Result<(), LifecycleError> {
    validate_identifier(job_id, "job id")?;
    let bytes = serde_json::to_vec(value)?;
    let path = root.join(format!("{job_id}.json"));
    let temporary = root.join(format!(".{job_id}.tmp"));
    let mut file = OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(&temporary)?;
    set_owner_only_file(&temporary)?;
    if let Err(error) = (|| -> Result<(), std::io::Error> {
        file.write_all(&bytes)?;
        file.sync_all()?;
        fs::rename(&temporary, &path)?;
        sync_directory(root)?;
        Ok(())
    })() {
        let _ = fs::remove_file(&temporary);
        return Err(error.into());
    }
    Ok(())
}

fn sync_directory(path: &Path) -> Result<(), std::io::Error> {
    File::open(path)?.sync_all()
}

#[cfg(unix)]
fn set_owner_only_directory(path: &Path) -> Result<(), std::io::Error> {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(path, fs::Permissions::from_mode(0o700))
}

#[cfg(not(unix))]
fn set_owner_only_directory(_path: &Path) -> Result<(), std::io::Error> {
    Ok(())
}

#[cfg(unix)]
fn set_owner_only_file(path: &Path) -> Result<(), std::io::Error> {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(path, fs::Permissions::from_mode(0o600))
}

#[cfg(not(unix))]
fn set_owner_only_file(_path: &Path) -> Result<(), std::io::Error> {
    Ok(())
}

#[derive(Debug, thiserror::Error)]
pub enum LifecycleError {
    #[error(transparent)]
    Io(#[from] std::io::Error),
    #[error(transparent)]
    Json(#[from] serde_json::Error),
    #[error("invalid lifecycle plan: {0}")]
    InvalidPlan(&'static str),
    #[error("invalid lifecycle execution: {0}")]
    InvalidExecution(&'static str),
    #[error("invalid {0}")]
    InvalidIdentifier(&'static str),
    #[error("invalid lifecycle timestamp")]
    InvalidTimestamp,
    #[error("reviewed plan digest does not match")]
    PlanChanged,
    #[error("lifecycle plan expired")]
    PlanExpired,
    #[error("lifecycle authority changed")]
    AuthorityChanged,
    #[error("lifecycle blockers changed or remain active")]
    BlockersChanged,
    #[error("destructive lifecycle operation requires confirmation")]
    ConfirmationRequired,
    #[error("data purge requires a distinct confirmation")]
    PurgeConfirmationRequired,
    #[error("lifecycle journal digest mismatch")]
    JournalDigestMismatch,
    #[error("restore scope does not match the active scope")]
    RestoreScopeMismatch,
    #[error("restore manifest digest mismatch")]
    RestoreManifestDigestMismatch,
    #[error("unsafe restore path: {0}")]
    UnsafeRestorePath(String),
    #[error("duplicate restore path: {0}")]
    DuplicateRestorePath(String),
    #[error("restore file does not match its manifest: {0}")]
    RestoreFileMismatch(String),
    #[error("restore paths must be distinct siblings and active state must exist")]
    RestoreLayout,
    #[error("restore failed ({apply}) and rollback also failed ({rollback})")]
    RestoreRollbackFailed { apply: String, rollback: String },
}

#[cfg(test)]
mod tests {
    use super::*;
    use hypermid_contracts::Id;
    use tempfile::tempdir;

    fn scope() -> Scope {
        Scope::new(
            Id::new("owner-1").unwrap(),
            Id::new("project-1").unwrap(),
            None,
        )
    }

    fn step() -> LifecycleStep {
        LifecycleStep {
            id: "verify".into(),
            title: "Verify restore".into(),
            effect: StepEffect::Read,
            state: StepState::Planned,
            detail: None,
        }
    }

    #[test]
    fn safe_restore_rejects_corruption_before_active_state_changes() {
        let temporary = tempdir().unwrap();
        let active = temporary.path().join("active");
        let staged = temporary.path().join("staged");
        let rollback = temporary.path().join("rollback");
        fs::create_dir(&active).unwrap();
        fs::create_dir(&staged).unwrap();
        fs::write(active.join("store.db"), b"authoritative-old").unwrap();
        fs::write(staged.join("store.db"), b"corrupt-new").unwrap();
        let body = RestoreManifestBody {
            schema_version: 1,
            scope: scope(),
            cursor: Cursor::new(4, 9).unwrap(),
            files: vec![RestoreFile {
                path: "store.db".into(),
                bytes: 11,
                digest: Digest::sha256(b"expected-new"),
                extensions: BTreeMap::from([("future".into(), Value::Bool(true))]),
            }],
            extensions: BTreeMap::from([("future_manifest".into(), Value::from(7))]),
        };
        let manifest = RestoreManifest {
            artifact_digest: Digest::sha256(serde_json::to_vec(&body).unwrap()),
            body,
        };

        let error =
            restore_atomically(&staged, &active, &rollback, &manifest, &scope()).unwrap_err();
        assert!(matches!(error, LifecycleError::RestoreFileMismatch(_)));
        assert_eq!(
            fs::read(active.join("store.db")).unwrap(),
            b"authoritative-old"
        );
        assert!(!rollback.exists());
    }

    #[test]
    fn plan_apply_and_journal_recovery_bind_current_evidence() {
        let input = LifecyclePlanInput {
            operation: LifecycleOperation::Uninstall,
            scope: scope(),
            created_at: "2026-10-02T12:00:00Z".into(),
            expires_at: "2026-10-02T12:10:00Z".into(),
            destructive: true,
            restart_required: true,
            steps: vec![step()],
            blockers: vec![],
            authority_digest: Digest::sha256(b"authority"),
            blocker_digest: Digest::sha256(b"blockers"),
            current_version: Some("1.0.0".into()),
            target_version: None,
            data_disposition: Some(DataDisposition::Retain),
            source_digest: None,
            destination: None,
            rollback_digest: None,
            staging_id: None,
            resume_after: None,
            estimated_items: Some(2),
            estimated_bytes: Some(16),
            inventory: BTreeMap::from([
                ("runtime".into(), vec!["daemon".into()]),
                ("user_data".into(), vec!["store".into()]),
            ]),
            exclusions: BTreeSet::new(),
            checks: BTreeMap::new(),
            params: BTreeMap::new(),
        };
        let plan = LifecyclePlan::new(input).unwrap();
        let evidence = ApplyEvidence {
            reviewed_plan_digest: plan.plan_digest,
            current_authority_digest: plan.input.authority_digest,
            current_blocker_digest: plan.input.blocker_digest,
            observed_at: "2026-10-02T12:01:00Z".into(),
            confirm_destructive: true,
            confirm_purge: false,
        };
        let journal_root = tempdir().unwrap();
        let journal = LifecycleJournal::open(journal_root.path()).unwrap();
        let receipt = journal
            .commit(
                &plan,
                &evidence,
                ExecutionResult {
                    job_id: "job-1".into(),
                    state: ExecutionState::Committed,
                    started_at: "2026-10-02T12:01:01Z".into(),
                    finished_at: Some("2026-10-02T12:01:02Z".into()),
                    cursor: Cursor::new(4, 10).unwrap(),
                    steps: vec![LifecycleStep {
                        state: StepState::Committed,
                        ..step()
                    }],
                    rollback_available: false,
                    artifact_digest: None,
                    artifact_bytes: None,
                    artifact_path: None,
                    error_code: None,
                    resume_supported: false,
                    effect_started: true,
                },
            )
            .unwrap();
        assert_eq!(receipt.plan_digest, plan.plan_digest);
        assert_eq!(
            journal.recover("job-1").unwrap().recovery_state,
            RecoveryState::Committed
        );
    }
}
