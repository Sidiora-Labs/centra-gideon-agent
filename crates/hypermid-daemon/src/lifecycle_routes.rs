use std::cmp::Ordering;
use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use hypermid_artifacts::{
    stage_verified, ArtifactCapability, ArtifactStore, InstallLimits, PinnedArtifact, Platform,
    SignedArtifactManifest, TrustStore,
};
use hypermid_contracts::{Cursor, Digest, EffectState, Error, Id, Scope, Trace};
use hypermid_memory::snapshot::create_memory_snapshot;
use hypermid_memory::MemoryStore;
use hypermid_protocol::Envelope;
use hypermid_registry::RegistryLifecycle;
use hypermid_transport::AuthenticatedSession;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest as _, Sha256};

use crate::lifecycle::{
    ApplyEvidence, DataDisposition, ExecutionResult, ExecutionState, LifecycleError,
    LifecycleJournal, LifecycleOperation, LifecyclePlan, LifecyclePlanInput, LifecycleReceipt,
    LifecycleStep, RecoveryState, StepEffect, StepState,
};
use crate::startup::{PendingRestoreIntent, RecoveryAuthority, StartupRestoreReceipt};

pub const LIFECYCLE_OPERATIONS: &[&str] = &[
    "lifecycle.install.plan",
    "lifecycle.install.apply",
    "lifecycle.update.plan",
    "lifecycle.update.apply",
    "lifecycle.uninstall.plan",
    "lifecycle.uninstall.apply",
    "lifecycle.migrate.plan",
    "lifecycle.migrate.apply",
    "lifecycle.export.plan",
    "lifecycle.export.apply",
    "lifecycle.restore.plan",
    "lifecycle.restore.apply",
    "lifecycle.rollback.plan",
    "lifecycle.rollback.apply",
    "lifecycle.status",
    "lifecycle.recover",
    "lifecycle.resume",
];

const PLAN_TTL_MS: u64 = 10 * 60 * 1000;
const EXPORT_EXCLUSIONS: [&str; 7] = [
    "credentials",
    "local_authentication_material",
    "vectors",
    "indexes",
    "leases",
    "jobs",
    "budget_reservations",
];

#[derive(Clone, Debug)]
pub struct LifecycleRouteResponse {
    pub payload: Option<Value>,
    pub error: Option<Error>,
}

pub struct LifecycleRoutes {
    root: PathBuf,
    state_root: PathBuf,
    registry: RegistryLifecycle,
    artifacts: ArtifactStore,
    journal: LifecycleJournal,
    recovery: Arc<RecoveryAuthority>,
    lock: Mutex<()>,
}

impl LifecycleRoutes {
    pub fn open(
        root: impl AsRef<Path>,
        startup_restore: Option<StartupRestoreReceipt>,
        recovery: Arc<RecoveryAuthority>,
    ) -> Result<Self, Error> {
        let root = root.as_ref().to_path_buf();
        let state_root = root
            .parent()
            .ok_or_else(|| {
                lifecycle_error(
                    "LIFECYCLE_LAYOUT_INVALID",
                    "lifecycle root has no state parent",
                    EffectState::NotStarted,
                )
            })?
            .to_path_buf();
        private_directory(&root).map_err(map_io)?;
        private_directory(&root.join("plans")).map_err(map_io)?;
        private_directory(&root.join("packages")).map_err(map_io)?;
        private_directory(&state_root.join("recovery")).map_err(map_io)?;
        private_directory(&root.join("trust")).map_err(map_io)?;
        private_directory(&root.join("rollbacks")).map_err(map_io)?;
        let registry_root = root.join("registry");
        let registry =
            RegistryLifecycle::open(&registry_root).map_err(|error| map_internal(error))?;
        let artifacts = ArtifactStore::open(registry_root.join("artifacts"))
            .map_err(|error| map_internal(error))?;
        let journal =
            LifecycleJournal::open(root.join("journal")).map_err(|error| map_internal(error))?;
        let routes = Self {
            root,
            state_root,
            registry,
            artifacts,
            journal,
            recovery,
            lock: Mutex::new(()),
        };
        if let Some(receipt) = startup_restore {
            routes.reconcile_startup_restore(receipt)?;
        }
        Ok(routes)
    }

    pub fn handles(operation: &str) -> bool {
        LIFECYCLE_OPERATIONS.contains(&operation)
    }

    pub fn dispatch(
        &self,
        session: &AuthenticatedSession,
        envelope: &Envelope,
        now_ms: u64,
    ) -> LifecycleRouteResponse {
        if envelope.scope.as_ref() != Some(&session.bound_scope) {
            return failure(denied(
                "request scope does not match the authenticated session",
            ));
        }
        let Some(trace) = envelope.trace.as_ref() else {
            return failure(invalid("lifecycle requests require a trace"));
        };
        let Some(operation) = envelope.operation.as_deref() else {
            return failure(invalid("lifecycle requests require an operation"));
        };
        if !Self::handles(operation) {
            return failure(invalid("lifecycle operation is unavailable"));
        }
        let _guard = match self.lock.lock() {
            Ok(guard) => guard,
            Err(_) => {
                return failure(lifecycle_error(
                    "LIFECYCLE_LOCK_FAILED",
                    "lifecycle authority lock is unavailable",
                    EffectState::Unknown,
                ))
            }
        };
        let payload = envelope.payload.clone().unwrap_or_else(|| json!({}));
        match self.dispatch_locked(operation, payload, &session.bound_scope, trace, now_ms) {
            Ok(result) => LifecycleRouteResponse {
                payload: Some(result),
                error: None,
            },
            Err(error) => failure(error),
        }
    }

    fn dispatch_locked(
        &self,
        operation: &str,
        payload: Value,
        scope: &Scope,
        trace: &Trace,
        now_ms: u64,
    ) -> Result<Value, Error> {
        if let Some(action) = operation
            .strip_prefix("lifecycle.")
            .and_then(|value| value.strip_suffix(".plan"))
        {
            let action = parse_action(action)?;
            let request: PlanPayload = parse(payload)?;
            let plan = self.plan(action, request, scope, trace, now_ms)?;
            return encode_plan(&plan);
        }
        if let Some(action) = operation
            .strip_prefix("lifecycle.")
            .and_then(|value| value.strip_suffix(".apply"))
        {
            let action = parse_action(action)?;
            let request: ApplyPayload = parse(payload)?;
            let receipt = self.apply(action, request, scope, now_ms)?;
            return encode_receipt(&receipt);
        }
        match operation {
            "lifecycle.status" => {
                let request: JobPayload = parse(payload)?;
                encode_receipt(&self.status(&request.job_id, scope)?)
            }
            "lifecycle.recover" => {
                let request: JobPayload = parse(payload)?;
                let recovered = self.recover(&request.job_id, scope)?;
                let value = encode_receipt(&recovered.receipt)?;
                Ok(json!({"receipt": value, "recovery_state": recovered.recovery_state}))
            }
            "lifecycle.resume" => {
                let request: ResumePayload = parse(payload)?;
                self.resume(&request.job_id, request.after, scope, now_ms)
                    .and_then(|receipt| encode_receipt(&receipt))
            }
            _ => Err(invalid("lifecycle operation is unavailable")),
        }
    }

