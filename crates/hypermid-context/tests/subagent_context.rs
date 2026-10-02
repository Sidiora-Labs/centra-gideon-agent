use hypermid_context::journal::Journal;
use hypermid_context::subagent_context::{ContextError, SubagentContext};
use hypermid_contracts::{Cursor, Digest, Id, Scope};
use hypermid_core::mode::ContextMode;
use hypermid_core::provider::{BudgetConfidence, ModelBudget};
use hypermid_core::subagent::{
    ChildUsage, GideonContributionAuthorization, SubagentContribution, SubagentSnapshot,
};

fn id(value: &str) -> Id {
    Id::new(value).unwrap()
}

fn scope() -> Scope {
    Scope::new(id("owner-1"), id("project-1"), Some(id("workspace-1")))
}

fn budget() -> ModelBudget {
    ModelBudget {
        context_window_tokens: 8_000,
        reserved_output_tokens: 1_000,
        max_input_tokens: 7_000,
        max_items: 100,
        max_images: 0,
        baseline_tokens: 1_000,
        delta_tokens: 500,
        tail_tokens: 1_500,
        confidence: BudgetConfidence::Measured,
    }
}

fn snapshot(child: &str, items: Vec<Id>) -> SubagentSnapshot {
    SubagentSnapshot::create(
        id(child),
        id("parent-1"),
        scope(),
        Cursor::new(1, 7).unwrap(),
        ContextMode::Primary,
        ContextMode::Shadow,
        Digest::sha256(b"provider-profile"),
        budget(),
        items,
    )
    .unwrap()
}

#[test]
fn child_snapshot_is_distinct_bounded_and_child_owned() {
    let mut contexts = SubagentContext::new();
    let inherited = snapshot("child-1", vec![id("item-1"), id("item-2")]);
    let stored = contexts.spawn(inherited.clone()).unwrap();
    assert_eq!(stored, inherited);
    assert!(stored.allows_item(&id("item-1")));
    assert!(!stored.allows_item(&id("item-3")));

    let conflicting = snapshot("child-1", vec![id("item-3")]);
    assert!(matches!(
        contexts.spawn(conflicting),
        Err(ContextError::ChildIdentityConflict)
    ));

    contexts
        .record_child_outcome(
            &id("child-1"),
            &scope(),
            Cursor::new(1, 3).unwrap(),
            ChildUsage {
                input_tokens: 11,
                output_tokens: 5,
                cache_read_tokens: 2,
                cache_write_tokens: 0,
            },
        )
        .unwrap();
    let usage = contexts.child_usage(&id("child-1"), &scope()).unwrap();
    assert_eq!(usage.input_tokens, 11);
    assert_eq!(usage.output_tokens, 5);
}

#[test]
fn parent_contribution_requires_exact_gideon_authorization_and_appends_once() {
    let root = tempfile::tempdir().unwrap();
    let parent = Journal::open(root.path(), scope(), id("parent-1"), 1).unwrap();
    let mut contexts = SubagentContext::new();
    contexts
        .spawn(snapshot("child-1", vec![id("item-1")]))
        .unwrap();
    contexts
        .record_child_outcome(
            &id("child-1"),
            &scope(),
            Cursor::new(1, 2).unwrap(),
            ChildUsage {
                input_tokens: 20,
                output_tokens: 8,
                cache_read_tokens: 0,
                cache_write_tokens: 0,
            },
        )
        .unwrap();
    let content = "bounded child result".to_owned();
    let contribution = SubagentContribution {
        child_session_id: id("child-1"),
        child_cursor: Cursor::new(1, 2).unwrap(),
        content_digest: Digest::sha256(content.as_bytes()),
        content,
        authorized_by: id("gideon-user-1"),
    };
    let denied = GideonContributionAuthorization {
        authorization_id: id("auth-1"),
        authorized_by: id("another-user"),
        parent_session_id: id("parent-1"),
        child_session_id: id("child-1"),
        scope: scope(),
        expires_at_ms: 2_000,
    };
    assert!(matches!(
        contexts.publish_contribution(
            &parent,
            &scope(),
            id("parent-item-1"),
            id("part-1"),
            id("contribution-1"),
            "1970-01-01T00:00:01.000Z".to_owned(),
            contribution.clone(),
            &denied,
            1_000,
        ),
        Err(ContextError::AuthorizationDenied)
    ));
    assert_eq!(parent.current_cursor(), Cursor::new(1, 0).unwrap());

    let allowed = GideonContributionAuthorization {
        authorization_id: id("auth-2"),
        authorized_by: id("gideon-user-1"),
        parent_session_id: id("parent-1"),
        child_session_id: id("child-1"),
        scope: scope(),
        expires_at_ms: 2_000,
    };
    let first = contexts
        .publish_contribution(
            &parent,
            &scope(),
            id("parent-item-1"),
            id("part-1"),
            id("contribution-1"),
            "1970-01-01T00:00:01.000Z".to_owned(),
            contribution.clone(),
            &allowed,
            1_000,
        )
        .unwrap();
    let replay = contexts
        .publish_contribution(
            &parent,
            &scope(),
            id("parent-item-replayed"),
            id("part-replayed"),
            id("contribution-1"),
            "1970-01-01T00:00:01.500Z".to_owned(),
            contribution,
            &allowed,
            1_500,
        )
        .unwrap();
    assert_eq!(first, replay);
    assert_eq!(parent.current_cursor(), Cursor::new(1, 1).unwrap());
    let items = parent.all_items();
    assert_eq!(items.len(), 1);
    assert_eq!(items[0].source_digest, first.content_digest);
    assert_eq!(
        items[0].parts[0].metadata.as_ref().unwrap()["child_session_id"],
        "child-1"
    );
}
