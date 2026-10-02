use hypermid_context::reduction_store::{ReductionState, ReductionStore};
use hypermid_contracts::{Cursor, Digest, Id, Scope, Trace};
use hypermid_core::reduction::{
    BoundaryKind, ContentClass, ContextRegion, ReductionBoundary, ReductionItem,
    ReductionProtection, ReductionRequest, ReductionStatus, ReductionTarget, ToolArcState,
};
use std::collections::BTreeSet;

fn id(value: &str) -> Id {
    Id::new(value).unwrap()
}

fn scope() -> Scope {
    Scope::new(id("owner-1"), id("project-1"), None)
}

fn item(
    tag: u64,
    class: ContentClass,
    region: ContextRegion,
    call_id: Option<&str>,
    arc: ToolArcState,
    protections: &[ReductionProtection],
) -> ReductionItem {
    ReductionItem {
        item_id: id(&format!("item-{tag}")),
        source_digest: Digest::sha256(format!("authoritative-{tag}")),
        scope: scope(),
        session_id: id("session-1"),
        cursor: Cursor::new(1, tag).unwrap(),
        reclaim_tag: tag,
        content_class: class,
        region,
        source_bytes: 1_000 + tag,
        estimated_tokens: 200 + tag,
        call_ids: call_id.into_iter().map(id).collect(),
        tool_arc_state: arc,
        protections: protections.iter().copied().collect(),
    }
}

fn request(key: &str, tags: &[u64]) -> ReductionRequest {
    ReductionRequest {
        scope: scope(),
        session_id: id("session-1"),
        expected_cursor: Cursor::new(1, 9).unwrap(),
        idempotency_key: id(key),
        targets: tags
            .iter()
            .map(|tag| ReductionTarget::new(*tag, *tag).unwrap())
            .collect(),
        trace: Trace::new(id("trace-1"), id(&format!("request-{key}"))),
    }
}

fn boundary(kind: BoundaryKind) -> ReductionBoundary {
    ReductionBoundary {
        kind,
        reason_code: match kind {
            BoundaryKind::TailSafe => "turn_boundary",
            BoundaryKind::HardBoundary => "cache_expiry",
            BoundaryKind::Deferred => "stable_cache",
            BoundaryKind::PressureRefusal => "pressure_refusal",
        }
        .to_owned(),
        cache_generation: 3,
    }
}

#[test]
fn reduction_is_partial_idempotent_tool_arc_safe_and_source_recoverable() {
    let items = vec![
        item(
            1,
            ContentClass::UserRequest,
            ContextRegion::Tail,
            None,
            ToolArcState::None,
            &[ReductionProtection::LatestUserRequest],
        ),
        item(
            2,
            ContentClass::ToolCall,
            ContextRegion::Tail,
            Some("call-open"),
            ToolArcState::Open,
            &[],
        ),
        item(
            3,
            ContentClass::ToolResult,
            ContextRegion::Tail,
            Some("call-open"),
            ToolArcState::Open,
            &[],
        ),
        item(
            4,
            ContentClass::AssistantProse,
            ContextRegion::Delta,
            None,
            ToolArcState::None,
            &[],
        ),
        item(
            5,
            ContentClass::AssistantProse,
            ContextRegion::Tail,
            None,
            ToolArcState::None,
            &[],
        ),
        item(
            6,
            ContentClass::ToolCall,
            ContextRegion::Delta,
            Some("call-complete"),
            ToolArcState::Complete,
            &[],
        ),
        item(
            7,
            ContentClass::ToolResult,
            ContextRegion::Delta,
            Some("call-complete"),
            ToolArcState::Complete,
            &[],
        ),
        item(
            9,
            ContentClass::ProviderFraming,
            ContextRegion::Baseline,
            None,
            ToolArcState::None,
            &[ReductionProtection::ProviderFraming],
        ),
    ];
    let mut store = ReductionStore::new();
    let command = request("reduce-1", &[1, 2, 4, 5, 6, 8, 9]);
    let result = store
        .submit(
            command.clone(),
            Cursor::new(1, 10).unwrap(),
            &items,
            boundary(BoundaryKind::TailSafe),
        )
        .unwrap();
    let by_tag = result
        .outcomes
        .iter()
        .map(|outcome| (outcome.tag, outcome))
        .collect::<std::collections::BTreeMap<_, _>>();

    assert_eq!(by_tag[&1].reason_code, "latest_user_request");
    assert_eq!(by_tag[&2].reason_code, "active_tool_arc");
    assert_eq!(by_tag[&4].status, ReductionStatus::Queued);
    assert_eq!(by_tag[&5].status, ReductionStatus::Applied);
    assert_eq!(by_tag[&6].reason_code, "incomplete_tool_arc");
    assert_eq!(by_tag[&8].reason_code, "unknown_tag");
    assert_eq!(by_tag[&9].reason_code, "provider_framing");
    let marker = by_tag[&5].marker.as_ref().unwrap();
    assert_eq!(marker.recovery.item_id, id("item-5"));
    assert_eq!(marker.recovery.reclaim_tag, 5);
    assert_eq!(marker.source_digest, Digest::sha256("authoritative-5"));

    let replay = store
        .submit(
            command,
            Cursor::new(1, 10).unwrap(),
            &items,
            boundary(BoundaryKind::HardBoundary),
        )
        .unwrap();
    assert_eq!(replay, result);

    let applied = store
        .apply_queued(
            &scope(),
            &id("session-1"),
            &items,
            boundary(BoundaryKind::HardBoundary),
        )
        .unwrap();
    assert_eq!(applied.len(), 1);
    assert_eq!(
        applied[0]
            .outcomes
            .iter()
            .find(|outcome| outcome.tag == 4)
            .unwrap()
            .status,
        ReductionStatus::Applied
    );

    let state_json = serde_json::to_string(&store.state()).unwrap();
    assert!(!state_json.contains("authoritative-5"));
    let restored_state: ReductionState = serde_json::from_str(&state_json).unwrap();
    let restored = ReductionStore::from_state(restored_state).unwrap();
    assert_eq!(restored.state(), store.state());
    assert_eq!(
        store
            .submit(
                request("reduce-2", &[5]),
                Cursor::new(1, 10).unwrap(),
                &items,
                boundary(BoundaryKind::HardBoundary),
            )
            .unwrap()
            .outcomes[0]
            .status,
        ReductionStatus::AlreadyApplied
    );

    let protections: BTreeSet<_> = items[0].protections.clone();
    assert!(protections.contains(&ReductionProtection::LatestUserRequest));
}
