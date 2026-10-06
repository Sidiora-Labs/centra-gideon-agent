use std::collections::{BTreeMap, HashSet};
use std::path::Path;
use std::sync::{Arc, Mutex};

use hypermid_contracts::{Cursor, EffectState, Error, Id, Scope, Trace};
use hypermid_core::capability::{AuthorizationRequest, CapabilityOperation};
use hypermid_memory::api::MaintenanceClaimKey;
use hypermid_memory::embedding::{EmbeddingRegistration, PublicationGuard, ProviderEmbedding};
use hypermid_memory::maintenance::{
    KnowledgePublication, KnowledgePublicationAuthorities, KnowledgePublicationAuthority,
};
use hypermid_memory::maintenance::{MaintenanceJobSpec, MaintenancePublication, TerminalState};
use hypermid_memory::search::{SearchMode, SearchResponse, StoredSearchRequest};
use hypermid_memory::{
    capability_operation, AccessRequest, Digest, GideonLegacySnapshot, GrantOperation, MemoryApi,
    MemoryExportBundle, MemoryImportBatch, MutationRequest, Operation, RecordDraft, RecordStatus,
    ShareGrantDraft, SourceSnapshot, VerificationState, SHARE_GRANTS_RESOURCE,
};
use hypermid_protocol::Envelope;
use hypermid_transport::AuthenticatedSession;
use serde::de::DeserializeOwned;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::dispatch::Dispatcher;

pub const MEMORY_OPERATIONS: &[&str] = &[
    "memory.health",
    "memory.app-scope.lookup",
    "memory.app-scope.issue",
    "memory.app-scope.resolve",
    "memory.app-scope.revoke",
    "memory.private-scope.issue",
    "memory.private-scope.resolve",
    "memory.private-scope.retire",
    "memory.drain",
    "memory.record.create",
    "memory.record.update",
    "memory.record.archive",
    "memory.record.restore",
    "memory.record.delete",
    "memory.record.purge",
    "memory.record.verify",
    "memory.record.merge",
    "memory.record.split",
    "memory.record.relocate",
    "memory.record.get",
    "memory.record.shared_get",
    "memory.record.list",
    "memory.search",
    "memory.smart_notes.candidates",
    "memory.maintenance.enqueue",
    "memory.maintenance.claim",
    "memory.maintenance.status",
    "memory.maintenance.heartbeat",
    "memory.maintenance.checkpoint",
    "memory.maintenance.publish",
    "memory.maintenance.publish_knowledge",
    "memory.maintenance.cancel",
    "memory.maintenance.terminate",
    "memory.maintenance.failed",
    "memory.maintenance.abandoned",
    "memory.embedding.register",
    "memory.embedding.rebind",
    "memory.embedding.publish",
    "memory.embedding.retire",
    "memory.embedding.active",
    "memory.index.enqueue",
    "memory.summary.enqueue",
    "memory.import.prepare",
    "memory.export.prepare",
    "memory.export.scope",
    "memory.import.stage",
    "memory.import.apply",
    "memory.import.legacy",
    "memory.diagnostics",
    "memory.grant.create",
    "memory.grant.revoke",
    "memory.grant.list",
];

#[derive(Clone, Debug)]
pub struct MemoryRouteResponse {
    pub payload: Option<Value>,
    pub error: Option<Error>,
}

pub struct MemoryRoutes {
    api: Arc<Mutex<MemoryApi>>,
}

impl MemoryRoutes {
    pub fn open(path: impl AsRef<Path>) -> Result<Self, Error> {
        Ok(Self {
            api: Arc::new(Mutex::new(MemoryApi::open(path)?)),
        })
    }

    pub(crate) fn api(&self) -> Arc<Mutex<MemoryApi>> {
        Arc::clone(&self.api)
    }

    pub fn handles(operation: &str) -> bool {
        MEMORY_OPERATIONS.contains(&operation)
    }

    pub fn dispatch(
        &self,
        session: &AuthenticatedSession,
        envelope: &Envelope,
        now_ms: u64,
    ) -> MemoryRouteResponse {
        if envelope.scope.as_ref() != Some(&session.bound_scope) {
            return failure(route_error(
                "SCOPE_DENIED",
                "request scope does not match the authenticated session scope",
                EffectState::NotStarted,
            ));
        }
        let Some(trace) = envelope.trace.as_ref() else {
            return failure(invalid("memory requests require a trace"));
        };
        let Some(operation) = envelope.operation.as_deref() else {
            return failure(invalid("memory requests require an operation"));
        };
        if !Self::handles(operation) {
            return failure(route_error(
                "UNKNOWN_OPERATION",
                "memory operation is not available",
                EffectState::NotStarted,
            ));
        }
        let payload = envelope.payload.clone().unwrap_or_else(|| json!({}));
        let mut api = match self.api.lock() {
            Ok(api) => api,
            Err(_) => {
                return failure(route_error(
                    "MEMORY_SERVICE_FAILED",
                    "memory service lock is unavailable",
                    EffectState::Unknown,
                ))
            }
        };
        match dispatch_operation(&mut api, session, operation, payload, trace, now_ms) {
            Ok(result) => MemoryRouteResponse {
                payload: Some(json!({"trace": trace, "result": result})),
                error: None,
            },
            Err(error) => failure(error),
        }
    }
}

