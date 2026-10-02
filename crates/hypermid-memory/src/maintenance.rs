use crate::budget::{
    self, ActualUsage, BudgetAmount, BudgetReservation, ModelBudget, UsageSummary,
};
use crate::lease::{self, LeaseClaim};
use crate::model::{
    scope_digest, Authorization, AuthorizationBasis, MutationRequest, Operation,
    RevisionPrecondition,
};
use crate::provenance::{put_source, SourceKind, SourceSnapshot};
use crate::records::{self, RecordDraft, RecordKind, VerificationState};
use crate::sharing::{self, KnowledgeSharingJudgment, SharingJudgmentReceipt};
use crate::smart_note::{PredicateContext, PredicateField, PredicateScalar, SmartPredicate};
use crate::{
    error, AuthContext, Cursor, Digest, EffectState, Id, MemoryResult, MemoryStore,
    MemoryTransaction, Scope, Trace,
};
use rusqlite::{params, OptionalExtension};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};

const MAX_KNOWLEDGE_OUTPUTS: usize = 200;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum MaintenanceKind {
    ExtractFacts,
    ExtractEpisodes,
    VerifyClaims,
    EvaluateSmartNotes,
    RefreshSummaries,
    DecaySummaries,
    EmbedRecords,
    ReembedModel,
    ReconcileFts,
    ReconcileSources,
    IndexMessages,
    IndexGitCommits,
    InvalidateLineage,
    SweepOrphans,
    CompactEvents,
    CheckIntegrity,
    ImportBatch,
    ExportBatch,
    PurgeTombstones,
}