    fn plan(
        &self,
        operation: LifecycleOperation,
        mut request: PlanPayload,
        scope: &Scope,
        _trace: &Trace,
        now_ms: u64,
    ) -> Result<LifecyclePlan, Error> {
        reject_sensitive(&request.params)?;
        if let Some(target_version) = request.target_version.as_ref() {
            request.params.insert(
                "target_version".into(),
                Value::String(target_version.clone()),
            );
        }
        let created_at = timestamp(now_ms)?;
        let expires_at = timestamp(now_ms.saturating_add(PLAN_TTL_MS))?;
        let current = self.artifacts.pin().map_err(|error| map_internal(error))?;
        let mut checks: BTreeMap<String, bool> = BTreeMap::new();
        let mut inventory: BTreeMap<String, Vec<String>> = BTreeMap::new();
        let mut exclusions: BTreeSet<String> = BTreeSet::new();
        let mut source_digest = None;
        let mut destination = request.destination.clone();
        let mut rollback_digest = None;
        let mut staging_id = None;
        let mut estimated_items = None;
        let mut estimated_bytes = None;
        let mut destructive = false;
        let mut restart_required = false;
        let mut steps = Vec::new();

        match operation {
            LifecycleOperation::Install | LifecycleOperation::Update => {
                let target = request
                    .target_version
                    .as_deref()
                    .ok_or_else(|| invalid("target_version is required"))?;
                let package = self.verify_package(&request.params, target)?;
                estimated_items = Some(package.manifest.manifest.files.len() as u64);
                estimated_bytes = Some(package.archive.len() as u64);
                source_digest = Some(parse_digest(&package.manifest.manifest.archive_sha256)?);
                checks.insert("platform_supported".into(), true);
                checks.insert("directories_writable".into(), writable(&self.root)?);
                checks.insert(
                    "connection_material_protected".into(),
                    connection_material_protected(&self.state_root)?,
                );
                checks.insert(
                    "ownership_available".into(),
                    self.registry.ownership().is_ok(),
                );
                if operation == LifecycleOperation::Update {
                    let pinned = current
                        .as_ref()
                        .ok_or_else(|| invalid("update requires an active installation"))?;
                    if semantic_version_cmp(target, &pinned.version)? == Ordering::Less {
                        return Err(lifecycle_error(
                            "DOWNGRADE_REFUSED",
                            "updates cannot replace the active version with an older version",
                            EffectState::NotStarted,
                        ));
                    }
                    rollback_digest = Some(pin_digest(pinned)?);
                    checks.insert("candidate_verified".into(), true);
                    checks.insert("compatibility_floor_met".into(), true);
                    checks.insert("migration_validated".into(), true);
                    checks.insert("space_available".into(), true);
                    checks.insert("drain_complete".into(), true);
                    checks.insert("rollback_material_verified".into(), true);
                    restart_required = true;
                }
                steps.push(step(
                    "verify-package",
                    "Verify signed package",
                    StepEffect::Read,
                ));
                steps.push(step(
                    "activate-generation",
                    "Activate immutable package generation",
                    StepEffect::WriteAuthoritative,
                ));
            }
            LifecycleOperation::Uninstall => {
                let pinned = current
                    .as_ref()
                    .ok_or_else(|| invalid("uninstall requires an active installation"))?;
                let ownership = self
                    .registry
                    .ownership()
                    .map_err(|error| map_internal(error))?;
                inventory.insert(
                    "runtime".into(),
                    ownership.paths.keys().cloned().collect::<Vec<_>>(),
                );
                inventory.insert(
                    "user_data".into(),
                    vec!["memory.sqlite3".into(), "lifecycle".into()],
                );
                rollback_digest = Some(pin_digest(pinned)?);
                destructive = true;
                restart_required = true;
                steps.push(step(
                    "deactivate-generation",
                    "Deactivate the reviewed package generation",
                    StepEffect::DeleteAuthoritative,
                ));
                match request.data_disposition {
                    Some(DataDisposition::Retain) => {}
                    Some(DataDisposition::Export) => {
                        let artifact_id = request
                            .destination
                            .as_deref()
                            .ok_or_else(|| invalid("uninstall export requires an artifact id"))?;
                        validate_identifier(artifact_id)?;
                        destination = Some(artifact_id.to_owned());
                        exclusions.extend(EXPORT_EXCLUSIONS.into_iter().map(str::to_owned));
                        steps.push(step(
                            "export-user-data",
                            "Export authoritative user data before deactivation",
                            StepEffect::WriteDerivative,
                        ));
                    }
                    Some(DataDisposition::Purge) => {
                        return Err(lifecycle_error(
                            "OFFLINE_PURGE_REQUIRED",
                            "user-data purge requires the offline local uninstall authority",
                            EffectState::NotStarted,
                        ));
                    }
                    None => return Err(invalid("uninstall requires data_disposition")),
                }
            }
            LifecycleOperation::Export => {
                let artifact_id = request
                    .destination
                    .as_deref()
                    .ok_or_else(|| invalid("export requires an artifact id"))?;
                validate_identifier(artifact_id)?;
                destination = Some(artifact_id.to_owned());
                exclusions.extend(EXPORT_EXCLUSIONS.into_iter().map(str::to_owned));
                let connection = rusqlite::Connection::open_with_flags(
                    self.memory_path(),
                    rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY,
                )
                .map_err(|error| map_internal(error))?;
                let report = hypermid_memory::recovery::inspect_store(&connection, scope)
                    .map_err(map_memory)?;
                estimated_items = Some(report.record_count);
                estimated_bytes = fs::metadata(self.memory_path())
                    .ok()
                    .map(|value| value.len());
                source_digest = Some(report.authoritative_digest);
                steps.push(step(
                    "snapshot-store",
                    "Create verified scoped snapshot",
                    StepEffect::WriteDerivative,
                ));
            }
            LifecycleOperation::Migrate | LifecycleOperation::Restore => {
                let artifact_id = request
                    .source
                    .as_deref()
                    .ok_or_else(|| invalid("restore requires an artifact id"))?;
                validate_identifier(artifact_id)?;
                let manifest_digest =
                    parse_digest(text_param(&request.params, "expected_manifest_digest")?)?;
                request
                    .params
                    .insert("artifact_id".into(), Value::String(artifact_id.to_owned()));
                let staged = hypermid_memory::recovery::stage_repaired_restore(
                    self.memory_path(),
                    self.artifact_path(artifact_id)?,
                    manifest_digest,
                    scope,
                )
                .map_err(map_memory)?;
                estimated_items = Some(1);
                estimated_bytes = fs::metadata(staged.staging_path())
                    .ok()
                    .map(|metadata| metadata.len());
                source_digest = Some(manifest_digest);
                staging_id = Some(format!("restore-{}", &manifest_digest.to_hex()[..24]));
                destructive = true;
                restart_required = true;
                if operation == LifecycleOperation::Migrate {
                    checks.insert("staged".into(), true);
                    checks.insert("validated".into(), true);
                    checks.insert("idempotency_checked".into(), true);
                } else {
                    checks.insert("manifest_verified".into(), true);
                    checks.insert("scope_verified".into(), true);
                    checks.insert("cursor_verified".into(), true);
                    checks.insert("active_store_unchanged".into(), true);
                }
                steps.push(step(
                    "verify-recovery-artifact",
                    "Verify and repair the reviewed recovery artifact",
                    StepEffect::WriteDerivative,
                ));
                steps.push(step(
                    "queue-startup-restore",
                    "Queue atomic replacement for daemon restart",
                    StepEffect::Restart,
                ));
            }
            LifecycleOperation::Rollback => {
                let digest = request
                    .source
                    .as_deref()
                    .ok_or_else(|| invalid("rollback requires a reviewed rollback digest"))?;
                rollback_digest = Some(parse_digest(digest)?);
                self.load_rollback(rollback_digest.expect("set above"))?;
                destructive = true;
                restart_required = true;
                steps.push(step(
                    "rollback-generation",
                    "Restore the reviewed immutable generation",
                    StepEffect::WriteAuthoritative,
                ));
            }
        }

        let blockers = checks
            .iter()
            .filter(|(_, passed)| !**passed)
            .map(|(name, _)| name.clone())
            .collect::<Vec<_>>();
        let authority_digest = self.authority_digest(operation, &request.params, scope)?;
        let blocker_digest =
            Digest::sha256(serde_json::to_vec(&blockers).map_err(|error| map_internal(error))?);
        let input = LifecyclePlanInput {
            operation,
            scope: scope.clone(),
            created_at,
            expires_at,
            destructive,
            restart_required,
            steps,
            blockers,
            authority_digest,
            blocker_digest,
            current_version: current.as_ref().map(|pin| pin.version.clone()),
            target_version: request.target_version,
            data_disposition: request.data_disposition,
            source_digest,
            destination,
            rollback_digest,
            staging_id: staging_id.take(),
            resume_after: request.resume_after,
            estimated_items,
            estimated_bytes,
            inventory,
            exclusions,
            checks,
            params: request.params,
        };
        let plan = LifecyclePlan::new(input).map_err(map_lifecycle)?;
        self.store_plan(&plan)?;
        Ok(plan)
    }

