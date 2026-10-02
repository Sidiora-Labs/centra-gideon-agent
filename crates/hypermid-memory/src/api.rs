use crate::embedding::{self, EmbeddingRegistration};
use crate::export::{self, MemoryExportBundle};
use crate::import::{self, ImportState, MemoryImportBatch};
use crate::legacy_import::{self, GideonLegacySnapshot, LegacyImportReceipt};
use crate::maintenance::{
    self, ClaimedJob, KnowledgePublication, KnowledgePublicationAuthorities,
    KnowledgePublicationReceipt, MaintenanceClaimReceipt, MaintenanceJobSpec, MaintenanceJobStatus,
    MaintenanceKind, MaintenancePublication, MaintenancePublicationReceipt, TerminalState,
};
use crate::model::{AccessRequest, GrantOperation, MutationRequest};
use crate::provenance::SourceSnapshot;
use crate::records::{
    MemoryRecord, RecordDraft, RecordMutation, RelocationMutation, SplitMutation, VerificationState,
};
use crate::search::{SearchResponse, StoredSearchRequest};
use crate::snapshot::create_memory_snapshot;
use crate::{
    error, AuthContext, CapabilityOperation, Cursor, Digest, EffectState, Id, MemoryResult,
    MemoryStore, MEMORY_SCHEMA_VERSION,
};
use hypermid_store::authorization::{authorize as authorize_capability, commit_authorized};
use hypermid_store::backup::SnapshotReceipt;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashMap};
use std::path::Path;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ServiceState {
    Ready,
    Draining,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct MemoryHealth {
    pub state: ServiceState,
    pub schema_version: u64,
    pub durable: bool,
    pub lexical_available: bool,
    pub schema_digest: Digest,
}

#[derive(Clone, Debug, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RecordView {
    pub record: Option<MemoryRecord>,
    pub cursor: Cursor,
}

#[derive(Clone, Debug, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RecordList {
    pub records: Vec<MemoryRecord>,
    pub cursor: Cursor,
}

