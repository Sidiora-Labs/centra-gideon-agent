use std::{
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
};

use hypermid_contracts::{Cursor, Digest, EffectState, Error, Id, Scope, Trace};
use hypermid_core::capability::{AuthorizationRequest, CapabilityOperation};
use hypermid_memory::{
    budget::ModelBudget,
    capability_operation,
    maintenance::{MaintenanceJobSpec, MaintenanceKind},
    AccessRequest, GrantOperation, MemoryApi, MutationRequest, Operation, RevisionPrecondition,
};
use hypermid_protocol::Envelope;
use hypermid_transport::AuthenticatedSession;
use rusqlite::{params, Connection, OpenFlags, OptionalExtension};
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};

use crate::{dispatch::Dispatcher, enrollment::LocalOperatorEnrollment};

pub const OPERATOR_ROUTE_OPERATIONS: &[&str] = &[
    "diagnostics.get",
    "diagnostics.rerun",
    "maintenance.plan",
    "maintenance.apply",
    "maintenance.status",
    "maintenance.cancel",
];

const PLAN_TTL_MS: u64 = 600_000;
const CLAIM_TTL_MS: i64 = 60_000;
const MEMORY_LIST_RESOURCE: &str = "memory-list";
const MEMORY_MAINTENANCE_RESOURCE: &str = "memory-maintenance";

#[derive(Clone, Debug)]
pub struct OperatorRouteResponse {
    pub payload: Option<Value>,
    pub error: Option<Error>,
}