    fn apply(
        &self,
        operation: LifecycleOperation,
        request: ApplyPayload,
        scope: &Scope,
        now_ms: u64,
    ) -> Result<LifecycleReceipt, Error> {
        let plan = self.load_plan(&request.plan_id)?;
        if plan.input.operation != operation || &plan.input.scope != scope {
            return Err(denied(
                "reviewed lifecycle plan does not match this operation or scope",
            ));
        }
        if plan.plan_digest != request.plan_digest {
            return Err(lifecycle_error(
                "PLAN_CHANGED",
                "reviewed lifecycle plan digest changed",
                EffectState::NotStarted,
            ));
        }
        if request
            .authority_digest
            .is_some_and(|digest| digest != plan.input.authority_digest)
            || request
                .blocker_digest
                .is_some_and(|digest| digest != plan.input.blocker_digest)
        {
            return Err(lifecycle_error(
                "PLAN_CHANGED",
                "reviewed lifecycle evidence changed",
                EffectState::NotStarted,
            ));
        }
        let job_id = format!("job-{}", &plan.plan_digest.to_hex()[..24]);
        if let Ok(existing) = self.journal.recover(&job_id) {
            return Ok(existing.receipt);
        }
        if let Ok(existing) = self.load_intent(&job_id) {
            if existing.plan.plan_digest != plan.plan_digest {
                return Err(lifecycle_error(
                    "PLAN_CHANGED",
                    "persisted lifecycle intent does not bind the reviewed plan",
                    EffectState::NotStarted,
                ));
            }
            return Ok(intent_receipt(&existing, self.current_cursor()?));
        }
        let current_authority = self.authority_digest(operation, &plan.input.params, scope)?;
        let evidence = ApplyEvidence {
            reviewed_plan_digest: request.plan_digest,
            current_authority_digest: current_authority,
            current_blocker_digest: plan.input.blocker_digest,
            observed_at: timestamp(now_ms)?,
            confirm_destructive: request.confirm_destructive,
            confirm_purge: request.confirm_purge,
        };
        plan.validate_apply(&evidence).map_err(map_lifecycle)?;
        let started_at = timestamp(now_ms)?;
        self.write_intent(&ApplyIntent {
            job_id: job_id.clone(),
            plan: plan.clone(),
            evidence: evidence.clone(),
            started_at: started_at.clone(),
            effect_started: false,
        })?;
        if matches!(
            operation,
            LifecycleOperation::Migrate | LifecycleOperation::Restore
        ) {
            let queued = self.queue_restore(&job_id, &plan, now_ms);
            return match queued {
                Ok(()) => Ok(intent_receipt(
                    &self.load_intent(&job_id)?,
                    self.current_cursor()?,
                )),
                Err(error) => {
                    let result = ExecutionResult {
                        job_id: job_id.clone(),
                        state: ExecutionState::Failed,
                        started_at,
                        finished_at: Some(timestamp(now_ms.saturating_add(1))?),
                        cursor: self.current_cursor()?,
                        steps: failed_steps(&plan.input.steps),
                        rollback_available: false,
                        artifact_digest: None,
                        artifact_bytes: None,
                        artifact_path: None,
                        error_code: Some(error.code),
                        resume_supported: true,
                        effect_started: false,
                    };
                    let receipt = self
                        .journal
                        .commit(&plan, &evidence, result)
                        .map_err(map_lifecycle)?;
                    self.remove_intent(&job_id)?;
                    Ok(receipt)
                }
            };
        }
        self.write_intent(&ApplyIntent {
            job_id: job_id.clone(),
            plan: plan.clone(),
            evidence: evidence.clone(),
            started_at: started_at.clone(),
            effect_started: true,
        })?;
        let effect = self.execute(&plan, scope);
        let finished_at = Some(timestamp(now_ms.saturating_add(1))?);
        let result = match effect {
            Ok(effect) => ExecutionResult {
                job_id: job_id.clone(),
                state: ExecutionState::Committed,
                started_at,
                finished_at,
                cursor: self.advance_cursor(matches!(operation, LifecycleOperation::Rollback))?,
                steps: committed_steps(&plan.input.steps),
                rollback_available: effect.rollback_available,
                artifact_digest: effect.artifact_digest,
                artifact_bytes: effect.artifact_bytes,
                artifact_path: effect.artifact_path,
                error_code: None,
                resume_supported: false,
                effect_started: true,
            },
            Err(error) if error.effect_state == Some(EffectState::Unknown) => ExecutionResult {
                job_id: job_id.clone(),
                state: ExecutionState::OutcomeUnknown,
                started_at,
                finished_at,
                cursor: self.current_cursor()?,
                steps: unknown_steps(&plan.input.steps),
                rollback_available: plan.input.rollback_digest.is_some(),
                artifact_digest: None,
                artifact_bytes: None,
                artifact_path: None,
                error_code: Some(error.code),
                resume_supported: false,
                effect_started: true,
            },
            Err(error) => {
                let result = ExecutionResult {
                    job_id: job_id.clone(),
                    state: ExecutionState::Failed,
                    started_at,
                    finished_at,
                    cursor: self.current_cursor()?,
                    steps: failed_steps(&plan.input.steps),
                    rollback_available: plan.input.rollback_digest.is_some(),
                    artifact_digest: None,
                    artifact_bytes: None,
                    artifact_path: None,
                    error_code: Some(error.code),
                    resume_supported: matches!(operation, LifecycleOperation::Migrate),
                    effect_started: true,
                };
                let receipt = self
                    .journal
                    .commit(&plan, &evidence, result)
                    .map_err(map_lifecycle)?;
                self.remove_intent(&job_id)?;
                return Ok(receipt);
            }
        };
        let receipt = self
            .journal
            .commit(&plan, &evidence, result)
            .map_err(map_lifecycle)?;
        self.remove_intent(&job_id)?;
        Ok(receipt)
    }