#[derive(Clone, Debug, Serialize)]
#[serde(deny_unknown_fields)]
pub struct EmbeddingView {
    pub registration: Option<EmbeddingRegistration>,
    pub cursor: Cursor,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct MemoryDiagnostics {
    pub schema_version: u64,
    pub record_count: u64,
    pub stale_record_count: u64,
    pub embedding_count: u64,
    pub queued_job_count: u64,
    pub active_lease_count: u64,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct MaintenanceClaimKey {
    pub job_id: Id,
    pub holder_id: String,
    pub fencing_token: String,
}

pub struct MemoryApi {
    pub(crate) store: MemoryStore,
    state: ServiceState,
    claims: HashMap<Id, ClaimedJob>,
}

impl MemoryApi {
    pub fn open(path: impl AsRef<Path>) -> MemoryResult<Self> {
        Ok(Self::new(MemoryStore::open(path)?))
    }

    pub fn new(store: MemoryStore) -> Self {
        Self {
            store,
            state: ServiceState::Ready,
            claims: HashMap::new(),
        }
    }

    pub fn health(&self) -> MemoryHealth {
        let evidence = self
            .store
            .schema_evidence()
            .expect("MemoryStore validates schema evidence when it opens");
        MemoryHealth {
            state: self.state,
            schema_version: MEMORY_SCHEMA_VERSION,
            durable: true,
            lexical_available: true,
            schema_digest: evidence.schema_digest,
        }
    }

    pub fn snapshot_scope(
        &mut self,
        scope: crate::Scope,
        artifact_path: impl AsRef<Path>,
    ) -> MemoryResult<SnapshotReceipt> {
        create_memory_snapshot(&self.store, artifact_path, scope)
    }

    pub fn drain(&mut self, context: &AuthContext) -> MemoryResult<MemoryHealth> {
        if context.request.operation != CapabilityOperation::Administer
            || context.request.resource_id.as_str() != "memory-service"
        {
            return Err(denied("memory drain requires exact administer authority"));
        }
        self.store.immediate(|transaction| {
            let authorization = authorize_capability(transaction.raw(), context)
                .map_err(|_| denied("memory drain capability was refused"))?;
            commit_authorized(transaction.raw(), &authorization, context, |_| Ok(()))
                .map_err(|_| denied("memory drain capability changed before commit"))
        })?;
        self.state = ServiceState::Draining;
        Ok(self.health())
    }

    pub fn create_record(
        &mut self,
        context: &AuthContext,
        request: &MutationRequest,
        draft: &RecordDraft,
        sources: &[SourceSnapshot],
        now_ms: u64,
    ) -> MemoryResult<RecordMutation> {
        self.require_ready()?;
        self.store
            .create_record(context, request, draft, sources, now_ms)
    }

    pub fn update_record(
        &mut self,
        context: &AuthContext,
        request: &MutationRequest,
        draft: &RecordDraft,
        sources: &[SourceSnapshot],
        now_ms: u64,
    ) -> MemoryResult<RecordMutation> {
        self.require_ready()?;
        self.store
            .update_record(context, request, draft, sources, now_ms)
    }

    pub fn archive_record(
        &mut self,
        context: &AuthContext,
        request: &MutationRequest,
        now_ms: u64,
    ) -> MemoryResult<RecordMutation> {
        self.require_ready()?;
        self.store.archive_record(context, request, now_ms)
    }

    pub fn restore_record(
        &mut self,
        context: &AuthContext,
        request: &MutationRequest,
        now_ms: u64,
    ) -> MemoryResult<RecordMutation> {
        self.require_ready()?;
        self.store.restore_record(context, request, now_ms)
    }

    pub fn delete_record(
        &mut self,
        context: &AuthContext,
        request: &MutationRequest,
        now_ms: u64,
    ) -> MemoryResult<RecordMutation> {
        self.require_ready()?;
        self.store.tombstone_record(context, request, now_ms)
    }

    pub fn purge_record(
        &mut self,
        context: &AuthContext,
        request: &MutationRequest,
        now_ms: u64,
    ) -> MemoryResult<Cursor> {
        self.require_ready()?;
        self.store.purge_record(context, request, now_ms)
    }

    pub fn verify_record(
        &mut self,
        context: &AuthContext,
        request: &MutationRequest,
        state: VerificationState,
        confidence: f64,
        evidence_source_id: Option<&Id>,
        now_ms: u64,
    ) -> MemoryResult<RecordMutation> {
        self.require_ready()?;
        self.store.verify_record(
            context,
            request,
            state,
            confidence,
            evidence_source_id,
            now_ms,
        )
    }

    pub fn merge_records(
        &mut self,
        context: &AuthContext,
        request: &MutationRequest,
        draft: &RecordDraft,
        source_revisions: &[(Id, crate::Digest)],
        sources: &[SourceSnapshot],
        now_ms: u64,
    ) -> MemoryResult<RecordMutation> {
        self.require_ready()?;
        self.store
            .merge_records(context, request, draft, source_revisions, sources, now_ms)
    }

    pub fn split_record(
        &mut self,
        context: &AuthContext,
        request: &MutationRequest,
        replacements: &[RecordDraft],
        now_ms: u64,
    ) -> MemoryResult<SplitMutation> {
        self.require_ready()?;
        self.store
            .split_record(context, request, replacements, now_ms)
    }

    pub fn relocate_record(
        &mut self,
        source_context: &AuthContext,
        source_request: &MutationRequest,
        destination_context: &AuthContext,
        destination_request: &MutationRequest,
        now_ms: u64,
    ) -> MemoryResult<RelocationMutation> {
        self.require_ready()?;
        self.store.relocate_record(
            source_context,
            source_request,
            destination_context,
            destination_request,
            now_ms,
        )
    }

    pub fn record(
        &mut self,
        context: &AuthContext,
        request: &AccessRequest,
    ) -> MemoryResult<RecordView> {
        if request.operation != GrantOperation::Read {
            return Err(error(
                "AUTHORIZATION_DENIED",
                "record inspection requires read authority",
                EffectState::NotStarted,
            ));
        }
        self.store
            .immediate_access(context, request, |transaction, authorization| {
                let record = crate::records::record_in(transaction.raw(), &request.resource_id)?;
                if record.as_ref().is_some_and(|record| {
                    record.owner_scope_digest != authorization.target_scope_digest
                }) {
                    return Err(error(
                        "AUTHORIZATION_DENIED",
                        "the record does not belong to the authorized target scope",
                        EffectState::NotStarted,
                    ));
                }
                Ok(RecordView {
                    record,
                    cursor: transaction.cursor(&request.target_scope)?,
                })
            })
    }

    pub fn list_records(
        &mut self,
        context: &AuthContext,
        request: &AccessRequest,
        category: Option<&str>,
        status: Option<crate::RecordStatus>,
        limit: usize,
    ) -> MemoryResult<RecordList> {
        if request.operation != GrantOperation::Read {
            return Err(denied("record listing requires read authority"));
        }
        self.store
            .immediate_access(context, request, |transaction, _| {
                let (cursor, records) = crate::records::list_records_in(
                    transaction.raw(),
                    &request.target_scope,
                    category,
                    status,
                    limit,
                )?;
                Ok(RecordList { records, cursor })
            })
    }

    pub fn search(
        &mut self,
        context: &AuthContext,
        access: &AccessRequest,
        request: &StoredSearchRequest,
    ) -> MemoryResult<SearchResponse> {
        self.store.search_stored(context, access, request)
    }

    pub fn register_embedding(
        &mut self,
        context: &AuthContext,
        request: &MutationRequest,
        registration: &EmbeddingRegistration,
        now_ms: u64,
    ) -> MemoryResult<Cursor> {
        self.require_ready()?;
        let now_i64 = timestamp(now_ms)?;
        self.store
            .immediate_authorized(context, request, |transaction, authorization| {
                transaction.reauthorize(context, request, authorization)?;
                transaction.ensure_scope(&request.target_scope, now_i64)?;
                embedding::register_embedding(transaction.raw(), registration, now_ms)?;
                transaction.advance_cursor(&request.target_scope, now_i64)
            })
    }

    pub fn retire_embedding(
        &mut self,
        context: &AuthContext,
        request: &MutationRequest,
        registration_id: &Id,
        now_ms: u64,
    ) -> MemoryResult<Cursor> {
        self.require_ready()?;
        let now_i64 = timestamp(now_ms)?;
        self.store
            .immediate_authorized(context, request, |transaction, authorization| {
                transaction.reauthorize(context, request, authorization)?;
                if !embedding::retire_embedding(
                    transaction.raw(),
                    &request.target_scope,
                    registration_id,
                    now_ms,
                )? {
                    return Err(error(
                        "NOT_FOUND",
                        "active embedding registration was not found",
                        EffectState::NotStarted,
                    ));
                }
                transaction.advance_cursor(&request.target_scope, now_i64)
            })
    }

    pub fn active_embedding(
        &mut self,
        context: &AuthContext,
        request: &AccessRequest,
    ) -> MemoryResult<EmbeddingView> {
        if request.operation != GrantOperation::Read {
            return Err(denied("embedding inspection requires read authority"));
        }
        self.store
            .immediate_access(context, request, |transaction, _| {
                Ok(EmbeddingView {
                    registration: embedding::active_embedding(
                        transaction.raw(),
                        &request.target_scope,
                    )?,
                    cursor: transaction.cursor(&request.target_scope)?,
                })
            })
    }

    pub fn diagnostics(
        &mut self,
        context: &AuthContext,
        request: &AccessRequest,
    ) -> MemoryResult<MemoryDiagnostics> {
        if request.operation != GrantOperation::Read {
            return Err(denied("memory diagnostics require read authority"));
        }
        self.store
            .immediate_access(context, request, |transaction, _| {
                let target = crate::scope_digest(&request.target_scope).to_hex();
                let count = |sql: &str| {
                    transaction
                        .raw()
                        .query_row(sql, [&target], |row| row.get::<_, u64>(0))
                        .map_err(|_| {
                            error(
                                "DIAGNOSTICS_FAILED",
                                "memory diagnostics could not read store counters",
                                EffectState::Unknown,
                            )
                        })
                };
                Ok(MemoryDiagnostics {
                    schema_version: MEMORY_SCHEMA_VERSION,
                    record_count: count(
                        "SELECT count(*) FROM memory_records WHERE owner_scope_digest=?1",
                    )?,
                    stale_record_count: count(
                        "SELECT count(*) FROM memory_records \
                     WHERE owner_scope_digest=?1 AND status='stale'",
                    )?,
                    embedding_count: count(
                        "SELECT count(*) FROM memory_embeddings e \
                     JOIN memory_records r ON r.record_id=e.record_id \
                     WHERE r.owner_scope_digest=?1",
                    )?,
                    queued_job_count: count(
                        "SELECT count(*) FROM maintenance_jobs \
                     WHERE owner_scope_digest=?1 AND state IN ('queued','checkpointed')",
                    )?,
                    active_lease_count: count(
                        "SELECT count(*) FROM maintenance_leases WHERE owner_scope_digest=?1",
                    )?,
                })
            })
    }

    pub fn enqueue_maintenance(
        &mut self,
        context: &AuthContext,
        request: &MutationRequest,
        spec: &MaintenanceJobSpec,
    ) -> MemoryResult<()> {
        self.require_ready()?;
        maintenance::enqueue(&mut self.store, context, request, spec)
    }

    pub fn enqueue_embedding(
        &mut self,
        context: &AuthContext,
        request: &MutationRequest,
        spec: &MaintenanceJobSpec,
    ) -> MemoryResult<()> {
        require_kind(
            spec,
            &[MaintenanceKind::EmbedRecords, MaintenanceKind::ReembedModel],
        )?;
        self.enqueue_maintenance(context, request, spec)
    }

    pub fn enqueue_index(
        &mut self,
        context: &AuthContext,
        request: &MutationRequest,
        spec: &MaintenanceJobSpec,
    ) -> MemoryResult<()> {
        require_kind(
            spec,
            &[
                MaintenanceKind::ReconcileFts,
                MaintenanceKind::ReconcileSources,
                MaintenanceKind::IndexMessages,
                MaintenanceKind::IndexGitCommits,
            ],
        )?;
        self.enqueue_maintenance(context, request, spec)
    }

    pub fn enqueue_summary(
        &mut self,
        context: &AuthContext,
        request: &MutationRequest,
        spec: &MaintenanceJobSpec,
    ) -> MemoryResult<()> {
        require_kind(
            spec,
            &[
                MaintenanceKind::RefreshSummaries,
                MaintenanceKind::DecaySummaries,
            ],
        )?;
        self.enqueue_maintenance(context, request, spec)
    }

    pub fn prepare_import(
        &mut self,
        context: &AuthContext,
        request: &MutationRequest,
        spec: &MaintenanceJobSpec,
    ) -> MemoryResult<()> {
        require_kind(spec, &[MaintenanceKind::ImportBatch])?;
        self.enqueue_maintenance(context, request, spec)
    }

    pub fn prepare_export(
        &mut self,
        context: &AuthContext,
        request: &MutationRequest,
        spec: &MaintenanceJobSpec,
    ) -> MemoryResult<()> {
        require_kind(spec, &[MaintenanceKind::ExportBatch])?;
        self.enqueue_maintenance(context, request, spec)
    }

    pub fn export_scope(
        &mut self,
        context: &AuthContext,
        request: &MutationRequest,
        export_id: Id,
        include_grants: bool,
        created_at_ms: u64,
    ) -> MemoryResult<MemoryExportBundle> {
        self.require_ready()?;
        export::export_scope(
            &mut self.store,
            context,
            request,
            export_id,
            include_grants,
            created_at_ms,
        )
    }

    pub fn stage_import(
        &mut self,
        context: &AuthContext,
        request: &MutationRequest,
        batch_id: Id,
        bundle: &MemoryExportBundle,
        target_scope: crate::Scope,
        scope_mapping: BTreeMap<String, crate::Scope>,
        created_at_ms: u64,
    ) -> MemoryResult<MemoryImportBatch> {
        self.require_ready()?;
        import::stage_import(
            &mut self.store,
            context,
            request,
            batch_id,
            bundle,
            target_scope,
            scope_mapping,
            created_at_ms,
        )
    }

    pub fn apply_import(
        &mut self,
        context: &AuthContext,
        request: &MutationRequest,
        batch: &MemoryImportBatch,
        bundle: &MemoryExportBundle,
        applied_at_ms: u64,
    ) -> MemoryResult<MemoryImportBatch> {
        self.require_ready()?;
        import::apply_import(
            &mut self.store,
            context,
            request,
            batch,
            bundle,
            applied_at_ms,
        )
    }

    pub fn import_legacy(
        &mut self,
        context: &AuthContext,
        request: &MutationRequest,
        batch_id: Id,
        snapshot: &GideonLegacySnapshot,
        applied_at_ms: u64,
    ) -> MemoryResult<LegacyImportReceipt> {
        self.require_ready()?;
        let plan = legacy_import::plan_legacy_import(snapshot, request, applied_at_ms)?;
        let staged = import::stage_import(
            &mut self.store,
            context,
            request,
            batch_id,
            &plan.bundle,
            request.target_scope.clone(),
            BTreeMap::new(),
            applied_at_ms,
        )?;
        if staged.state == ImportState::Rejected {
            Ok(LegacyImportReceipt {
                source_digest: plan.source_digest,
                destination_digest: plan.destination_digest,
                batch: staged,
                record_count: plan.record_count,
                item_count: plan.item_count,
            })
        } else {
            legacy_import::apply_legacy_import(
                &mut self.store,
                context,
                request,
                &staged,
                &plan,
                applied_at_ms,
            )
        }
    }

    pub fn claim_maintenance(
        &mut self,
        context: &AuthContext,
        request: &MutationRequest,
        worker_id: &Id,
        ttl_ms: i64,
    ) -> MemoryResult<Option<MaintenanceClaimReceipt>> {
        self.require_ready()?;
        let claim = maintenance::claim_next(&mut self.store, context, request, worker_id, ttl_ms)?;
        Ok(claim.map(|claim| {
            let receipt = claim.receipt();
            self.claims.insert(claim.id.clone(), claim);
            receipt
        }))
    }

    pub fn maintenance_status(
        &mut self,
        context: &AuthContext,
        request: &MutationRequest,
        job_id: &Id,
    ) -> MemoryResult<MaintenanceJobStatus> {
        maintenance::status(&mut self.store, context, request, job_id)
    }

    pub fn heartbeat_maintenance(
        &mut self,
        context: &AuthContext,
        request: &MutationRequest,
        presented: &MaintenanceClaimKey,
        ttl_ms: i64,
    ) -> MemoryResult<MaintenanceClaimReceipt> {
        let claim = self
            .claims
            .get_mut(&presented.job_id)
            .ok_or_else(claim_not_found)?;
        require_claim_key(claim, presented)?;
        maintenance::heartbeat(&mut self.store, context, request, claim, ttl_ms)?;
        Ok(claim.receipt())
    }

    pub fn publish_maintenance(
        &mut self,
        job_context: &AuthContext,
        job_request: &MutationRequest,
        summary_context: &AuthContext,
        summary_request: &MutationRequest,
        presented: &MaintenanceClaimKey,
        publication: &MaintenancePublication,
    ) -> MemoryResult<MaintenancePublicationReceipt> {
        let job_id = publication.job_id().clone();
        if presented.job_id != job_id {
            return Err(claim_not_found());
        }
        let claim = self.claims.get(&job_id).ok_or_else(claim_not_found)?;
        require_claim_key(claim, presented)?;
        let receipt = maintenance::publish_receipt(
            &mut self.store,
            job_context,
            job_request,
            summary_context,
            summary_request,
            claim,
            publication,
        )?;
        self.claims.remove(&job_id);
        Ok(receipt)
    }

    pub fn publish_knowledge_maintenance(
        &mut self,
        job_context: &AuthContext,
        job_request: &MutationRequest,
        presented: &MaintenanceClaimKey,
        publication: &KnowledgePublication,
        authorities: &KnowledgePublicationAuthorities,
    ) -> MemoryResult<KnowledgePublicationReceipt> {
        if presented.job_id != publication.job_id {
            return Err(claim_not_found());
        }
        let claim = self
            .claims
            .get(&publication.job_id)
            .ok_or_else(claim_not_found)?;
        require_claim_key(claim, presented)?;
        let receipt = maintenance::publish_knowledge_receipt(
            &mut self.store,
            job_context,
            job_request,
            claim,
            publication,
            authorities,
        )?;
        self.claims.remove(&publication.job_id);
        Ok(receipt)
    }

    pub fn publish_integrity_maintenance(
        &mut self,
        job_context: &AuthContext,
        job_request: &MutationRequest,
        presented: &MaintenanceClaimKey,
        expected_input_digest: Digest,
        expected_config_digest: Digest,
    ) -> MemoryResult<MaintenancePublicationReceipt> {
        let claim = self
            .claims
            .get(&presented.job_id)
            .ok_or_else(claim_not_found)?;
        require_claim_key(claim, presented)?;
        let receipt = maintenance::publish_integrity(
            &mut self.store,
            job_context,
            job_request,
            claim,
            expected_input_digest,
            expected_config_digest,
        )?;
        self.claims.remove(&presented.job_id);
        Ok(receipt)
    }

    pub fn checkpoint_maintenance(
        &mut self,
        context: &AuthContext,
        request: &MutationRequest,
        presented: &MaintenanceClaimKey,
        cursor: Cursor,
        available_at_ms: i64,
    ) -> MemoryResult<()> {
        let claim = self
            .claims
            .get(&presented.job_id)
            .ok_or_else(claim_not_found)?;
        require_claim_key(claim, presented)?;
        maintenance::checkpoint(
            &mut self.store,
            context,
            request,
            claim,
            cursor,
            available_at_ms,
        )?;
        self.claims.remove(&presented.job_id);
        Ok(())
    }

    pub fn cancel_maintenance(
        &mut self,
        context: &AuthContext,
        request: &MutationRequest,
        presented: &MaintenanceClaimKey,
    ) -> MemoryResult<()> {
        let claim = self
            .claims
            .get(&presented.job_id)
            .ok_or_else(claim_not_found)?;
        require_claim_key(claim, presented)?;
        maintenance::cancel(&mut self.store, context, request, claim)?;
        self.claims.remove(&presented.job_id);
        Ok(())
    }

    pub fn terminate_maintenance(
        &mut self,
        context: &AuthContext,
        request: &MutationRequest,
        presented: &MaintenanceClaimKey,
        state: TerminalState,
        error_code: Option<&str>,
    ) -> MemoryResult<()> {
        let claim = self
            .claims
            .get(&presented.job_id)
            .ok_or_else(claim_not_found)?;
        require_claim_key(claim, presented)?;
        maintenance::terminate(&mut self.store, context, request, claim, state, error_code)?;
        self.claims.remove(&presented.job_id);
        Ok(())
    }

    pub fn into_store(self) -> MemoryStore {
        self.store
    }

    fn require_ready(&self) -> MemoryResult<()> {
        if self.state == ServiceState::Ready {
            Ok(())
        } else {
            Err(error(
                "SERVICE_DRAINING",
                "the memory service is draining and refuses new mutations",
                EffectState::NotStarted,
            ))
        }
    }
}

fn require_kind(spec: &MaintenanceJobSpec, allowed: &[MaintenanceKind]) -> MemoryResult<()> {
    if allowed.contains(&spec.kind) {
        Ok(())
    } else {
        Err(error(
            "INVALID_ARGUMENT",
            "maintenance kind does not match the requested memory operation",
            EffectState::NotStarted,
        ))
    }
}

fn denied(message: &'static str) -> crate::Error {
    error("AUTHORIZATION_DENIED", message, EffectState::NotStarted)
}

fn claim_not_found() -> crate::Error {
    error(
        "CLAIM_NOT_FOUND",
        "the maintenance claim is not active in this memory service",
        EffectState::NotStarted,
    )
}

fn require_claim_key(job: &ClaimedJob, presented: &MaintenanceClaimKey) -> MemoryResult<()> {
    let expected = job.receipt();
    if expected.job_id == presented.job_id
        && expected.holder_id == presented.holder_id
        && constant_time_eq(
            expected.fencing_token.as_bytes(),
            presented.fencing_token.as_bytes(),
        )
    {
        Ok(())
    } else {
        Err(claim_not_found())
    }
}

fn constant_time_eq(expected: &[u8], presented: &[u8]) -> bool {
    if expected.len() != presented.len() {
        return false;
    }
    expected
        .iter()
        .zip(presented)
        .fold(0_u8, |difference, (left, right)| {
            difference | (left ^ right)
        })
        == 0
}

fn timestamp(value: u64) -> MemoryResult<i64> {
    i64::try_from(value).map_err(|_| {
        error(
            "INVALID_ARGUMENT",
            "timestamp is outside the SQLite integer range",
            EffectState::NotStarted,
        )
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        AuthenticatedPrincipal, AuthorizationRequest, CapabilityGrant, PrincipalKind, Scope,
    };
    use hypermid_store::authorization::put_grant;
    use std::collections::BTreeSet;

    fn id(value: &str) -> Id {
        Id::new(value).unwrap()
    }

    #[test]
    fn api_lifecycle_uses_live_schema_and_authorized_drain() {
        let directory = tempfile::tempdir().unwrap();
        let scope = Scope::new(id("owner-1"), id("project-1"), None);
        let principal = AuthenticatedPrincipal {
            principal_id: id("principal-1"),
            owner_id: scope.owner_id.clone(),
            kind: PrincipalKind::Foreground,
        };
        let issuer = AuthenticatedPrincipal {
            principal_id: id("issuer-1"),
            owner_id: scope.owner_id.clone(),
            kind: PrincipalKind::Foreground,
        };
        let capability_id = id("capability-1");
        let resource_id = id("memory-service");
        let grant = CapabilityGrant {
            capability_id: capability_id.clone(),
            issuer_owner_id: scope.owner_id.clone(),
            principal_id: principal.principal_id.clone(),
            claimed_scope: scope.clone(),
            target_scope: scope.clone(),
            operations: BTreeSet::from([CapabilityOperation::Administer]),
            resources: BTreeSet::from([resource_id.clone()]),
            expires_at_ms: 1_000,
        };
        let mut store = MemoryStore::open(directory.path().join("memory.sqlite3")).unwrap();
        store
            .immediate(|transaction| {
                put_grant(transaction.raw(), &issuer, &grant).unwrap();
                Ok(())
            })
            .unwrap();

        let evidence = store.schema_evidence().unwrap();
        assert_eq!(evidence.current_version, MEMORY_SCHEMA_VERSION);
        assert_eq!(evidence.schema_digest, crate::migrations::schema_digest());

        let context = AuthContext {
            principal,
            request: AuthorizationRequest {
                claimed_scope: scope.clone(),
                target_scope: scope,
                operation: CapabilityOperation::Administer,
                resource_id,
                now_ms: 10,
            },
            capability_id,
        };
        let mut api = MemoryApi::new(store);
        assert_eq!(api.health().state, ServiceState::Ready);
        let drained = api.drain(&context).unwrap();
        assert_eq!(drained.state, ServiceState::Draining);
        assert_eq!(drained.schema_digest, evidence.schema_digest);
        assert!(drained.durable && drained.lexical_available);
    }

    #[test]
    fn api_snapshot_scope_uses_the_canonical_memory_snapshot() {
        let directory = tempfile::tempdir().unwrap();
        let scope = Scope::new(id("owner-snapshot"), id("project-snapshot"), None);
        let mut store = MemoryStore::open(directory.path().join("memory.sqlite3")).unwrap();
        let cursor = store.ensure_scope(&scope, 10).unwrap();
        let mut api = MemoryApi::new(store);

        let receipt = api
            .snapshot_scope(scope.clone(), directory.path().join("artifact"))
            .unwrap();

        assert_eq!(receipt.scope, scope);
        assert_eq!(receipt.cursor, cursor);
        assert!(receipt.bytes > 0);
    }
}
