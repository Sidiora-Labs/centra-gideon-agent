use hypermid_bus::{
    AuthoritativeEffectOutcome, BeginEffect, BusError, DurableEffectLedger, DurableEventBus,
    EffectStatus, EventBusError, EventDraft, ReconciliationOutcome,
};
use hypermid_contracts::{Cursor, Digest, Id, Scope};
use hypermid_protocol::{Principal, PrincipalKind};

fn id(value: &str) -> Id {
    Id::new(value).unwrap()
}

#[test]
fn durable_consumer_resume_preserves_inflight_redelivery_and_refuses_identity_drift() {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("resume-events.journal");
    let scope = Scope::new(id("owner-1"), id("project-1"), None);
    let producer = Principal {
        id: id("producer-1"),
        kind: PrincipalKind::SupervisedModule,
        scopes: vec!["events.publish:*".into()],
        module_id: Some(id("producer-1")),
        spawn_generation: Some(1),
    };
    let consumer = Principal {
        id: id("consumer-1"),
        kind: PrincipalKind::SupervisedModule,
        scopes: vec!["events.subscribe:*".into()],
        module_id: Some(id("consumer-1")),
        spawn_generation: Some(1),
    };
    let cursor = Cursor::new(9, 0).unwrap();

    {
        let mut bus = DurableEventBus::open(&path, 9, 128, 3).unwrap();
        bus.publish(
            &producer,
            EventDraft {
                event_id: id("event-resume-1"),
                topic: "module.ready".into(),
                scope: scope.clone(),
                at_ms: 10,
                schema_name: "module.ready".into(),
                schema_version: 1,
                trace: None,
                payload: serde_json::json!({"generation": 1}),
            },
        )
        .unwrap();
        bus.subscribe(
            &consumer,
            id("durable-resume-1"),
            scope.clone(),
            "module.*".into(),
            Some(cursor),
        )
        .unwrap();
        assert_eq!(
            bus.next_delivery(&id("durable-resume-1"), 20)
                .unwrap()
                .unwrap()
                .delivery_count,
            1
        );
    }

    let mut bus = DurableEventBus::open(&path, 9, 128, 3).unwrap();
    let resumed = bus
        .subscribe(
            &consumer,
            id("durable-resume-1"),
            scope.clone(),
            "module.*".into(),
            Some(cursor),
        )
        .unwrap();
    assert_eq!(resumed.replay.len(), 1);
    assert_eq!(
        bus.next_delivery(&id("durable-resume-1"), 30)
            .unwrap()
            .unwrap()
            .delivery_count,
        2
    );

    for (different_scope, different_filter, different_cursor) in [
        (
            Scope::new(id("owner-1"), id("project-2"), None),
            "module.*",
            cursor,
        ),
        (scope.clone(), "module.ready", cursor),
        (scope.clone(), "module.*", Cursor::new(9, 1).unwrap()),
    ] {
        assert!(matches!(
            bus.subscribe(
                &consumer,
                id("durable-resume-1"),
                different_scope,
                different_filter.into(),
                Some(different_cursor),
            ),
            Err(EventBusError::ConsumerResumeMismatch)
        ));
    }
}

#[test]
fn durable_event_delivery_reopens_in_order_and_dead_letters_after_bounded_redelivery() {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("events.journal");
    let producer = Principal {
        id: id("module-1"),
        kind: PrincipalKind::SupervisedModule,
        scopes: vec!["events.publish:*".into()],
        module_id: Some(id("module-1")),
        spawn_generation: Some(1),
    };
    let consumer = Principal {
        id: id("consumer-1"),
        kind: PrincipalKind::SupervisedModule,
        scopes: vec!["events.subscribe:*".into()],
        module_id: Some(id("consumer-1")),
        spawn_generation: Some(1),
    };
    let scoped = Scope::new(id("owner-1"), id("project-1"), None);
    let draft = EventDraft {
        event_id: id("event-1"),
        topic: "module.ready".into(),
        scope: scoped.clone(),
        at_ms: 10,
        schema_name: "module.ready".into(),
        schema_version: 1,
        trace: None,
        payload: serde_json::json!({"generation":1}),
    };

    let first_cursor;
    {
        let mut bus = DurableEventBus::open(&path, 7, 128, 2).unwrap();
        let first = bus.publish(&producer, draft.clone()).unwrap();
        first_cursor = first.cursor;
        assert_eq!(
            bus.publish(&producer, draft.clone()).unwrap().cursor,
            first_cursor
        );
        let mut divergent = draft.clone();
        divergent.payload = serde_json::json!({"generation":2});
        assert!(bus.publish(&producer, divergent).is_err());
        let snapshot = bus
            .subscribe(
                &consumer,
                id("durable-consumer-1"),
                scoped.clone(),
                "module.*".into(),
                None,
            )
            .unwrap();
        assert_eq!(snapshot.replay.len(), 1);
        assert_eq!(
            bus.next_delivery(&id("durable-consumer-1"), 20)
                .unwrap()
                .unwrap()
                .delivery_count,
            1
        );
    }

    let mut bus = DurableEventBus::open(&path, 7, 128, 2).unwrap();
    let redelivery = bus
        .next_delivery(&id("durable-consumer-1"), 30)
        .unwrap()
        .unwrap();
    assert_eq!(redelivery.event.cursor, first_cursor);
    assert_eq!(redelivery.delivery_count, 2);
    assert!(bus
        .next_delivery(&id("durable-consumer-1"), 40)
        .unwrap()
        .is_none());
    assert_eq!(bus.dead_letters().len(), 1);
    assert_eq!(bus.dead_letters()[0].original_event_id, id("event-1"));
}