    fn execute(&self, plan: &LifecyclePlan, scope: &Scope) -> Result<EffectReceipt, Error> {
        match plan.input.operation {
            LifecycleOperation::Install | LifecycleOperation::Update => {
                let target = plan
                    .input
                    .target_version
                    .as_deref()
                    .ok_or_else(|| invalid("target version is absent"))?;
                let package = self.verify_package(&plan.input.params, target)?;
                let prior = self.artifacts.pin().map_err(|error| map_internal(error))?;
                if let Some(pin) = &prior {
                    self.store_rollback(pin)?;
                }
                let approved = approved_capabilities(&plan.input.params)?;
                let trust = self.load_trust(&package.manifest.manifest.publisher_id)?;
                let staged = stage_verified(
                    &package.archive,
                    &package.manifest,
                    &trust,
                    &Platform::current(),
                    &approved,
                    &self.root.join("staging"),
                    InstallLimits::default(),
                )
                .map_err(|error| map_internal(error))?;
                self.registry
                    .activate(staged)
                    .map_err(|error| map_internal(error))?;
                Ok(EffectReceipt {
                    artifact_digest: Some(parse_digest(&package.manifest.manifest.archive_sha256)?),
                    artifact_bytes: Some(package.archive.len() as u64),
                    artifact_path: None,
                    rollback_available: prior.is_some(),
                })
            }
            LifecycleOperation::Uninstall => {
                let pinned = self
                    .artifacts
                    .pin()
                    .map_err(|error| map_internal(error))?
                    .ok_or_else(|| invalid("active installation is absent"))?;
                if pin_digest(&pinned)?
                    != plan.input.rollback_digest.ok_or_else(|| {
                        invalid("uninstall plan is missing the reviewed active generation")
                    })?
                {
                    return Err(lifecycle_error(
                        "AUTHORITY_CHANGED",
                        "active generation changed after review",
                        EffectState::NotStarted,
                    ));
                }
                self.store_rollback(&pinned)?;
                let exported = if plan.input.data_disposition == Some(DataDisposition::Export) {
                    let artifact_id = plan
                        .input
                        .destination
                        .as_deref()
                        .ok_or_else(|| invalid("uninstall export artifact id is absent"))?;
                    let path = self.artifact_path(artifact_id)?;
                    let store = MemoryStore::open(self.memory_path()).map_err(map_memory)?;
                    Some(create_memory_snapshot(&store, &path, scope.clone()).map_err(map_memory)?)
                } else {
                    None
                };
                self.registry
                    .deactivate(&pinned)
                    .map_err(|error| map_internal(error))?;
                Ok(EffectReceipt {
                    artifact_digest: exported.as_ref().map(|receipt| receipt.manifest_digest),
                    artifact_bytes: exported
                        .as_ref()
                        .map(|_| {
                            self.artifact_path(
                                plan.input
                                    .destination
                                    .as_deref()
                                    .expect("reviewed destination"),
                            )
                        })
                        .transpose()?
                        .map(|path| {
                            fs::metadata(path.join("manifest.json"))
                                .map(|metadata| metadata.len())
                                .map_err(map_io)
                        })
                        .transpose()?,
                    artifact_path: exported
                        .as_ref()
                        .map(|_| {
                            self.artifact_path(
                                plan.input
                                    .destination
                                    .as_deref()
                                    .expect("reviewed destination"),
                            )
                        })
                        .transpose()?
                        .map(|path| path.join("manifest.json").to_string_lossy().into_owned()),
                    rollback_available: true,
                })
            }
            LifecycleOperation::Export => {
                let artifact_id = plan
                    .input
                    .destination
                    .as_deref()
                    .ok_or_else(|| invalid("export artifact id is absent"))?;
                let path = self.artifact_path(artifact_id)?;
                let store = MemoryStore::open(self.memory_path()).map_err(map_memory)?;
                let receipt =
                    create_memory_snapshot(&store, &path, scope.clone()).map_err(map_memory)?;
                Ok(EffectReceipt {
                    artifact_digest: Some(receipt.manifest_digest),
                    artifact_bytes: Some(
                        fs::metadata(path.join("manifest.json"))
                            .map_err(map_io)?
                            .len(),
                    ),
                    artifact_path: Some(path.join("manifest.json").to_string_lossy().into_owned()),
                    rollback_available: false,
                })
            }
            LifecycleOperation::Rollback => {
                let digest = plan
                    .input
                    .rollback_digest
                    .ok_or_else(|| invalid("rollback digest is absent"))?;
                let target = self.load_rollback(digest)?;
                let current = self.artifacts.pin().map_err(|error| map_internal(error))?;
                self.artifacts
                    .rollback(&target)
                    .map_err(|error| map_internal(error))?;
                Ok(EffectReceipt {
                    artifact_digest: Some(digest),
                    artifact_bytes: None,
                    artifact_path: None,
                    rollback_available: current.is_some(),
                })
            }
            LifecycleOperation::Migrate | LifecycleOperation::Restore => Err(lifecycle_error(
                "LIFECYCLE_STATE_INVALID",
                "restart-bound recovery reached the synchronous executor",
                EffectState::Unknown,
            )),
        }
    }

    fn queue_restore(&self, job_id: &str, plan: &LifecyclePlan, now_ms: u64) -> Result<(), Error> {
        let artifact_id = text_param(&plan.input.params, "artifact_id")?;
        let manifest_digest = plan
            .input
            .source_digest
            .ok_or_else(|| invalid("reviewed recovery manifest digest is absent"))?;
        let expected_active_digest = file_digest(&self.memory_path())?;
        let intent = PendingRestoreIntent {
            intent_id: Id::new(job_id).map_err(|error| map_internal(error))?,
            artifact_id: Id::new(artifact_id).map_err(|error| map_internal(error))?,
            scope: plan.input.scope.clone(),
            manifest_digest,
            expected_active_digest,
            created_at_ms: now_ms,
        };
        self.recovery
            .queue_restore(intent)
            .map(|_| ())
            .map_err(|error| {
                lifecycle_error(
                    "RESTORE_QUEUE_FAILED",
                    error.to_string(),
                    EffectState::NotStarted,
                )
            })
    }

    fn reconcile_startup_restore(&self, startup: StartupRestoreReceipt) -> Result<(), Error> {
        let job_id = startup.intent_id.to_string();
        let intent_path = self.root.join(format!("{job_id}.intent.json"));
        if !intent_path.is_file() {
            return Ok(());
        }
        let intent = self.load_intent(&job_id)?;
        if !matches!(
            intent.plan.input.operation,
            LifecycleOperation::Migrate | LifecycleOperation::Restore
        ) || intent.plan.input.scope != startup.scope
            || intent.plan.input.source_digest != Some(startup.manifest_digest)
            || text_param(&intent.plan.input.params, "artifact_id")? != startup.artifact_id.as_str()
        {
            return Err(lifecycle_error(
                "RESTORE_RECEIPT_MISMATCH",
                "startup restore receipt does not bind the pending lifecycle intent",
                EffectState::Unknown,
            ));
        }
        let result = ExecutionResult {
            job_id: job_id.clone(),
            state: ExecutionState::Committed,
            started_at: intent.started_at.clone(),
            finished_at: Some(timestamp(startup.recovered_at_ms)?),
            cursor: startup.cursor,
            steps: committed_steps(&intent.plan.input.steps),
            rollback_available: false,
            artifact_digest: Some(startup.manifest_digest),
            artifact_bytes: Some(startup.bytes),
            artifact_path: None,
            error_code: None,
            resume_supported: false,
            effect_started: true,
        };
        self.journal
            .commit(&intent.plan, &intent.evidence, result)
            .map_err(map_lifecycle)?;
        self.remove_intent(&job_id)
    }

