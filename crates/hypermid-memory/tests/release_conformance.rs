use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};

use hypermid_core::capability::{
    AuthContext, AuthenticatedPrincipal, AuthorizationRequest, CapabilityGrant,
    CapabilityOperation, PrincipalKind,
};
use hypermid_memory::budget::ModelBudget;
use hypermid_memory::import::ImportState;
use hypermid_memory::maintenance::{MaintenanceJobSpec, MaintenanceKind, TerminalState};
use hypermid_memory::search::{SearchMode, StoredSearchRequest};
use hypermid_memory::{
    scope_digest, AccessRequest, Digest, GrantOperation, Id, LineageEdge, LineageRelation,
    MaintenanceClaimKey, MemoryApi, MemoryStore, MutationRequest, Operation, ProvenanceSpan,
    RecordDraft, RecordKind, RecordStatus, RevisionPrecondition, Scope, SourceKind, SourceSnapshot,
    SummaryDetails, SummaryLevel, Trace, VerificationState,
};
use hypermid_store::authorization::put_grant;
use rusqlite::Connection;
use serde_json::Map;

fn id(value: &str) -> Id {
    Id::new(value).unwrap()
}

fn scope() -> Scope {
    Scope::new(
        id("release-owner"),
        id("release-project"),
        Some(id("release-workspace")),
    )
}

fn principal() -> AuthenticatedPrincipal {
    AuthenticatedPrincipal {
        principal_id: id("release-principal"),
        owner_id: id("release-owner"),
        kind: PrincipalKind::Foreground,
    }
}

fn context(
    target: &Scope,
    operation: CapabilityOperation,
    resource_id: &Id,
    now_ms: u64,
) -> AuthContext {
    AuthContext {
        principal: principal(),
        request: AuthorizationRequest {
            claimed_scope: target.clone(),
            target_scope: target.clone(),
            operation,
            resource_id: resource_id.clone(),
            now_ms,
        },
        capability_id: id("release-capability"),
    }
}

fn trace(suffix: &str) -> Trace {
    Trace::new(
        id(&format!("release-trace-{suffix}")),
        id(&format!("release-request-{suffix}")),
    )
}

fn mutation(
    target: &Scope,
    operation: Operation,
    record_id: &Id,
    category: &str,
    revision: RevisionPrecondition,
    suffix: &str,
) -> MutationRequest {
    MutationRequest {
        operation,
        actor_scope: target.clone(),
        target_scope: target.clone(),
        record_id: Some(record_id.clone()),
        category: Some(category.to_owned()),
        revision,
        trace: trace(suffix),
    }
}

fn access(
    target: &Scope,
    operation: GrantOperation,
    resource_id: &Id,
    category: Option<&str>,
    suffix: &str,
) -> AccessRequest {
    AccessRequest {
        operation,
        actor_scope: target.clone(),
        target_scope: target.clone(),
        resource_id: resource_id.clone(),
        category: category.map(str::to_owned),
        trace: trace(suffix),
    }
}

fn draft(
    target: &Scope,
    record_id: &str,
    kind: RecordKind,
    category: &str,
    content: &str,
) -> RecordDraft {
    RecordDraft {
        id: id(record_id),
        scope: target.clone(),
        kind,
        category: category.to_owned(),
        content: content.to_owned(),
        metadata: Map::new(),
        importance: 0.75,
        confidence: 0.8,
        expires_at_ms: None,
        retention_until_ms: Some(50_000),
        provenance: Vec::new(),
        lineage: Vec::new(),
        smart_predicate: (kind == RecordKind::SmartNote).then(|| {
            serde_json::from_value(serde_json::json!({
                "operator": "all",
                "clauses": [{
                    "field": "record.category",
                    "comparison": "eq",
                    "value": category
                }]
            }))
            .unwrap()
        }),
        summary: None,
    }
}

fn install_capability(path: &std::path::Path, target: &Scope, resources: BTreeSet<Id>) {
    drop(MemoryStore::open(path).unwrap());
    let mut connection = Connection::open(path).unwrap();
    let transaction = connection.transaction().unwrap();
    put_grant(
        &transaction,
        &principal(),
        &CapabilityGrant {
            capability_id: id("release-capability"),
            issuer_owner_id: target.owner_id.clone(),
            principal_id: principal().principal_id,
            claimed_scope: target.clone(),
            target_scope: target.clone(),
            operations: BTreeSet::from([
                CapabilityOperation::Read,
                CapabilityOperation::Append,
                CapabilityOperation::Revise,
                CapabilityOperation::Archive,
                CapabilityOperation::Restore,
                CapabilityOperation::Delete,
                CapabilityOperation::Export,
            ]),
            resources,
            expires_at_ms: 100_000,
        },
    )
    .unwrap();
    transaction.commit().unwrap();
}

