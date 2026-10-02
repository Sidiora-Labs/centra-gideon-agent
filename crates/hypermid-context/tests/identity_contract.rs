use hypermid_context::identity_store::{IdentityStore, IdentityStoreError};
use hypermid_contracts::{Cursor, Digest, Id, Scope};
use hypermid_core::identity::{
    ContextIdentity, IdentityKind, IdentityRelation, RelationKind, SourceIdentity,
};

fn id(value: &str) -> Id {
    Id::new(value).unwrap()
}

fn scope(workspace: Option<&str>) -> Scope {
    Scope::new(id("owner-1"), id("project-1"), workspace.map(id))
}

fn source(event: &str, body: &str) -> SourceIdentity {
    SourceIdentity::new(id(event), Digest::sha256(body))
}

fn journal_item(
    identity_id: &str,
    source_event_id: &str,
    body: &str,
    item_scope: Scope,
) -> ContextIdentity {
    ContextIdentity::new(
        id(identity_id),
        IdentityKind::JournalItem,
        item_scope,
        id("session-1"),
        Some(source(source_event_id, body)),
        Vec::new(),
    )
    .unwrap()
}

#[test]
fn acknowledged_source_replay_retains_the_first_item_identity_after_restart() {
    let bound_scope = scope(Some("workspace-1"));
    let mut store = IdentityStore::new();
    store
        .bind_session(id("session-1"), bound_scope.clone())
        .unwrap();
    let accepted = store
        .register_source_item(journal_item(
            "item-1",
            "gideon-event-7",
            "authoritative bytes",
            bound_scope.clone(),
        ))
        .unwrap();
    assert_eq!(accepted.identity_id, id("item-1"));

    let state_bytes = serde_json::to_vec(&store.state()).unwrap();
    let state = serde_json::from_slice(&state_bytes).unwrap();
    let mut restored = IdentityStore::from_state(state).unwrap();
    let replay = restored
        .register_source_item(journal_item(
            "item-retry",
            "gideon-event-7",
            "authoritative bytes",
            bound_scope,
        ))
        .unwrap();
    assert_eq!(replay.identity_id, id("item-1"));
}

#[test]
fn explicit_relations_preserve_identity_across_edits_forks_and_derived_records() {
    let bound_scope = scope(None);
    let mut store = IdentityStore::new();
    store
        .bind_session(id("session-1"), bound_scope.clone())
        .unwrap();
    store
        .register_source_item(journal_item(
            "item-1",
            "source-1",
            "first",
            bound_scope.clone(),
        ))
        .unwrap();
    let source_digest = Digest::sha256("first");
    for (kind, identity_id, relation) in [
        (
            IdentityKind::JournalItem,
            "item-edit",
            RelationKind::Supersedes,
        ),
        (
            IdentityKind::JournalItem,
            "item-fork",
            RelationKind::ForksFrom,
        ),
        (IdentityKind::ToolCall, "call-1", RelationKind::Continues),
        (
            IdentityKind::Summary,
            "summary-1",
            RelationKind::DerivedFrom,
        ),
        (
            IdentityKind::Projection,
            "projection-1",
            RelationKind::DerivedFrom,
        ),
        (
            IdentityKind::SubagentSnapshot,
            "snapshot-1",
            RelationKind::ContributedBy,
        ),
    ] {
        let source = (kind == IdentityKind::JournalItem)
            .then(|| source(&format!("source-{identity_id}"), identity_id));
        let record = ContextIdentity::new(
            id(identity_id),
            kind,
            bound_scope.clone(),
            id("session-1"),
            source,
            vec![IdentityRelation::new(
                relation,
                id("item-1"),
                Some(source_digest),
            )],
        )
        .unwrap();
        assert_eq!(
            store.register(record).unwrap().relations[0].item_id,
            id("item-1")
        );
    }
}

#[test]
fn scope_mismatch_fails_closed_and_workspace_rebind_is_recorded() {
    let previous = scope(Some("workspace-old"));
    let next = scope(Some("workspace-new"));
    let mut store = IdentityStore::new();
    store
        .bind_session(id("session-1"), previous.clone())
        .unwrap();
    store
        .register_source_item(journal_item("item-1", "source-1", "body", previous.clone()))
        .unwrap();
    assert_eq!(
        store.bind_session(id("session-1"), next.clone()),
        Err(IdentityStoreError::ScopeMismatch)
    );

    let cursor = Cursor::new(3, 8).unwrap();
    let record = store
        .rebind(
            id("rebind-1"),
            id("session-1"),
            previous.clone(),
            next.clone(),
            cursor,
            "workspace renamed",
        )
        .unwrap();
    assert_eq!(record.previous_scope, previous);
    assert_eq!(store.identity(&id("item-1"), &next).unwrap().scope, next);
    assert_eq!(
        store
            .rebind(
                id("rebind-1"),
                id("session-1"),
                record.previous_scope.clone(),
                record.next_scope.clone(),
                cursor,
                "workspace renamed",
            )
            .unwrap(),
        record
    );

    assert_eq!(
        store.rebind(
            id("rebind-2"),
            id("session-1"),
            record.next_scope,
            Scope::new(id("owner-2"), id("project-1"), Some(id("workspace-new"))),
            Cursor::new(3, 9).unwrap(),
            "owner changed",
        ),
        Err(IdentityStoreError::ScopeMismatch)
    );
}

#[test]
fn source_event_identity_rejects_digest_changes() {
    let bound_scope = scope(None);
    let mut store = IdentityStore::new();
    store
        .bind_session(id("session-1"), bound_scope.clone())
        .unwrap();
    store
        .register_source_item(journal_item(
            "item-1",
            "source-1",
            "original",
            bound_scope.clone(),
        ))
        .unwrap();
    assert_eq!(
        store.register_source_item(journal_item("item-2", "source-1", "changed", bound_scope,)),
        Err(IdentityStoreError::SourceIdentityConflict)
    );
}