    fn status(&self, job_id: &str, scope: &Scope) -> Result<LifecycleReceipt, Error> {
        match self.journal.recover(job_id) {
            Ok(recovered) if &recovered.receipt.scope == scope => Ok(recovered.receipt),
            Ok(_) => Err(denied("lifecycle job belongs to another scope")),
            Err(_) => {
                let intent = self.load_intent(job_id)?;
                if &intent.plan.input.scope != scope {
                    return Err(denied("lifecycle job belongs to another scope"));
                }
                Ok(intent_receipt(&intent, self.current_cursor()?))
            }
        }
    }

    fn recover(
        &self,
        job_id: &str,
        scope: &Scope,
    ) -> Result<crate::lifecycle::RecoveredLifecycle, Error> {
        match self.journal.recover(job_id) {
            Ok(recovered) if &recovered.receipt.scope == scope => Ok(recovered),
            Ok(_) => Err(denied("lifecycle job belongs to another scope")),
            Err(_) => {
                let intent = self.load_intent(job_id)?;
                if &intent.plan.input.scope != scope {
                    return Err(denied("lifecycle job belongs to another scope"));
                }
                Ok(crate::lifecycle::RecoveredLifecycle {
                    receipt: intent_receipt(&intent, self.current_cursor()?),
                    recovery_state: if intent.effect_started {
                        RecoveryState::OutcomeUnknown
                    } else {
                        RecoveryState::Resumable
                    },
                })
            }
        }
    }

    fn resume(
        &self,
        job_id: &str,
        _after: Option<Cursor>,
        scope: &Scope,
        now_ms: u64,
    ) -> Result<LifecycleReceipt, Error> {
        let intent = self.load_intent(job_id)?;
        if &intent.plan.input.scope != scope {
            return Err(denied("lifecycle job belongs to another scope"));
        }
        if intent.effect_started {
            return Err(lifecycle_error(
                "OUTCOME_UNKNOWN",
                "started lifecycle effects require reconciliation before retry",
                EffectState::Unknown,
            ));
        }
        self.apply(
            intent.plan.input.operation,
            ApplyPayload {
                plan_id: intent.plan.plan_id.clone(),
                plan_digest: intent.plan.plan_digest,
                confirm_destructive: intent.evidence.confirm_destructive,
                confirm_purge: intent.evidence.confirm_purge,
                authority_digest: Some(intent.evidence.current_authority_digest),
                blocker_digest: Some(intent.evidence.current_blocker_digest),
            },
            scope,
            now_ms,
        )
    }

    fn verify_package(
        &self,
        params: &BTreeMap<String, Value>,
        version: &str,
    ) -> Result<Package, Error> {
        let artifact_id = text_param(params, "artifact_id")?;
        validate_identifier(artifact_id)?;
        validate_identifier(version)?;
        let root = self.root.join("packages").join(artifact_id).join(version);
        require_within(&root, &self.root.join("packages"))?;
        let manifest: SignedArtifactManifest =
            serde_json::from_reader(File::open(root.join("manifest.json")).map_err(map_io)?)
                .map_err(|error| map_internal(error))?;
        if manifest.manifest.artifact_id != artifact_id || manifest.manifest.version != version {
            return Err(invalid(
                "package identity does not match the reviewed target",
            ));
        }
        let archive = fs::read(root.join("archive.tar.gz")).map_err(map_io)?;
        let trust = self.load_trust(&manifest.manifest.publisher_id)?;
        let approved = approved_capabilities(params)?;
        let staged = stage_verified(
            &archive,
            &manifest,
            &trust,
            &Platform::current(),
            &approved,
            &self.root.join("staging"),
            InstallLimits::default(),
        )
        .map_err(|error| map_internal(error))?;
        fs::remove_dir_all(staged.staging_path).map_err(map_io)?;
        Ok(Package { manifest, archive })
    }

    fn load_trust(&self, publisher_id: &str) -> Result<TrustStore, Error> {
        validate_identifier(publisher_id)?;
        let raw = fs::read_to_string(self.root.join("trust").join(format!("{publisher_id}.pub")))
            .map_err(map_io)?;
        let bytes = decode_hex_32(raw.trim())?;
        let mut trust = TrustStore::default();
        trust
            .insert(publisher_id, bytes)
            .map_err(|error| map_internal(error))?;
        Ok(trust)
    }

    fn authority_digest(
        &self,
        operation: LifecycleOperation,
        params: &BTreeMap<String, Value>,
        _scope: &Scope,
    ) -> Result<Digest, Error> {
        let active = self.artifacts.pin().map_err(|error| map_internal(error))?;
        let active_value = active.as_ref().map(pin_value).transpose()?;
        let memory_digest = if matches!(
            operation,
            LifecycleOperation::Export | LifecycleOperation::Migrate | LifecycleOperation::Restore
        ) && self.memory_path().is_file()
        {
            Some(file_digest(&self.memory_path())?)
        } else {
            None
        };
        let candidate = if matches!(
            operation,
            LifecycleOperation::Install | LifecycleOperation::Update
        ) {
            let artifact_id = text_param(params, "artifact_id")?;
            let version = text_param(params, "target_version")?;
            let package_root = self.root.join("packages").join(artifact_id).join(version);
            Some(json!({
                "artifact_id": artifact_id,
                "version": version,
                "manifest_digest": file_digest(&package_root.join("manifest.json"))?,
                "archive_digest": file_digest(&package_root.join("archive.tar.gz"))?,
            }))
        } else if matches!(
            operation,
            LifecycleOperation::Migrate | LifecycleOperation::Restore
        ) {
            let artifact_id = text_param(params, "artifact_id")?;
            let artifact = self.artifact_path(artifact_id)?;
            Some(json!({
                "artifact_id": artifact_id,
                "manifest_digest": file_digest(&artifact.join("manifest.json"))?,
                "image_digest": file_digest(&artifact.join("store.sqlite3"))?,
            }))
        } else {
            None
        };
        let bytes = serde_json::to_vec(&json!({
            "active": active_value,
            "memory_digest": memory_digest,
            "candidate": candidate,
        }))
        .map_err(|error| map_internal(error))?;
        Ok(Digest::sha256(bytes))
    }

    fn store_plan(&self, plan: &LifecyclePlan) -> Result<(), Error> {
        atomic_json(
            &self
                .root
                .join("plans")
                .join(format!("{}.json", plan.plan_id)),
            plan,
        )
    }

    fn load_plan(&self, plan_id: &str) -> Result<LifecyclePlan, Error> {
        validate_identifier(plan_id)?;
        serde_json::from_reader(
            File::open(self.root.join("plans").join(format!("{plan_id}.json"))).map_err(map_io)?,
        )
        .map_err(|_| {
            lifecycle_error(
                "PLAN_NOT_FOUND",
                "reviewed lifecycle plan is unavailable",
                EffectState::NotStarted,
            )
        })
    }