fn dispatch_operation(
    api: &mut MemoryApi,
    session: &AuthenticatedSession,
    operation: &str,
    payload: Value,
    trace: &Trace,
    now_ms: u64,
) -> Result<Value, Error> {
    match operation {
        "memory.app-scope.lookup" => {
            let p:AppRevokePayload=parse(payload)?;
            let ctx=context(session,session.bound_scope.clone(),CapabilityOperation::Read,static_id("memory-records"),p.capability_id,now_ms)?;
            Ok(json!({"receipt":api.lookup_app_scope(&ctx,p.app_name)?}))
        }

        "memory.app-scope.issue" => {
            let p: AppIssuePayload=parse(payload)?;
            let ctx=context(session,session.bound_scope.clone(),CapabilityOperation::Administer,static_id("memory-service"),p.capability_id,now_ms)?;
            encode(api.issue_app_scope(&ctx,p.app_name,p.manifest_digest,p.ttl_ms)?)
        }
        "memory.app-scope.resolve" => {
            let p: PrivateResolvePayload=parse(payload)?;
            let ctx=context(session,p.target_scope,CapabilityOperation::Read,static_id("memory-records"),p.capability_id,now_ms)?;
            encode(api.resolve_app_scope(&ctx,p.scope_id)?)
        }
        "memory.app-scope.revoke" => {
            let p: AppRevokePayload=parse(payload)?;
            let ctx=context(session,session.bound_scope.clone(),CapabilityOperation::Administer,static_id("memory-service"),p.capability_id,now_ms)?;
            Ok(json!({"receipt":api.revoke_app_scope(&ctx,p.app_name,p.expected_capability_id)?}))
        }

        "memory.private-scope.issue" => {
            let p: PrivateIssuePayload = parse(payload)?;
            let ctx = context(session, session.bound_scope.clone(), CapabilityOperation::Administer, static_id("memory-service"), p.capability_id, now_ms)?;
            encode(api.issue_private_scope(&ctx, p.origin_session_key, p.original_actor, p.memory_mode, p.ttl_ms)?)
        }
        "memory.private-scope.retire" => {
            let p: PrivateRetirePayload = parse(payload)?;
            let ctx = context(session, session.bound_scope.clone(), CapabilityOperation::Administer, static_id("memory-service"), p.capability_id, now_ms)?;
            Ok(json!({"receipt": api.retire_private_scope(&ctx, p.origin_session_key)?}))
        }
        "memory.private-scope.resolve" => {
            let p: PrivateResolvePayload = parse(payload)?;
            let ctx = context(session, p.target_scope, CapabilityOperation::Read, static_id("private-work"), p.capability_id, now_ms)?;
            encode(api.resolve_private_scope(&ctx, p.scope_id)?)
        }
        "memory.health" => {
            parse::<EmptyPayload>(payload)?;
            encode(api.health())
        }
        "memory.drain" => {
            let payload: CapabilityPayload = parse(payload)?;
            let context = context(
                session,
                session.bound_scope.clone(),
                CapabilityOperation::Administer,
                static_id("memory-service"),
                payload.capability_id,
                now_ms,
            )?;
            encode(api.drain(&context)?)
        }
        "memory.record.create" | "memory.record.update" => {
            let payload: WriteRecordPayload = parse(payload)?;
            require_trace_scope(
                trace,
                session,
                &payload.request.actor_scope,
                &payload.request.trace,
            )?;
            require_operation(
                &payload.request,
                if operation.ends_with("create") {
                    Operation::Create
                } else {
                    Operation::Update
                },
            )?;
            let record_id = record_resource(&payload.request)?;
            let resource = record_authority_resource(
                session,
                &payload.request.target_scope,
                &record_id,
                payload.authority_resource,
            )?;
            let context = mutation_context(
                session,
                &payload.request,
                payload.capability_id,
                resource,
                now_ms,
            )?;
            if operation.ends_with("create") {
                encode(api.create_record(
                    &context,
                    &payload.request,
                    &payload.draft,
                    &payload.sources,
                    now_ms,
                )?)
            } else {
                encode(api.update_record(
                    &context,
                    &payload.request,
                    &payload.draft,
                    &payload.sources,
                    now_ms,
                )?)
            }
        }
        "memory.record.archive"
        | "memory.record.restore"
        | "memory.record.delete"
        | "memory.record.purge" => {
            let payload: RecordStatePayload = parse(payload)?;
            require_trace_scope(
                trace,
                session,
                &payload.request.actor_scope,
                &payload.request.trace,
            )?;
            let expected = match operation {
                "memory.record.archive" => Operation::Archive,
                "memory.record.restore" => Operation::Restore,
                "memory.record.delete" => Operation::Delete,
                _ => Operation::Purge,
            };
            require_operation(&payload.request, expected)?;
            let record_id = record_resource(&payload.request)?;
            let resource = record_authority_resource(
                session,
                &payload.request.target_scope,
                &record_id,
                payload.authority_resource,
            )?;
            let context = mutation_context(
                session,
                &payload.request,
                payload.capability_id,
                resource,
                now_ms,
            )?;
            match expected {
                Operation::Archive => {
                    encode(api.archive_record(&context, &payload.request, now_ms)?)
                }
                Operation::Restore => {
                    encode(api.restore_record(&context, &payload.request, now_ms)?)
                }
                Operation::Delete => {
                    encode(api.delete_record(&context, &payload.request, now_ms)?)
                }
                Operation::Purge => encode(json!({
                    "cursor": api.purge_record(&context, &payload.request, now_ms)?
                })),
                _ => unreachable!(),
            }
        }
        "memory.record.verify" => {
            let payload: VerifyPayload = parse(payload)?;
            require_trace_scope(
                trace,
                session,
                &payload.request.actor_scope,
                &payload.request.trace,
            )?;
            require_operation(&payload.request, Operation::Verify)?;
            let record_id = record_resource(&payload.request)?;
            let resource = record_authority_resource(
                session,
                &payload.request.target_scope,
                &record_id,
                payload.authority_resource,
            )?;
            let context = mutation_context(
                session,
                &payload.request,
                payload.capability_id,
                resource,
                now_ms,
            )?;
            encode(api.verify_record(
                &context,
                &payload.request,
                payload.state,
                payload.confidence,
                payload.evidence_source_id.as_ref(),
                now_ms,
            )?)
        }
        "memory.record.merge" => {
            let payload: MergePayload = parse(payload)?;
            require_trace_scope(
                trace,
                session,
                &payload.request.actor_scope,
                &payload.request.trace,
            )?;
            require_operation(&payload.request, Operation::Merge)?;
            let record_id = record_resource(&payload.request)?;
            let resource = record_authority_resource(
                session,
                &payload.request.target_scope,
                &record_id,
                payload.authority_resource,
            )?;
            let context = mutation_context(
                session,
                &payload.request,
                payload.capability_id,
                resource,
                now_ms,
            )?;
            let revisions = payload
                .source_revisions
                .into_iter()
                .map(|item| (item.id, item.digest))
                .collect::<Vec<_>>();
            encode(api.merge_records(
                &context,
                &payload.request,
                &payload.draft,
                &revisions,
                &payload.sources,
                now_ms,
            )?)
        }
        "memory.record.split" => {
            let payload: SplitPayload = parse(payload)?;
            require_trace_scope(
                trace,
                session,
                &payload.request.actor_scope,
                &payload.request.trace,
            )?;
            require_operation(&payload.request, Operation::Split)?;
            let record_id = record_resource(&payload.request)?;
            let resource = record_authority_resource(
                session,
                &payload.request.target_scope,
                &record_id,
                payload.authority_resource,
            )?;
            let context = mutation_context(
                session,
                &payload.request,
                payload.capability_id,
                resource,
                now_ms,
            )?;
            encode(api.split_record(&context, &payload.request, &payload.replacements, now_ms)?)
        }
        "memory.record.relocate" => {
            let payload: RelocatePayload = parse(payload)?;
            require_trace_scope(
                trace,
                session,
                &payload.source_request.actor_scope,
                &payload.source_request.trace,
            )?;
            if payload.destination_request.trace != *trace {
                return Err(denied("relocation request traces do not match"));
            }
            require_operation(&payload.source_request, Operation::Relocate)?;
            require_operation(&payload.destination_request, Operation::Relocate)?;
            let record_id = record_resource(&payload.source_request)?;
            let source_resource = record_authority_resource(
                session,
                &payload.source_request.target_scope,
                &record_id,
                payload.source_authority_resource,
            )?;
            let destination_resource = record_authority_resource(
                session,
                &payload.destination_request.target_scope,
                &record_id,
                payload.destination_authority_resource,
            )?;
            let source_context = mutation_context(
                session,
                &payload.source_request,
                payload.capability_id,
                source_resource,
                now_ms,
            )?;
            let destination_context = mutation_context(
                session,
                &payload.destination_request,
                payload.destination_capability_id,
                destination_resource,
                now_ms,
            )?;
            encode(api.relocate_record(
                &source_context,
                &payload.source_request,
                &destination_context,
                &payload.destination_request,
                now_ms,
            )?)
        }
        "memory.record.get" => {
            let payload: AccessPayload = parse(payload)?;
            require_access(trace, session, &payload.request, GrantOperation::Read)?;
            let resource = record_authority_resource(
                session,
                &payload.request.target_scope,
                &payload.request.resource_id,
                payload.authority_resource,
            )?;
            let context = context(
                session,
                payload.request.target_scope.clone(),
                CapabilityOperation::Read,
                resource,
                payload.capability_id,
                now_ms,
            )?;
            encode(api.record(&context, &payload.request)?)
        }
        "memory.record.shared_get" => {
            let payload: SharedAccessPayload = parse(payload)?;
            require_access(trace, session, &payload.request, GrantOperation::Read)?;
            let resource = record_authority_resource(
                session,
                &payload.request.target_scope,
                &payload.request.resource_id,
                payload.authority_resource,
            )?;
            let context = context(
                session,
                payload.request.target_scope.clone(),
                CapabilityOperation::Read,
                resource,
                payload.capability_id,
                now_ms,
            )?;
            encode(api.shared_record(&context, &payload.request, payload.policy_digest)?)
        }
        "memory.record.list" => {
            let payload: ListPayload = parse(payload)?;
            require_access(trace, session, &payload.request, GrantOperation::Read)?;
            let context = access_context(session, &payload.request, payload.capability_id, now_ms)?;
            let page = if payload.ordered_by_id { api.list_records_after(
                &context, &payload.request, payload.category.as_deref(), payload.status,
                payload.after_id.as_ref(), payload.limit,
            )? } else { api.list_records(
                &context,
                &payload.request,
                payload.category.as_deref(),
                payload.status,
                payload.limit,
            )? };
            if payload.cursor.is_some_and(|cursor| cursor != page.cursor) {
                return Err(route_error(
                    "STALE_CURSOR",
                    "record list cursor no longer matches durable memory state",
                    EffectState::NotStarted,
                ));
            }
            encode(page)
        }
        "memory.search" => {
            let payload: SearchPayload = parse(payload)?;
            require_access(trace, session, &payload.request, GrantOperation::Search)?;
            if payload.search.scope != payload.request.target_scope
                || payload.search.trace != *trace
            {
                return Err(denied(
                    "search scope or trace does not match the authorized access request",
                ));
            }
            let context = access_context(session, &payload.request, payload.capability_id, now_ms)?;
            let search = StoredSearchRequest {
                query: payload.search.query,
                scope: payload.search.scope,
                mode: payload.search.mode,
                limit: payload.search.limit,
                candidate_limit_per_source: payload.search.candidate_limit_per_source,
                include_archived: payload.search.include_archived,
                visible_digests: payload.search.visible_digests,
                query_vector: payload.search.query_vector,
                vector_fingerprint: payload.search.vector_fingerprint,
                semantic_required: payload.search.semantic_required,
                now_ms: i64::try_from(now_ms)
                    .map_err(|_| invalid("daemon time is outside the supported range"))?,
                from_ms: payload.search.from_ms,
                to_ms: payload.search.to_ms,
                sources: payload.search.sources,
                kinds: payload.search.kinds,
                categories: payload.search.categories,
                cursor: payload.search.cursor,
                trace: payload.search.trace,
            };
            encode_search(api.search(&context, &payload.request, &search)?)
        }
        "memory.smart_notes.candidates" => {
            let payload: SmartNoteCandidatesPayload = parse(payload)?;
            require_access(trace, session, &payload.request, GrantOperation::Read)?;
            if payload.request.resource_id.as_str() != "memory-records" {
                return Err(invalid(
                    "smart-note candidate access requires memory-records authority",
                ));
            }
            let context = access_context(session, &payload.request, payload.capability_id, now_ms)?;
            encode(api.smart_note_candidates(&context, &payload.request, payload.limit)?)
        }
        "memory.maintenance.enqueue"
        | "memory.index.enqueue"
        | "memory.summary.enqueue"
        | "memory.import.prepare"
        | "memory.export.prepare" => {
            let payload: EnqueuePayload = parse(payload)?;
            require_trace_scope(
                trace,
                session,
                &payload.request.actor_scope,
                &payload.request.trace,
            )?;
            let resource = if matches!(operation, "memory.import.prepare" | "memory.export.prepare")
            {
                require_portability_request(&payload.request)?;
                static_id("memory-portability")
            } else {
                require_job_request(&payload.request, &payload.spec)?;
                payload.spec.id.clone()
            };
            let context = mutation_context(
                session,
                &payload.request,
                payload.capability_id,
                resource,
                now_ms,
            )?;
            match operation {
                "memory.maintenance.enqueue" => {
                    api.enqueue_maintenance(&context, &payload.request, &payload.spec)?
                }
                "memory.index.enqueue" => {
                    api.enqueue_index(&context, &payload.request, &payload.spec)?
                }
                "memory.summary.enqueue" => {
                    api.enqueue_summary(&context, &payload.request, &payload.spec)?
                }
                "memory.import.prepare" => {
                    api.prepare_import(&context, &payload.request, &payload.spec)?
                }
                "memory.export.prepare" => {
                    api.prepare_export(&context, &payload.request, &payload.spec)?
                }
                _ => unreachable!(),
            }
            Ok(json!({"job_id": payload.spec.id, "accepted": true}))
        }
        "memory.maintenance.claim" => {
            let payload: ClaimPayload = parse(payload)?;
            require_trace_scope(
                trace,
                session,
                &payload.request.actor_scope,
                &payload.request.trace,
            )?;
            if payload.request.record_id.is_some() || payload.request.category.is_some() {
                return Err(invalid(
                    "maintenance claim requires the uncategorized memory-maintenance queue resource",
                ));
            }
            let context = mutation_context(
                session,
                &payload.request,
                payload.capability_id,
                static_id("memory-maintenance"),
                now_ms,
            )?;
            encode(json!({
                "claim": api.claim_maintenance(
                    &context,
                    &payload.request,
                    &payload.worker_id,
                    payload.ttl_ms,
                )?
            }))
        }
        "memory.export.scope" => {
            let payload: ExportPayload = parse(payload)?;
            require_trace_scope(
                trace,
                session,
                &payload.request.actor_scope,
                &payload.request.trace,
            )?;
            require_operation(&payload.request, Operation::Export)?;
            require_portability_request(&payload.request)?;
            if payload.include_grants {
                return Err(invalid("memory export cannot include capability grants"));
            }
            let context = mutation_context(
                session,
                &payload.request,
                payload.capability_id,
                static_id("memory-portability"),
                now_ms,
            )?;
            encode(api.export_scope(
                &context,
                &payload.request,
                payload.export_id,
                false,
                now_ms,
            )?)
        }
        "memory.import.stage" => {
            let payload: StageImportPayload = parse(payload)?;
            require_trace_scope(
                trace,
                session,
                &payload.request.actor_scope,
                &payload.request.trace,
            )?;
            require_operation(&payload.request, Operation::Import)?;
            require_portability_request(&payload.request)?;
            if payload.target_scope != payload.request.target_scope {
                return Err(denied(
                    "import target does not match the authorized target scope",
                ));
            }
            let context = mutation_context(
                session,
                &payload.request,
                payload.capability_id,
                static_id("memory-portability"),
                now_ms,
            )?;
            encode(api.stage_import(
                &context,
                &payload.request,
                payload.batch_id,
                &payload.bundle,
                payload.target_scope,
                payload.scope_mapping,
                now_ms,
            )?)
        }
        "memory.import.apply" => {
            let payload: ApplyImportPayload = parse(payload)?;
            require_trace_scope(
                trace,
                session,
                &payload.request.actor_scope,
                &payload.request.trace,
            )?;
            require_operation(&payload.request, Operation::Import)?;
            require_portability_request(&payload.request)?;
            let context = mutation_context(
                session,
                &payload.request,
                payload.capability_id,
                static_id("memory-portability"),
                now_ms,
            )?;
            encode(api.apply_import(
                &context,
                &payload.request,
                &payload.batch,
                &payload.bundle,
                now_ms,
            )?)
        }
        "memory.import.legacy" => {
            let payload: LegacyImportPayload = parse(payload)?;
            require_trace_scope(
                trace,
                session,
                &payload.request.actor_scope,
                &payload.request.trace,
            )?;
            require_operation(&payload.request, Operation::Import)?;
            require_portability_request(&payload.request)?;
            if payload.snapshot.scope != payload.request.target_scope {
                return Err(denied(
                    "legacy snapshot scope does not match the authorized import target",
                ));
            }
            let context = mutation_context(
                session,
                &payload.request,
                payload.capability_id,
                static_id("memory-portability"),
                now_ms,
            )?;
            encode(api.import_legacy(
                &context,
                &payload.request,
                payload.batch_id,
                &payload.snapshot,
                now_ms,
            )?)
        }
        "memory.maintenance.status" => {
            let payload: JobPayload = parse(payload)?;
            require_trace_scope(
                trace,
                session,
                &payload.request.actor_scope,
                &payload.request.trace,
            )?;
            require_job_id(&payload.request, &payload.job_id)?;
            let context = mutation_context(
                session,
                &payload.request,
                payload.capability_id,
                payload.job_id.clone(),
                now_ms,
            )?;
            encode(api.maintenance_status(&context, &payload.request, &payload.job_id)?)
        }
        "memory.maintenance.heartbeat" => {
            let payload: HeartbeatPayload = parse(payload)?;
            require_trace_scope(
                trace,
                session,
                &payload.request.actor_scope,
                &payload.request.trace,
            )?;
            require_job_id(&payload.request, &payload.claim.job_id)?;
            let context = mutation_context(
                session,
                &payload.request,
                payload.capability_id,
                payload.claim.job_id.clone(),
                now_ms,
            )?;
            encode(api.heartbeat_maintenance(
                &context,
                &payload.request,
                &payload.claim,
                payload.ttl_ms,
            )?)
        }
        "memory.maintenance.publish" => {
            let payload: PublishPayload = parse(payload)?;
            require_trace_scope(
                trace,
                session,
                &payload.request.actor_scope,
                &payload.request.trace,
            )?;
            require_trace_scope(
                trace,
                session,
                &payload.summary_request.actor_scope,
                &payload.summary_request.trace,
            )?;
            let job_id = payload.publication.job_id().clone();
            require_job_id(&payload.request, &job_id)?;
            if payload.claim.job_id != job_id {
                return Err(invalid(
                    "maintenance claim does not match the publication job",
                ));
            }
            let (publication, summary_resource) = match payload.publication {
                MaintenancePublication::Summary {
                    job_id,
                    expected_input_digest,
                    expected_config_digest,
                    draft,
                    sources,
                    ..
                } => {
                    let resource = draft.id.clone();
                    (
                        MaintenancePublication::Summary {
                            job_id,
                            expected_input_digest,
                            expected_config_digest,
                            draft,
                            sources,
                            now_ms,
                        },
                        resource,
                    )
                }
                MaintenancePublication::Knowledge(_) => {
                    return Err(invalid(
                        "knowledge publication requires memory.maintenance.publish_knowledge",
                    ))
                }
            };
            let job_context = mutation_context(
                session,
                &payload.request,
                payload.capability_id,
                job_id,
                now_ms,
            )?;
            let summary_context = mutation_context(
                session,
                &payload.summary_request,
                payload.summary_capability_id,
                summary_resource,
                now_ms,
            )?;
            encode(api.publish_maintenance(
                &job_context,
                &payload.request,
                &summary_context,
                &payload.summary_request,
                &payload.claim,
                &publication,
            )?)
        }
        "memory.maintenance.publish_knowledge" => {
            let payload: KnowledgePublishPayload = parse(payload)?;
            require_trace_scope(
                trace,
                session,
                &payload.request.actor_scope,
                &payload.request.trace,
            )?;
            require_job_id(&payload.request, &payload.claim.job_id)?;
            if payload.claim.job_id != payload.publication.job_id {
                return Err(invalid(
                    "maintenance claim does not match the knowledge publication job",
                ));
            }
            let publication = KnowledgePublication {
                now_ms,
                ..payload.publication
            };
            let job_context = mutation_context(
                session,
                &payload.request,
                payload.capability_id,
                payload.claim.job_id.clone(),
                now_ms,
            )?;
            let smart_notes = build_knowledge_authorities(
                session,
                payload.smart_notes,
                Operation::Index,
                now_ms,
            )?;
            let verifications = build_knowledge_authorities(
                session,
                payload.verifications,
                Operation::Verify,
                now_ms,
            )?;
            let sharing_judgments = build_knowledge_authorities(
                session,
                payload.sharing_judgments,
                Operation::Verify,
                now_ms,
            )?;
            let recall = payload
                .recall
                .map(|authority| {
                    build_knowledge_authority(session, authority, Operation::Create, now_ms)
                })
                .transpose()?;
            let authorities = KnowledgePublicationAuthorities {
                smart_notes,
                verifications,
                sharing_judgments,
                recall,
            };
            encode(api.publish_knowledge_maintenance(
                &job_context,
                &payload.request,
                &payload.claim,
                &publication,
                &authorities,
            )?)
        }
        "memory.maintenance.checkpoint" => {
            let payload: CheckpointPayload = parse(payload)?;
            require_trace_scope(
                trace,
                session,
                &payload.request.actor_scope,
                &payload.request.trace,
            )?;
            require_job_id(&payload.request, &payload.claim.job_id)?;
            let context = mutation_context(
                session,
                &payload.request,
                payload.capability_id,
                payload.claim.job_id.clone(),
                now_ms,
            )?;
            api.checkpoint_maintenance(
                &context,
                &payload.request,
                &payload.claim,
                payload.cursor,
                payload.available_at_ms,
            )?;
            Ok(json!({"job_id": payload.claim.job_id, "checkpointed": true}))
        }
        "memory.maintenance.cancel" => {
            let payload: ClaimedJobPayload = parse(payload)?;
            require_trace_scope(
                trace,
                session,
                &payload.request.actor_scope,
                &payload.request.trace,
            )?;
            require_job_id(&payload.request, &payload.claim.job_id)?;
            let context = mutation_context(
                session,
                &payload.request,
                payload.capability_id,
                payload.claim.job_id.clone(),
                now_ms,
            )?;
            api.cancel_maintenance(&context, &payload.request, &payload.claim)?;
            Ok(json!({"job_id": payload.claim.job_id, "state": "abandoned"}))
        }
        "memory.maintenance.terminate"
        | "memory.maintenance.failed"
        | "memory.maintenance.abandoned" => {
            let payload: TerminatePayload = parse(payload)?;
            require_trace_scope(
                trace,
                session,
                &payload.request.actor_scope,
                &payload.request.trace,
            )?;
            require_job_id(&payload.request, &payload.claim.job_id)?;
            let state = match operation {
                "memory.maintenance.failed" => TerminalState::Failed,
                "memory.maintenance.abandoned" => TerminalState::Abandoned,
                _ => payload
                    .state
                    .ok_or_else(|| invalid("terminate requires a terminal state"))?,
            };
            let context = mutation_context(
                session,
                &payload.request,
                payload.capability_id,
                payload.claim.job_id.clone(),
                now_ms,
            )?;
            api.terminate_maintenance(
                &context,
                &payload.request,
                &payload.claim,
                state,
                payload.error_code.as_deref(),
            )?;
            encode(json!({"job_id": payload.claim.job_id, "state": state}))
        }
        "memory.embedding.register" | "memory.embedding.rebind" => {
            let payload: RegisterEmbeddingPayload = parse(payload)?;
            require_trace_scope(
                trace,
                session,
                &payload.request.actor_scope,
                &payload.request.trace,
            )?;
            require_operation(&payload.request, Operation::Embed)?;
            if payload.registration.owner_scope != payload.request.target_scope {
                return Err(denied(
                    "embedding registration scope does not match the target scope",
                ));
            }
            if payload.request.record_id.as_ref() != Some(&payload.registration.registration_id)
                || payload.request.revision != hypermid_memory::RevisionPrecondition::MustNotExist
            {
                return Err(denied("embedding registration requires its exact new identity"));
            }
            let context = mutation_context(
                session,
                &payload.request,
                payload.capability_id,
                if operation == "memory.embedding.rebind" { static_id("memory-embedding") }
                else { payload.registration.registration_id.clone() },
                now_ms,
            )?;
            encode(json!({"cursor": api.register_embedding(
                &context,
                &payload.request,
                &payload.registration,
                now_ms,
            )?}))
        }
        "memory.embedding.publish" => {
            let payload: PublishEmbeddingPayload = parse(payload)?;
            require_trace_scope(trace, session, &payload.request.actor_scope, &payload.request.trace)?;
            require_operation(&payload.request, Operation::Embed)?;
            let context = mutation_context(session, &payload.request, payload.capability_id,
                static_id("memory-embedding"), now_ms)?;
            encode(json!({"cursor": api.publish_embedding(&context, &payload.request,
                &payload.guard, payload.response, now_ms)?}))
        }
        "memory.embedding.retire" => {
            let payload: RetireEmbeddingPayload = parse(payload)?;
            require_trace_scope(
                trace,
                session,
                &payload.request.actor_scope,
                &payload.request.trace,
            )?;
            require_operation(&payload.request, Operation::Embed)?;
            let context = mutation_context(
                session,
                &payload.request,
                payload.capability_id,
                payload.registration_id.clone(),
                now_ms,
            )?;
            encode(json!({"cursor": api.retire_embedding(
                &context,
                &payload.request,
                &payload.registration_id,
                now_ms,
            )?}))
        }
        "memory.embedding.active" => {
            let payload: AccessPayload = parse(payload)?;
            require_access(trace, session, &payload.request, GrantOperation::Read)?;
            let context = access_context(session, &payload.request, payload.capability_id, now_ms)?;
            encode(api.active_embedding(&context, &payload.request)?)
        }
        "memory.diagnostics" => {
            let payload: AccessPayload = parse(payload)?;
            require_access(trace, session, &payload.request, GrantOperation::Read)?;
            let context = access_context(session, &payload.request, payload.capability_id, now_ms)?;
            encode(api.diagnostics(&context, &payload.request)?)
        }
        "memory.grant.create" => {
            let payload: CreateShareGrantPayload = parse(payload)?;
            let context = context(
                session,
                payload.draft.owner_scope.clone(),
                CapabilityOperation::Administer,
                payload.draft.id.clone(),
                payload.capability_id,
                now_ms,
            )?;
            encode(api.create_share_grant(&context, &payload.draft)?)
        }
        "memory.grant.revoke" => {
            let payload: RevokeShareGrantPayload = parse(payload)?;
            let context = context(
                session,
                payload.owner_scope.clone(),
                CapabilityOperation::Administer,
                payload.grant_id.clone(),
                payload.capability_id,
                now_ms,
            )?;
            encode(api.revoke_share_grant(
                &context,
                &payload.owner_scope,
                &payload.grant_id,
                payload.expected_revision,
            )?)
        }
        "memory.grant.list" => {
            let payload: ListShareGrantPayload = parse(payload)?;
            let context = context(
                session,
                payload.owner_scope.clone(),
                CapabilityOperation::Administer,
                static_id(SHARE_GRANTS_RESOURCE),
                payload.capability_id,
                now_ms,
            )?;
            encode(json!({
                "grants": api.list_share_grants(&context, &payload.owner_scope)?
            }))
        }
        _ => Err(route_error(
            "UNKNOWN_OPERATION",
            "memory operation is not available",
            EffectState::NotStarted,
        )),
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct EmptyPayload {}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CapabilityPayload {
    capability_id: Id,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct WriteRecordPayload {
    capability_id: Id,
    #[serde(default)]
    authority_resource: Option<Id>,
    request: MutationRequest,
    draft: RecordDraft,
    #[serde(default)]
    sources: Vec<SourceSnapshot>,
    #[serde(rename = "now_ms")]
    _now_ms: u64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RecordStatePayload {
    capability_id: Id,
    #[serde(default)]
    authority_resource: Option<Id>,
    request: MutationRequest,
    #[serde(rename = "now_ms")]
    _now_ms: u64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct VerifyPayload {
    capability_id: Id,
    #[serde(default)]
    authority_resource: Option<Id>,
    request: MutationRequest,
    state: VerificationState,
    confidence: f64,
    evidence_source_id: Option<Id>,
    #[serde(rename = "now_ms")]
    _now_ms: u64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SourceRevision {
    id: Id,
    digest: Digest,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct MergePayload {
    capability_id: Id,
    #[serde(default)]
    authority_resource: Option<Id>,
    request: MutationRequest,
    draft: RecordDraft,
    source_revisions: Vec<SourceRevision>,
    #[serde(default)]
    sources: Vec<SourceSnapshot>,
    #[serde(rename = "now_ms")]
    _now_ms: u64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SplitPayload {
    capability_id: Id,
    #[serde(default)]
    authority_resource: Option<Id>,
    request: MutationRequest,
    replacements: Vec<RecordDraft>,
    #[serde(rename = "now_ms")]
    _now_ms: u64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RelocatePayload {
    capability_id: Id,
    destination_capability_id: Id,
    #[serde(default)]
    source_authority_resource: Option<Id>,
    #[serde(default)]
    destination_authority_resource: Option<Id>,
    source_request: MutationRequest,
    destination_request: MutationRequest,
    #[serde(rename = "now_ms")]
    _now_ms: u64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct AccessPayload {
    capability_id: Id,
    #[serde(default)]
    authority_resource: Option<Id>,
    request: AccessRequest,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SharedAccessPayload {
    capability_id: Id,
    #[serde(default)]
    authority_resource: Option<Id>,
    request: AccessRequest,
    policy_digest: Digest,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ListPayload {
    #[serde(default)]
    ordered_by_id: bool,
    after_id: Option<Id>,
    capability_id: Id,
    request: AccessRequest,
    cursor: Option<Cursor>,
    category: Option<String>,
    status: Option<RecordStatus>,
    limit: usize,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SearchPayload {
    capability_id: Id,
    request: AccessRequest,
    search: SearchWire,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SmartNoteCandidatesPayload {
    capability_id: Id,
    request: AccessRequest,
    limit: usize,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SearchWire {
    query: String,
    scope: Scope,
    mode: SearchMode,
    limit: usize,
    candidate_limit_per_source: usize,
    #[serde(default)]
    include_archived: bool,
    #[serde(default)]
    visible_digests: HashSet<Digest>,
    #[serde(default)]
    query_vector: Option<Vec<f32>>,
    #[serde(default)]
    vector_fingerprint: Option<Digest>,
    semantic_required: bool,
    #[serde(rename = "now_ms")]
    _now_ms: u64,
    #[serde(default)]
    from_ms: Option<i64>,
    #[serde(default)]
    to_ms: Option<i64>,
    #[serde(default)]
    sources: HashSet<String>,
    #[serde(default)]
    kinds: HashSet<String>,
    #[serde(default)]
    categories: HashSet<String>,
    #[serde(default)]
    cursor: Option<Cursor>,
    trace: Trace,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct EnqueuePayload {
    capability_id: Id,
    request: MutationRequest,
    spec: MaintenanceJobSpec,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ExportPayload {
    capability_id: Id,
    request: MutationRequest,
    export_id: Id,
    #[serde(default)]
    include_grants: bool,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct StageImportPayload {
    capability_id: Id,
    request: MutationRequest,
    batch_id: Id,
    bundle: MemoryExportBundle,
    target_scope: Scope,
    scope_mapping: BTreeMap<String, Scope>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ApplyImportPayload {
    capability_id: Id,
    request: MutationRequest,
    batch: MemoryImportBatch,
    bundle: MemoryExportBundle,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct LegacyImportPayload {
    capability_id: Id,
    request: MutationRequest,
    batch_id: Id,
    snapshot: GideonLegacySnapshot,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ClaimPayload {
    capability_id: Id,
    request: MutationRequest,
    worker_id: Id,
    ttl_ms: i64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct JobPayload {
    capability_id: Id,
    request: MutationRequest,
    job_id: Id,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ClaimedJobPayload {
    capability_id: Id,
    request: MutationRequest,
    claim: MaintenanceClaimKey,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct HeartbeatPayload {
    capability_id: Id,
    request: MutationRequest,
    claim: MaintenanceClaimKey,
    ttl_ms: i64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PublishPayload {
    capability_id: Id,
    request: MutationRequest,
    summary_capability_id: Id,
    summary_request: MutationRequest,
    claim: MaintenanceClaimKey,
    publication: MaintenancePublication,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct KnowledgePublishPayload {
    capability_id: Id,
    request: MutationRequest,
    claim: MaintenanceClaimKey,
    publication: KnowledgePublication,
    #[serde(default)]
    smart_notes: Vec<KnowledgeAuthorityPayload>,
    #[serde(default)]
    verifications: Vec<KnowledgeAuthorityPayload>,
    #[serde(default)]
    sharing_judgments: Vec<KnowledgeAuthorityPayload>,
    #[serde(default)]
    recall: Option<KnowledgeAuthorityPayload>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct KnowledgeAuthorityPayload {
    capability_id: Id,
    #[serde(default)]
    authority_resource: Option<Id>,
    request: MutationRequest,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CheckpointPayload {
    capability_id: Id,
    request: MutationRequest,
    claim: MaintenanceClaimKey,
    cursor: Cursor,
    available_at_ms: i64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct TerminatePayload {
    capability_id: Id,
    request: MutationRequest,
    claim: MaintenanceClaimKey,
    state: Option<TerminalState>,
    error_code: Option<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RegisterEmbeddingPayload {
    capability_id: Id,
    request: MutationRequest,
    registration: EmbeddingRegistration,
    #[serde(rename = "now_ms")]
    _now_ms: u64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PublishEmbeddingPayload {
    capability_id: Id,
    request: MutationRequest,
    guard: PublicationGuard,
    response: ProviderEmbedding,
    #[serde(rename = "now_ms")]
    _now_ms: u64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RetireEmbeddingPayload {
    capability_id: Id,
    request: MutationRequest,
    registration_id: Id,
    #[serde(rename = "now_ms")]
    _now_ms: u64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CreateShareGrantPayload {
    capability_id: Id,
    draft: ShareGrantDraft,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RevokeShareGrantPayload {
    capability_id: Id,
    owner_scope: Scope,
    grant_id: Id,
    expected_revision: u64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ListShareGrantPayload {
    capability_id: Id,
    owner_scope: Scope,
}

fn mutation_context(
    session: &AuthenticatedSession,
    request: &MutationRequest,
    capability_id: Id,
    resource_id: Id,
    now_ms: u64,
) -> Result<hypermid_memory::AuthContext, Error> {
    context(
        session,
        request.target_scope.clone(),
        capability_operation(request.operation),
        resource_id,
        capability_id,
        now_ms,
    )
}

fn access_context(
    session: &AuthenticatedSession,
    request: &AccessRequest,
    capability_id: Id,
    now_ms: u64,
) -> Result<hypermid_memory::AuthContext, Error> {
    context(
        session,
        request.target_scope.clone(),
        CapabilityOperation::Read,
        request.resource_id.clone(),
        capability_id,
        now_ms,
    )
}

fn context(
    session: &AuthenticatedSession,
    target_scope: Scope,
    operation: CapabilityOperation,
    resource_id: Id,
    capability_id: Id,
    now_ms: u64,
) -> Result<hypermid_memory::AuthContext, Error> {
    Dispatcher
        .context_for_session(
            session,
            AuthorizationRequest {
                claimed_scope: session.bound_scope.clone(),
                target_scope,
                operation,
                resource_id,
                now_ms,
            },
            capability_id,
        )
        .map_err(|error| {
            Error::new(
                error.code,
                error.message,
                error.retryable,
                None,
                Some(EffectState::NotStarted),
            )
            .expect("dispatcher errors satisfy the shared error contract")
        })
}

fn require_trace_scope(
    trace: &Trace,
    session: &AuthenticatedSession,
    actor_scope: &Scope,
    request_trace: &Trace,
) -> Result<(), Error> {
    if actor_scope != &session.bound_scope || request_trace != trace {
        return Err(denied(
            "memory request scope or trace does not match the authenticated envelope",
        ));
    }
    Ok(())
}

fn require_access(
    trace: &Trace,
    session: &AuthenticatedSession,
    request: &AccessRequest,
    operation: GrantOperation,
) -> Result<(), Error> {
    require_trace_scope(trace, session, &request.actor_scope, &request.trace)?;
    if request.operation != operation {
        return Err(denied("memory access operation does not match the route"));
    }
    Ok(())
}

fn require_operation(request: &MutationRequest, operation: Operation) -> Result<(), Error> {
    if request.operation != operation {
        return Err(denied("memory mutation operation does not match the route"));
    }
    Ok(())
}

fn require_job_request(request: &MutationRequest, spec: &MaintenanceJobSpec) -> Result<(), Error> {
    if request.record_id.as_ref() != Some(&spec.id) {
        return Err(denied(
            "maintenance request resource does not match the job",
        ));
    }
    Ok(())
}

fn require_job_id(request: &MutationRequest, job_id: &Id) -> Result<(), Error> {
    if request.record_id.as_ref() != Some(job_id) {
        return Err(denied(
            "maintenance request resource does not match the job",
        ));
    }
    Ok(())
}

fn require_portability_request(request: &MutationRequest) -> Result<(), Error> {
    if request.record_id.is_some() {
        return Err(invalid(
            "portability requests require the memory-portability resource",
        ));
    }
    Ok(())
}

fn record_resource(request: &MutationRequest) -> Result<Id, Error> {
    request
        .record_id
        .clone()
        .ok_or_else(|| invalid("record mutation requires a record id"))
}

fn build_knowledge_authorities(
    session: &AuthenticatedSession,
    values: Vec<KnowledgeAuthorityPayload>,
    operation: Operation,
    now_ms: u64,
) -> Result<Vec<KnowledgePublicationAuthority>, Error> {
    values
        .into_iter()
        .map(|value| build_knowledge_authority(session, value, operation, now_ms))
        .collect()
}

fn build_knowledge_authority(
    session: &AuthenticatedSession,
    value: KnowledgeAuthorityPayload,
    operation: Operation,
    now_ms: u64,
) -> Result<KnowledgePublicationAuthority, Error> {
    if value.request.actor_scope != session.bound_scope {
        return Err(denied(
            "knowledge authority scope does not match the authenticated session",
        ));
    }
    require_operation(&value.request, operation)?;
    let record_id = record_resource(&value.request)?;
    let resource = record_authority_resource(
        session,
        &value.request.target_scope,
        &record_id,
        value.authority_resource,
    )?;
    let context = mutation_context(
        session,
        &value.request,
        value.capability_id,
        resource,
        now_ms,
    )?;
    Ok(KnowledgePublicationAuthority {
        record_id,
        context,
        request: value.request,
    })
}

fn record_authority_resource(
    session: &AuthenticatedSession,
    target_scope: &Scope,
    record_id: &Id,
    requested: Option<Id>,
) -> Result<Id, Error> {
    let Some(requested) = requested else {
        return Ok(record_id.clone());
    };
    if requested.as_str() == record_id.as_str() {
        return Ok(requested);
    }
    if requested.as_str() == "memory-records"
        && session.bound_scope.owner_id == target_scope.owner_id
        && session.bound_scope.project_id == target_scope.project_id
    {
        return Ok(requested);
    }
    Err(denied(
        "record authority resource must be the exact record or the same-project collection",
    ))
}

fn static_id(value: &str) -> Id {
    Id::new(value).expect("static memory resource identifiers are valid")
}

fn parse<T: DeserializeOwned>(value: Value) -> Result<T, Error> {
    serde_json::from_value(value).map_err(|_| invalid("memory request payload is invalid"))
}

fn encode(value: impl serde::Serialize) -> Result<Value, Error> {
    serde_json::to_value(value).map_err(|_| {
        route_error(
            "MEMORY_RESPONSE_FAILED",
            "memory response could not be encoded",
            EffectState::Unknown,
        )
    })
}

fn encode_search(response: SearchResponse) -> Result<Value, Error> {
    let hits = response
        .hits
        .into_iter()
        .map(|hit| {
            let mut readable_scopes = hit
                .candidate
                .readable_scopes
                .into_iter()
                .collect::<Vec<_>>();
            readable_scopes.sort();
            json!({
                "id": hit.candidate.id,
                "scope": hit.candidate.owner_scope,
                "readable_scopes": readable_scopes,
                "content": hit.candidate.content,
                "content_digest": hit.candidate.content_digest,
                "source": hit.candidate.source,
                "kind": hit.candidate.kind,
                "category": hit.candidate.category,
                "status": hit.candidate.status,
                "expires_at_ms": hit.candidate.expires_at_ms,
                "source_time_ms": hit.candidate.source_time_ms,
                "importance": hit.candidate.importance,
                "verification": hit.candidate.verification,
                "provenance": hit.candidate.provenance,
                "contradiction_group": hit.candidate.contradiction_group,
                "decay": hit.candidate.decay,
                "useful_count": hit.candidate.useful_count,
                "not_useful_count": hit.candidate.not_useful_count,
                "scores": hit.scores,
            })
        })
        .collect::<Vec<_>>();
    encode(json!({
        "hits": hits,
        "cursor": response.cursor,
        "suppressed": response.suppressed,
        "degraded": response.degraded,
        "degradation_reason": if response.degraded {
            Some("semantic_unavailable")
        } else {
            None
        },
        "trace": response.trace,
    }))
}

fn invalid(message: &'static str) -> Error {
    route_error("INVALID_REQUEST", message, EffectState::NotStarted)
}

fn denied(message: &'static str) -> Error {
    route_error("AUTHORIZATION_DENIED", message, EffectState::NotStarted)
}

fn route_error(code: &'static str, message: &'static str, effect_state: EffectState) -> Error {
    Error::new(code, message, false, None, Some(effect_state))
        .expect("memory route errors satisfy the shared contract")
}

fn failure(error: Error) -> MemoryRouteResponse {
    MemoryRouteResponse {
        payload: None,
        error: Some(error),
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PrivateIssuePayload { capability_id: Id, origin_session_key: Id, original_actor: Id, memory_mode: String, ttl_ms: u64 }
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PrivateRetirePayload { capability_id: Id, origin_session_key: Id }
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PrivateResolvePayload { capability_id: Id, scope_id: Id, target_scope: Scope }

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct AppIssuePayload {capability_id:Id,app_name:String,manifest_digest:Digest,ttl_ms:u64}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct AppRevokePayload {capability_id:Id,app_name:String,expected_capability_id:Option<Id>}