impl MaintenanceKind {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::ExtractFacts => "extract_facts",
            Self::ExtractEpisodes => "extract_episodes",
            Self::VerifyClaims => "verify_claims",
            Self::EvaluateSmartNotes => "evaluate_smart_notes",
            Self::RefreshSummaries => "refresh_summaries",
            Self::DecaySummaries => "decay_summaries",
            Self::EmbedRecords => "embed_records",
            Self::ReembedModel => "reembed_model",
            Self::ReconcileFts => "reconcile_fts",
            Self::ReconcileSources => "reconcile_sources",
            Self::IndexMessages => "index_messages",
            Self::IndexGitCommits => "index_git_commits",
            Self::InvalidateLineage => "invalidate_lineage",
            Self::SweepOrphans => "sweep_orphans",
            Self::CompactEvents => "compact_events",
            Self::CheckIntegrity => "check_integrity",
            Self::ImportBatch => "import_batch",
            Self::ExportBatch => "export_batch",
            Self::PurgeTombstones => "purge_tombstones",
        }
    }

    const fn lock_family(self) -> &'static str {
        match self {
            Self::EmbedRecords | Self::ReembedModel => "vectors",
            Self::ReconcileFts
            | Self::ReconcileSources
            | Self::IndexMessages
            | Self::IndexGitCommits
            | Self::SweepOrphans => "indexes",
            Self::ImportBatch | Self::ExportBatch => "portability",
            Self::CheckIntegrity => "integrity",
            Self::CompactEvents => "events",
            _ => "records",
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct MaintenanceJobSpec {
    pub id: Id,
    pub kind: MaintenanceKind,
    pub target_scope: Scope,
    pub actor_scope: Scope,
    pub required_operation: Operation,
    pub input_cursor: Cursor,
    pub input_digest: Digest,
    pub config_digest: Digest,
    pub budget: ModelBudget,
    pub available_at_ms: i64,
    pub created_at_ms: i64,
}

#[derive(Clone, Debug)]
pub struct ClaimedJob {
    pub id: Id,
    pub kind: MaintenanceKind,
    pub actor_scope: Scope,
    pub target_scope: Scope,
    pub required_operation: Operation,
    pub input_cursor: Cursor,
    pub input_digest: Digest,
    pub config_digest: Digest,
    pub budget: ModelBudget,
    pub available_at_ms: i64,
    pub trace: Trace,
    pub authorization: Authorization,
    pub lease: LeaseClaim,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum TerminalState {
    Failed,
    Abandoned,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct MaintenanceClaimReceipt {
    pub job_id: Id,
    pub kind: MaintenanceKind,
    pub actor_scope: Scope,
    pub target_scope: Scope,
    pub required_operation: Operation,
    pub input_cursor: Cursor,
    pub input_digest: Digest,
    pub config_digest: Digest,
    pub budget: ModelBudget,
    pub available_at_ms: i64,
    pub trace: Trace,
    pub holder_id: String,
    pub fencing_token: String,
    pub expires_at_ms: i64,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct MaintenanceJobStatus {
    pub job_id: Id,
    pub kind: MaintenanceKind,
    pub state: String,
    pub input_cursor: Cursor,
    pub checkpoint_cursor: Option<Cursor>,
    pub attempt: u64,
    pub available_at_ms: i64,
    pub finished_at_ms: Option<i64>,
    pub last_error_code: Option<String>,
    pub usage: UsageSummary,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum MaintenancePublication {
    Summary {
        job_id: Id,
        expected_input_digest: Digest,
        expected_config_digest: Digest,
        draft: RecordDraft,
        #[serde(default)]
        sources: Vec<SourceSnapshot>,
        now_ms: u64,
    },
    Knowledge(KnowledgePublication),
}

impl MaintenancePublication {
    pub fn job_id(&self) -> &Id {
        match self {
            Self::Summary { job_id, .. } => job_id,
            Self::Knowledge(publication) => &publication.job_id,
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct KnowledgePublication {
    pub job_id: Id,
    pub expected_input_digest: Digest,
    pub expected_config_digest: Digest,
    pub source: SourceSnapshot,
    pub repository_identity_digest: Option<Digest>,
    pub refs_digest: Option<Digest>,
    pub evaluated_cursor: Cursor,
    pub next_evaluation_at_ms: Option<i64>,
    #[serde(default)]
    pub smart_notes: Vec<SmartNoteEvaluation>,
    #[serde(default)]
    pub verifications: Vec<KnowledgeVerification>,
    #[serde(default)]
    pub sharing_judgments: Vec<KnowledgeSharingJudgment>,
    pub recall: Option<KnowledgeRecall>,
    pub now_ms: u64,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SmartNoteEvaluation {
    pub record_id: Id,
    pub expected_revision_digest: Digest,
    pub expected_predicate_digest: Digest,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct KnowledgeVerification {
    pub record_id: Id,
    pub expected_revision_digest: Digest,
    pub state: VerificationState,
    pub confidence: f64,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct KnowledgeRecall {
    pub draft: RecordDraft,
}

#[derive(Clone, Debug)]
pub struct KnowledgePublicationAuthority {
    pub record_id: Id,
    pub context: AuthContext,
    pub request: MutationRequest,
}

#[derive(Clone, Debug, Default)]
pub struct KnowledgePublicationAuthorities {
    pub smart_notes: Vec<KnowledgePublicationAuthority>,
    pub verifications: Vec<KnowledgePublicationAuthority>,
    pub sharing_judgments: Vec<KnowledgePublicationAuthority>,
    pub recall: Option<KnowledgePublicationAuthority>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SmartNoteDecision {
    pub record_id: Id,
    pub result: bool,
    pub cursor: Cursor,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct KnowledgeVerificationReceipt {
    pub record_id: Id,
    pub state: VerificationState,
    pub cursor: Cursor,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct KnowledgeRecallReceipt {
    pub record_id: Id,
    pub revision_digest: Digest,
    pub cursor: Cursor,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct KnowledgeSharingReceipt {
    pub judgment: SharingJudgmentReceipt,
    pub cursor: Cursor,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct KnowledgePublicationReceipt {
    pub job_id: Id,
    pub kind: MaintenanceKind,
    pub state: String,
    pub output_cursor: Cursor,
    pub output_digest: Digest,
    pub source_digest: Digest,
    pub smart_notes: Vec<SmartNoteDecision>,
    pub verifications: Vec<KnowledgeVerificationReceipt>,
    pub sharing_judgments: Vec<KnowledgeSharingReceipt>,
    pub recall: Option<KnowledgeRecallReceipt>,
    pub invalidated_ids: Vec<Id>,
    pub finished_at_ms: i64,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct MaintenancePublicationReceipt {
    pub job_id: Id,
    pub kind: MaintenanceKind,
    pub state: String,
    pub output_cursor: Cursor,
    pub output_digest: Digest,
    pub finished_at_ms: i64,
}

impl ClaimedJob {
    pub fn receipt(&self) -> MaintenanceClaimReceipt {
        MaintenanceClaimReceipt {
            job_id: self.id.clone(),
            kind: self.kind,
            actor_scope: self.actor_scope.clone(),
            target_scope: self.target_scope.clone(),
            required_operation: self.required_operation,
            input_cursor: self.input_cursor,
            input_digest: self.input_digest,
            config_digest: self.config_digest,
            budget: self.budget,
            available_at_ms: self.available_at_ms,
            trace: self.trace.clone(),
            holder_id: self.lease.holder_id.clone(),
            fencing_token: self.lease.token().to_hex(),
            expires_at_ms: self.lease.expires_at_ms,
        }
    }
}

pub fn enqueue(
    store: &mut MemoryStore,
    context: &AuthContext,
    request: &MutationRequest,
    spec: &MaintenanceJobSpec,
) -> MemoryResult<()> {
    validate_spec(request, spec)?;
    let now_ms = context_now(context)?;
    store.immediate_authorized(context, request, |transaction, authorization| {
        transaction.ensure_scope(&spec.target_scope, now_ms)?;
        transaction.ensure_scope(&spec.actor_scope, now_ms)?;
        let budget_json = serde_json::to_string(&spec.budget)
            .map_err(|_| corrupt("maintenance budget could not be encoded"))?;
        let input_cursor_json = serde_json::to_string(&spec.input_cursor)
            .map_err(|_| corrupt("maintenance cursor could not be encoded"))?;
        transaction
            .raw()
            .execute(
                "INSERT INTO maintenance_jobs(
                    job_id, owner_scope_digest, actor_scope_digest, required_operation,
                    claimed_grant_id, kind, state, input_cursor_json, input_digest,
                    config_digest, budget_json, usage_json, attempt, available_at_ms,
                    created_at_ms
                 ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, 'queued', ?7, ?8, ?9, ?10,
                           '{}', 1, ?11, ?12)",
                params![
                    spec.id.as_str(),
                    authorization.target_scope_digest.to_hex(),
                    authorization.actor_scope_digest.to_hex(),
                    spec.required_operation.as_str(),
                    authorization.grant.as_ref().map(|grant| grant.id.as_str()),
                    spec.kind.as_str(),
                    input_cursor_json,
                    spec.input_digest.to_hex(),
                    spec.config_digest.to_hex(),
                    budget_json,
                    spec.available_at_ms,
                    spec.created_at_ms,
                ],
            )
            .map_err(sql_error)?;
        Ok(())
    })
}

pub fn claim_next(
    store: &mut MemoryStore,
    context: &AuthContext,
    request: &MutationRequest,
    worker_id: &Id,
    ttl_ms: i64,
) -> MemoryResult<Option<ClaimedJob>> {
    let now_ms = context_now(context)?;
    store.immediate_authorized(context, request, |transaction, authorization| {
        let target_digest = authorization.target_scope_digest.to_hex();
        let actor_digest = authorization.actor_scope_digest.to_hex();
        transaction
            .raw()
            .execute(
                "UPDATE maintenance_jobs SET state='checkpointed', available_at_ms=?2,
                        last_error_code='LEASE_EXPIRED'
                 WHERE owner_scope_digest=?1 AND state IN ('claimed', 'running')
                   AND EXISTS (
                       SELECT 1 FROM maintenance_leases l
                       WHERE l.job_id=maintenance_jobs.job_id
                         AND l.owner_scope_digest=maintenance_jobs.owner_scope_digest
                         AND l.expires_at_ms<=?2
                   )",
                params![target_digest, now_ms],
            )
            .map_err(sql_error)?;
        transaction
            .raw()
            .execute(
                "DELETE FROM maintenance_leases
                 WHERE owner_scope_digest=?1 AND expires_at_ms<=?2",
                params![target_digest, now_ms],
            )
            .map_err(sql_error)?;
        let row = transaction
            .raw()
            .query_row(
                "SELECT job_id, kind, required_operation, input_cursor_json,
                        input_digest, config_digest, budget_json, available_at_ms
                 FROM maintenance_jobs
                 WHERE owner_scope_digest=?1 AND actor_scope_digest=?2
                   AND required_operation=?3 AND state IN ('queued', 'checkpointed')
                   AND available_at_ms<=?4
                 ORDER BY available_at_ms ASC, created_at_ms ASC, job_id ASC LIMIT 1",
                params![
                    target_digest,
                    actor_digest,
                    request.operation.as_str(),
                    now_ms
                ],
                |row| {
                    Ok((
                        row.get::<_, String>(0)?,
                        row.get::<_, String>(1)?,
                        row.get::<_, String>(2)?,
                        row.get::<_, String>(3)?,
                        row.get::<_, String>(4)?,
                        row.get::<_, String>(5)?,
                        row.get::<_, String>(6)?,
                        row.get::<_, i64>(7)?,
                    ))
                },
            )
            .optional()
            .map_err(sql_error)?;
        let Some((
            job_id,
            kind,
            required_operation,
            cursor,
            input_digest,
            config_digest,
            budget,
            available_at_ms,
        )) = row
        else {
            return Ok(None);
        };
        let kind = parse_kind(&kind)?;
        let required_operation = parse_operation(&required_operation)?;
        let lease = lease::claim(
            transaction,
            &target_digest,
            kind.lock_family(),
            &job_id,
            worker_id.as_str(),
            now_ms,
            ttl_ms,
        )?;
        let changed = transaction
            .raw()
            .execute(
                "UPDATE maintenance_jobs SET state='claimed',
                        attempt=attempt + CASE WHEN state='checkpointed' THEN 1 ELSE 0 END,
                        claimed_grant_id=?2
                 WHERE job_id=?1 AND state IN ('queued', 'checkpointed')",
                params![
                    job_id,
                    authorization.grant.as_ref().map(|grant| grant.id.as_str())
                ],
            )
            .map_err(sql_error)?;
        if changed != 1 {
            return Err(stale("maintenance job changed while it was claimed"));
        }
        Ok(Some(ClaimedJob {
            id: Id::new(job_id).map_err(|_| corrupt("maintenance job id is invalid"))?,
            kind,
            actor_scope: request.actor_scope.clone(),
            target_scope: request.target_scope.clone(),
            required_operation,
            input_cursor: serde_json::from_str(&cursor)
                .map_err(|_| corrupt("maintenance cursor is malformed"))?,
            input_digest: input_digest
                .parse()
                .map_err(|_| corrupt("maintenance input digest is malformed"))?,
            config_digest: config_digest
                .parse()
                .map_err(|_| corrupt("maintenance config digest is malformed"))?,
            budget: serde_json::from_str(&budget)
                .map_err(|_| corrupt("maintenance budget is malformed"))?,
            available_at_ms,
            trace: request.trace.clone(),
            authorization: authorization.clone(),
            lease,
        }))
    })
}

pub fn heartbeat(
    store: &mut MemoryStore,
    context: &AuthContext,
    request: &MutationRequest,
    job: &mut ClaimedJob,
    ttl_ms: i64,
) -> MemoryResult<()> {
    let now_ms = context_now(context)?;
    store.immediate_authorized(context, request, |transaction, authorization| {
        require_job_authority(transaction, context, request, job, authorization, now_ms)?;
        lease::heartbeat(transaction, &mut job.lease, now_ms, ttl_ms)
    })
}

pub fn reserve_model_call(
    store: &mut MemoryStore,
    context: &AuthContext,
    request: &MutationRequest,
    job: &ClaimedJob,
    reservation_id: &Id,
    amount: BudgetAmount,
) -> MemoryResult<BudgetReservation> {
    let now_ms = context_now(context)?;
    store.immediate_authorized(context, request, |transaction, authorization| {
        require_job_authority(transaction, context, request, job, authorization, now_ms)?;
        budget::reserve(
            transaction,
            job.id.as_str(),
            reservation_id.as_str(),
            amount,
        )
    })
}

pub fn settle_model_call(
    store: &mut MemoryStore,
    context: &AuthContext,
    request: &MutationRequest,
    job: &ClaimedJob,
    reservation: &BudgetReservation,
    actual: ActualUsage,
) -> MemoryResult<UsageSummary> {
    let now_ms = context_now(context)?;
    store.immediate_authorized(context, request, |transaction, authorization| {
        require_job_authority(transaction, context, request, job, authorization, now_ms)?;
        budget::settle(transaction, reservation, actual)
    })
}

pub fn checkpoint(
    store: &mut MemoryStore,
    context: &AuthContext,
    request: &MutationRequest,
    job: &ClaimedJob,
    checkpoint_cursor: Cursor,
    available_at_ms: i64,
) -> MemoryResult<()> {
    let now_ms = context_now(context)?;
    store.immediate_authorized(context, request, |transaction, authorization| {
        require_job_authority(transaction, context, request, job, authorization, now_ms)?;
        let cursor = serde_json::to_string(&checkpoint_cursor)
            .map_err(|_| corrupt("checkpoint cursor could not be encoded"))?;
        let changed = transaction
            .raw()
            .execute(
                "UPDATE maintenance_jobs SET state='checkpointed', checkpoint_cursor_json=?2,
                        available_at_ms=?3
                 WHERE job_id=?1 AND state IN ('claimed', 'running')",
                params![job.id.as_str(), cursor, available_at_ms],
            )
            .map_err(sql_error)?;
        if changed != 1 {
            return Err(stale(
                "maintenance job cannot checkpoint from its current state",
            ));
        }
        lease::release(transaction, &job.lease, now_ms)
    })
}

pub(crate) fn publish<T>(
    store: &mut MemoryStore,
    context: &AuthContext,
    request: &MutationRequest,
    job: &ClaimedJob,
    expected_input_digest: Digest,
    expected_config_digest: Digest,
    publication: impl FnOnce(&MemoryTransaction<'_>, &Authorization) -> MemoryResult<T>,
) -> MemoryResult<T> {
    let now_ms = context_now(context)?;
    store.immediate_authorized(context, request, |transaction, authorization| {
        require_job_authority(transaction, context, request, job, authorization, now_ms)?;
        require_publication_predicates(
            transaction,
            job,
            expected_input_digest,
            expected_config_digest,
        )?;
        let result = publication(transaction, authorization)?;
        let changed = transaction
            .raw()
            .execute(
                "UPDATE maintenance_jobs SET state='succeeded', finished_at_ms=?2
                 WHERE job_id=?1 AND state IN ('claimed', 'running')",
                params![job.id.as_str(), now_ms],
            )
            .map_err(sql_error)?;
        if changed != 1 {
            return Err(stale(
                "maintenance job cannot publish from its current state",
            ));
        }
        lease::release(transaction, &job.lease, now_ms)?;
        Ok(result)
    })
}

pub fn publish_receipt(
    store: &mut MemoryStore,
    job_context: &AuthContext,
    job_request: &MutationRequest,
    summary_context: &AuthContext,
    summary_request: &MutationRequest,
    job: &ClaimedJob,
    publication: &MaintenancePublication,
) -> MemoryResult<MaintenancePublicationReceipt> {
    if publication.job_id() != &job.id {
        return Err(invalid("maintenance publication names another job"));
    }
    let now_ms = context_now(job_context)?;
    match publication {
        MaintenancePublication::Summary {
            expected_input_digest,
            expected_config_digest,
            draft,
            sources,
            now_ms: publication_now_ms,
            ..
        } => {
            if !matches!(
                job.kind,
                MaintenanceKind::RefreshSummaries | MaintenanceKind::DecaySummaries
            ) || draft.kind != RecordKind::Summary
                || summary_request.operation != Operation::Create
                || summary_context.request.now_ms != *publication_now_ms
                || u64::try_from(now_ms).ok() != Some(*publication_now_ms)
            {
                return Err(invalid(
                    "summary publication must match its job kind, create authority, and timestamp",
                ));
            }
            publish(
                store,
                job_context,
                job_request,
                job,
                *expected_input_digest,
                *expected_config_digest,
                |transaction, _job_authorization| {
                    let summary_authorization =
                        transaction.authorize(summary_context, summary_request)?;
                    let mutation = records::create_record_in(
                        transaction,
                        &summary_authorization,
                        summary_request,
                        draft,
                        sources,
                        *publication_now_ms,
                    )?;
                    let output_cursor = mutation.cursor;
                    let output_digest = mutation.record.current.digest;
                    let cursor_json = serde_json::to_string(&output_cursor)
                        .map_err(|_| corrupt("maintenance output cursor could not be encoded"))?;
                    transaction
                        .raw()
                        .execute(
                            "UPDATE maintenance_jobs SET checkpoint_cursor_json=?2 WHERE job_id=?1",
                            params![job.id.as_str(), cursor_json],
                        )
                        .map_err(sql_error)?;
                    Ok(MaintenancePublicationReceipt {
                        job_id: job.id.clone(),
                        kind: job.kind,
                        state: "succeeded".to_owned(),
                        output_cursor,
                        output_digest,
                        finished_at_ms: now_ms,
                    })
                },
            )
        }
        MaintenancePublication::Knowledge(_) => Err(invalid(
            "knowledge publication requires its typed authority bundle",
        )),
    }
}

#[allow(clippy::too_many_arguments)]
pub fn publish_knowledge_receipt(
    store: &mut MemoryStore,
    job_context: &AuthContext,
    job_request: &MutationRequest,
    job: &ClaimedJob,
    publication: &KnowledgePublication,
    authorities: &KnowledgePublicationAuthorities,
) -> MemoryResult<KnowledgePublicationReceipt> {
    validate_knowledge_publication(job, publication, authorities)?;
    let finished_at_ms = context_now(job_context)?;
    if u64::try_from(finished_at_ms).ok() != Some(publication.now_ms) {
        return Err(invalid(
            "knowledge publication time does not match live job authority",
        ));
    }
    publish(
        store,
        job_context,
        job_request,
        job,
        publication.expected_input_digest,
        publication.expected_config_digest,
        |transaction, _job_authorization| {
            let owner = scope_digest(&job.target_scope);
            validate_knowledge_source(publication, owner)?;
            let canonical_source =
                put_source(transaction.raw(), &publication.source, publication.now_ms)?;
            if canonical_source != publication.source.source_id {
                return Err(stale(
                    "knowledge source now resolves to a different canonical source",
                ));
            }

            let smart_authorities = authority_map(&authorities.smart_notes)?;
            let verification_authorities = authority_map(&authorities.verifications)?;
            let sharing_authorities = authority_map(&authorities.sharing_judgments)?;
            let mut smart_inputs = publication.smart_notes.iter().collect::<Vec<_>>();
            smart_inputs.sort_by_key(|item| &item.record_id);
            let mut verification_inputs = publication.verifications.iter().collect::<Vec<_>>();
            verification_inputs.sort_by_key(|item| &item.record_id);
            let mut sharing_inputs = publication.sharing_judgments.iter().collect::<Vec<_>>();
            sharing_inputs.sort_by_key(|item| &item.record_id);

            let mut smart_rows = Vec::with_capacity(smart_inputs.len());
            for item in &smart_inputs {
                let authority = smart_authorities
                    .get(item.record_id.as_str())
                    .ok_or_else(|| denied())?;
                let row = load_smart_note(transaction, &item.record_id, &owner)?;
                let authorization = validate_record_authority(
                    transaction,
                    authority,
                    job,
                    Operation::Index,
                    item.expected_revision_digest,
                    &row.category,
                    publication.now_ms,
                )?;
                if row.revision_digest != item.expected_revision_digest
                    || row.predicate_digest != item.expected_predicate_digest
                    || row.predicate.digest().map_err(predicate_error)?
                        != item.expected_predicate_digest
                    || row
                        .next_evaluation_at_ms
                        .is_some_and(|next| next > finished_at_ms)
                    || row
                        .last_evaluated_cursor
                        .is_some_and(|cursor| cursor >= publication.evaluated_cursor)
                {
                    return Err(stale(
                        "smart-note revision, predicate, cursor, or schedule changed",
                    ));
                }
                smart_rows.push((item, authority, authorization, row));
            }

            let mut verification_rows = Vec::with_capacity(verification_inputs.len());
            for item in &verification_inputs {
                let authority = verification_authorities
                    .get(item.record_id.as_str())
                    .ok_or_else(|| denied())?;
                let category = load_current_category(
                    transaction,
                    &item.record_id,
                    &owner,
                    item.expected_revision_digest,
                )?;
                let authorization = validate_record_authority(
                    transaction,
                    authority,
                    job,
                    Operation::Verify,
                    item.expected_revision_digest,
                    &category,
                    publication.now_ms,
                )?;
                if publication.source.kind != SourceKind::GitCommit {
                    return Err(invalid("code verification requires a guarded Git source"));
                }
                verification_rows.push((*item, *authority, authorization));
            }

            let mut sharing_rows = Vec::with_capacity(sharing_inputs.len());
            for item in &sharing_inputs {
                let authority = sharing_authorities
                    .get(item.record_id.as_str())
                    .ok_or_else(denied)?;
                let category = load_current_category(
                    transaction,
                    &item.record_id,
                    &owner,
                    item.expected_revision_digest,
                )?;
                let authorization = validate_record_authority(
                    transaction,
                    authority,
                    job,
                    Operation::Verify,
                    item.expected_revision_digest,
                    &category,
                    publication.now_ms,
                )?;
                if item.evidence_digest != publication.source.source_digest {
                    return Err(stale(
                        "sharing judgment evidence is not the current knowledge source",
                    ));
                }
                sharing_rows.push((*item, *authority, authorization));
            }

            let recall_authorization = if let Some(recall) = &publication.recall {
                let authority = authorities.recall.as_ref().ok_or_else(denied)?;
                Some(validate_recall_authority(
                    transaction,
                    authority,
                    job,
                    recall,
                    &publication.source,
                    publication.now_ms,
                )?)
            } else {
                None
            };

            let context = knowledge_predicate_context(&publication.source, publication.now_ms)?;
            let mut smart_notes = Vec::with_capacity(smart_rows.len());
            let mut verifications = Vec::with_capacity(verification_inputs.len());
            let mut sharing_judgments = Vec::with_capacity(sharing_inputs.len());
            let mut invalidated = BTreeSet::new();
            let mut output_cursor = None;

            for (item, authority, authorization, row) in smart_rows {
                let mut record_context = context.clone();
                record_context.insert(
                    PredicateField::RecordKind,
                    PredicateScalar::String("smart_note".to_owned()),
                );
                record_context.insert(
                    PredicateField::RecordCategory,
                    PredicateScalar::String(row.category.clone()),
                );
                record_context.insert(
                    PredicateField::RecordStatus,
                    PredicateScalar::String(row.status.clone()),
                );
                let result = row
                    .predicate
                    .evaluate(&record_context)
                    .map_err(predicate_error)?;
                let cursor = transaction.advance_cursor(&job.target_scope, finished_at_ms)?;
                let cursor_json = serde_json::to_string(&publication.evaluated_cursor)
                    .map_err(|_| corrupt("smart-note cursor could not be encoded"))?;
                let changed = transaction
                    .raw()
                    .execute(
                        "UPDATE smart_note_details
                         SET last_evaluated_cursor_json=?2, last_result=?3,
                             next_evaluation_at_ms=?4
                         WHERE record_id=?1 AND predicate_digest=?5",
                        params![
                            item.record_id.as_str(),
                            cursor_json,
                            i64::from(result),
                            publication.next_evaluation_at_ms,
                            item.expected_predicate_digest.to_hex(),
                        ],
                    )
                    .map_err(sql_error)?;
                if changed != 1 {
                    return Err(stale("smart-note predicate changed before publication"));
                }
                insert_knowledge_event(
                    transaction,
                    &authority.request,
                    &authorization,
                    cursor,
                    &item.record_id,
                    item.expected_revision_digest,
                    publication.now_ms,
                )?;
                output_cursor = Some(cursor);
                smart_notes.push(SmartNoteDecision {
                    record_id: item.record_id.clone(),
                    result,
                    cursor,
                });
            }

            for (item, authority, authorization) in verification_rows {
                let mutation = records::verify_record_in(
                    transaction,
                    &authorization,
                    &authority.request,
                    item.state,
                    item.confidence,
                    Some(&publication.source.source_id),
                    publication.now_ms,
                )?;
                invalidated.extend(mutation.invalidated_ids);
                output_cursor = Some(mutation.cursor);
                verifications.push(KnowledgeVerificationReceipt {
                    record_id: item.record_id.clone(),
                    state: item.state,
                    cursor: mutation.cursor,
                });
            }

            for (item, authority, authorization) in sharing_rows {
                let judgment = sharing::publish_sharing_judgment_in(
                    transaction,
                    &authorization,
                    &authority.request,
                    item,
                    publication.now_ms,
                )?;
                let cursor = transaction.advance_cursor(&job.target_scope, finished_at_ms)?;
                insert_knowledge_event(
                    transaction,
                    &authority.request,
                    &authorization,
                    cursor,
                    &item.record_id,
                    item.expected_revision_digest,
                    publication.now_ms,
                )?;
                output_cursor = Some(cursor);
                sharing_judgments.push(KnowledgeSharingReceipt { judgment, cursor });
            }

            let recall = if let Some(recall) = &publication.recall {
                let authority = authorities
                    .recall
                    .as_ref()
                    .expect("authority set was validated");
                let mutation = records::create_record_in(
                    transaction,
                    recall_authorization
                        .as_ref()
                        .expect("recall authority was validated"),
                    &authority.request,
                    &recall.draft,
                    &[],
                    publication.now_ms,
                )?;
                invalidated.extend(mutation.invalidated_ids);
                output_cursor = Some(mutation.cursor);
                Some(KnowledgeRecallReceipt {
                    record_id: mutation.record.id,
                    revision_digest: mutation.record.current.digest,
                    cursor: mutation.cursor,
                })
            } else {
                None
            };

            let output_cursor = output_cursor.ok_or_else(|| {
                invalid("knowledge publication must contain at least one durable output")
            })?;
            let invalidated_ids = invalidated.into_iter().collect::<Vec<_>>();
            let output_digest = knowledge_output_digest(
                &publication.source.source_digest,
                &smart_notes,
                &verifications,
                &sharing_judgments,
                recall.as_ref(),
                &invalidated_ids,
                output_cursor,
            )?;
            let cursor_json = serde_json::to_string(&output_cursor)
                .map_err(|_| corrupt("maintenance output cursor could not be encoded"))?;
            transaction
                .raw()
                .execute(
                    "UPDATE maintenance_jobs SET checkpoint_cursor_json=?2 WHERE job_id=?1",
                    params![job.id.as_str(), cursor_json],
                )
                .map_err(sql_error)?;
            Ok(KnowledgePublicationReceipt {
                job_id: job.id.clone(),
                kind: job.kind,
                state: "succeeded".to_owned(),
                output_cursor,
                output_digest,
                source_digest: publication.source.source_digest,
                smart_notes,
                verifications,
                sharing_judgments,
                recall,
                invalidated_ids,
                finished_at_ms,
            })
        },
    )
}

struct SmartNoteRow {
    revision_digest: Digest,
    category: String,
    status: String,
    predicate: SmartPredicate,
    predicate_digest: Digest,
    last_evaluated_cursor: Option<Cursor>,
    next_evaluation_at_ms: Option<i64>,
}

fn validate_knowledge_publication(
    job: &ClaimedJob,
    publication: &KnowledgePublication,
    authorities: &KnowledgePublicationAuthorities,
) -> MemoryResult<()> {
    let output_count = publication.smart_notes.len()
        + publication.verifications.len()
        + publication.sharing_judgments.len()
        + usize::from(publication.recall.is_some());
    if publication.job_id != job.id
        || !matches!(
            job.kind,
            MaintenanceKind::EvaluateSmartNotes
                | MaintenanceKind::VerifyClaims
                | MaintenanceKind::IndexGitCommits
        )
        || job.required_operation != Operation::Index
        || publication.evaluated_cursor != job.input_cursor
        || publication.expected_input_digest != publication.source.source_digest
        || output_count == 0
        || output_count > MAX_KNOWLEDGE_OUTPUTS
        || publication.smart_notes.len() != authorities.smart_notes.len()
        || publication.verifications.len() != authorities.verifications.len()
        || publication.sharing_judgments.len() != authorities.sharing_judgments.len()
        || publication.recall.is_some() != authorities.recall.is_some()
        || publication
            .next_evaluation_at_ms
            .is_some_and(|next| next < 0)
        || publication
            .verifications
            .iter()
            .any(|item| !item.confidence.is_finite() || !(0.0..=1.0).contains(&item.confidence))
    {
        return Err(invalid(
            "knowledge publication does not match its bounded maintenance job",
        ));
    }
    let smart_ids = publication
        .smart_notes
        .iter()
        .map(|item| item.record_id.as_str())
        .collect::<BTreeSet<_>>();
    let verification_ids = publication
        .verifications
        .iter()
        .map(|item| item.record_id.as_str())
        .collect::<BTreeSet<_>>();
    let sharing_ids = publication
        .sharing_judgments
        .iter()
        .map(|item| item.record_id.as_str())
        .collect::<BTreeSet<_>>();
    let smart_authority_ids = authorities
        .smart_notes
        .iter()
        .map(|item| item.record_id.as_str())
        .collect::<BTreeSet<_>>();
    let verification_authority_ids = authorities
        .verifications
        .iter()
        .map(|item| item.record_id.as_str())
        .collect::<BTreeSet<_>>();
    let sharing_authority_ids = authorities
        .sharing_judgments
        .iter()
        .map(|item| item.record_id.as_str())
        .collect::<BTreeSet<_>>();
    if smart_ids.len() != publication.smart_notes.len()
        || verification_ids.len() != publication.verifications.len()
        || sharing_ids.len() != publication.sharing_judgments.len()
        || smart_ids != smart_authority_ids
        || verification_ids != verification_authority_ids
        || sharing_ids != sharing_authority_ids
        || publication
            .recall
            .as_ref()
            .zip(authorities.recall.as_ref())
            .is_some_and(|(recall, authority)| recall.draft.id != authority.record_id)
    {
        return Err(denied());
    }
    Ok(())
}

fn validate_knowledge_source(
    publication: &KnowledgePublication,
    owner_scope_digest: Digest,
) -> MemoryResult<()> {
    let source = &publication.source;
    if source.owner_scope_digest != owner_scope_digest
        || source.source_digest != publication.expected_input_digest
        || source
            .captured_content
            .as_ref()
            .is_none_or(|content| Digest::sha256(content.as_bytes()) != source.source_digest)
    {
        return Err(stale(
            "knowledge source scope or current content digest changed",
        ));
    }
    match source.kind {
        SourceKind::GitCommit => {
            let repository_identity = publication
                .repository_identity_digest
                .ok_or_else(|| invalid("Git knowledge source lacks repository identity"))?;
            let refs_digest = publication
                .refs_digest
                .ok_or_else(|| invalid("Git knowledge source lacks refs digest"))?;
            let expected_locator = format!("git:{repository_identity}");
            if source.capture_method != "gideon_guarded_local_git"
                || source.locator.as_deref() != Some(expected_locator.as_str())
            {
                return Err(invalid("Git knowledge source was not guard-captured"));
            }
            let content: Value = serde_json::from_str(
                source
                    .captured_content
                    .as_deref()
                    .expect("captured content was checked"),
            )
            .map_err(|_| invalid("Git knowledge source content is invalid"))?;
            let object = content
                .as_object()
                .ok_or_else(|| invalid("Git knowledge source content is invalid"))?;
            let head = object
                .get("head")
                .and_then(Value::as_str)
                .filter(|head| !head.is_empty())
                .ok_or_else(|| invalid("Git knowledge source head is missing"))?;
            let refs = object
                .get("refs")
                .and_then(Value::as_array)
                .filter(|refs| refs.len() <= 4_096)
                .ok_or_else(|| invalid("Git knowledge source refs are invalid"))?;
            if refs.iter().any(|value| value.as_str().is_none()) {
                return Err(invalid("Git knowledge source refs are invalid"));
            }
            let material = serde_json::to_vec(&json!({"head": head, "refs": refs}))
                .map_err(|_| invalid("Git refs could not be canonicalized"))?;
            if Digest::sha256(&material) != refs_digest {
                return Err(stale("Git refs changed before knowledge publication"));
            }
        }
        SourceKind::File => {
            if publication.repository_identity_digest.is_some() || publication.refs_digest.is_some()
            {
                return Err(invalid("file knowledge source cannot carry Git guards"));
            }
        }
        SourceKind::Memory | SourceKind::Message | SourceKind::External => {
            return Err(invalid(
                "knowledge cycle accepts only guarded note or Git sources",
            ));
        }
    }
    Ok(())
}

fn authority_map<'a>(
    authorities: &'a [KnowledgePublicationAuthority],
) -> MemoryResult<BTreeMap<&'a str, &'a KnowledgePublicationAuthority>> {
    let mut result = BTreeMap::new();
    for authority in authorities {
        if result
            .insert(authority.record_id.as_str(), authority)
            .is_some()
        {
            return Err(denied());
        }
    }
    Ok(result)
}

fn validate_record_authority(
    transaction: &MemoryTransaction<'_>,
    authority: &KnowledgePublicationAuthority,
    job: &ClaimedJob,
    operation: Operation,
    revision: Digest,
    category: &str,
    now_ms: u64,
) -> MemoryResult<Authorization> {
    let request = &authority.request;
    if request.record_id.as_ref() != Some(&authority.record_id)
        || request.operation != operation
        || request.actor_scope != job.actor_scope
        || request.target_scope != job.target_scope
        || request.category.as_deref() != Some(category)
        || request.revision != RevisionPrecondition::Match(revision)
        || authority.context.request.now_ms != now_ms
    {
        return Err(denied());
    }
    transaction.authorize(&authority.context, request)
}

fn validate_recall_authority(
    transaction: &MemoryTransaction<'_>,
    authority: &KnowledgePublicationAuthority,
    job: &ClaimedJob,
    recall: &KnowledgeRecall,
    source: &SourceSnapshot,
    now_ms: u64,
) -> MemoryResult<Authorization> {
    let request = &authority.request;
    if source.kind != SourceKind::GitCommit
        || authority.record_id != recall.draft.id
        || !matches!(recall.draft.kind, RecordKind::Fact | RecordKind::Note)
        || recall.draft.scope != job.target_scope
        || request.operation != Operation::Create
        || request.actor_scope != job.actor_scope
        || request.target_scope != job.target_scope
        || request.record_id.as_ref() != Some(&recall.draft.id)
        || request.category.as_deref() != Some(recall.draft.category.as_str())
        || request.revision != RevisionPrecondition::MustNotExist
        || authority.context.request.now_ms != now_ms
        || !recall.draft.provenance.iter().any(|span| {
            span.source_id == source.source_id && span.quoted_digest == Some(source.source_digest)
        })
    {
        return Err(denied());
    }
    transaction.authorize(&authority.context, request)
}

fn load_smart_note(
    transaction: &MemoryTransaction<'_>,
    record_id: &Id,
    owner: &Digest,
) -> MemoryResult<SmartNoteRow> {
    let row = transaction
        .raw()
        .query_row(
            "SELECT r.current_revision_digest, r.category, r.status,
                    d.predicate_json, d.predicate_digest,
                    d.last_evaluated_cursor_json, d.next_evaluation_at_ms
             FROM memory_records r
             JOIN smart_note_details d ON d.record_id=r.record_id
             WHERE r.record_id=?1 AND r.owner_scope_digest=?2
               AND r.kind='smart_note' AND r.status='active'",
            params![record_id.as_str(), owner.to_hex()],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, String>(4)?,
                    row.get::<_, Option<String>>(5)?,
                    row.get::<_, Option<i64>>(6)?,
                ))
            },
        )
        .optional()
        .map_err(sql_error)?
        .ok_or_else(|| stale("smart note is no longer current and owned by the job scope"))?;
    Ok(SmartNoteRow {
        revision_digest: row
            .0
            .parse()
            .map_err(|_| corrupt("smart-note revision digest is invalid"))?,
        category: row.1,
        status: row.2,
        predicate: serde_json::from_str(&row.3)
            .map_err(|_| corrupt("smart-note predicate is invalid"))?,
        predicate_digest: row
            .4
            .parse()
            .map_err(|_| corrupt("smart-note predicate digest is invalid"))?,
        last_evaluated_cursor: row
            .5
            .map(|value| {
                serde_json::from_str(&value)
                    .map_err(|_| corrupt("smart-note evaluation cursor is invalid"))
            })
            .transpose()?,
        next_evaluation_at_ms: row.6,
    })
}

fn load_current_category(
    transaction: &MemoryTransaction<'_>,
    record_id: &Id,
    owner: &Digest,
    revision: Digest,
) -> MemoryResult<String> {
    transaction
        .raw()
        .query_row(
            "SELECT category FROM memory_records
             WHERE record_id=?1 AND owner_scope_digest=?2
               AND current_revision_digest=?3 AND status='active'",
            params![record_id.as_str(), owner.to_hex(), revision.to_hex()],
            |row| row.get(0),
        )
        .optional()
        .map_err(sql_error)?
        .ok_or_else(|| stale("verification target revision is no longer current"))
}

fn knowledge_predicate_context(
    source: &SourceSnapshot,
    now_ms: u64,
) -> MemoryResult<PredicateContext> {
    let mut context = PredicateContext::default();
    context.insert(
        PredicateField::EventKind,
        PredicateScalar::String(source.kind.as_str().to_owned()),
    );
    context.insert(
        PredicateField::EventLabel,
        PredicateScalar::String(
            source
                .locator
                .clone()
                .unwrap_or_else(|| source.source_id.to_string()),
        ),
    );
    let hours = (now_ms / 3_600_000) % 24;
    let days = now_ms / 86_400_000;
    context.insert(
        PredicateField::TimeHour,
        PredicateScalar::Number(hours as f64),
    );
    context.insert(
        PredicateField::TimeWeekday,
        PredicateScalar::Number(((days + 4) % 7) as f64),
    );
    Ok(context)
}

fn insert_knowledge_event(
    transaction: &MemoryTransaction<'_>,
    request: &MutationRequest,
    authorization: &Authorization,
    cursor: Cursor,
    record_id: &Id,
    revision: Digest,
    now_ms: u64,
) -> MemoryResult<()> {
    let trace_json = serde_json::to_string(&request.trace)
        .map_err(|_| corrupt("knowledge trace could not be encoded"))?;
    let event_digest = Digest::sha256(
        format!(
            "knowledge:{}:{}:{}:{}:{}",
            request.trace.trace_id,
            request.trace.request_id,
            record_id,
            cursor.epoch,
            cursor.sequence
        )
        .as_bytes(),
    );
    let (basis, grant_id) = match (&authorization.basis, &authorization.grant) {
        (AuthorizationBasis::Owner, None) => ("owner", None),
        (AuthorizationBasis::Grant, Some(grant)) => ("grant", Some(grant.id.as_str())),
        _ => return Err(corrupt("knowledge authorization basis is inconsistent")),
    };
    transaction
        .raw()
        .execute(
            "INSERT INTO memory_mutation_events(
                event_id, owner_scope_digest, epoch, sequence, operation,
                record_id, previous_revision_digest, result_revision_digest,
                actor_scope_digest, grant_id, authorization_basis, trace_json, created_at_ms
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?7, ?8, ?9, ?10, ?11, ?12)",
            params![
                format!("event-{}", &event_digest.to_hex()[..32]),
                authorization.target_scope_digest.to_hex(),
                cursor.epoch,
                cursor.sequence,
                request.operation.as_str(),
                record_id.as_str(),
                revision.to_hex(),
                authorization.actor_scope_digest.to_hex(),
                grant_id,
                basis,
                trace_json,
                now_ms,
            ],
        )
        .map_err(sql_error)?;
    Ok(())
}

fn knowledge_output_digest(
    source_digest: &Digest,
    smart_notes: &[SmartNoteDecision],
    verifications: &[KnowledgeVerificationReceipt],
    sharing_judgments: &[KnowledgeSharingReceipt],
    recall: Option<&KnowledgeRecallReceipt>,
    invalidated_ids: &[Id],
    output_cursor: Cursor,
) -> MemoryResult<Digest> {
    let bytes = serde_json::to_vec(&json!({
        "source_digest": source_digest,
        "smart_notes": smart_notes,
        "verifications": verifications,
        "sharing_judgments": sharing_judgments,
        "recall": recall,
        "invalidated_ids": invalidated_ids,
        "output_cursor": output_cursor,
    }))
    .map_err(|_| corrupt("knowledge output manifest could not be encoded"))?;
    Ok(Digest::sha256(&bytes))
}

fn predicate_error(source: crate::smart_note::PredicateError) -> hypermid_contracts::Error {
    error(
        "INVALID_SMART_NOTE_PREDICATE",
        source.to_string(),
        EffectState::NotStarted,
    )
}

pub fn publish_integrity(
    store: &mut MemoryStore,
    job_context: &AuthContext,
    job_request: &MutationRequest,
    job: &ClaimedJob,
    expected_input_digest: Digest,
    expected_config_digest: Digest,
) -> MemoryResult<MaintenancePublicationReceipt> {
    if job.kind != MaintenanceKind::CheckIntegrity || job.required_operation != Operation::Index {
        return Err(invalid(
            "integrity publication requires a check-integrity index job",
        ));
    }
    let finished_at_ms = context_now(job_context)?;
    publish(
        store,
        job_context,
        job_request,
        job,
        expected_input_digest,
        expected_config_digest,
        |transaction, _authorization| {
            let mut quick_check = transaction
                .raw()
                .prepare("PRAGMA quick_check")
                .map_err(sql_error)?;
            let quick_rows = quick_check
                .query_map([], |row| row.get::<_, String>(0))
                .map_err(sql_error)?
                .collect::<Result<Vec<_>, _>>()
                .map_err(sql_error)?;
            if quick_rows.as_slice() != ["ok"] {
                return Err(error(
                    "INTEGRITY_CHECK_FAILED",
                    "SQLite quick_check reported corruption",
                    EffectState::NotStarted,
                ));
            }
            let mut foreign_keys = transaction
                .raw()
                .prepare("PRAGMA foreign_key_check")
                .map_err(sql_error)?;
            if foreign_keys
                .query([])
                .map_err(sql_error)?
                .next()
                .map_err(sql_error)?
                .is_some()
            {
                return Err(error(
                    "INTEGRITY_CHECK_FAILED",
                    "SQLite foreign_key_check reported a broken reference",
                    EffectState::NotStarted,
                ));
            }

            let owner = scope_digest(&job.target_scope).to_hex();
            let (epoch, sequence): (u64, u64) = transaction
                .raw()
                .query_row(
                    "SELECT epoch, sequence FROM memory_scopes WHERE scope_digest=?1",
                    [&owner],
                    |row| Ok((row.get(0)?, row.get(1)?)),
                )
                .optional()
                .map_err(sql_error)?
                .ok_or_else(|| stale("integrity target scope no longer exists"))?;
            let output_cursor = Cursor::new(epoch, sequence)
                .map_err(|_| corrupt("integrity target cursor is invalid"))?;
            let schema_digest: String = transaction
                .raw()
                .query_row(
                    "SELECT schema_digest FROM hypermid_schema_version WHERE singleton=1",
                    [],
                    |row| row.get(0),
                )
                .map_err(sql_error)?;
            let source_fingerprint = source_index_fingerprint(transaction, &owner)?;
            let evidence = serde_json::to_vec(&json!({
                "quick_check": "ok",
                "foreign_key_violations": 0,
                "schema_digest": schema_digest,
                "source_index_fingerprint": source_fingerprint,
                "scope_digest": owner,
                "cursor": output_cursor,
            }))
            .map_err(|_| corrupt("integrity evidence could not be encoded"))?;
            let output_digest = Digest::sha256(&evidence);
            let cursor_json = serde_json::to_string(&output_cursor)
                .map_err(|_| corrupt("integrity cursor could not be encoded"))?;
            transaction
                .raw()
                .execute(
                    "UPDATE maintenance_jobs SET checkpoint_cursor_json=?2 WHERE job_id=?1",
                    params![job.id.as_str(), cursor_json],
                )
                .map_err(sql_error)?;
            Ok(MaintenancePublicationReceipt {
                job_id: job.id.clone(),
                kind: job.kind,
                state: "succeeded".to_owned(),
                output_cursor,
                output_digest,
                finished_at_ms,
            })
        },
    )
}

fn source_index_fingerprint(
    transaction: &MemoryTransaction<'_>,
    owner_scope_digest: &str,
) -> MemoryResult<Digest> {
    let mut documents = transaction
        .raw()
        .prepare(
            "SELECT document_id, source_kind, source_key, content_digest,
                    source_time_ms, indexed_at_ms, tombstoned_at_ms
             FROM source_index_documents WHERE owner_scope_digest=?1
             ORDER BY source_kind, source_key, document_id",
        )
        .map_err(sql_error)?;
    let document_rows = documents
        .query_map([owner_scope_digest], |row| {
            Ok(json!({
                "document_id": row.get::<_, String>(0)?,
                "source_kind": row.get::<_, String>(1)?,
                "source_key": row.get::<_, String>(2)?,
                "content_digest": row.get::<_, String>(3)?,
                "source_time_ms": row.get::<_, Option<i64>>(4)?,
                "indexed_at_ms": row.get::<_, i64>(5)?,
                "tombstoned_at_ms": row.get::<_, Option<i64>>(6)?,
            }))
        })
        .map_err(sql_error)?
        .collect::<Result<Vec<_>, _>>()
        .map_err(sql_error)?;
    let mut states = transaction
        .raw()
        .prepare(
            "SELECT source_kind, cursor_json, dirty_floor_sequence,
                    repository_identity_digest, refs_digest, next_probe_at_ms, updated_at_ms
             FROM source_index_state WHERE owner_scope_digest=?1 ORDER BY source_kind",
        )
        .map_err(sql_error)?;
    let state_rows = states
        .query_map([owner_scope_digest], |row| {
            Ok(json!({
                "source_kind": row.get::<_, String>(0)?,
                "cursor": row.get::<_, Option<String>>(1)?,
                "dirty_floor_sequence": row.get::<_, Option<u64>>(2)?,
                "repository_identity_digest": row.get::<_, Option<String>>(3)?,
                "refs_digest": row.get::<_, Option<String>>(4)?,
                "next_probe_at_ms": row.get::<_, Option<i64>>(5)?,
                "updated_at_ms": row.get::<_, i64>(6)?,
            }))
        })
        .map_err(sql_error)?
        .collect::<Result<Vec<_>, _>>()
        .map_err(sql_error)?;
    let bytes = serde_json::to_vec(&json!({"documents": document_rows, "states": state_rows}))
        .map_err(|_| corrupt("source index fingerprint could not be encoded"))?;
    Ok(Digest::sha256(&bytes))
}

pub fn status(
    store: &mut MemoryStore,
    context: &AuthContext,
    request: &MutationRequest,
    job_id: &Id,
) -> MemoryResult<MaintenanceJobStatus> {
    store.immediate_authorized(context, request, |transaction, authorization| {
        if request.record_id.as_ref() != Some(job_id) {
            return Err(denied());
        }
        let row = transaction
            .raw()
            .query_row(
                "SELECT kind, state, input_cursor_json, checkpoint_cursor_json,
                        attempt, available_at_ms, finished_at_ms, last_error_code
                 FROM maintenance_jobs
                 WHERE job_id=?1 AND owner_scope_digest=?2 AND actor_scope_digest=?3
                   AND required_operation=?4",
                params![
                    job_id.as_str(),
                    authorization.target_scope_digest.to_hex(),
                    authorization.actor_scope_digest.to_hex(),
                    request.operation.as_str(),
                ],
                |row| {
                    Ok((
                        row.get::<_, String>(0)?,
                        row.get::<_, String>(1)?,
                        row.get::<_, String>(2)?,
                        row.get::<_, Option<String>>(3)?,
                        row.get::<_, u64>(4)?,
                        row.get::<_, i64>(5)?,
                        row.get::<_, Option<i64>>(6)?,
                        row.get::<_, Option<String>>(7)?,
                    ))
                },
            )
            .optional()
            .map_err(sql_error)?
            .ok_or_else(|| stale("maintenance job is unavailable"))?;
        Ok(MaintenanceJobStatus {
            job_id: job_id.clone(),
            kind: parse_kind(&row.0)?,
            state: row.1,
            input_cursor: serde_json::from_str(&row.2)
                .map_err(|_| corrupt("maintenance input cursor is malformed"))?,
            checkpoint_cursor: row
                .3
                .map(|value| {
                    serde_json::from_str(&value)
                        .map_err(|_| corrupt("maintenance checkpoint cursor is malformed"))
                })
                .transpose()?,
            attempt: row.4,
            available_at_ms: row.5,
            finished_at_ms: row.6,
            last_error_code: row.7,
            usage: budget::usage(transaction.raw(), job_id.as_str())?,
        })
    })
}

pub fn cancel(
    store: &mut MemoryStore,
    context: &AuthContext,
    request: &MutationRequest,
    job: &ClaimedJob,
) -> MemoryResult<()> {
    terminate(
        store,
        context,
        request,
        job,
        TerminalState::Abandoned,
        Some("CANCELLED"),
    )
}

pub fn terminate(
    store: &mut MemoryStore,
    context: &AuthContext,
    request: &MutationRequest,
    job: &ClaimedJob,
    state: TerminalState,
    error_code: Option<&str>,
) -> MemoryResult<()> {
    let now_ms = context_now(context)?;
    store.immediate_authorized(context, request, |transaction, authorization| {
        require_job_authority(transaction, context, request, job, authorization, now_ms)?;
        let state = match state {
            TerminalState::Failed => "failed",
            TerminalState::Abandoned => "abandoned",
        };
        let changed = transaction
            .raw()
            .execute(
                "UPDATE maintenance_jobs SET state=?2, finished_at_ms=?3, last_error_code=?4
                 WHERE job_id=?1 AND state IN ('claimed', 'running')",
                params![job.id.as_str(), state, now_ms, error_code],
            )
            .map_err(sql_error)?;
        if changed != 1 {
            return Err(stale(
                "maintenance job cannot terminate from its current state",
            ));
        }
        lease::release(transaction, &job.lease, now_ms)
    })
}

fn require_job_authority(
    transaction: &MemoryTransaction<'_>,
    context: &AuthContext,
    request: &MutationRequest,
    job: &ClaimedJob,
    authorization: &Authorization,
    now_ms: i64,
) -> MemoryResult<()> {
    transaction.reauthorize(context, request, authorization)?;
    let exact_job_resource = request.record_id.as_ref() == Some(&job.id);
    let maintenance_collection_resource =
        request.record_id.is_none() && context.request.resource_id.as_str() == "memory-maintenance";
    if (!exact_job_resource && !maintenance_collection_resource)
        || request.actor_scope != job.actor_scope
        || request.target_scope != job.target_scope
        || request.operation != job.required_operation
        || request.category.is_some()
        || !same_job_authority(authorization, &job.authorization)
    {
        return Err(denied());
    }
    lease::require_live(transaction, &job.lease, now_ms)?;
    let valid = transaction
        .raw()
        .query_row(
            "SELECT 1 FROM maintenance_jobs
             WHERE job_id=?1 AND owner_scope_digest=?2 AND actor_scope_digest=?3
               AND required_operation=?4 AND state IN ('claimed', 'running')",
            params![
                job.id.as_str(),
                authorization.target_scope_digest.to_hex(),
                authorization.actor_scope_digest.to_hex(),
                request.operation.as_str(),
            ],
            |_| Ok(()),
        )
        .optional()
        .map_err(sql_error)?;
    valid.ok_or_else(|| stale("maintenance job ownership or state changed"))
}

fn same_job_authority(current: &Authorization, claimed: &Authorization) -> bool {
    current.basis == claimed.basis
        && current.actor_scope_digest == claimed.actor_scope_digest
        && current.target_scope_digest == claimed.target_scope_digest
        && current.operation == claimed.operation
        && current.grant == claimed.grant
        && current.capability.revision == claimed.capability.revision
        && current.capability.context.capability_id == claimed.capability.context.capability_id
        && current.capability.context.principal == claimed.capability.context.principal
        && current.capability.context.request.claimed_scope
            == claimed.capability.context.request.claimed_scope
        && current.capability.context.request.target_scope
            == claimed.capability.context.request.target_scope
        && current.capability.context.request.operation
            == claimed.capability.context.request.operation
}

fn require_publication_predicates(
    transaction: &MemoryTransaction<'_>,
    job: &ClaimedJob,
    input_digest: Digest,
    config_digest: Digest,
) -> MemoryResult<()> {
    if input_digest != job.input_digest || config_digest != job.config_digest {
        return Err(stale("maintenance input or active configuration changed"));
    }
    let predicates = transaction
        .raw()
        .query_row(
            "SELECT 1 FROM maintenance_jobs
             WHERE job_id=?1 AND input_digest=?2 AND config_digest=?3
               AND (json_type(usage_json, '$.reservations') IS NULL
                    OR json_extract(usage_json, '$.reservations')='{}')",
            params![
                job.id.as_str(),
                input_digest.to_hex(),
                config_digest.to_hex()
            ],
            |_| Ok(()),
        )
        .optional()
        .map_err(sql_error)?;
    predicates.ok_or_else(|| stale("maintenance predicates or model reservations are unresolved"))
}

fn validate_spec(request: &MutationRequest, spec: &MaintenanceJobSpec) -> MemoryResult<()> {
    if request.actor_scope != spec.actor_scope
        || request.target_scope != spec.target_scope
        || request.operation != spec.required_operation
        || request.category.is_some()
        || spec.available_at_ms < 0
        || spec.created_at_ms < 0
    {
        return Err(invalid(
            "maintenance job does not match its authorization request",
        ));
    }
    Ok(())
}

fn context_now(context: &AuthContext) -> MemoryResult<i64> {
    i64::try_from(context.request.now_ms)
        .map_err(|_| invalid("authorization timestamp is outside the supported range"))
}

fn parse_kind(value: &str) -> MemoryResult<MaintenanceKind> {
    serde_json::from_value(serde_json::Value::String(value.to_owned()))
        .map_err(|_| corrupt("maintenance kind is invalid"))
}

fn parse_operation(value: &str) -> MemoryResult<Operation> {
    serde_json::from_value(serde_json::Value::String(value.to_owned()))
        .map_err(|_| corrupt("maintenance operation is invalid"))
}

fn invalid(message: &'static str) -> hypermid_contracts::Error {
    error("INVALID_ARGUMENT", message, EffectState::NotStarted)
}

fn denied() -> hypermid_contracts::Error {
    error(
        "AUTHORIZATION_DENIED",
        "maintenance publication requires the same exact live owner or grant authority",
        EffectState::NotStarted,
    )
}

fn stale(message: &'static str) -> hypermid_contracts::Error {
    error("STALE_MAINTENANCE_JOB", message, EffectState::NotStarted)
}

fn corrupt(message: &'static str) -> hypermid_contracts::Error {
    error("STORE_CORRUPT", message, EffectState::Unknown)
}

fn sql_error(source: rusqlite::Error) -> hypermid_contracts::Error {
    error(
        "STORE_WRITE_FAILED",
        format!("maintenance persistence failed: {source}"),
        EffectState::Unknown,
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        capability_operation, AuthenticatedPrincipal, AuthorizationRequest, CapabilityGrant,
        GrantOperation, PrincipalKind, RevisionPrecondition, ShareGrant, SummaryDetails,
        SummaryLevel,
    };
    use hypermid_store::authorization::put_grant;
    use std::collections::BTreeSet;

    fn id(value: &str) -> Id {
        Id::new(value).unwrap()
    }

    fn scope(owner: &str, project: &str) -> Scope {
        Scope::new(id(owner), id(project), None)
    }

    fn mutation(actor: Scope, target: Scope, job_id: &Id) -> MutationRequest {
        MutationRequest {
            operation: Operation::Update,
            actor_scope: actor,
            target_scope: target,
            record_id: Some(job_id.clone()),
            category: None,
            revision: RevisionPrecondition::MustNotExist,
            trace: crate::Trace::new(id("trace-1"), id("request-1")),
        }
    }

    fn context(
        actor: &Scope,
        target: &Scope,
        job_id: &Id,
        capability_id: &Id,
        now_ms: u64,
    ) -> AuthContext {
        operation_context(
            actor,
            target,
            job_id,
            capability_id,
            Operation::Update,
            now_ms,
        )
    }

    fn operation_context(
        actor: &Scope,
        target: &Scope,
        resource_id: &Id,
        capability_id: &Id,
        operation: Operation,
        now_ms: u64,
    ) -> AuthContext {
        AuthContext {
            principal: AuthenticatedPrincipal {
                principal_id: id("worker-principal"),
                owner_id: actor.owner_id.clone(),
                kind: PrincipalKind::Background,
            },
            request: AuthorizationRequest {
                claimed_scope: actor.clone(),
                target_scope: target.clone(),
                operation: capability_operation(operation),
                resource_id: resource_id.clone(),
                now_ms,
            },
            capability_id: capability_id.clone(),
        }
    }

    fn install_capability(
        store: &mut MemoryStore,
        actor: &Scope,
        target: &Scope,
        job_id: &Id,
        capability_id: &Id,
    ) {
        let issuer = AuthenticatedPrincipal {
            principal_id: id("owner-principal"),
            owner_id: target.owner_id.clone(),
            kind: PrincipalKind::Foreground,
        };
        let grant = CapabilityGrant {
            capability_id: capability_id.clone(),
            issuer_owner_id: target.owner_id.clone(),
            principal_id: id("worker-principal"),
            claimed_scope: actor.clone(),
            target_scope: target.clone(),
            operations: BTreeSet::from([capability_operation(Operation::Update)]),
            resources: BTreeSet::from([job_id.clone()]),
            expires_at_ms: 20_000,
        };
        store
            .immediate(|transaction| {
                put_grant(transaction.raw(), &issuer, &grant).unwrap();
                Ok(())
            })
            .unwrap();
    }

    fn install_create_capability(
        store: &mut MemoryStore,
        owner: &Scope,
        record_id: &Id,
        capability_id: &Id,
    ) {
        let issuer = AuthenticatedPrincipal {
            principal_id: id("owner-principal"),
            owner_id: owner.owner_id.clone(),
            kind: PrincipalKind::Foreground,
        };
        let grant = CapabilityGrant {
            capability_id: capability_id.clone(),
            issuer_owner_id: owner.owner_id.clone(),
            principal_id: id("worker-principal"),
            claimed_scope: owner.clone(),
            target_scope: owner.clone(),
            operations: BTreeSet::from([capability_operation(Operation::Create)]),
            resources: BTreeSet::from([record_id.clone()]),
            expires_at_ms: 20_000,
        };
        store
            .immediate(|transaction| {
                put_grant(transaction.raw(), &issuer, &grant).unwrap();
                Ok(())
            })
            .unwrap();
    }

    fn install_operation_capability(
        store: &mut MemoryStore,
        owner: &Scope,
        resources: &[Id],
        capability_id: &Id,
        operation: Operation,
    ) {
        let issuer = AuthenticatedPrincipal {
            principal_id: id("owner-principal"),
            owner_id: owner.owner_id.clone(),
            kind: PrincipalKind::Foreground,
        };
        let grant = CapabilityGrant {
            capability_id: capability_id.clone(),
            issuer_owner_id: owner.owner_id.clone(),
            principal_id: id("worker-principal"),
            claimed_scope: owner.clone(),
            target_scope: owner.clone(),
            operations: BTreeSet::from([capability_operation(operation)]),
            resources: resources.iter().cloned().collect(),
            expires_at_ms: 20_000,
        };
        store
            .immediate(|transaction| {
                put_grant(transaction.raw(), &issuer, &grant).unwrap();
                Ok(())
            })
            .unwrap();
    }

    fn record_request(
        owner: &Scope,
        operation: Operation,
        record_id: &Id,
        category: Option<&str>,
        revision: RevisionPrecondition,
        trace: &Trace,
    ) -> MutationRequest {
        MutationRequest {
            operation,
            actor_scope: owner.clone(),
            target_scope: owner.clone(),
            record_id: Some(record_id.clone()),
            category: category.map(str::to_owned),
            revision,
            trace: trace.clone(),
        }
    }

    fn spec(actor: Scope, target: Scope, job_id: Id) -> MaintenanceJobSpec {
        MaintenanceJobSpec {
            id: job_id,
            kind: MaintenanceKind::RefreshSummaries,
            target_scope: target,
            actor_scope: actor,
            required_operation: Operation::Update,
            input_cursor: Cursor::new(1, 0).unwrap(),
            input_digest: Digest::sha256(b"input-v1"),
            config_digest: Digest::sha256(b"config-v1"),
            budget: ModelBudget {
                max_items: 2,
                max_input_tokens: 100,
                max_output_tokens: 100,
                max_requests: 2,
                max_cost_units: 100,
                max_retries: 1,
                max_wall_ms: 10_000,
            },
            available_at_ms: 1_000,
            created_at_ms: 1_000,
        }
    }

    #[test]
    fn knowledge_cycle_publishes_predicate_verification_sharing_and_git_recall_atomically() {
        let directory = tempfile::tempdir().unwrap();
        let mut store = MemoryStore::open(directory.path().join("memory.sqlite3")).unwrap();
        let owner = scope("knowledge-owner", "knowledge-project");
        let trace = Trace::new(id("knowledge-trace"), id("knowledge-request"));

        let smart_id = id("smart-note-code");
        let smart_create_cap = id("cap-smart-create");
        install_create_capability(&mut store, &owner, &smart_id, &smart_create_cap);
        let predicate: SmartPredicate = serde_json::from_value(json!({
            "operator": "all",
            "clauses": [
                {"field": "event.kind", "comparison": "eq", "value": "git_commit"},
                {"field": "record.category", "comparison": "eq", "value": "code_condition"}
            ]
        }))
        .unwrap();
        let smart_create = record_request(
            &owner,
            Operation::Create,
            &smart_id,
            Some("code_condition"),
            RevisionPrecondition::MustNotExist,
            &trace,
        );
        let smart = store
            .create_record(
                &operation_context(
                    &owner,
                    &owner,
                    &smart_id,
                    &smart_create_cap,
                    Operation::Create,
                    1_000,
                ),
                &smart_create,
                &RecordDraft {
                    id: smart_id.clone(),
                    scope: owner.clone(),
                    kind: RecordKind::SmartNote,
                    category: "code_condition".to_owned(),
                    content: "Inspect guarded Git changes".to_owned(),
                    metadata: serde_json::Map::new(),
                    importance: 0.8,
                    confidence: 1.0,
                    expires_at_ms: None,
                    retention_until_ms: None,
                    provenance: Vec::new(),
                    lineage: Vec::new(),
                    smart_predicate: Some(predicate.clone()),
                    summary: None,
                },
                &[],
                1_000,
            )
            .unwrap();

        let fact_id = id("code-claim");
        let fact_create_cap = id("cap-fact-create");
        install_create_capability(&mut store, &owner, &fact_id, &fact_create_cap);
        let fact_create = record_request(
            &owner,
            Operation::Create,
            &fact_id,
            Some("code_fact"),
            RevisionPrecondition::MustNotExist,
            &trace,
        );
        let fact = store
            .create_record(
                &operation_context(
                    &owner,
                    &owner,
                    &fact_id,
                    &fact_create_cap,
                    Operation::Create,
                    1_100,
                ),
                &fact_create,
                &RecordDraft {
                    id: fact_id.clone(),
                    scope: owner.clone(),
                    kind: RecordKind::Fact,
                    category: "code_fact".to_owned(),
                    content: "Signed releases are required".to_owned(),
                    metadata: serde_json::Map::new(),
                    importance: 0.9,
                    confidence: 0.5,
                    expires_at_ms: None,
                    retention_until_ms: None,
                    provenance: Vec::new(),
                    lineage: Vec::new(),
                    smart_predicate: None,
                    summary: None,
                },
                &[],
                1_100,
            )
            .unwrap();

        let repository_identity_digest = Digest::sha256(b"knowledge-repository");
        let git_value = json!({
            "head": "0123456789abcdef",
            "refs": ["0123456789abcdef"],
            "commits": [{"id": "0123456789abcdef", "message": "require signed releases"}]
        });
        let git_content = serde_json::to_string(&git_value).unwrap();
        let source_digest = Digest::sha256(git_content.as_bytes());
        let refs_digest = Digest::sha256(
            &serde_json::to_vec(&json!({
                "head": "0123456789abcdef",
                "refs": ["0123456789abcdef"]
            }))
            .unwrap(),
        );
        let source_id = id("guarded-git-source");
        let source = SourceSnapshot {
            source_id: source_id.clone(),
            owner_scope_digest: scope_digest(&owner),
            kind: SourceKind::GitCommit,
            source_digest,
            locator: Some(format!("git:{repository_identity_digest}")),
            captured_content: Some(git_content),
            capture_method: "gideon_guarded_local_git".to_owned(),
            observed_at_ms: 2_000,
        };

        let job_id = id("knowledge-job");
        let job_cap = id("cap-knowledge-job");
        install_operation_capability(
            &mut store,
            &owner,
            std::slice::from_ref(&job_id),
            &job_cap,
            Operation::Index,
        );
        let job_request = record_request(
            &owner,
            Operation::Index,
            &job_id,
            None,
            RevisionPrecondition::MustNotExist,
            &trace,
        );
        let mut job_spec = spec(owner.clone(), owner.clone(), job_id.clone());
        job_spec.kind = MaintenanceKind::EvaluateSmartNotes;
        job_spec.required_operation = Operation::Index;
        job_spec.input_cursor = store.cursor(&owner).unwrap();
        job_spec.input_digest = source_digest;
        enqueue(
            &mut store,
            &operation_context(&owner, &owner, &job_id, &job_cap, Operation::Index, 2_000),
            &job_request,
            &job_spec,
        )
        .unwrap();
        let claimed = claim_next(
            &mut store,
            &operation_context(&owner, &owner, &job_id, &job_cap, Operation::Index, 2_100),
            &job_request,
            &id("knowledge-worker"),
            5_000,
        )
        .unwrap()
        .unwrap();

        let smart_cap = id("cap-smart-index");
        install_operation_capability(
            &mut store,
            &owner,
            std::slice::from_ref(&smart_id),
            &smart_cap,
            Operation::Index,
        );
        let verify_cap = id("cap-fact-verify");
        install_operation_capability(
            &mut store,
            &owner,
            std::slice::from_ref(&fact_id),
            &verify_cap,
            Operation::Verify,
        );
        let recall_id = id("git-recall");
        let recall_cap = id("cap-recall-create");
        install_create_capability(&mut store, &owner, &recall_id, &recall_cap);
        let smart_request = record_request(
            &owner,
            Operation::Index,
            &smart_id,
            Some("code_condition"),
            RevisionPrecondition::Match(smart.record.current.digest),
            &trace,
        );
        let verify_request = record_request(
            &owner,
            Operation::Verify,
            &fact_id,
            Some("code_fact"),
            RevisionPrecondition::Match(fact.record.current.digest),
            &trace,
        );
        let recall_request = record_request(
            &owner,
            Operation::Create,
            &recall_id,
            Some("git_recall"),
            RevisionPrecondition::MustNotExist,
            &trace,
        );
        let authority =
            |record_id: &Id,
             capability_id: &Id,
             operation: Operation,
             request: &MutationRequest| KnowledgePublicationAuthority {
                record_id: record_id.clone(),
                context: operation_context(
                    &owner,
                    &owner,
                    record_id,
                    capability_id,
                    operation,
                    2_200,
                ),
                request: MutationRequest {
                    operation,
                    ..request.clone()
                },
            };
        let publication = KnowledgePublication {
            job_id: job_id.clone(),
            expected_input_digest: source_digest,
            expected_config_digest: job_spec.config_digest,
            source,
            repository_identity_digest: Some(repository_identity_digest),
            refs_digest: Some(refs_digest),
            evaluated_cursor: job_spec.input_cursor,
            next_evaluation_at_ms: Some(10_000),
            smart_notes: vec![SmartNoteEvaluation {
                record_id: smart_id.clone(),
                expected_revision_digest: smart.record.current.digest,
                expected_predicate_digest: predicate.digest().unwrap(),
            }],
            verifications: vec![KnowledgeVerification {
                record_id: fact_id.clone(),
                expected_revision_digest: fact.record.current.digest,
                state: VerificationState::Supported,
                confidence: 0.95,
            }],
            sharing_judgments: vec![KnowledgeSharingJudgment {
                record_id: fact_id.clone(),
                expected_revision_digest: fact.record.current.digest,
                classification: crate::sharing::SharingClassification::Shared,
                trust_decision: crate::sharing::TrustDecision::Allow,
                policy_id: "knowledge-policy".to_owned(),
                policy_version: 1,
                policy_digest: Digest::sha256(b"knowledge-policy-v1"),
                provider_id: Some("centra".to_owned()),
                model_id: Some("verified-model".to_owned()),
                evidence_digest: source_digest,
            }],
            recall: Some(KnowledgeRecall {
                draft: RecordDraft {
                    id: recall_id.clone(),
                    scope: owner.clone(),
                    kind: RecordKind::Note,
                    category: "git_recall".to_owned(),
                    content: "The repository requires signed releases.".to_owned(),
                    metadata: serde_json::Map::new(),
                    importance: 0.8,
                    confidence: 0.95,
                    expires_at_ms: None,
                    retention_until_ms: None,
                    provenance: vec![crate::ProvenanceSpan {
                        source_id: source_id.clone(),
                        span_start: None,
                        span_end: None,
                        quoted_digest: Some(source_digest),
                    }],
                    lineage: Vec::new(),
                    smart_predicate: None,
                    summary: None,
                },
            }),
            now_ms: 2_200,
        };
        let authorities = KnowledgePublicationAuthorities {
            smart_notes: vec![authority(
                &smart_id,
                &smart_cap,
                Operation::Index,
                &smart_request,
            )],
            verifications: vec![authority(
                &fact_id,
                &verify_cap,
                Operation::Verify,
                &verify_request,
            )],
            sharing_judgments: vec![authority(
                &fact_id,
                &verify_cap,
                Operation::Verify,
                &verify_request,
            )],
            recall: Some(authority(
                &recall_id,
                &recall_cap,
                Operation::Create,
                &recall_request,
            )),
        };
        let cursor_before = store.cursor(&owner).unwrap();
        let mut stale_config = publication.clone();
        stale_config.expected_config_digest = Digest::sha256(b"stale-config");
        assert_eq!(
            publish_knowledge_receipt(
                &mut store,
                &operation_context(&owner, &owner, &job_id, &job_cap, Operation::Index, 2_200,),
                &job_request,
                &claimed,
                &stale_config,
                &authorities,
            )
            .unwrap_err()
            .code,
            "STALE_MAINTENANCE_JOB"
        );
        assert_eq!(store.cursor(&owner).unwrap(), cursor_before);

        let receipt = publish_knowledge_receipt(
            &mut store,
            &operation_context(&owner, &owner, &job_id, &job_cap, Operation::Index, 2_200),
            &job_request,
            &claimed,
            &publication,
            &authorities,
        )
        .unwrap();
        assert_eq!(receipt.state, "succeeded");
        assert!(receipt.smart_notes[0].result);
        assert_eq!(receipt.verifications[0].state, VerificationState::Supported);
        assert_eq!(receipt.sharing_judgments.len(), 1);
        assert_eq!(receipt.recall.as_ref().unwrap().record_id, recall_id);
        assert!(receipt.invalidated_ids.is_empty());
        assert!(receipt.output_cursor > cursor_before);
    }

    #[test]
    fn maintenance_revoked_foreign_grant_blocks_real_publication() {
        let directory = tempfile::tempdir().unwrap();
        let mut store = MemoryStore::open(directory.path().join("memory.sqlite3")).unwrap();
        let actor = scope("worker-owner", "worker-project");
        let target = scope("record-owner", "record-project");
        let job_id = id("job-revoked");
        let capability_id = id("cap-revoked");
        install_capability(&mut store, &actor, &target, &job_id, &capability_id);
        let share = ShareGrant {
            id: capability_id.clone(),
            owner_scope: target.clone(),
            grantee_scope: actor.clone(),
            operations: BTreeSet::from([GrantOperation::Update]),
            categories: None,
            granted_at_ms: 500,
            expires_at_ms: Some(10_000),
            revoked_at_ms: None,
            revision: 1,
        };
        store.put_share_grant(&target, &share, 500).unwrap();
        let request = mutation(actor.clone(), target.clone(), &job_id);
        let job_spec = spec(actor.clone(), target.clone(), job_id.clone());
        enqueue(
            &mut store,
            &context(&actor, &target, &job_id, &capability_id, 1_000),
            &request,
            &job_spec,
        )
        .unwrap();
        let claimed = claim_next(
            &mut store,
            &context(&actor, &target, &job_id, &capability_id, 2_000),
            &request,
            &id("worker-1"),
            5_000,
        )
        .unwrap()
        .unwrap();
        store
            .revoke_share_grant(&target, &capability_id, 1, 2_500)
            .unwrap();

        let mut revoked_claim = claimed.clone();
        let heartbeat_failure = heartbeat(
            &mut store,
            &context(&actor, &target, &job_id, &capability_id, 2_750),
            &request,
            &mut revoked_claim,
            5_000,
        )
        .unwrap_err();
        assert_eq!(heartbeat_failure.code, "AUTHORIZATION_DENIED");

        let failure = publish(
            &mut store,
            &context(&actor, &target, &job_id, &capability_id, 3_000),
            &request,
            &claimed,
            job_spec.input_digest,
            job_spec.config_digest,
            |transaction, _| transaction.advance_cursor(&target, 3_000),
        )
        .unwrap_err();

        assert_eq!(failure.code, "AUTHORIZATION_DENIED");
        assert_eq!(store.cursor(&target).unwrap(), Cursor::new(1, 0).unwrap());
    }

    #[test]
    fn maintenance_reclaims_expired_fence_and_records_unknown_usage() {
        let directory = tempfile::tempdir().unwrap();
        let mut store = MemoryStore::open(directory.path().join("memory.sqlite3")).unwrap();
        let owner = scope("owner", "project");
        let job_id = id("job-fenced");
        let capability_id = id("cap-fenced");
        install_capability(&mut store, &owner, &owner, &job_id, &capability_id);
        let request = mutation(owner.clone(), owner.clone(), &job_id);
        let job_spec = spec(owner.clone(), owner.clone(), job_id.clone());
        enqueue(
            &mut store,
            &context(&owner, &owner, &job_id, &capability_id, 1_000),
            &request,
            &job_spec,
        )
        .unwrap();
        let stale_claim = claim_next(
            &mut store,
            &context(&owner, &owner, &job_id, &capability_id, 2_000),
            &request,
            &id("worker-old"),
            100,
        )
        .unwrap()
        .unwrap();
        let current_claim = claim_next(
            &mut store,
            &context(&owner, &owner, &job_id, &capability_id, 2_200),
            &request,
            &id("worker-new"),
            5_000,
        )
        .unwrap()
        .unwrap();
        let failure = publish(
            &mut store,
            &context(&owner, &owner, &job_id, &capability_id, 2_300),
            &request,
            &stale_claim,
            job_spec.input_digest,
            job_spec.config_digest,
            |transaction, _| transaction.advance_cursor(&owner, 2_300),
        )
        .unwrap_err();
        assert_eq!(failure.code, "STALE_FENCE");

        let reserved = BudgetAmount {
            items: 1,
            input_tokens: 40,
            output_tokens: 20,
            requests: 1,
            cost_units: 10,
            retries: 0,
            wall_ms: 500,
        };
        let reservation = reserve_model_call(
            &mut store,
            &context(&owner, &owner, &job_id, &capability_id, 2_400),
            &request,
            &current_claim,
            &id("reservation-1"),
            reserved,
        )
        .unwrap();
        let summary = settle_model_call(
            &mut store,
            &context(&owner, &owner, &job_id, &capability_id, 2_500),
            &request,
            &current_claim,
            &reservation,
            ActualUsage {
                items: Some(1),
                input_tokens: None,
                output_tokens: Some(0),
                requests: Some(1),
                cost_units: None,
                retries: Some(0),
                wall_ms: Some(250),
            },
        )
        .unwrap();
        assert_eq!(summary.charged.input_tokens, 40);
        assert_eq!(summary.charged.output_tokens, 0);
        assert_ne!(summary.unknown_mask & (1 << 1), 0);
        assert_ne!(summary.unknown_mask & (1 << 4), 0);

        let summary_id = id("published-summary");
        let summary_capability_id = id("cap-summary-create");
        install_create_capability(&mut store, &owner, &summary_id, &summary_capability_id);
        let summary_request = MutationRequest {
            operation: Operation::Create,
            actor_scope: owner.clone(),
            target_scope: owner.clone(),
            record_id: Some(summary_id.clone()),
            category: Some("project_fact".to_owned()),
            revision: RevisionPrecondition::MustNotExist,
            trace: crate::Trace::new(id("trace-summary"), id("request-summary")),
        };
        let summary_context = AuthContext {
            principal: AuthenticatedPrincipal {
                principal_id: id("worker-principal"),
                owner_id: owner.owner_id.clone(),
                kind: PrincipalKind::Background,
            },
            request: AuthorizationRequest {
                claimed_scope: owner.clone(),
                target_scope: owner.clone(),
                operation: capability_operation(Operation::Create),
                resource_id: summary_id.clone(),
                now_ms: 2_600,
            },
            capability_id: summary_capability_id,
        };
        let draft = RecordDraft {
            id: summary_id.clone(),
            scope: owner.clone(),
            kind: RecordKind::Summary,
            category: "project_fact".to_owned(),
            content: "durably published summary".to_owned(),
            metadata: serde_json::Map::new(),
            importance: 0.7,
            confidence: 0.8,
            expires_at_ms: None,
            retention_until_ms: None,
            provenance: Vec::new(),
            lineage: Vec::new(),
            summary: Some(SummaryDetails {
                input_set_digest: job_spec.input_digest,
                level: SummaryLevel::Standard,
                decay_half_life_ms: None,
            }),
            smart_predicate: None,
        };
        let receipt = publish_receipt(
            &mut store,
            &context(&owner, &owner, &job_id, &capability_id, 2_600),
            &request,
            &summary_context,
            &summary_request,
            &current_claim,
            &MaintenancePublication::Summary {
                job_id: job_id.clone(),
                expected_input_digest: job_spec.input_digest,
                expected_config_digest: job_spec.config_digest,
                draft,
                sources: Vec::new(),
                now_ms: 2_600,
            },
        )
        .unwrap();
        assert_eq!(receipt.output_cursor, Cursor::new(1, 1).unwrap());
        assert_eq!(receipt.state, "succeeded");
        let persisted: String = store
            .read(|connection| {
                connection
                    .query_row(
                        "SELECT v.content FROM memory_records r JOIN memory_revisions v ON v.record_id=r.record_id AND v.revision=r.current_revision WHERE r.record_id=?1",
                        [summary_id.as_str()],
                        |row| row.get(0),
                    )
                    .map_err(sql_error)
            })
            .unwrap();
        assert_eq!(persisted, "durably published summary");
        let status = status(
            &mut store,
            &context(&owner, &owner, &job_id, &capability_id, 2_700),
            &request,
            &job_id,
        )
        .unwrap();
        assert_eq!(status.checkpoint_cursor, Some(receipt.output_cursor));
    }
}