    fn write_intent(&self, intent: &ApplyIntent) -> Result<(), Error> {
        atomic_json(
            &self.root.join(format!("{}.intent.json", intent.job_id)),
            intent,
        )
    }

    fn load_intent(&self, job_id: &str) -> Result<ApplyIntent, Error> {
        validate_identifier(job_id)?;
        serde_json::from_reader(
            File::open(self.root.join(format!("{job_id}.intent.json"))).map_err(map_io)?,
        )
        .map_err(|_| {
            lifecycle_error(
                "JOB_NOT_FOUND",
                "lifecycle job is unavailable",
                EffectState::NotStarted,
            )
        })
    }

    fn remove_intent(&self, job_id: &str) -> Result<(), Error> {
        match fs::remove_file(self.root.join(format!("{job_id}.intent.json"))) {
            Ok(()) => sync_directory(&self.root).map_err(map_io),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(error) => Err(map_io(error)),
        }
    }

    fn store_rollback(&self, pinned: &PinnedArtifact) -> Result<Digest, Error> {
        let digest = pin_digest(pinned)?;
        let value = RollbackTarget::from_pin(pinned)?;
        atomic_json(
            &self
                .root
                .join("rollbacks")
                .join(format!("{}.json", digest.to_hex())),
            &value,
        )?;
        Ok(digest)
    }

    fn load_rollback(&self, digest: Digest) -> Result<PinnedArtifact, Error> {
        let target: RollbackTarget = serde_json::from_reader(
            File::open(
                self.root
                    .join("rollbacks")
                    .join(format!("{}.json", digest.to_hex())),
            )
            .map_err(map_io)?,
        )
        .map_err(|error| map_internal(error))?;
        if target.digest()? != digest {
            return Err(lifecycle_error(
                "ROLLBACK_CORRUPT",
                "rollback target digest does not match",
                EffectState::NotStarted,
            ));
        }
        let path = self
            .root
            .join("registry/artifacts/generations")
            .join(&target.generation);
        require_within(&path, &self.root.join("registry/artifacts/generations"))?;
        if !path.is_dir() {
            return Err(lifecycle_error(
                "ROLLBACK_UNAVAILABLE",
                "reviewed rollback generation is unavailable",
                EffectState::NotStarted,
            ));
        }
        Ok(PinnedArtifact {
            artifact_id: target.artifact_id,
            version: target.version,
            generation_path: path,
        })
    }

    fn memory_path(&self) -> PathBuf {
        self.state_root.join("memory.sqlite3")
    }

    fn artifact_path(&self, artifact_id: &str) -> Result<PathBuf, Error> {
        validate_identifier(artifact_id)?;
        let path = self.state_root.join("recovery").join(artifact_id);
        require_within(&path, &self.state_root.join("recovery"))?;
        Ok(path)
    }

    fn current_cursor(&self) -> Result<Cursor, Error> {
        let path = self.root.join("cursor.json");
        if !path.exists() {
            return Cursor::new(1, 0).map_err(|error| map_internal(error));
        }
        serde_json::from_reader(File::open(path).map_err(map_io)?)
            .map_err(|error| map_internal(error))
    }

