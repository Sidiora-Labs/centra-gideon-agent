use hypermid_bus::{
    AuthoritativeEffectOutcome, BeginEffect, BusError, DurableEffectLedger, EffectReviewPlan,
    EffectStatus, ReconciliationOutcome,
};
use hypermid_contracts::{Digest, Id, Scope};
use hypermid_daemon::effect_routes::{EffectRouteResponse, EffectRoutes, EFFECT_OPERATIONS};
use hypermid_protocol::{
    ConnectionLimits, Envelope, MessageKind, Principal, PrincipalKind, SessionAccepted, PROTOCOL,
};
use hypermid_transport::{AuthenticatedSession, PeerEvidence};
use serde_json::{json, Value};

fn id(value: &str) -> Id {
    Id::new(value).unwrap()
}

fn scope(project: &str) -> Scope {
    Scope::new(id("owner-1"), id(project), Some(id("workspace-1")))
}

fn session(
    kind: PrincipalKind,
    principal_id: &str,
    module_id: Option<&str>,
    scope: Scope,
) -> AuthenticatedSession {
    AuthenticatedSession {
        accepted: SessionAccepted {
            kind: "accepted".into(),
            protocol: PROTOCOL.into(),
            session_id: id(&format!("session-{principal_id}")),
            principal: Principal {
                id: id(principal_id),
                kind,
                scopes: EFFECT_OPERATIONS
                    .iter()
                    .map(|value| (*value).into())
                    .collect(),
                module_id: module_id.map(id),
                spawn_generation: module_id.map(|_| 1),
            },
            limits: ConnectionLimits::default(),
            server_time_ms: 1_000,
        },
        peer: PeerEvidence::UnixOwner { uid: 1_000 },
        bound_scope: scope,
    }
}

fn request(operation: &str, scope: Scope, payload: Value) -> Envelope {
    Envelope {
        protocol: PROTOCOL.into(),
        kind: MessageKind::Request,
        message_id: id("effect-request-1"),
        sequence: 1,
        reply_to: None,
        route_id: Some(id("control")),
        route_epoch: Some(1),
        operation: Some(operation.into()),
        scope: Some(scope),
        trace: None,
        deadline_ms: Some(10_000),
        payload: Some(payload),
        error: None,
    }
}

fn success(response: EffectRouteResponse) -> Value {
    assert!(
        response.error.is_none(),
        "unexpected route error: {:?}",
        response.error
    );
    response.payload.unwrap()
}

fn error_code(response: EffectRouteResponse) -> String {
    assert!(response.payload.is_none());
    response.error.unwrap().code
}

