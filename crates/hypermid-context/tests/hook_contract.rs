use hypermid_context::hook_dispatch::{
    ContextHook, FailurePolicy, HookDispatcher, HookOutcomeStore,
};
use hypermid_contracts::{Cursor, Digest, Id, Scope, Trace};
use hypermid_core::hooks::{HookDecision, HookEvent, HookKind, HookPhase, SyntheticBlockRef};
use std::collections::{BTreeMap, BTreeSet};
use std::sync::Arc;

struct FixedHook {
    id: Id,
    decision: HookDecision,
    kinds: BTreeSet<HookKind>,
    phases: BTreeSet<HookPhase>,
}

impl ContextHook for FixedHook {
    fn hook_id(&self) -> &Id {
        &self.id
    }

    fn kinds(&self) -> &BTreeSet<HookKind> {
        &self.kinds
    }

    fn phases(&self) -> &BTreeSet<HookPhase> {
        &self.phases
    }

    fn timeout_ms(&self) -> u64 {
        100
    }

    fn call(&self, _event: &HookEvent) -> Result<HookDecision, hypermid_core::hooks::HookFailure> {
        Ok(self.decision.clone())
    }
}

fn id(value: &str) -> Id {
    Id::new(value).unwrap()
}

fn event(number: usize) -> HookEvent {
    HookEvent::redacted(
        id(&format!("event-{number}")),
        HookKind::Project,
        Scope::new(id("owner-1"), id("project-1"), None),
        Trace::new(id("trace-1"), id(&format!("request-{number}"))),
        id("session-1"),
        Cursor::new(1, number as u64).unwrap(),
        1,
        HookPhase::Pre,
        BTreeMap::from([
            (
                "pressure_band".to_owned(),
                hypermid_core::hooks::MetadataValue::String("normal".to_owned()),
            ),
            (
                "provider_token".to_owned(),
                hypermid_core::hooks::MetadataValue::String("secret-value".to_owned()),
            ),
        ]),
        "2026-10-02T13:00:00Z",
    )
    .unwrap()
}

#[test]
fn hook_dispatch_is_deterministic_redacted_and_announces_retention_gaps() {
    let store = HookOutcomeStore::new(2).unwrap();
    let mut dispatcher = HookDispatcher::new(store.clone(), FailurePolicy::Deny);
    dispatcher.register(Arc::new(FixedHook {
        id: id("hook-b"),
        decision: HookDecision::deny("policy_denied").unwrap(),
        kinds: BTreeSet::from([HookKind::Project]),
        phases: BTreeSet::from([HookPhase::Pre]),
    }));
    dispatcher.register(Arc::new(FixedHook {
        id: id("hook-a"),
        decision: HookDecision::inject(vec![SyntheticBlockRef {
            block_id: id("synthetic-1"),
            content_digest: Digest::sha256(b"safe synthetic context"),
            token_mass: 4,
        }])
        .unwrap(),
        kinds: BTreeSet::from([HookKind::Project]),
        phases: BTreeSet::from([HookPhase::Pre]),
    }));

    for number in 1..=2 {
        let event = event(number);
        assert_eq!(
            event.metadata["provider_token"],
            hypermid_core::hooks::MetadataValue::String("[redacted]".to_owned())
        );
        let result = dispatcher.dispatch_pre(&event, number as u64).unwrap();
        assert!(!result.allowed);
        assert_eq!(result.reason_code.as_deref(), Some("policy_denied"));
        assert!(result.synthetic_blocks.is_empty());
        assert_eq!(result.outcomes[0].hook_id, id("hook-a"));
        assert_eq!(result.outcomes[1].hook_id, id("hook-b"));
    }

    let replay = store.read_after(0).unwrap();
    assert!(replay.gap);
    assert_eq!(replay.outcomes.len(), 2);
    assert_eq!(replay.next_cursor, 4);
}