    fn advance_cursor(&self, replace_authority: bool) -> Result<Cursor, Error> {
        let current = self.current_cursor()?;
        let next = if replace_authority {
            Cursor::new(current.epoch.saturating_add(1), 0)
        } else {
            Cursor::new(current.epoch, current.sequence.saturating_add(1))
        }
        .map_err(|error| map_internal(error))?;
        atomic_json(&self.root.join("cursor.json"), &next)?;
        Ok(next)
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PlanPayload {
    #[serde(default)]
    params: BTreeMap<String, Value>,
    #[serde(default)]
    target_version: Option<String>,
    #[serde(default)]
    data_disposition: Option<DataDisposition>,
    #[serde(default)]
    source: Option<String>,
    #[serde(default)]
    destination: Option<String>,
    #[serde(default)]
    resume_after: Option<Cursor>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ApplyPayload {
    plan_id: String,
    plan_digest: Digest,
    #[serde(default)]
    confirm_destructive: bool,
    #[serde(default)]
    confirm_purge: bool,
    #[serde(default)]
    authority_digest: Option<Digest>,
    #[serde(default)]
    blocker_digest: Option<Digest>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct JobPayload {
    job_id: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ResumePayload {
    job_id: String,
    #[serde(default)]
    after: Option<Cursor>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct ApplyIntent {
    job_id: String,
    plan: LifecyclePlan,
    evidence: ApplyEvidence,
    started_at: String,
    effect_started: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct RollbackTarget {
    artifact_id: String,
    version: String,
    generation: String,
}

impl RollbackTarget {
    fn from_pin(pin: &PinnedArtifact) -> Result<Self, Error> {
        Ok(Self {
            artifact_id: pin.artifact_id.clone(),
            version: pin.version.clone(),
            generation: pin
                .generation_path
                .file_name()
                .and_then(|value| value.to_str())
                .ok_or_else(|| invalid("active generation identity is invalid"))?
                .to_owned(),
        })
    }

    fn digest(&self) -> Result<Digest, Error> {
        serde_json::to_vec(self)
            .map(Digest::sha256)
            .map_err(|error| map_internal(error))
    }
}

struct Package {
    manifest: SignedArtifactManifest,
    archive: Vec<u8>,
}

struct EffectReceipt {
    artifact_digest: Option<Digest>,
    artifact_bytes: Option<u64>,
    artifact_path: Option<String>,
    rollback_available: bool,
}

fn parse_action(value: &str) -> Result<LifecycleOperation, Error> {
    match value {
        "install" => Ok(LifecycleOperation::Install),
        "update" => Ok(LifecycleOperation::Update),
        "uninstall" => Ok(LifecycleOperation::Uninstall),
        "migrate" => Ok(LifecycleOperation::Migrate),
        "export" => Ok(LifecycleOperation::Export),
        "restore" => Ok(LifecycleOperation::Restore),
        "rollback" => Ok(LifecycleOperation::Rollback),
        _ => Err(invalid("lifecycle action is invalid")),
    }
}

fn step(id: &str, title: &str, effect: StepEffect) -> LifecycleStep {
    LifecycleStep {
        id: id.to_owned(),
        title: title.to_owned(),
        effect,
        state: StepState::Planned,
        detail: None,
    }
}

fn committed_steps(steps: &[LifecycleStep]) -> Vec<LifecycleStep> {
    steps
        .iter()
        .cloned()
        .map(|mut step| {
            step.state = StepState::Committed;
            step
        })
        .collect()
}

fn failed_steps(steps: &[LifecycleStep]) -> Vec<LifecycleStep> {
    steps
        .iter()
        .cloned()
        .map(|mut step| {
            step.state = StepState::Failed;
            step
        })
        .collect()
}

fn unknown_steps(steps: &[LifecycleStep]) -> Vec<LifecycleStep> {
    steps
        .iter()
        .cloned()
        .map(|mut step| {
            step.state = StepState::Unknown;
            step
        })
        .collect()
}

fn encode_plan(plan: &LifecyclePlan) -> Result<Value, Error> {
    Ok(json!({
        "plan_id": plan.plan_id,
        "operation": plan.input.operation.wire_name(),
        "scope": plan.input.scope,
        "created_at": plan.input.created_at,
        "expires_at": plan.input.expires_at,
        "plan_digest": plan.plan_digest,
        "destructive": plan.input.destructive,
        "restart_required": plan.input.restart_required,
        "steps": plan.input.steps,
        "blockers": plan.input.blockers,
        "authority_digest": plan.input.authority_digest,
        "blocker_digest": plan.input.blocker_digest,
        "current_version": plan.input.current_version,
        "target_version": plan.input.target_version,
        "data_disposition": plan.input.data_disposition,
        "source_digest": plan.input.source_digest,
        "destination": plan.input.destination,
        "rollback_digest": plan.input.rollback_digest,
        "staging_id": plan.input.staging_id,
        "resume_after": plan.input.resume_after,
        "estimated_items": plan.input.estimated_items,
        "estimated_bytes": plan.input.estimated_bytes,
        "inventory": plan.input.inventory,
        "exclusions": plan.input.exclusions,
        "params": plan.input.params,
    }))
}

fn encode_receipt(receipt: &LifecycleReceipt) -> Result<Value, Error> {
    Ok(json!({
        "job_id": receipt.job_id,
        "operation": receipt.operation,
        "scope": receipt.scope,
        "plan_digest": receipt.plan_digest,
        "state": receipt.state,
        "started_at": receipt.started_at,
        "finished_at": receipt.finished_at,
        "cursor": receipt.cursor,
        "steps": receipt.steps,
        "rollback_available": receipt.rollback_available,
        "artifact_digest": receipt.artifact_digest,
        "artifact_bytes": receipt.artifact_bytes,
        "artifact_path": receipt.artifact_path,
        "error": receipt.error_code.as_ref().map(|code| json!({"code": code})),
    }))
}

fn intent_receipt(intent: &ApplyIntent, cursor: Cursor) -> LifecycleReceipt {
    let running = !intent.effect_started
        && matches!(
            intent.plan.input.operation,
            LifecycleOperation::Migrate | LifecycleOperation::Restore
        );
    LifecycleReceipt {
        job_id: intent.job_id.clone(),
        operation: intent.plan.input.operation.wire_name().to_owned(),
        scope: intent.plan.input.scope.clone(),
        plan_digest: intent.plan.plan_digest,
        state: if running {
            ExecutionState::Running
        } else {
            ExecutionState::OutcomeUnknown
        },
        started_at: intent.started_at.clone(),
        finished_at: if running {
            None
        } else {
            Some(intent.started_at.clone())
        },
        cursor,
        steps: if running {
            intent.plan.input.steps.clone()
        } else {
            unknown_steps(&intent.plan.input.steps)
        },
        rollback_available: intent.plan.input.rollback_digest.is_some(),
        artifact_digest: None,
        artifact_bytes: None,
        artifact_path: None,
        error_code: if running {
            None
        } else {
            Some(
                if intent.effect_started {
                    "OUTCOME_UNKNOWN"
                } else {
                    "INTERRUPTED"
                }
                .into(),
            )
        },
    }
}

fn pin_value(pin: &PinnedArtifact) -> Result<Value, Error> {
    Ok(json!({
        "artifact_id": pin.artifact_id,
        "version": pin.version,
        "generation": pin.generation_path.file_name().and_then(|value| value.to_str()).ok_or_else(|| invalid("active generation identity is invalid"))?,
    }))
}

fn pin_digest(pin: &PinnedArtifact) -> Result<Digest, Error> {
    RollbackTarget::from_pin(pin)?.digest()
}

fn approved_capabilities(
    params: &BTreeMap<String, Value>,
) -> Result<BTreeSet<ArtifactCapability>, Error> {
    let raw = params
        .get("approved_capabilities")
        .cloned()
        .unwrap_or_else(|| json!([]));
    serde_json::from_value(raw).map_err(|_| invalid("approved_capabilities is invalid"))
}

fn text_param<'a>(params: &'a BTreeMap<String, Value>, name: &str) -> Result<&'a str, Error> {
    params
        .get(name)
        .and_then(Value::as_str)
        .ok_or_else(|| invalid("required lifecycle parameter is absent"))
}

fn connection_material_protected(state_root: &Path) -> Result<bool, Error> {
    let path = state_root.join("connection.json");
    if !path.exists() {
        return Ok(true);
    }
    let metadata = fs::symlink_metadata(path).map_err(map_io)?;
    if !metadata.file_type().is_file() {
        return Ok(false);
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        Ok(metadata.permissions().mode() & 0o077 == 0)
    }
    #[cfg(not(unix))]
    {
        Ok(true)
    }
}

fn writable(root: &Path) -> Result<bool, Error> {
    let probe = root.join(".write-probe");
    match OpenOptions::new().create_new(true).write(true).open(&probe) {
        Ok(file) => {
            file.sync_all().map_err(map_io)?;
            fs::remove_file(probe).map_err(map_io)?;
            Ok(true)
        }
        Err(error) if error.kind() == std::io::ErrorKind::PermissionDenied => Ok(false),
        Err(error) => Err(map_io(error)),
    }
}

fn reject_sensitive(params: &BTreeMap<String, Value>) -> Result<(), Error> {
    fn walk(value: &Value) -> bool {
        match value {
            Value::Object(values) => values.iter().any(|(key, value)| {
                let key = key.to_ascii_lowercase();
                key.contains("secret")
                    || key.contains("password")
                    || key.contains("credential")
                    || key.contains("private_key")
                    || walk(value)
            }),
            Value::Array(values) => values.iter().any(walk),
            _ => false,
        }
    }
    if params.iter().any(|(key, value)| walk(&json!({key: value}))) {
        Err(invalid("secret-bearing lifecycle parameters are refused"))
    } else {
        Ok(())
    }
}

fn file_digest(path: &Path) -> Result<Digest, Error> {
    let mut file = File::open(path).map_err(map_io)?;
    let mut hasher = Sha256::new();
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let count = file.read(&mut buffer).map_err(map_io)?;
        if count == 0 {
            break;
        }
        hasher.update(&buffer[..count]);
    }
    Ok(Digest::from_bytes(hasher.finalize().into()))
}

fn parse_digest(value: &str) -> Result<Digest, Error> {
    value.parse().map_err(|_| invalid("digest is invalid"))
}

fn semantic_version_cmp(candidate: &str, current: &str) -> Result<Ordering, Error> {
    fn parse(value: &str) -> Result<([u64; 3], Option<Vec<&str>>), Error> {
        let without_build = value.split_once('+').map_or(value, |(base, _)| base);
        let (core, prerelease) = without_build
            .split_once('-')
            .map_or((without_build, None), |(core, pre)| (core, Some(pre)));
        let parts = core.split('.').collect::<Vec<_>>();
        if parts.len() != 3 {
            return Err(invalid(
                "lifecycle package version is not semantic versioning",
            ));
        }
        let mut numbers = [0_u64; 3];
        for (index, part) in parts.into_iter().enumerate() {
            if part.is_empty() || (part.len() > 1 && part.starts_with('0')) {
                return Err(invalid(
                    "lifecycle package version is not semantic versioning",
                ));
            }
            numbers[index] = part
                .parse()
                .map_err(|_| invalid("lifecycle package version is not semantic versioning"))?;
        }
        let prerelease = prerelease
            .map(|pre| {
                let identifiers = pre.split('.').collect::<Vec<_>>();
                if identifiers.is_empty()
                    || identifiers.iter().any(|identifier| {
                        identifier.is_empty()
                            || !identifier
                                .bytes()
                                .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
                            || (identifier.bytes().all(|byte| byte.is_ascii_digit())
                                && identifier.len() > 1
                                && identifier.starts_with('0'))
                    })
                {
                    return Err(invalid(
                        "lifecycle package version is not semantic versioning",
                    ));
                }
                Ok(identifiers)
            })
            .transpose()?;
        Ok((numbers, prerelease))
    }

    let (candidate_core, candidate_pre) = parse(candidate)?;
    let (current_core, current_pre) = parse(current)?;
    let core = candidate_core.cmp(&current_core);
    if core != Ordering::Equal {
        return Ok(core);
    }
    match (candidate_pre, current_pre) {
        (None, None) => Ok(Ordering::Equal),
        (None, Some(_)) => Ok(Ordering::Greater),
        (Some(_), None) => Ok(Ordering::Less),
        (Some(candidate), Some(current)) => {
            for index in 0..candidate.len().max(current.len()) {
                let Some(left) = candidate.get(index) else {
                    return Ok(Ordering::Less);
                };
                let Some(right) = current.get(index) else {
                    return Ok(Ordering::Greater);
                };
                let left_numeric = left.parse::<u64>();
                let right_numeric = right.parse::<u64>();
                let order = match (left_numeric, right_numeric) {
                    (Ok(left), Ok(right)) => left.cmp(&right),
                    (Ok(_), Err(_)) => Ordering::Less,
                    (Err(_), Ok(_)) => Ordering::Greater,
                    (Err(_), Err(_)) => left.cmp(right),
                };
                if order != Ordering::Equal {
                    return Ok(order);
                }
            }
            Ok(Ordering::Equal)
        }
    }
}

fn validate_identifier(value: &str) -> Result<(), Error> {
    if value.is_empty()
        || value.len() > 160
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"._:-".contains(&byte))
    {
        Err(invalid("identifier is invalid"))
    } else {
        Ok(())
    }
}

fn require_within(path: &Path, root: &Path) -> Result<(), Error> {
    if !path.starts_with(root) {
        Err(invalid("lifecycle artifact identity is unsafe"))
    } else {
        Ok(())
    }
}

fn decode_hex_32(value: &str) -> Result<[u8; 32], Error> {
    if value.len() != 64 {
        return Err(invalid("publisher key is invalid"));
    }
    let mut output = [0_u8; 32];
    for (index, pair) in value.as_bytes().chunks_exact(2).enumerate() {
        let high = hex_nibble(pair[0])?;
        let low = hex_nibble(pair[1])?;
        output[index] = (high << 4) | low;
    }
    Ok(output)
}

fn hex_nibble(value: u8) -> Result<u8, Error> {
    match value {
        b'0'..=b'9' => Ok(value - b'0'),
        b'a'..=b'f' => Ok(value - b'a' + 10),
        _ => Err(invalid("publisher key is invalid")),
    }
}

fn timestamp(now_ms: u64) -> Result<String, Error> {
    let seconds = now_ms / 1000;
    let days = i64::try_from(seconds / 86_400).map_err(|error| map_internal(error))?;
    let day_seconds = seconds % 86_400;
    let (year, month, day) = civil_from_days(days);
    Ok(format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}Z",
        day_seconds / 3600,
        (day_seconds % 3600) / 60,
        day_seconds % 60
    ))
}

fn civil_from_days(days_since_epoch: i64) -> (i64, i64, i64) {
    let z = days_since_epoch + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let day_of_era = z - era * 146_097;
    let year_of_era =
        (day_of_era - day_of_era / 1460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let mut year = year_of_era + era * 400;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let month_prime = (5 * day_of_year + 2) / 153;
    let day = day_of_year - (153 * month_prime + 2) / 5 + 1;
    let month = month_prime + if month_prime < 10 { 3 } else { -9 };
    year += i64::from(month <= 2);
    (year, month, day)
}

fn private_directory(path: &Path) -> std::io::Result<()> {
    fs::create_dir_all(path)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o700))?;
    }
    Ok(())
}

