use std::collections::BTreeSet;
use std::fs;
use std::path::Path;

use hypermid_bus::{BeginEffect, DurableEffectLedger, EffectStatus};
use hypermid_contracts::{Digest, Id, Scope, Trace};
use hypermid_daemon::federation::{FederationCatalog, FederationGate, RemoteGrant};
use hypermid_daemon::registry::Registry;
use hypermid_daemon::router::Router;
use hypermid_protocol::{Envelope, MessageKind, Principal, PrincipalKind, PROTOCOL};
use hypermid_transport::federation::{EffectClass, FederatedCall};
use serde_json::json;
use sha2::{Digest as _, Sha256};

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

fn write_remote_module(root: &Path) {
    let executable = root.join("message-module.bin");
    let bytes = b"real message module artifact\n";
    fs::write(&executable, bytes).unwrap();
    let digest = format!("{:x}", Sha256::digest(bytes));
    let manifest = json!({
        "schema_version": 1,
        "module_id": "message-module",
        "version": "1.0.0",
        "executable": "message-module.bin",
        "artifact_digest": digest,
        "protocol": PROTOCOL,
        "roles": ["operation_provider", "federation_gateway"],
        "operations": [{
            "name": "messages.send",
            "effect": "durable",
            "remote": true,
            "required_scopes": ["messages.send"]
        }],
        "requires": [],
        "concurrency": "serial",
        "overlap": "exclusive",
        "restart": {
            "mode": "on_failure",
            "max_restarts": 3,
            "window_ms": 10000,
            "base_backoff_ms": 10,
            "max_backoff_ms": 1000,
            "drain_timeout_ms": 500
        },
        "health": {
            "cadence_ms": 1000,
            "deadline_ms": 100,
            "failure_threshold": 2,
            "action": "report"
        }
    });
    fs::write(
        root.join("message-module.json"),
        serde_json::to_vec_pretty(&manifest).unwrap(),
    )
    .unwrap();
}

#[test]
fn effect_federation_consumer_slice_routes_once_and_recovers_unknown_without_retry() {
    let root = tempfile::tempdir().unwrap();
    write_remote_module(root.path());
    let registry = Registry::new(vec![root.path().to_path_buf()]).unwrap();
    registry.rescan().unwrap();
    let operations = registry.snapshot().unwrap().entries["message-module"]
        .manifest
        .manifest
        .operations
        .clone();
    let generation = registry.prepare_spawn("message-module", &[9; 32]).unwrap();
    registry
        .authenticate_module("message-module", generation, &[9; 32], &operations)
        .unwrap();
    registry.mark_ready("message-module", generation).unwrap();

    let catalog = FederationCatalog::from_registry(&registry).unwrap();
    let peer_id = id("paired-phone-1");
    let mut gate = FederationGate::enabled(["messages.send".into()]);
    gate.pair(RemoteGrant {
        peer_id: peer_id.clone(),
        principal_id: id("phone-1"),
        scope: scope(),
        scopes: BTreeSet::from(["messages.send".into()]),
        operations: BTreeSet::from(["messages.send".into()]),
        expires_ms: 10_000,
    });
    let call = FederatedCall {
        kind: "federated_call".into(),
        message_id: id("message-1"),
        operation: "messages.send".into(),
        principal: principal(),
        scope: scope(),
        trace: Trace::new(id("trace-1"), id("request-1")),
        deadline_ms: 9_000,
        effect: EffectClass::Durable,
        effect_id: Some(id("effect-1")),
        input_digest: Some(Digest::sha256(b"hello")),
        payload: json!({"body":"hello"}),
    };
    let router = Router::new(registry);
    let (admitted, reservation) = gate
        .admit_and_reserve(&peer_id, &catalog, &router, call, 100)
        .unwrap();
    let binding = router
        .acknowledge_bind(
            &reservation,
            admitted.module_id.as_str(),
            reservation.spawn_generation,
        )
        .unwrap();

    let effects_path = root.path().join("effects.journal");
    {
        let mut effects = DurableEffectLedger::open(&effects_path, 100).unwrap();
        effects
            .begin(BeginEffect {
                effect_id: admitted.call.effect_id.clone().unwrap(),
                module_id: admitted.module_id.clone(),
                operation: admitted.call.operation.clone(),
                principal: admitted.principal.clone(),
                scope: admitted.call.scope.clone(),
                input_digest: admitted.call.input_digest.unwrap(),
                created_ms: 100,
            })
            .unwrap();
        effects
            .mark_dispatched(admitted.call.effect_id.as_ref().unwrap())
            .unwrap();
        router
            .dispatch(
                &admitted.principal,
                Envelope {
                    protocol: PROTOCOL.into(),
                    kind: MessageKind::Request,
                    message_id: admitted.call.message_id,
                    sequence: 0,
                    reply_to: None,
                    route_id: Some(id(&binding.route_id)),
                    route_epoch: Some(binding.route_epoch),
                    operation: Some(admitted.call.operation),
                    scope: Some(admitted.call.scope),
                    trace: Some(admitted.call.trace),
                    deadline_ms: Some(admitted.call.deadline_ms),
                    payload: Some(admitted.call.payload),
                    error: None,
                },
                110,
            )
            .unwrap();
    }

    let effects = DurableEffectLedger::open(&effects_path, 200).unwrap();
    assert!(matches!(
        &effects.status(&id("effect-1")).unwrap().status,
        EffectStatus::Unknown { .. }
    ));
    assert_eq!(effects.unsettled().len(), 1);
}