fn intent(effect_id: &str) -> BeginEffect {
    BeginEffect {
        effect_id: id(effect_id),
        module_id: id("phone-mutations"),
        operation: "message.send".into(),
        principal: Principal {
            id: id("phone-1"),
            kind: PrincipalKind::Device,
            scopes: vec!["message.send".into()],
            module_id: None,
            spawn_generation: None,
        },
        scope: Scope::new(id("owner-1"), id("project-1"), Some(id("workspace-1"))),
        input_digest: Digest::sha256(b"send once"),
        created_ms: 10,
    }
}

#[test]
fn kill_window_recovery_never_blindly_replays_a_dispatched_effect() {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("effects.journal");

    {
        let mut ledger = DurableEffectLedger::open(&path, 0).unwrap();
        ledger.begin(intent("effect-before-dispatch")).unwrap();
    }
    let ledger = DurableEffectLedger::open(&path, 20).unwrap();
    assert!(matches!(
        ledger.status(&id("effect-before-dispatch")).unwrap().status,
        EffectStatus::NotStarted { .. }
    ));
    drop(ledger);

    {
        let mut ledger = DurableEffectLedger::open(&path, 30).unwrap();
        ledger.begin(intent("effect-after-dispatch")).unwrap();
        ledger
            .mark_dispatched(&id("effect-after-dispatch"))
            .unwrap();
    }
    let mut ledger = DurableEffectLedger::open(&path, 40).unwrap();
    assert!(matches!(
        ledger.status(&id("effect-after-dispatch")).unwrap().status,
        EffectStatus::Unknown { .. }
    ));
    assert!(ledger.begin(intent("effect-after-dispatch")).is_ok());
    assert!(matches!(
        ledger.reconcile(
            &id("effect-after-dispatch"),
            ReconciliationOutcome::Committed {
                result: b"server receipt".to_vec(),
                settled_ms: 50,
            },
        ),
        Err(BusError::ProviderProofRequired)
    ));
    assert!(matches!(
        ledger.record_provider_proof(
            id("proof-wrong-provider"),
            &id("effect-after-dispatch"),
            id("foreign-provider"),
            AuthoritativeEffectOutcome::Committed {
                result: b"server receipt".to_vec(),
            },
            50,
        ),
        Err(BusError::ProviderIdentityMismatch)
    ));
    ledger
        .record_provider_proof(
            id("proof-effect-after-dispatch"),
            &id("effect-after-dispatch"),
            id("phone-mutations"),
            AuthoritativeEffectOutcome::Committed {
                result: b"server receipt".to_vec(),
            },
            50,
        )
        .unwrap();
    assert!(matches!(
        ledger.review_unknown_current(
            id("review-wrong-scope"),
            &id("effect-after-dispatch"),
            &Scope::new(id("owner-2"), id("project-1"), Some(id("workspace-1"))),
            51,
        ),
        Err(BusError::UnknownEffect)
    ));
    let review = ledger
        .review_unknown_current(
            id("review-effect-after-dispatch"),
            &id("effect-after-dispatch"),
            &intent("effect-after-dispatch").scope,
            51,
        )
        .unwrap();
    assert!(matches!(
        ledger.reconcile_reviewed(
            &id("effect-after-dispatch"),
            &intent("effect-after-dispatch").scope,
            &review.review_id,
            &Digest::sha256(b"wrong review"),
        ),
        Err(BusError::ReviewMismatch)
    ));
    ledger
        .reconcile_reviewed(
            &id("effect-after-dispatch"),
            &intent("effect-after-dispatch").scope,
            &review.review_id,
            &review.plan_digest,
        )
        .unwrap();
    ledger
        .reconcile_reviewed(
            &id("effect-after-dispatch"),
            &intent("effect-after-dispatch").scope,
            &review.review_id,
            &review.plan_digest,
        )
        .unwrap();
    drop(ledger);

    let ledger = DurableEffectLedger::open(&path, 60).unwrap();
    assert!(matches!(
        ledger.status(&id("effect-after-dispatch")).unwrap().status,
        EffectStatus::Committed { .. }
    ));

    let mut divergent = intent("effect-after-dispatch");
    divergent.input_digest = Digest::sha256(b"send twice");
    let mut ledger = ledger;
    assert!(ledger.begin(divergent).is_err());
}