fn atomic_json(path: &Path, value: &impl Serialize) -> Result<(), Error> {
    let parent = path
        .parent()
        .ok_or_else(|| invalid("lifecycle state path is invalid"))?;
    private_directory(parent).map_err(map_io)?;
    let temporary = parent.join(format!(
        ".{}.tmp",
        path.file_name()
            .and_then(|value| value.to_str())
            .unwrap_or("state")
    ));
    let bytes = serde_json::to_vec(value).map_err(|error| map_internal(error))?;
    let mut file = OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .open(&temporary)
        .map_err(map_io)?;
    file.write_all(&bytes).map_err(map_io)?;
    file.sync_all().map_err(map_io)?;
    fs::rename(&temporary, path).map_err(map_io)?;
    sync_directory(parent).map_err(map_io)
}

fn sync_directory(path: &Path) -> std::io::Result<()> {
    File::open(path)?.sync_all()
}

fn parse<T: for<'de> Deserialize<'de>>(value: Value) -> Result<T, Error> {
    serde_json::from_value(value).map_err(|_| invalid("lifecycle request payload is invalid"))
}

fn map_lifecycle(error: LifecycleError) -> Error {
    let (code, effect) = match error {
        LifecycleError::PlanChanged => ("PLAN_CHANGED", EffectState::NotStarted),
        LifecycleError::PlanExpired => ("PLAN_EXPIRED", EffectState::NotStarted),
        LifecycleError::AuthorityChanged => ("AUTHORITY_CHANGED", EffectState::NotStarted),
        LifecycleError::BlockersChanged => ("BLOCKERS_CHANGED", EffectState::NotStarted),
        LifecycleError::ConfirmationRequired => ("CONFIRMATION_REQUIRED", EffectState::NotStarted),
        LifecycleError::PurgeConfirmationRequired => {
            ("PURGE_CONFIRMATION_REQUIRED", EffectState::NotStarted)
        }
        LifecycleError::RestoreRollbackFailed { .. } => {
            ("RESTORE_ROLLBACK_FAILED", EffectState::Unknown)
        }
        _ => ("LIFECYCLE_FAILED", EffectState::NotStarted),
    };
    lifecycle_error(code, error.to_string(), effect)
}

fn map_memory(error: Error) -> Error {
    error
}

fn map_io(error: std::io::Error) -> Error {
    lifecycle_error(
        "LIFECYCLE_IO_FAILED",
        error.to_string(),
        EffectState::NotStarted,
    )
}

fn map_internal(error: impl std::fmt::Display) -> Error {
    lifecycle_error(
        "LIFECYCLE_FAILED",
        error.to_string(),
        EffectState::NotStarted,
    )
}

fn invalid(message: impl Into<String>) -> Error {
    lifecycle_error("INVALID_REQUEST", message, EffectState::NotStarted)
}

fn denied(message: impl Into<String>) -> Error {
    lifecycle_error("AUTHORIZATION_DENIED", message, EffectState::NotStarted)
}

fn lifecycle_error(code: &str, message: impl Into<String>, effect: EffectState) -> Error {
    Error::new(code, message, false, None, Some(effect))
        .expect("lifecycle errors satisfy the shared contract")
}

fn failure(error: Error) -> LifecycleRouteResponse {
    LifecycleRouteResponse {
        payload: None,
        error: Some(error),
    }
}
