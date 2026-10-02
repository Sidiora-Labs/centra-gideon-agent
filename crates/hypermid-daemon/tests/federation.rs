use std::collections::BTreeSet;

use hypermid_bus::{
    AuthoritativeEffectOutcome, BeginEffect, BusError, EffectStatus, PhoneChannelError,
    PhoneEffectChannel, ReconciliationOutcome,
};
use hypermid_contracts::{Digest, Id, Scope, Trace};
use hypermid_daemon::federation::{
    FederatedOperation, FederationCatalog, FederationGate, RemoteGrant,
};
use hypermid_protocol::{Principal, PrincipalKind};
use hypermid_transport::federation::{EffectClass, FederatedCall};
use hypermid_transport::relay::{RelayGrant, RelayGrantRegistry, RelaySide};

fn id(value: &str) -> Id {
    Id::new(value).unwrap()
}

fn scope() -> Scope {
    Scope::new(id("owner-1"), id("project-1"), Some(id("workspace-1")))
}

fn principal() -> Principal {
    Principal {
        id: id("phone-1"),
        kind: PrincipalKind::Device,
        scopes: vec!["messages.send".into()],
        module_id: None,
        spawn_generation: None,
    }
}

fn call(effect_id: &str) -> FederatedCall {
    FederatedCall {
        kind: "federated_call".into(),
        message_id: id("message-1"),
        operation: "messages.send".into(),
        principal: principal(),
        scope: scope(),
        trace: Trace::new(id("trace-1"), id("request-1")),
        deadline_ms: 1_000,
        effect: EffectClass::Durable,
        effect_id: Some(id(effect_id)),
        input_digest: Some(Digest::sha256(b"hello")),
        payload: serde_json::json!({"body":"hello"}),
    }
}

#[test]
fn relay_reconnect_preserves_grants_and_blocks_unknown_phone_mutations() {
    let peer_id = id("peer-phone-1");
    let mut relay = RelayGrantRegistry::default();
    relay
        .issue(
            RelayGrant {
                kind: "relay_grant".into(),
                pipe_id: id("pipe-1"),
                side: RelaySide::Initiator,
                token: "phone-side-single-use-relay-token".into(),
                expires_ms: 500,
            },
            100,
        )
        .unwrap();
    relay
        .consume(
            &id("pipe-1"),
            RelaySide::Initiator,
            "phone-side-single-use-relay-token",
            110,
        )
        .unwrap();

    let catalog = FederationCatalog::new([
        FederatedOperation {
            module_id: id("message-module"),
            name: "messages.send".into(),
            effect: EffectClass::Durable,
            remote: true,
            required_scopes: BTreeSet::from(["messages.send".into()]),
        },
        FederatedOperation {
            module_id: id("private-module"),
            name: "messages.private".into(),
            effect: EffectClass::Query,
            remote: false,
            required_scopes: BTreeSet::new(),
        },
    ]);
    assert!(catalog.get("messages.private").is_none());

    let mut gate = FederationGate::enabled(["messages.send".into()]);
    gate.pair(RemoteGrant {
        peer_id: peer_id.clone(),
        principal_id: id("phone-1"),
        scope: scope(),
        scopes: BTreeSet::from(["messages.send".into()]),
        operations: BTreeSet::from(["messages.send".into()]),
        expires_ms: 1_000,
    });
    let admitted = gate
        .admit(&peer_id, &catalog, call("effect-1"), 120)
        .unwrap();
    assert_eq!(admitted.call.scope, scope());
    assert_eq!(admitted.principal, principal());

    let root = tempfile::tempdir().unwrap();
    let journal = root.path().join("phone-effects.journal");
    {
        let mut phone = PhoneEffectChannel::open(&journal, 120).unwrap();
        phone
            .prepare_mutation(BeginEffect {
                effect_id: id("effect-1"),
                module_id: admitted.module_id,
                operation: admitted.call.operation,
                principal: admitted.principal,
                scope: admitted.call.scope,
                input_digest: admitted.call.input_digest.unwrap(),
                created_ms: 120,
            })
            .unwrap();
        phone.mark_sent(&id("effect-1")).unwrap();
    }

    let mut phone = PhoneEffectChannel::open(&journal, 200).unwrap();
    assert!(matches!(
        phone.status(&id("effect-1")).unwrap().status,
        EffectStatus::Unknown { .. }
    ));
    let blocked = phone.prepare_mutation(BeginEffect {
        effect_id: id("effect-2"),
        module_id: id("message-module"),
        operation: "messages.send".into(),
        principal: principal(),
        scope: scope(),
        input_digest: Digest::sha256(b"second"),
        created_ms: 210,
    });
    assert!(matches!(
        blocked,
        Err(PhoneChannelError::ReconciliationRequired | PhoneChannelError::UnknownMutation)
    ));
    assert!(matches!(
        phone.reconcile(
            &id("effect-1"),
            ReconciliationOutcome::Committed {
                result: b"receipt-1".to_vec(),
                settled_ms: 220,
            },
        ),
        Err(PhoneChannelError::Bus(BusError::ProviderProofRequired))
    ));
    assert!(matches!(
        phone.record_provider_proof(
            id("proof-wrong-provider"),
            &id("effect-1"),
            id("wrong-message-module"),
            AuthoritativeEffectOutcome::Committed {
                result: b"receipt-1".to_vec(),
            },
            220,
        ),
        Err(PhoneChannelError::Bus(BusError::ProviderIdentityMismatch))
    ));
    phone
        .record_provider_proof(
            id("proof-1"),
            &id("effect-1"),
            id("message-module"),
            AuthoritativeEffectOutcome::Committed {
                result: b"receipt-1".to_vec(),
            },
            220,
        )
        .unwrap();
    assert!(matches!(
        phone.review_unknown(
            id("review-wrong-owner"),
            &id("effect-1"),
            &Scope::new(id("owner-2"), id("project-1"), Some(id("workspace-1"))),
            221,
        ),
        Err(PhoneChannelError::Bus(BusError::UnknownEffect))
    ));
    let review = phone
        .review_unknown(id("review-1"), &id("effect-1"), &scope(), 221)
        .unwrap();
    assert!(matches!(
        phone.reconcile_reviewed(
            &id("effect-1"),
            &scope(),
            &review.review_id,
            &Digest::sha256(b"stale review"),
        ),
        Err(PhoneChannelError::Bus(BusError::ReviewMismatch))
    ));
    assert!(matches!(
        phone.prepare_mutation(BeginEffect {
            effect_id: id("effect-2"),
            module_id: id("message-module"),
            operation: "messages.send".into(),
            principal: principal(),
            scope: scope(),
            input_digest: Digest::sha256(b"second"),
            created_ms: 222,
        }),
        Err(PhoneChannelError::ReconciliationRequired | PhoneChannelError::UnknownMutation)
    ));
    drop(phone);

    let mut phone = PhoneEffectChannel::open(&journal, 225).unwrap();
    phone
        .reconcile_reviewed(
            &id("effect-1"),
            &scope(),
            &review.review_id,
            &review.plan_digest,
        )
        .unwrap();
    phone
        .reconcile_reviewed(
            &id("effect-1"),
            &scope(),
            &review.review_id,
            &review.plan_digest,
        )
        .unwrap();
    phone
        .prepare_mutation(BeginEffect {
            effect_id: id("effect-2"),
            module_id: id("message-module"),
            operation: "messages.send".into(),
            principal: principal(),
            scope: scope(),
            input_digest: Digest::sha256(b"second"),
            created_ms: 230,
        })
        .unwrap();
}