#[test]
fn unknown_effect_requires_persisted_provider_proof_and_exact_review_after_restart() {
    let root = tempfile::tempdir().unwrap();
    let journal = root.path().join("effects.journal");
    let effect_scope = scope("project-1");
    let effect_id = id("effect-1");
    let result = br#"{"provider":"committed"}"#.to_vec();
    let result_digest = Digest::sha256(&result);

    {
        let mut ledger = DurableEffectLedger::open(&journal, 100).unwrap();
        ledger
            .begin(BeginEffect {
                effect_id: effect_id.clone(),
                module_id: id("provider-module"),
                operation: "phone.send".into(),
                principal: Principal {
                    id: id("operator-1"),
                    kind: PrincipalKind::LocalUser,
                    scopes: vec!["phone.send".into()],
                    module_id: None,
                    spawn_generation: None,
                },
                scope: effect_scope.clone(),
                input_digest: Digest::sha256(b"send-message"),
                created_ms: 100,
            })
            .unwrap();
        ledger.mark_dispatched(&effect_id).unwrap();
    }

    {
        let mut recovered = DurableEffectLedger::open(&journal, 200).unwrap();
        assert!(matches!(
            recovered.status(&effect_id).unwrap().status,
            EffectStatus::Unknown { .. }
        ));
        assert!(matches!(
            recovered.reconcile(
                &effect_id,
                ReconciliationOutcome::Committed {
                    result: result.clone(),
                    settled_ms: 201,
                },
            ),
            Err(BusError::ProviderProofRequired)
        ));
    }

    let operator = session(
        PrincipalKind::LocalUser,
        "operator-1",
        None,
        effect_scope.clone(),
    );
    let provider = session(
        PrincipalKind::SupervisedModule,
        "provider-principal",
        Some("provider-module"),
        effect_scope.clone(),
    );
    let routes = EffectRoutes::open(&journal, 202).unwrap();

    let before = success(routes.dispatch(
        &operator,
        &request(
            "effects.status",
            effect_scope.clone(),
            json!({"effect_id": effect_id}),
        ),
        203,
    ));
    assert_eq!(before["state"], "unknown");
    assert_eq!(before["reviewable"], false);
    assert!(before.get("result").is_none());
    assert_eq!(
        error_code(routes.dispatch(
            &operator,
            &request(
                "effects.review",
                effect_scope.clone(),
                json!({"effect_id": effect_id, "review_id": "review-1"}),
            ),
            204,
        )),
        "PROVIDER_PROOF_NOT_FOUND"
    );

    let foreign_provider = session(
        PrincipalKind::SupervisedModule,
        "foreign-provider",
        Some("foreign-module"),
        effect_scope.clone(),
    );
    assert!(matches!(
        routes.record_provider_proof(
            &foreign_provider,
            id("proof-foreign"),
            &effect_id,
            AuthoritativeEffectOutcome::Committed {
                result: result.clone(),
            },
            205,
        ),
        Err(BusError::ProviderIdentityMismatch)
    ));
    let wrong_scope_provider = session(
        PrincipalKind::SupervisedModule,
        "provider-principal",
        Some("provider-module"),
        scope("project-2"),
    );
    assert!(matches!(
        routes.record_provider_proof(
            &wrong_scope_provider,
            id("proof-wrong-scope"),
            &effect_id,
            AuthoritativeEffectOutcome::Committed {
                result: result.clone(),
            },
            205,
        ),
        Err(BusError::UnknownEffect)
    ));

    let proof = routes
        .record_provider_proof(
            &provider,
            id("proof-1"),
            &effect_id,
            AuthoritativeEffectOutcome::Committed {
                result: result.clone(),
            },
            206,
        )
        .unwrap();
    assert_eq!(proof.effect_id, effect_id);
    assert_eq!(proof.scope, effect_scope);
    assert_eq!(proof.provider_id, id("provider-module"));

    let review_value = success(routes.dispatch(
        &operator,
        &request(
            "effects.review",
            effect_scope.clone(),
            json!({"effect_id": effect_id, "review_id": "review-1"}),
        ),
        207,
    ));
    let plan: EffectReviewPlan = serde_json::from_value(review_value["plan"].clone()).unwrap();
    assert_eq!(plan.provider_proof_id, id("proof-1"));
    assert_eq!(plan.proposed_state, "committed");
    assert_eq!(plan.result_digest.as_ref(), Some(&result_digest));
    let repeated_review = success(routes.dispatch(
        &operator,
        &request(
            "effects.review",
            effect_scope.clone(),
            json!({"effect_id": effect_id, "review_id": "review-1"}),
        ),
        250,
    ));
    assert_eq!(repeated_review["plan"], json!(plan));

    let reviewed = success(routes.dispatch(
        &operator,
        &request(
            "effects.status",
            effect_scope.clone(),
            json!({"effect_id": effect_id}),
        ),
        208,
    ));
    assert_eq!(reviewed["state"], "unknown");
    assert_eq!(reviewed["reviewable"], false);
    assert_eq!(reviewed["review_plan"]["review_id"], "review-1");
    assert_eq!(
        error_code(routes.dispatch(
            &operator,
            &request(
                "effects.reconcile",
                effect_scope.clone(),
                json!({
                    "effect_id": effect_id,
                    "review_id": "review-1",
                    "reviewed_plan_digest": Digest::sha256(b"different-plan")
                }),
            ),
            209,
        )),
        "REVIEW_MISMATCH"
    );
    assert_eq!(
        error_code(routes.dispatch(
            &operator,
            &request(
                "effects.reconcile",
                effect_scope.clone(),
                json!({
                    "effect_id": effect_id,
                    "review_id": "review-1",
                    "reviewed_plan_digest": plan.plan_digest,
                    "state": "committed"
                }),
            ),
            210,
        )),
        "INVALID_REQUEST"
    );
    drop(routes);

    let reopened = EffectRoutes::open(&journal, 300).unwrap();
    let listed = success(reopened.dispatch(
        &operator,
        &request("effects.list", effect_scope.clone(), json!({})),
        301,
    ));
    assert_eq!(listed["effects"].as_array().unwrap().len(), 1);
    assert_eq!(
        listed["effects"][0]["review_plan"]["plan_digest"],
        json!(plan.plan_digest)
    );

    let foreign_operator = session(
        PrincipalKind::LocalUser,
        "operator-2",
        None,
        scope("project-2"),
    );
    assert_eq!(
        error_code(reopened.dispatch(
            &foreign_operator,
            &request(
                "effects.status",
                scope("project-2"),
                json!({"effect_id": effect_id}),
            ),
            302,
        )),
        "EFFECT_NOT_FOUND"
    );

    let settled = success(reopened.dispatch(
        &operator,
        &request(
            "effects.reconcile",
            effect_scope.clone(),
            json!({
                "effect_id": effect_id,
                "review_id": "review-1",
                "reviewed_plan_digest": plan.plan_digest
            }),
        ),
        303,
    ));
    assert_eq!(settled["state"], "committed");
    assert_eq!(settled["result_digest"], json!(result_digest));
    assert_eq!(settled["settled_ms"], 206);
    assert!(settled.get("result").is_none());
    let repeated_settlement = success(reopened.dispatch(
        &operator,
        &request(
            "effects.reconcile",
            effect_scope.clone(),
            json!({
                "effect_id": effect_id,
                "review_id": "review-1",
                "reviewed_plan_digest": plan.plan_digest
            }),
        ),
        304,
    ));
    assert_eq!(repeated_settlement, settled);
    drop(reopened);

    let final_routes = EffectRoutes::open(&journal, 400).unwrap();
    let final_status = success(final_routes.dispatch(
        &operator,
        &request(
            "effects.status",
            effect_scope.clone(),
            json!({"effect_id": effect_id}),
        ),
        401,
    ));
    assert_eq!(final_status["state"], "committed");
    let final_list = success(final_routes.dispatch(
        &operator,
        &request("effects.list", effect_scope, json!({})),
        402,
    ));
    assert_eq!(final_list["effects"], json!([]));
}
