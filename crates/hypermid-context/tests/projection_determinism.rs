use hypermid_context::projector::DeterministicProjector;
use hypermid_contracts::{Cursor, Digest, Id, Scope};
use hypermid_core::projection::{
    projection_bytes, ContextMode, ContextPart, ContextRole, ProjectionBudgetInputs,
    ProjectionItem, ProjectionRequest, RegionKind,
};

fn id(value: &str) -> Id {
    Id::new(value).unwrap()
}

#[test]
fn projection_is_byte_identical_and_preserves_source_input() {
    let items = vec![
        ProjectionItem {
            item_id: id("item-1"),
            cursor: Cursor::new(1, 1).unwrap(),
            role: ContextRole::System,
            parts: vec![ContextPart::text(id("part-1"), "fixed system prefix")],
            region: RegionKind::Baseline,
            token_mass: 4,
        },
        ProjectionItem {
            item_id: id("item-2"),
            cursor: Cursor::new(1, 2).unwrap(),
            role: ContextRole::User,
            parts: vec![ContextPart::text(id("part-2"), "latest request")],
            region: RegionKind::Tail,
            token_mass: 3,
        },
    ];
    let source_before = serde_json::to_vec(&items).unwrap();
    let request = ProjectionRequest {
        scope: Scope::new(id("owner-1"), id("project-1"), None),
        session_id: id("session-1"),
        source_cursor: Cursor::new(1, 2).unwrap(),
        source_digest: Digest::sha256(b"journal-range"),
        generation: 7,
        policy_revision: 3,
        mode: ContextMode::Primary,
        provider_profile_digest: Digest::sha256(b"provider-v1"),
        budget_inputs: ProjectionBudgetInputs {
            context_window_tokens: 100,
            reserved_output_tokens: 20,
            max_input_tokens: 80,
            max_items: 10,
            max_images: 0,
        },
        created_at: "2026-10-02T12:00:00Z".to_owned(),
        items,
        summaries: Vec::new(),
    };

    let projector = DeterministicProjector;
    let first = projector.project(&request).unwrap();
    let second = projector.project(&request).unwrap();
    assert_eq!(
        projection_bytes(&first).unwrap(),
        projection_bytes(&second).unwrap()
    );
    assert_eq!(first.selected_item_ids, vec![id("item-1"), id("item-2")]);
    assert_eq!(first.baseline.bytes, second.baseline.bytes);
    assert_eq!(serde_json::to_vec(&request.items).unwrap(), source_before);
}
