use hypermid_context::serializer::HostSerializer;
use hypermid_contracts::{Cursor, Digest, Id, Scope};
use hypermid_core::projection::{
    project, ContextMode, ContextPart, ContextRole, PartKind, ProjectionBudgetInputs,
    ProjectionItem, ProjectionRequest, RegionKind,
};
use hypermid_core::provider::{
    ImageAccounting, ProviderCapabilities, ProviderGeneration, ProviderSerializationError,
};
use std::collections::BTreeSet;

fn id(value: &str) -> Id {
    Id::new(value).unwrap()
}

fn tool_part(part_id: &str, kind: PartKind, call_id: &str, body: &str) -> ContextPart {
    let fields = if kind == PartKind::ToolCall {
        vec![call_id.as_bytes(), b"lookup".as_slice(), body.as_bytes()]
    } else {
        vec![call_id.as_bytes(), body.as_bytes()]
    };
    let mut material = Vec::new();
    for field in fields {
        material.extend_from_slice(&(field.len() as u64).to_be_bytes());
        material.extend_from_slice(field);
    }
    ContextPart {
        part_id: id(part_id),
        kind,
        content_digest: Digest::sha256(material),
        text: None,
        call_id: Some(id(call_id)),
        tool_name: (kind == PartKind::ToolCall).then(|| "lookup".to_owned()),
        arguments_json: (kind == PartKind::ToolCall).then(|| body.to_owned()),
        result_json: (kind == PartKind::ToolResult).then(|| body.to_owned()),
        media_type: None,
        source_uri: None,
        width: None,
        height: None,
        metadata: None,
    }
}

#[test]
fn host_serialization_preserves_valid_tool_pair_and_profile_generation() {
    let capabilities = ProviderCapabilities {
        profile_id: id("provider-1"),
        context_window_tokens: 100,
        reserved_output_tokens: 20,
        roles: BTreeSet::from([ContextRole::Assistant, ContextRole::Tool]),
        part_kinds: BTreeSet::from([PartKind::ToolCall, PartKind::ToolResult]),
        requires_tool_adjacency: true,
        supports_reasoning: false,
        supports_cache_boundaries: false,
        max_cache_boundaries: 0,
        max_images: 0,
        image_accounting: ImageAccounting::Tokens,
    };
    let profile = capabilities.into_profile().unwrap();
    let request = ProjectionRequest {
        scope: Scope::new(id("owner-1"), id("project-1"), None),
        session_id: id("session-1"),
        source_cursor: Cursor::new(1, 2).unwrap(),
        source_digest: Digest::sha256(b"journal-range"),
        generation: 2,
        policy_revision: 1,
        mode: ContextMode::Primary,
        provider_profile_digest: profile.profile_digest,
        budget_inputs: ProjectionBudgetInputs {
            context_window_tokens: 100,
            reserved_output_tokens: 20,
            max_input_tokens: 80,
            max_items: 10,
            max_images: 0,
        },
        created_at: "2026-10-02T12:00:00Z".to_owned(),
        items: vec![
            ProjectionItem {
                item_id: id("item-1"),
                cursor: Cursor::new(1, 1).unwrap(),
                role: ContextRole::Assistant,
                parts: vec![tool_part("part-1", PartKind::ToolCall, "call-1", "{}")],
                region: RegionKind::Tail,
                token_mass: 2,
            },
            ProjectionItem {
                item_id: id("item-2"),
                cursor: Cursor::new(1, 2).unwrap(),
                role: ContextRole::Tool,
                parts: vec![tool_part(
                    "part-2",
                    PartKind::ToolResult,
                    "call-1",
                    "{\"ok\":true}",
                )],
                region: RegionKind::Tail,
                token_mass: 2,
            },
        ],
        summaries: Vec::new(),
    };
    let projection = project(&request).unwrap();
    let previous = ProviderGeneration {
        generation: 1,
        profile_digest: Digest::sha256(b"old"),
    };
    let serialized = HostSerializer
        .serialize(&projection, &profile, Some(&previous))
        .unwrap();
    assert_eq!(
        serialized.blocks[0].parts[0].call_id,
        serialized.blocks[1].parts[0].call_id
    );
    assert_ne!(serialized.serialized_digest, Digest::sha256([]));

    let mut orphan = projection;
    orphan.blocks.remove(0);
    assert_eq!(
        HostSerializer.serialize(&orphan, &profile, None),
        Err(ProviderSerializationError::OrphanToolResult)
    );
}