#[test]
fn memory_api_release_path_preserves_authoritative_records_and_guards_derivatives() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("release-memory.sqlite3");
    let target = scope();
    let record_ids = [
        "release-fact",
        "release-episode",
        "release-note",
        "release-smart-note",
        "release-anchor",
        "release-summary",
    ];
    let list_id = id("release-list");
    let search_id = id("release-search");
    let job_id = id("release-job");
    let maintenance_queue_id = id("memory-maintenance");
    let diagnostics_id = id("release-diagnostics");
    let export_id = id("release-export");
    let batch_id = id("release-import");
    let portability_id = id("memory-portability");
    let resources: BTreeSet<Id> = record_ids
        .iter()
        .map(|value| id(value))
        .chain([
            list_id.clone(),
            search_id.clone(),
            job_id.clone(),
            maintenance_queue_id.clone(),
            diagnostics_id.clone(),
            portability_id.clone(),
        ])
        .collect();
    install_capability(&path, &target, resources.clone());
    let mut api = MemoryApi::open(&path).unwrap();
    assert!(api.health().durable);
    assert!(api.health().lexical_available);

    let raw_source = "byte-exact source: alpha βeta";
    let source = SourceSnapshot {
        source_id: id("release-source"),
        owner_scope_digest: scope_digest(&target),
        kind: SourceKind::Message,
        source_digest: Digest::sha256(raw_source.as_bytes()),
        locator: Some("session-release:message-1".to_owned()),
        captured_content: Some(raw_source.to_owned()),
        capture_method: "journal-copy".to_owned(),
        observed_at_ms: 100,
    };
    let mut fact = draft(
        &target,
        "release-fact",
        RecordKind::Fact,
        "release",
        "alpha fact",
    );
    fact.provenance.push(ProvenanceSpan {
        source_id: source.source_id.clone(),
        span_start: None,
        span_end: None,
        quoted_digest: Some(source.source_digest),
    });
    let created_fact = api
        .create_record(
            &context(&target, CapabilityOperation::Append, &fact.id, 1_000),
            &mutation(
                &target,
                Operation::Create,
                &fact.id,
                &fact.category,
                RevisionPrecondition::MustNotExist,
                "fact-create",
            ),
            &fact,
            std::slice::from_ref(&source),
            1_000,
        )
        .unwrap();

    let kinds = [
        ("release-episode", RecordKind::Episode, "episode memory"),
        ("release-note", RecordKind::Note, "note memory"),
        (
            "release-smart-note",
            RecordKind::SmartNote,
            "smart note memory",
        ),
        ("release-anchor", RecordKind::Anchor, "chronological anchor"),
    ];
    let mut created = HashMap::new();
    for (offset, (record_id, kind, content)) in kinds.into_iter().enumerate() {
        let item = draft(&target, record_id, kind, "release", content);
        let receipt = api
            .create_record(
                &context(
                    &target,
                    CapabilityOperation::Append,
                    &item.id,
                    1_100 + offset as u64,
                ),
                &mutation(
                    &target,
                    Operation::Create,
                    &item.id,
                    &item.category,
                    RevisionPrecondition::MustNotExist,
                    &format!("{record_id}-create"),
                ),
                &item,
                &[],
                1_100 + offset as u64,
            )
            .unwrap();
        created.insert(record_id, receipt.record);
    }

    let mut summary = draft(
        &target,
        "release-summary",
        RecordKind::Summary,
        "release",
        "summary derived from alpha fact",
    );
    summary.lineage.push(LineageEdge {
        parent_id: created_fact.record.id.clone(),
        relation: LineageRelation::DerivedFrom,
        parent_revision_digest: created_fact.record.current.digest,
    });
    summary.summary = Some(SummaryDetails {
        input_set_digest: Digest::sha256(created_fact.record.current.digest.as_bytes()),
        level: SummaryLevel::Standard,
        decay_half_life_ms: Some(60_000),
    });
    let created_summary = api
        .create_record(
            &context(&target, CapabilityOperation::Append, &summary.id, 1_200),
            &mutation(
                &target,
                Operation::Create,
                &summary.id,
                &summary.category,
                RevisionPrecondition::MustNotExist,
                "summary-create",
            ),
            &summary,
            &[],
            1_200,
        )
        .unwrap();
    assert_eq!(created_summary.record.kind, RecordKind::Summary);

    let note = created["release-note"].clone();
    let archived = api
        .archive_record(
            &context(&target, CapabilityOperation::Archive, &note.id, 1_300),
            &mutation(
                &target,
                Operation::Archive,
                &note.id,
                &note.category,
                RevisionPrecondition::Match(note.current.digest),
                "note-archive",
            ),
            1_300,
        )
        .unwrap();
    assert_eq!(archived.record.status, RecordStatus::Archived);
    let restored = api
        .restore_record(
            &context(&target, CapabilityOperation::Restore, &note.id, 1_400),
            &mutation(
                &target,
                Operation::Restore,
                &note.id,
                &note.category,
                RevisionPrecondition::Match(archived.record.current.digest),
                "note-restore",
            ),
            1_400,
        )
        .unwrap();
    assert_eq!(restored.record.status, RecordStatus::Active);

    let episode = created["release-episode"].clone();
    let verified = api
        .verify_record(
            &context(&target, CapabilityOperation::Revise, &episode.id, 1_500),
            &mutation(
                &target,
                Operation::Verify,
                &episode.id,
                &episode.category,
                RevisionPrecondition::Match(episode.current.digest),
                "episode-verify",
            ),
            VerificationState::Supported,
            0.95,
            Some(&source.source_id),
            1_500,
        )
        .unwrap();
    assert_eq!(verified.record.verification, VerificationState::Supported);

    let anchor = created["release-anchor"].clone();
    let mut changed_anchor = draft(
        &target,
        "release-anchor",
        RecordKind::Anchor,
        "release",
        "changed chronological anchor",
    );
    changed_anchor
        .metadata
        .insert("attempt".to_owned(), true.into());
    let anchor_error = api
        .update_record(
            &context(&target, CapabilityOperation::Revise, &anchor.id, 1_600),
            &mutation(
                &target,
                Operation::Update,
                &anchor.id,
                &anchor.category,
                RevisionPrecondition::Match(anchor.current.digest),
                "anchor-update",
            ),
            &changed_anchor,
            &[],
            1_600,
        )
        .unwrap_err();
    assert_eq!(anchor_error.code, "IMMUTABLE_ANCHOR");

    let stale_digest = created_fact.record.current.digest;
    fact.content = "corrected alpha fact".to_owned();
    let revised_fact = api
        .update_record(
            &context(&target, CapabilityOperation::Revise, &fact.id, 1_700),
            &mutation(
                &target,
                Operation::Update,
                &fact.id,
                &fact.category,
                RevisionPrecondition::Match(stale_digest),
                "fact-update",
            ),
            &fact,
            std::slice::from_ref(&source),
            1_700,
        )
        .unwrap();
    assert_eq!(revised_fact.record.current.number, 2);
    assert!(revised_fact.invalidated_ids.contains(&summary.id));
    let stale_error = api
        .update_record(
            &context(&target, CapabilityOperation::Revise, &fact.id, 1_701),
            &mutation(
                &target,
                Operation::Update,
                &fact.id,
                &fact.category,
                RevisionPrecondition::Match(stale_digest),
                "fact-stale-update",
            ),
            &fact,
            std::slice::from_ref(&source),
            1_701,
        )
        .unwrap_err();
    assert_eq!(stale_error.code, "REVISION_CONFLICT");

    let summary_access = access(
        &target,
        GrantOperation::Read,
        &summary.id,
        Some("release"),
        "summary-get",
    );
    let summary_view = api
        .record(
            &context(&target, CapabilityOperation::Read, &summary.id, 1_800),
            &summary_access,
        )
        .unwrap();
    assert_eq!(summary_view.record.unwrap().status, RecordStatus::Stale);

    let list_access = access(
        &target,
        GrantOperation::Read,
        &list_id,
        Some("release"),
        "record-list",
    );
    let page = api
        .list_records(
            &context(&target, CapabilityOperation::Read, &list_id, 1_900),
            &list_access,
            Some("release"),
            None,
            100,
        )
        .unwrap();
    assert_eq!(page.records.len(), 6);

    let search_trace = trace("search");
    let search_access = AccessRequest {
        operation: GrantOperation::Search,
        actor_scope: target.clone(),
        target_scope: target.clone(),
        resource_id: search_id.clone(),
        category: Some("release".to_owned()),
        trace: search_trace.clone(),
    };
    let search = api
        .search(
            &context(&target, CapabilityOperation::Read, &search_id, 2_000),
            &search_access,
            &StoredSearchRequest {
                query: "corrected alpha".to_owned(),
                scope: target.clone(),
                mode: SearchMode::Lexical,
                limit: 10,
                candidate_limit_per_source: 10,
                include_archived: false,
                visible_digests: HashSet::new(),
                query_vector: None,
                vector_fingerprint: None,
                semantic_required: false,
                now_ms: 2_000,
                from_ms: None,
                to_ms: None,
                sources: HashSet::from(["memory".to_owned()]),
                kinds: HashSet::from(["fact".to_owned()]),
                categories: HashSet::from(["release".to_owned()]),
                cursor: None,
                trace: search_trace,
            },
        )
        .unwrap();
    assert_eq!(search.hits.len(), 1);
    assert_eq!(search.hits[0].candidate.id, fact.id.to_string());

    let maintenance_request = MutationRequest {
        operation: Operation::Summarize,
        actor_scope: target.clone(),
        target_scope: target.clone(),
        record_id: Some(job_id.clone()),
        category: None,
        revision: RevisionPrecondition::MustNotExist,
        trace: trace("maintenance"),
    };
    let job = MaintenanceJobSpec {
        id: job_id.clone(),
        kind: MaintenanceKind::RefreshSummaries,
        target_scope: target.clone(),
        actor_scope: target.clone(),
        required_operation: Operation::Summarize,
        input_cursor: page.cursor,
        input_digest: Digest::sha256(b"release-input"),
        config_digest: Digest::sha256(b"release-config"),
        budget: ModelBudget {
            max_items: 10,
            max_input_tokens: 1_000,
            max_output_tokens: 1_000,
            max_requests: 5,
            max_cost_units: 100,
            max_retries: 1,
            max_wall_ms: 60_000,
        },
        available_at_ms: 2_100,
        created_at_ms: 2_100,
    };
    api.enqueue_summary(
        &context(&target, CapabilityOperation::Revise, &job_id, 2_100),
        &maintenance_request,
        &job,
    )
    .unwrap();
    let claim_request = MutationRequest {
        operation: Operation::Summarize,
        actor_scope: target.clone(),
        target_scope: target.clone(),
        record_id: None,
        category: None,
        revision: RevisionPrecondition::MustNotExist,
        trace: trace("maintenance-claim"),
    };
    let claim = api
        .claim_maintenance(
            &context(
                &target,
                CapabilityOperation::Revise,
                &maintenance_queue_id,
                2_200,
            ),
            &claim_request,
            &id("release-worker"),
            5_000,
        )
        .unwrap()
        .unwrap();
    assert_eq!(claim.job_id, job_id);
    let wrong_claim_key = MaintenanceClaimKey {
        job_id: claim.job_id.clone(),
        holder_id: claim.holder_id.clone(),
        fencing_token: "0".repeat(64),
    };
    let refused = api
        .terminate_maintenance(
            &context(&target, CapabilityOperation::Revise, &job_id, 2_250),
            &maintenance_request,
            &wrong_claim_key,
            TerminalState::Abandoned,
            Some("WRONG_FENCE"),
        )
        .unwrap_err();
    assert_eq!(refused.code, "CLAIM_NOT_FOUND");
    assert_eq!(
        api.maintenance_status(
            &context(&target, CapabilityOperation::Revise, &job_id, 2_260),
            &maintenance_request,
            &job_id,
        )
        .unwrap()
        .state,
        "claimed"
    );
    let claim_key = MaintenanceClaimKey {
        job_id: claim.job_id,
        holder_id: claim.holder_id,
        fencing_token: claim.fencing_token,
    };
    api.terminate_maintenance(
        &context(&target, CapabilityOperation::Revise, &job_id, 2_300),
        &maintenance_request,
        &claim_key,
        TerminalState::Abandoned,
        Some("RELEASE_CONFORMANCE_COMPLETE"),
    )
    .unwrap();
    let status = api
        .maintenance_status(
            &context(&target, CapabilityOperation::Revise, &job_id, 2_400),
            &maintenance_request,
            &job_id,
        )
        .unwrap();
    assert_eq!(status.state, "abandoned");

    let diagnostics_access = access(
        &target,
        GrantOperation::Read,
        &diagnostics_id,
        None,
        "diagnostics",
    );
    let diagnostics = api
        .diagnostics(
            &context(&target, CapabilityOperation::Read, &diagnostics_id, 2_450),
            &diagnostics_access,
        )
        .unwrap();
    assert_eq!(diagnostics.record_count, 6);
    assert_eq!(diagnostics.stale_record_count, 1);
    assert_eq!(diagnostics.embedding_count, 0);
    assert_eq!(diagnostics.queued_job_count, 0);
    assert_eq!(diagnostics.active_lease_count, 0);

    let tombstoned = api
        .delete_record(
            &context(&target, CapabilityOperation::Delete, &fact.id, 2_500),
            &mutation(
                &target,
                Operation::Delete,
                &fact.id,
                &fact.category,
                RevisionPrecondition::Match(revised_fact.record.current.digest),
                "fact-delete",
            ),
            2_500,
        )
        .unwrap();
    assert_eq!(tombstoned.record.status, RecordStatus::Tombstoned);

    let export_request = MutationRequest {
        operation: Operation::Export,
        actor_scope: target.clone(),
        target_scope: target.clone(),
        record_id: None,
        category: Some("release".to_owned()),
        revision: RevisionPrecondition::MustNotExist,
        trace: trace("export"),
    };
    let bundle = api
        .export_scope(
            &context(&target, CapabilityOperation::Export, &portability_id, 2_600),
            &export_request,
            export_id,
            false,
            2_600,
        )
        .unwrap();
    bundle.verify().unwrap();
    assert_eq!(bundle.manifest.record_count, 6);
    assert!(!bundle.to_jsonl().unwrap().is_empty());

    drop(api);
    let mut connection = Connection::open(&path).unwrap();
    let transaction = connection.transaction().unwrap();
    let recovered = hypermid_memory::provenance::recover_source_bytes(
        &transaction,
        &source.source_id,
        None,
        None,
    )
    .unwrap();
    assert_eq!(recovered.as_bytes(), raw_source.as_bytes());
    transaction.rollback().unwrap();

    let restored_path = directory.path().join("restored-memory.sqlite3");
    install_capability(&restored_path, &target, resources);
    let mut restored_api = MemoryApi::open(&restored_path).unwrap();
    let import_request = MutationRequest {
        operation: Operation::Import,
        actor_scope: target.clone(),
        target_scope: target.clone(),
        record_id: None,
        category: Some("release".to_owned()),
        revision: RevisionPrecondition::MustNotExist,
        trace: trace("import"),
    };
    let staged = restored_api
        .stage_import(
            &context(&target, CapabilityOperation::Append, &portability_id, 2_700),
            &import_request,
            batch_id,
            &bundle,
            target.clone(),
            BTreeMap::new(),
            2_700,
        )
        .unwrap();
    assert_eq!(staged.state, ImportState::Validated);
    let applied = restored_api
        .apply_import(
            &context(&target, CapabilityOperation::Append, &portability_id, 2_800),
            &import_request,
            &staged,
            &bundle,
            2_800,
        )
        .unwrap();
    assert_eq!(applied.state, ImportState::Applied);

    let restored_page = restored_api
        .list_records(
            &context(&target, CapabilityOperation::Read, &list_id, 2_900),
            &list_access,
            Some("release"),
            None,
            100,
        )
        .unwrap();
    assert_eq!(restored_page.records.len(), 6);
    let restored_fact = restored_page
        .records
        .iter()
        .find(|record| record.id == fact.id)
        .unwrap();
    assert_eq!(
        restored_fact.current.digest,
        tombstoned.record.current.digest
    );
    assert_eq!(
        restored_fact.current.content,
        tombstoned.record.current.content
    );
    assert_eq!(restored_fact.status, RecordStatus::Tombstoned);
    drop(restored_api);

    let mut restored_connection = Connection::open(&restored_path).unwrap();
    let restored_transaction = restored_connection.transaction().unwrap();
    let restored_source = hypermid_memory::provenance::recover_source_bytes(
        &restored_transaction,
        &source.source_id,
        None,
        None,
    )
    .unwrap();
    assert_eq!(restored_source.as_bytes(), raw_source.as_bytes());
    restored_transaction.rollback().unwrap();
}