pub struct OperatorRoutes {
    state: Mutex<Connection>,
    memory_path: PathBuf,
    memory: Arc<Mutex<MemoryApi>>,
    capability_id: Id,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct StoredPlan {
    plan_id: String,
    operation: String,
    scope: Scope,
    created_ms: u64,
    expires_ms: u64,
    plan_digest: String,
    destructive: bool,
    restart_required: bool,
    steps: Vec<Value>,
    blockers: Vec<String>,
    authority_digest: String,
    blocker_digest: String,
    input_digest: String,
    config_digest: String,
    params: Value,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct StoredJob {
    job_id: String,
    plan_id: String,
    plan_digest: String,
    operation: String,
    scope: Scope,
    started_ms: u64,
    #[serde(default)]
    finished_ms: Option<u64>,
    #[serde(default)]
    outcome_digest: Option<String>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct PlanPayload {
    action: String,
    #[serde(default)]
    params: Map<String, Value>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ApplyPayload {
    plan_id: String,
    plan_digest: String,
    #[serde(default)]
    confirm_destructive: bool,
    authority_digest: Option<String>,
    blocker_digest: Option<String>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct JobPayload {
    job_id: String,
}

#[derive(Clone, Copy)]
struct Action {
    kind: MaintenanceKind,
    operation: Operation,
    title: &'static str,
    effect: &'static str,
    destructive: bool,
}

impl OperatorRoutes {
    pub fn open(
        state_root: impl AsRef<Path>,
        memory_path: impl AsRef<Path>,
        memory: Arc<Mutex<MemoryApi>>,
        enrollment: &LocalOperatorEnrollment,
    ) -> Result<Self, String> {
        let path = state_root.as_ref().join("operator.sqlite3");
        let connection = Connection::open(&path).map_err(|error| error.to_string())?;
        connection
            .execute_batch(
                "PRAGMA journal_mode=WAL;
                 PRAGMA foreign_keys=ON;
                 CREATE TABLE IF NOT EXISTS operator_state(
                     key TEXT PRIMARY KEY,
                     value TEXT NOT NULL
                 );
                 CREATE TABLE IF NOT EXISTS operator_plans(
                     plan_id TEXT PRIMARY KEY,
                     plan_json TEXT NOT NULL
                 );
                 CREATE TABLE IF NOT EXISTS operator_jobs(
                     job_id TEXT PRIMARY KEY,
                     job_json TEXT NOT NULL
                 );",
            )
            .map_err(|error| error.to_string())?;
        protect_file(&path)?;
        Ok(Self {
            state: Mutex::new(connection),
            memory_path: memory_path.as_ref().to_path_buf(),
            memory,
            capability_id: enrollment.capability_id.clone(),
        })
    }

    pub fn handles(operation: &str) -> bool {
        OPERATOR_ROUTE_OPERATIONS.contains(&operation)
    }

    pub fn dispatch(
        &self,
        session: &AuthenticatedSession,
        envelope: &Envelope,
        now_ms: u64,
    ) -> OperatorRouteResponse {
        if envelope.scope.as_ref() != Some(&session.bound_scope) {
            return failure(error(
                "SCOPE_DENIED",
                "request scope does not match the authenticated session scope",
                EffectState::NotStarted,
            ));
        }
        let Some(operation) = envelope.operation.as_deref() else {
            return failure(invalid("operator request requires an operation"));
        };
        if !Self::handles(operation) {
            return failure(invalid("operator operation is unavailable"));
        }
        let Some(trace) = envelope.trace.as_ref() else {
            return failure(invalid("operator request requires a trace"));
        };
        let payload = envelope.payload.clone().unwrap_or_else(|| json!({}));
        let result = match operation {
            "diagnostics.get" => self.diagnostics(session, trace, now_ms, false),
            "diagnostics.rerun" => self.diagnostics(session, trace, now_ms, true),
            "maintenance.plan" => self.plan(session, envelope, trace, payload, now_ms),
            "maintenance.apply" => self.apply(session, trace, payload, now_ms),
            "maintenance.status" => self.status(session, payload, now_ms),
            "maintenance.cancel" => self.cancel(session, payload, now_ms),
            _ => unreachable!(),
        };
        match result {
            Ok(payload) => OperatorRouteResponse {
                payload: Some(payload),
                error: None,
            },
            Err(route_error) => failure(route_error),
        }
    }

    fn diagnostics(
        &self,
        session: &AuthenticatedSession,
        trace: &Trace,
        now_ms: u64,
        refresh: bool,
    ) -> Result<Value, Error> {
        if !refresh {
            if let Some(mut cached) = self.load_state("diagnostics")? {
                mark_cached(&mut cached);
                return Ok(cached);
            }
        }
        let access = AccessRequest {
            operation: GrantOperation::Read,
            actor_scope: session.bound_scope.clone(),
            target_scope: session.bound_scope.clone(),
            resource_id: static_id(MEMORY_LIST_RESOURCE),
            category: None,
            trace: trace.clone(),
        };
        let context = Dispatcher
            .context_for_session(
                session,
                AuthorizationRequest {
                    claimed_scope: session.bound_scope.clone(),
                    target_scope: session.bound_scope.clone(),
                    operation: CapabilityOperation::Read,
                    resource_id: static_id(MEMORY_LIST_RESOURCE),
                    now_ms,
                },
                self.capability_id.clone(),
            )
            .map_err(|_| denied())?;
        let diagnostics = self
            .memory
            .lock()
            .map_err(|_| unavailable("memory service lock is unavailable"))?
            .diagnostics(&context, &access)?;
        let evidence = self.evidence(&session.bound_scope)?;
        let sequence = self.next_diagnostics_sequence()?;
        let observed_at = timestamp(now_ms)?;
        let checks = vec![
            check(
                "store-schema",
                "Memory schema",
                if evidence.quick_check == "ok" {
                    "healthy"
                } else {
                    "unhealthy"
                },
                &observed_at,
                format!(
                    "schema version {} is readable; SQLite integrity is {}",
                    diagnostics.schema_version, evidence.quick_check
                ),
                if evidence.quick_check == "ok" {
                    None
                } else {
                    Some("Run an integrity maintenance plan before writing memory")
                },
                json!({"schema_version": diagnostics.schema_version, "quick_check": evidence.quick_check}),
            ),
            check(
                "memory-indexes",
                "Memory indexes",
                if diagnostics.stale_record_count == 0 {
                    "healthy"
                } else {
                    "degraded"
                },
                &observed_at,
                format!(
                    "{} records are stale across {} authoritative records",
                    diagnostics.stale_record_count, diagnostics.record_count
                ),
                if diagnostics.stale_record_count == 0 {
                    None
                } else {
                    Some("Review and run a reconciliation or reindex plan")
                },
                json!({"record_count": diagnostics.record_count, "stale_record_count": diagnostics.stale_record_count}),
            ),
            check(
                "memory-vectors",
                "Vector derivatives",
                "healthy",
                &observed_at,
                format!(
                    "{} stored embeddings are readable",
                    diagnostics.embedding_count
                ),
                None,
                json!({"embedding_count": diagnostics.embedding_count}),
            ),
            check(
                "maintenance-queue",
                "Maintenance queue",
                "healthy",
                &observed_at,
                format!(
                    "{} maintenance jobs are queued",
                    diagnostics.queued_job_count
                ),
                None,
                json!({"queued_job_count": diagnostics.queued_job_count}),
            ),
            check(
                "maintenance-leases",
                "Maintenance leases",
                "healthy",
                &observed_at,
                format!(
                    "{} maintenance leases are active",
                    diagnostics.active_lease_count
                ),
                None,
                json!({"active_lease_count": diagnostics.active_lease_count}),
            ),
            check(
                "authenticated-session",
                "Authenticated daemon session",
                "healthy",
                &observed_at,
                "the request scope and authenticated transport session agree".into(),
                None,
                json!({"scope": session.bound_scope, "protocol": session.accepted.protocol}),
            ),
        ];
        let snapshot = json!({
            "scope": session.bound_scope,
            "observed_at": observed_at,
            "cursor": Cursor::new(1, sequence).map_err(|_| unavailable("diagnostic cursor exhausted"))?,
            "cached": false,
            "checks": checks,
        });
        self.save_state("diagnostics", &snapshot)?;
        Ok(snapshot)
    }

    fn plan(
        &self,
        session: &AuthenticatedSession,
        envelope: &Envelope,
        _trace: &Trace,
        payload: Value,
        now_ms: u64,
    ) -> Result<Value, Error> {
        let request: PlanPayload = parse(payload)?;
        let action = action(&request.action)?;
        if serde_json::to_vec(&request.params)
            .map_err(|_| invalid("maintenance params are invalid"))?
            .len()
            > 65_536
        {
            return Err(invalid("maintenance params exceed their bound"));
        }
        let authority_digest = self.authority_digest(session, now_ms)?;
        let evidence = self.evidence(&session.bound_scope)?;
        let blocker_digest = digest_json(
            &serde_json::to_value(&evidence)
                .map_err(|_| unavailable("maintenance evidence could not be encoded"))?,
        )?;
        let input_digest = blocker_digest.clone();
        let config_digest =
            digest_json(&json!({"action": request.action, "params": request.params}))?;
        let plan_id = format!("maintenance-plan-{}", envelope.message_id.as_str());
        Id::new(plan_id.clone()).map_err(|_| invalid("maintenance plan id is invalid"))?;
        let created_at = timestamp(now_ms)?;
        let expires_ms = now_ms
            .checked_add(PLAN_TTL_MS)
            .ok_or_else(|| unavailable("maintenance plan expiry overflowed"))?;
        let blockers = if evidence.quick_check != "ok" && request.action != "integrity_check" {
            vec!["memory store integrity must be restored before this action".to_owned()]
        } else {
            Vec::new()
        };
        let steps = vec![json!({
            "id": "execute",
            "title": action.title,
            "effect": action.effect,
            "state": "planned",
        })];
        let mut plan = StoredPlan {
            plan_id,
            operation: request.action,
            scope: session.bound_scope.clone(),
            created_ms: now_ms,
            expires_ms,
            plan_digest: String::new(),
            destructive: action.destructive,
            restart_required: false,
            steps,
            blockers,
            authority_digest,
            blocker_digest,
            input_digest,
            config_digest,
            params: Value::Object(request.params),
        };
        plan.plan_digest = digest_json(&plan_digest_value(&plan)?)?;
        self.store_plan(&plan)?;
        plan_wire(&plan, &created_at)
    }

    fn apply(
        &self,
        session: &AuthenticatedSession,
        trace: &Trace,
        payload: Value,
        now_ms: u64,
    ) -> Result<Value, Error> {
        let request: ApplyPayload = parse(payload)?;
        let plan = self.load_plan(&request.plan_id)?;
        if plan.scope != session.bound_scope {
            return Err(denied());
        }
        if request.plan_digest != plan.plan_digest {
            return Err(error(
                "PLAN_CHANGED",
                "reviewed maintenance plan digest changed",
                EffectState::NotStarted,
            ));
        }
        if now_ms >= plan.expires_ms {
            return Err(error(
                "PLAN_EXPIRED",
                "reviewed maintenance plan expired",
                EffectState::NotStarted,
            ));
        }
        if !plan.blockers.is_empty() {
            return Err(error(
                "PLAN_BLOCKED",
                "reviewed maintenance plan has active blockers",
                EffectState::NotStarted,
            ));
        }
        if plan.destructive && !request.confirm_destructive {
            return Err(error(
                "CONFIRMATION_REQUIRED",
                "destructive maintenance requires confirmation",
                EffectState::NotStarted,
            ));
        }
        if request.authority_digest.as_deref() != Some(plan.authority_digest.as_str())
            || request.blocker_digest.as_deref() != Some(plan.blocker_digest.as_str())
        {
            return Err(error(
                "PLAN_CHANGED",
                "maintenance authority or blockers differ from the reviewed plan",
                EffectState::NotStarted,
            ));
        }
        if self.authority_digest(session, now_ms)? != plan.authority_digest {
            return Err(denied());
        }
        let evidence = self.evidence(&session.bound_scope)?;
        let current_blockers = digest_json(
            &serde_json::to_value(&evidence)
                .map_err(|_| unavailable("maintenance evidence could not be encoded"))?,
        )?;
        if current_blockers != plan.blocker_digest {
            return Err(error(
                "BLOCKERS_CHANGED",
                "maintenance blockers changed after review",
                EffectState::NotStarted,
            ));
        }
        let job_id = format!(
            "maintenance-job-{}",
            plan.plan_id.trim_start_matches("maintenance-plan-")
        );
        Id::new(job_id.clone()).map_err(|_| invalid("maintenance job id is invalid"))?;
        if let Some(job) = self.load_job(&job_id)? {
            return self.receipt(session, &job, now_ms);
        }
        let mut job = StoredJob {
            job_id: job_id.clone(),
            plan_id: plan.plan_id.clone(),
            plan_digest: plan.plan_digest.clone(),
            operation: plan.operation.clone(),
            scope: plan.scope.clone(),
            started_ms: now_ms,
            finished_ms: None,
            outcome_digest: None,
        };
        self.store_job(&job)?;
        let action = action(&plan.operation)?;
        let job_id = Id::new(job_id).map_err(|_| invalid("maintenance job id is invalid"))?;
        let mutation = MutationRequest {
            operation: action.operation,
            actor_scope: session.bound_scope.clone(),
            target_scope: session.bound_scope.clone(),
            record_id: None,
            category: None,
            revision: RevisionPrecondition::MustNotExist,
            trace: trace.clone(),
        };
        let context = Dispatcher
            .context_for_session(
                session,
                AuthorizationRequest {
                    claimed_scope: session.bound_scope.clone(),
                    target_scope: session.bound_scope.clone(),
                    operation: capability_operation(action.operation),
                    resource_id: static_id(MEMORY_MAINTENANCE_RESOURCE),
                    now_ms,
                },
                self.capability_id.clone(),
            )
            .map_err(|_| denied())?;
        let spec = MaintenanceJobSpec {
            id: job_id.clone(),
            kind: action.kind,
            target_scope: session.bound_scope.clone(),
            actor_scope: session.bound_scope.clone(),
            required_operation: action.operation,
            input_cursor: evidence.cursor,
            input_digest: parse_digest(&plan.input_digest)?,
            config_digest: parse_digest(&plan.config_digest)?,
            budget: ModelBudget {
                max_items: 0,
                max_input_tokens: 0,
                max_output_tokens: 0,
                max_requests: 0,
                max_cost_units: 0,
                max_retries: 0,
                max_wall_ms: 30_000,
            },
            available_at_ms: i64::try_from(now_ms)
                .map_err(|_| invalid("maintenance time is out of range"))?,
            created_at_ms: i64::try_from(now_ms)
                .map_err(|_| invalid("maintenance time is out of range"))?,
        };
        let mut memory = self
            .memory
            .lock()
            .map_err(|_| unavailable("memory service lock is unavailable"))?;
        memory.enqueue_maintenance(&context, &mutation, &spec)?;
        if action.kind == MaintenanceKind::CheckIntegrity {
            let claim = memory
                .claim_maintenance(
                    &context,
                    &mutation,
                    &static_id("operator-integrity"),
                    CLAIM_TTL_MS,
                )?
                .ok_or_else(|| {
                    error(
                        "MAINTENANCE_BUSY",
                        "integrity maintenance could not acquire its scoped lease",
                        EffectState::Committed,
                    )
                })?;
            if claim.job_id != job_id {
                return Err(error(
                    "MAINTENANCE_BUSY",
                    "another maintenance job owns the scoped lease",
                    EffectState::Committed,
                ));
            }
            let proof = hypermid_memory::api::MaintenanceClaimKey {
                job_id: claim.job_id,
                holder_id: claim.holder_id,
                fencing_token: claim.fencing_token,
            };
            let publication = memory.publish_integrity_maintenance(
                &context,
                &mutation,
                &proof,
                parse_digest(&plan.input_digest)?,
                parse_digest(&plan.config_digest)?,
            )?;
            job.finished_ms = u64::try_from(publication.finished_at_ms).ok();
            job.outcome_digest = Some(publication.output_digest.to_hex());
            self.update_job(&job)?;
        }
        drop(memory);
        self.receipt(session, &job, now_ms)
    }

    fn status(
        &self,
        session: &AuthenticatedSession,
        payload: Value,
        now_ms: u64,
    ) -> Result<Value, Error> {
        let request: JobPayload = parse(payload)?;
        let job = self.load_job(&request.job_id)?.ok_or_else(|| {
            error(
                "JOB_NOT_FOUND",
                "maintenance receipt is unavailable",
                EffectState::NotStarted,
            )
        })?;
        if job.scope != session.bound_scope {
            return Err(denied());
        }
        self.authority_digest(session, now_ms)?;
        self.receipt(session, &job, now_ms)
    }

    fn cancel(
        &self,
        session: &AuthenticatedSession,
        payload: Value,
        now_ms: u64,
    ) -> Result<Value, Error> {
        let request: JobPayload = parse(payload)?;
        let job = self.load_job(&request.job_id)?.ok_or_else(|| {
            error(
                "JOB_NOT_FOUND",
                "maintenance receipt is unavailable",
                EffectState::NotStarted,
            )
        })?;
        if job.scope != session.bound_scope {
            return Err(denied());
        }
        self.authority_digest(session, now_ms)?;
        let receipt = self.receipt(session, &job, now_ms)?;
        if receipt.get("state").and_then(Value::as_str) != Some("running") {
            return Ok(receipt);
        }
        Err(error(
            "CANCELLATION_PENDING",
            "maintenance cancellation requires the active fenced worker to acknowledge it",
            EffectState::NotStarted,
        ))
    }

    fn receipt(
        &self,
        _session: &AuthenticatedSession,
        job: &StoredJob,
        now_ms: u64,
    ) -> Result<Value, Error> {
        let plan = self.load_plan(&job.plan_id)?;
        let connection = self.open_memory()?;
        let owner = hypermid_memory::scope_digest(&job.scope).to_hex();
        let row: Option<(String, String, Option<String>, Option<i64>, Option<String>)> = connection
            .query_row(
                "SELECT state,input_cursor_json,checkpoint_cursor_json,finished_at_ms,last_error_code
                 FROM maintenance_jobs WHERE job_id=?1 AND owner_scope_digest=?2",
                params![job.job_id, owner],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?, row.get(4)?)),
            )
            .optional()
            .map_err(|_| unavailable("maintenance receipt could not be read"))?;
        let Some((scheduler_state, input_cursor, checkpoint_cursor, finished_ms, last_error)) = row
        else {
            return Ok(receipt_wire(
                job,
                &plan,
                "outcome_unknown",
                None,
                None,
                Some(
                    json!({"code": "OUTCOME_UNKNOWN", "message": "scheduler acceptance could not be reconciled"}),
                ),
                now_ms,
            )?);
        };
        let state = match scheduler_state.as_str() {
            "succeeded" => "committed",
            "failed" => "failed",
            "abandoned" if last_error.as_deref() == Some("CANCELLED") => "cancelled",
            "abandoned" => "failed",
            _ => "running",
        };
        let cursor_text = checkpoint_cursor.unwrap_or(input_cursor);
        let cursor: Cursor = serde_json::from_str(&cursor_text)
            .map_err(|_| unavailable("maintenance cursor is malformed"))?;
        let error = last_error.map(|code| json!({"code": code, "message": "maintenance ended without a committed publication"}));
        receipt_wire(
            job,
            &plan,
            state,
            finished_ms.map(|value| value as u64).or(job.finished_ms),
            Some(cursor),
            error,
            now_ms,
        )
    }

    fn authority_digest(
        &self,
        session: &AuthenticatedSession,
        now_ms: u64,
    ) -> Result<String, Error> {
        let connection = self.open_memory()?;
        let authorization = Dispatcher
            .authorize_session(
                &connection,
                session,
                AuthorizationRequest {
                    claimed_scope: session.bound_scope.clone(),
                    target_scope: session.bound_scope.clone(),
                    operation: CapabilityOperation::Revise,
                    resource_id: static_id(MEMORY_MAINTENANCE_RESOURCE),
                    now_ms,
                },
                self.capability_id.clone(),
            )
            .map_err(|_| denied())?;
        digest_json(&json!({
            "capability_id": self.capability_id,
            "principal_id": session.accepted.principal.id,
            "scope": session.bound_scope,
            "operation": "revise",
            "resource": MEMORY_MAINTENANCE_RESOURCE,
            "revision": authorization.revision,
        }))
    }

    fn evidence(&self, scope: &Scope) -> Result<StoreEvidence, Error> {
        let connection = self.open_memory()?;
        let owner = hypermid_memory::scope_digest(scope).to_hex();
        let quick_check: String = connection
            .query_row("PRAGMA quick_check", [], |row| row.get(0))
            .map_err(|_| unavailable("memory integrity evidence is unavailable"))?;
        let (schema_version, schema_digest): (u64, String) = connection
            .query_row(
                "SELECT current_version,schema_digest FROM hypermid_schema_version WHERE singleton=1",
                [],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .map_err(|_| unavailable("memory schema evidence is unavailable"))?;
        let cursor = connection
            .query_row(
                "SELECT epoch,sequence FROM memory_scopes WHERE scope_digest=?1",
                [&owner],
                |row| Ok((row.get::<_, u64>(0)?, row.get::<_, u64>(1)?)),
            )
            .optional()
            .map_err(|_| unavailable("memory cursor evidence is unavailable"))?
            .map(|(epoch, sequence)| Cursor::new(epoch, sequence))
            .transpose()
            .map_err(|_| unavailable("memory cursor evidence is invalid"))?
            .unwrap_or(Cursor::new(1, 0).expect("initial cursor is valid"));
        let count = |sql: &str| {
            connection
                .query_row(sql, [&owner], |row| row.get::<_, u64>(0))
                .map_err(|_| unavailable("memory blocker evidence is unavailable"))
        };
        Ok(StoreEvidence {
            quick_check,
            schema_version,
            schema_digest,
            cursor,
            records: count("SELECT count(*) FROM memory_records WHERE owner_scope_digest=?1")?,
            stale_records: count("SELECT count(*) FROM memory_records WHERE owner_scope_digest=?1 AND status='stale'")?,
            queued_jobs: count("SELECT count(*) FROM maintenance_jobs WHERE owner_scope_digest=?1 AND state IN ('queued','checkpointed','claimed','running')")?,
            active_leases: count("SELECT count(*) FROM maintenance_leases WHERE owner_scope_digest=?1")?,
        })
    }

    fn open_memory(&self) -> Result<Connection, Error> {
        Connection::open_with_flags(
            &self.memory_path,
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )
        .map_err(|_| unavailable("memory authority store is unavailable"))
    }

    fn load_state(&self, key: &str) -> Result<Option<Value>, Error> {
        let connection = self
            .state
            .lock()
            .map_err(|_| unavailable("operator state lock is unavailable"))?;
        let value: Option<String> = connection
            .query_row(
                "SELECT value FROM operator_state WHERE key=?1",
                [key],
                |row| row.get(0),
            )
            .optional()
            .map_err(|_| unavailable("operator state is unavailable"))?;
        value
            .map(|value| {
                serde_json::from_str(&value).map_err(|_| unavailable("operator state is corrupt"))
            })
            .transpose()
    }

    fn save_state(&self, key: &str, value: &Value) -> Result<(), Error> {
        let encoded = serde_json::to_string(value)
            .map_err(|_| unavailable("operator state could not be encoded"))?;
        self.state
            .lock()
            .map_err(|_| unavailable("operator state lock is unavailable"))?
            .execute(
                "INSERT INTO operator_state(key,value) VALUES(?1,?2) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                params![key, encoded],
            )
            .map_err(|_| unavailable("operator state could not be saved"))?;
        Ok(())
    }

    fn next_diagnostics_sequence(&self) -> Result<u64, Error> {
        let current = self
            .load_state("diagnostics-sequence")?
            .and_then(|value| value.as_u64())
            .unwrap_or(0);
        let next = current
            .checked_add(1)
            .ok_or_else(|| unavailable("diagnostic cursor exhausted"))?;
        self.save_state("diagnostics-sequence", &json!(next))?;
        Ok(next)
    }

    fn store_plan(&self, plan: &StoredPlan) -> Result<(), Error> {
        let encoded = serde_json::to_string(plan)
            .map_err(|_| unavailable("maintenance plan could not be encoded"))?;
        let connection = self
            .state
            .lock()
            .map_err(|_| unavailable("operator state lock is unavailable"))?;
        let existing: Option<String> = connection
            .query_row(
                "SELECT plan_json FROM operator_plans WHERE plan_id=?1",
                [&plan.plan_id],
                |row| row.get(0),
            )
            .optional()
            .map_err(|_| unavailable("maintenance plan store is unavailable"))?;
        if let Some(existing) = existing {
            if existing != encoded {
                return Err(error(
                    "PLAN_ID_CONFLICT",
                    "maintenance plan id was reused with different inputs",
                    EffectState::NotStarted,
                ));
            }
            return Ok(());
        }
        connection
            .execute(
                "INSERT INTO operator_plans(plan_id,plan_json) VALUES(?1,?2)",
                params![plan.plan_id, encoded],
            )
            .map_err(|_| unavailable("maintenance plan could not be saved"))?;
        Ok(())
    }

    fn load_plan(&self, plan_id: &str) -> Result<StoredPlan, Error> {
        let connection = self
            .state
            .lock()
            .map_err(|_| unavailable("operator state lock is unavailable"))?;
        let encoded: Option<String> = connection
            .query_row(
                "SELECT plan_json FROM operator_plans WHERE plan_id=?1",
                [plan_id],
                |row| row.get(0),
            )
            .optional()
            .map_err(|_| unavailable("maintenance plan store is unavailable"))?;
        serde_json::from_str(&encoded.ok_or_else(|| {
            error(
                "PLAN_NOT_FOUND",
                "reviewed maintenance plan is unavailable",
                EffectState::NotStarted,
            )
        })?)
        .map_err(|_| unavailable("maintenance plan store is corrupt"))
    }

    fn store_job(&self, job: &StoredJob) -> Result<(), Error> {
        let encoded = serde_json::to_string(job)
            .map_err(|_| unavailable("maintenance receipt could not be encoded"))?;
        self.state
            .lock()
            .map_err(|_| unavailable("operator state lock is unavailable"))?
            .execute(
                "INSERT INTO operator_jobs(job_id,job_json) VALUES(?1,?2)",
                params![job.job_id, encoded],
            )
            .map_err(|_| unavailable("maintenance receipt could not be saved"))?;
        Ok(())
    }

    fn update_job(&self, job: &StoredJob) -> Result<(), Error> {
        let encoded = serde_json::to_string(job)
            .map_err(|_| unavailable("maintenance receipt could not be encoded"))?;
        let changed = self
            .state
            .lock()
            .map_err(|_| unavailable("operator state lock is unavailable"))?
            .execute(
                "UPDATE operator_jobs SET job_json=?2 WHERE job_id=?1",
                params![job.job_id, encoded],
            )
            .map_err(|_| unavailable("maintenance receipt could not be saved"))?;
        if changed != 1 {
            return Err(unavailable(
                "maintenance receipt disappeared during publication",
            ));
        }
        Ok(())
    }

    fn load_job(&self, job_id: &str) -> Result<Option<StoredJob>, Error> {
        let connection = self
            .state
            .lock()
            .map_err(|_| unavailable("operator state lock is unavailable"))?;
        let encoded: Option<String> = connection
            .query_row(
                "SELECT job_json FROM operator_jobs WHERE job_id=?1",
                [job_id],
                |row| row.get(0),
            )
            .optional()
            .map_err(|_| unavailable("maintenance receipt store is unavailable"))?;
        encoded
            .map(|value| {
                serde_json::from_str(&value)
                    .map_err(|_| unavailable("maintenance receipt store is corrupt"))
            })
            .transpose()
    }
}

#[derive(Clone, Debug, Serialize)]
struct StoreEvidence {
    quick_check: String,
    schema_version: u64,
    schema_digest: String,
    cursor: Cursor,
    records: u64,
    stale_records: u64,
    queued_jobs: u64,
    active_leases: u64,
}

fn action(value: &str) -> Result<Action, Error> {
    match value {
        "integrity_check" => Ok(Action {
            kind: MaintenanceKind::CheckIntegrity,
            operation: Operation::Index,
            title: "Check durable memory integrity",
            effect: "read",
            destructive: false,
        }),
        "reconcile" => Ok(Action {
            kind: MaintenanceKind::ReconcileSources,
            operation: Operation::Index,
            title: "Reconcile memory sources",
            effect: "write_derivative",
            destructive: false,
        }),
        "reindex" => Ok(Action {
            kind: MaintenanceKind::ReconcileFts,
            operation: Operation::Index,
            title: "Rebuild lexical indexes",
            effect: "write_derivative",
            destructive: false,
        }),
        "compact" => Ok(Action {
            kind: MaintenanceKind::CompactEvents,
            operation: Operation::Update,
            title: "Compact durable event storage",
            effect: "write_derivative",
            destructive: false,
        }),
        "cleanup_stale_cache" => Ok(Action {
            kind: MaintenanceKind::SweepOrphans,
            operation: Operation::Index,
            title: "Clean stale derived cache state",
            effect: "delete_derivative",
            destructive: false,
        }),
        "rebuild_derivatives" => Ok(Action {
            kind: MaintenanceKind::ReconcileFts,
            operation: Operation::Index,
            title: "Rebuild derived memory state",
            effect: "write_derivative",
            destructive: false,
        }),
        _ => Err(invalid("maintenance action is unsupported")),
    }
}

fn plan_digest_value(plan: &StoredPlan) -> Result<Value, Error> {
    serde_json::to_value(json!({
        "plan_id": plan.plan_id,
        "operation": plan.operation,
        "scope": plan.scope,
        "created_ms": plan.created_ms,
        "expires_ms": plan.expires_ms,
        "destructive": plan.destructive,
        "restart_required": plan.restart_required,
        "steps": plan.steps,
        "blockers": plan.blockers,
        "authority_digest": plan.authority_digest,
        "blocker_digest": plan.blocker_digest,
        "input_digest": plan.input_digest,
        "config_digest": plan.config_digest,
        "params": plan.params,
    }))
    .map_err(|_| unavailable("maintenance plan could not be encoded"))
}

fn plan_wire(plan: &StoredPlan, created_at: &str) -> Result<Value, Error> {
    Ok(json!({
        "plan_id": plan.plan_id,
        "operation": plan.operation,
        "scope": plan.scope,
        "created_at": created_at,
        "expires_at": timestamp(plan.expires_ms)?,
        "plan_digest": plan.plan_digest,
        "destructive": plan.destructive,
        "restart_required": plan.restart_required,
        "steps": plan.steps,
        "blockers": plan.blockers,
        "authority_digest": plan.authority_digest,
        "blocker_digest": plan.blocker_digest,
        "params": plan.params,
    }))
}

fn receipt_wire(
    job: &StoredJob,
    plan: &StoredPlan,
    state: &str,
    finished_ms: Option<u64>,
    cursor: Option<Cursor>,
    error_value: Option<Value>,
    now_ms: u64,
) -> Result<Value, Error> {
    let terminal = state != "running";
    let step_state = match state {
        "committed" => "committed",
        "cancelled" => "cancelled",
        "failed" => "failed",
        "outcome_unknown" => "unknown",
        _ => "running",
    };
    Ok(json!({
        "job_id": job.job_id,
        "operation": job.operation,
        "scope": job.scope,
        "plan_digest": job.plan_digest,
        "state": state,
        "started_at": timestamp(job.started_ms)?,
        "finished_at": if terminal { Some(timestamp(finished_ms.unwrap_or(now_ms))?) } else { None },
        "cursor": cursor,
        "steps": [{
            "id": "execute",
            "title": plan.steps.first().and_then(|step| step.get("title")).and_then(Value::as_str).unwrap_or("Execute maintenance"),
            "effect": plan.steps.first().and_then(|step| step.get("effect")).and_then(Value::as_str).unwrap_or("write_derivative"),
            "state": step_state,
        }],
        "rollback_available": false,
        "artifact_digest": job.outcome_digest,
        "error": error_value,
    }))
}

fn check(
    id: &str,
    title: &str,
    health: &str,
    observed_at: &str,
    summary: String,
    next_action: Option<&str>,
    evidence: Value,
) -> Value {
    json!({
        "id": id,
        "title": title,
        "health": health,
        "observed_at": observed_at,
        "cached": false,
        "summary": summary,
        "next_action": next_action,
        "evidence_digest": Digest::sha256(serde_json::to_vec(&evidence).unwrap_or_default()).to_hex(),
    })
}

fn mark_cached(value: &mut Value) {
    if let Some(object) = value.as_object_mut() {
        object.insert("cached".into(), Value::Bool(true));
        if let Some(checks) = object.get_mut("checks").and_then(Value::as_array_mut) {
            for check in checks {
                if let Some(check) = check.as_object_mut() {
                    check.insert("cached".into(), Value::Bool(true));
                }
            }
        }
    }
}

fn parse<T: for<'de> Deserialize<'de>>(payload: Value) -> Result<T, Error> {
    serde_json::from_value(payload).map_err(|_| invalid("operator request payload is invalid"))
}

fn parse_digest(value: &str) -> Result<Digest, Error> {
    value
        .parse()
        .map_err(|_| invalid("maintenance digest is invalid"))
}

fn digest_json(value: &Value) -> Result<String, Error> {
    let encoded = serde_json::to_vec(value)
        .map_err(|_| unavailable("operator evidence could not be encoded"))?;
    Ok(Digest::sha256(encoded).to_hex())
}

fn timestamp(epoch_ms: u64) -> Result<String, Error> {
    const MAX_TIMESTAMP_MS: u64 = 253_402_300_799_999;
    if epoch_ms > MAX_TIMESTAMP_MS {
        return Err(invalid("timestamp is outside the supported range"));
    }
    let seconds = epoch_ms / 1_000;
    let millis = epoch_ms % 1_000;
    let days = seconds / 86_400;
    let day_seconds = seconds % 86_400;
    let (year, month, day) = civil_from_days(days as i64);
    Ok(format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}.{millis:03}Z",
        day_seconds / 3_600,
        (day_seconds % 3_600) / 60,
        day_seconds % 60,
    ))
}

fn civil_from_days(days_since_epoch: i64) -> (i64, i64, i64) {
    let z = days_since_epoch + 719_468;
    let era = z.div_euclid(146_097);
    let day_of_era = z - era * 146_097;
    let year_of_era =
        (day_of_era - day_of_era / 1_460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let mut year = year_of_era + era * 400;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let month_prime = (5 * day_of_year + 2) / 153;
    let day = day_of_year - (153 * month_prime + 2) / 5 + 1;
    let month = month_prime + if month_prime < 10 { 3 } else { -9 };
    year += if month <= 2 { 1 } else { 0 };
    (year, month, day)
}

fn static_id(value: &str) -> Id {
    Id::new(value).expect("static operator resource id is valid")
}

fn invalid(message: &str) -> Error {
    error("INVALID_ARGUMENT", message, EffectState::NotStarted)
}

fn denied() -> Error {
    error(
        "AUTHORIZATION_DENIED",
        "operator maintenance requires the exact live enrolled authority",
        EffectState::NotStarted,
    )
}

fn unavailable(message: &str) -> Error {
    error("OPERATOR_BACKEND_FAILED", message, EffectState::Unknown)
}

fn error(code: &str, message: &str, state: EffectState) -> Error {
    Error::new(code, message, false, None, Some(state))
        .expect("operator route errors satisfy the shared contract")
}

fn failure(error: Error) -> OperatorRouteResponse {
    OperatorRouteResponse {
        payload: None,
        error: Some(error),
    }
}

#[cfg(unix)]
fn protect_file(path: &Path) -> Result<(), String> {
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600))
        .map_err(|error| error.to_string())
}

#[cfg(not(unix))]
fn protect_file(_path: &Path) -> Result<(), String> {
    Ok(())
}
