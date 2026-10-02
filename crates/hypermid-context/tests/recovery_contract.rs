use hypermid_context::journal::{Journal, RawSourceJournal};
use hypermid_context::recovery_store::{rebuild_from_journal, RecoveryStore};
use hypermid_contracts::{Cursor, Digest, Id, Scope};
use hypermid_core::history::{
    ContextPart as HistoryPart, IngestRequest, PartKind as HistoryPartKind, PendingContextItem,
    Role,
};
use hypermid_core::projection::{
    project, ContextMode, ContextPart, ContextRole, PartKind, ProjectionBudgetInputs,
    ProjectionItem, ProjectionRequest, RegionKind,
};
use hypermid_core::provider::{BudgetConfidence, ModelBudget};
use hypermid_core::recovery::{LastKnownGood, RecoveryBinding, ReplayRequest};
use std::fs;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

struct TestDirectory(PathBuf);

impl TestDirectory {
    fn new() -> Self {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let path =
            std::env::temp_dir().join(format!("hypermid-recovery-{}-{nonce}", std::process::id()));
        fs::create_dir_all(&path).unwrap();
        Self(path)
    }
}

impl Drop for TestDirectory {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn id(value: &str) -> Id {
    Id::new(value).unwrap()
}

#[test]
fn last_known_good_is_bound_budgeted_quarantined_and_rebuildable() {
    let directory = TestDirectory::new();
    let scope = Scope::new(id("owner-1"), id("project-1"), None);
    let session_id = id("session-1");
    let source_bytes = br#"{"role":"user","content":"recover this"}"#.to_vec();
    let source_digest = Digest::sha256(&source_bytes);
    let sources = RawSourceJournal::open(directory.0.join("raw")).unwrap();
    sources
        .append(
            scope.clone(),
            session_id.clone(),
            id("event-1"),
            source_bytes.clone(),
        )
        .unwrap();
    let journal = Journal::open(
        directory.0.join("journal"),
        scope.clone(),
        session_id.clone(),
        1,
    )
    .unwrap();
    let text = "recover this";
    let item = journal
        .append(IngestRequest {
            expected_cursor: Cursor::new(1, 0).unwrap(),
            idempotency_key: id("append-1"),
            item: PendingContextItem {
                item_id: id("item-1"),
                source_event_id: id("event-1"),
                source_digest,
                scope: scope.clone(),
                session_id: session_id.clone(),
                role: Role::User,
                parts: vec![HistoryPart {
                    part_id: id("part-1"),
                    kind: HistoryPartKind::Text,
                    content_digest: Digest::sha256(text.as_bytes()),
                    text: Some(text.into()),
                    call_id: None,
                    tool_name: None,
                    arguments_json: None,
                    result_json: None,
                    media_type: None,
                    source_uri: None,
                    width: None,
                    height: None,
                    metadata: None,
                }],
                relations: vec![],
                created_at: "2026-10-02T12:00:00Z".into(),
                recoverable: true,
                tombstone: false,
            },
            source_snapshot: Some(source_bytes.clone()),
        })
        .unwrap();

    let provider_profile_digest = Digest::sha256(b"provider-profile");
    let projection = project(&ProjectionRequest {
        scope: scope.clone(),
        session_id: session_id.clone(),
        source_cursor: item.cursor,
        source_digest: journal
            .source_digest(
                hypermid_core::history::JournalRange::new(item.cursor, item.cursor).unwrap(),
            )
            .unwrap(),
        generation: 3,
        policy_revision: 5,
        mode: ContextMode::Primary,
        provider_profile_digest,
        budget_inputs: ProjectionBudgetInputs {
            context_window_tokens: 1_000,
            reserved_output_tokens: 100,
            max_input_tokens: 900,
            max_items: 10,
            max_images: 0,
        },
        created_at: "2026-10-02T12:00:01Z".into(),
        items: vec![ProjectionItem {
            item_id: item.item_id.clone(),
            cursor: item.cursor,
            role: ContextRole::User,
            parts: vec![ContextPart {
                part_id: id("part-1"),
                kind: PartKind::Text,
                content_digest: Digest::sha256(text.as_bytes()),
                text: Some(text.into()),
                call_id: None,
                tool_name: None,
                arguments_json: None,
                result_json: None,
                media_type: None,
                source_uri: None,
                width: None,
                height: None,
                metadata: None,
            }],
            region: RegionKind::Tail,
            token_mass: 4,
        }],
        summaries: vec![],
    })
    .unwrap();
    let model_budget = ModelBudget {
        context_window_tokens: 1_000,
        reserved_output_tokens: 100,
        max_input_tokens: 900,
        max_items: 10,
        max_images: 0,
        baseline_tokens: 0,
        delta_tokens: 0,
        tail_tokens: 4,
        confidence: BudgetConfidence::Measured,
    };
    let record =
        LastKnownGood::capture(&projection, model_budget.clone(), "2026-10-02T12:00:02Z").unwrap();
    let store = RecoveryStore::open(directory.0.join("recovery")).unwrap();
    store.publish(&record).unwrap();
    let request = ReplayRequest {
        binding: record.binding.clone(),
        input_tokens: 4,
        item_count: 1,
        image_count: 0,
    };
    assert_eq!(store.replay(&request).unwrap(), projection);

    let mut incompatible = record.binding.clone();
    incompatible.provider_profile_digest = Digest::sha256(b"another-provider");
    let refused = store
        .replay(&ReplayRequest {
            binding: incompatible,
            input_tokens: 4,
            item_count: 1,
            image_count: 0,
        })
        .unwrap_err();
    assert_eq!(refused.code, "RECOVERY_BINDING_MISMATCH");
    assert!(refused.retryable);

    fs::write(
        directory.0.join("recovery/last_known_good.json"),
        b"{partial",
    )
    .unwrap();
    assert!(store.load().unwrap().is_none());
    let quarantine = store.quarantine_entries().unwrap();
    assert_eq!(quarantine.len(), 1);
    assert_eq!(quarantine[0].byte_length, 8);

    let rebuilt = rebuild_from_journal(&journal, &sources).unwrap();
    assert_eq!(rebuilt.cursor, item.cursor);
    assert_eq!(rebuilt.items[0].source_bytes, source_bytes);
    assert_eq!(rebuilt.source_digest, record.binding.source_digest);

    let _binding_type_check: RecoveryBinding = request.binding;
}
