use std::path::PathBuf;

use hypermid_bus::{AuthoritativeEffectOutcome, BeginEffect, DurableEffectLedger};
use hypermid_contracts::{Digest, Id, Scope};
use hypermid_daemon::effect_routes::EffectRoutes;
use hypermid_protocol::{ConnectionLimits, Principal, PrincipalKind, SessionAccepted, PROTOCOL};
use hypermid_transport::{AuthenticatedSession, PeerEvidence};

fn id(value: &str) -> Id {
    Id::new(value).unwrap()
}

fn main() {
    let root = PathBuf::from(std::env::args_os().nth(1).expect("state root is required"));
    std::fs::create_dir_all(&root).unwrap();
    let path = root.join("effects.journal");
    let scope = Scope::new(id("effect-owner"), id("effect-project"), None);
    let effect_id = id("effect-persisted-unknown");
    let module_id = id("effect-provider");
    let mut ledger = DurableEffectLedger::open(&path, 1).unwrap();
    ledger
        .begin(BeginEffect {
            effect_id: effect_id.clone(),
            module_id: module_id.clone(),
            operation: "external.publish".into(),
            principal: Principal {
                id: id("effect-operator"),
                kind: PrincipalKind::LocalUser,
                scopes: vec!["external.publish".into()],
                module_id: None,
                spawn_generation: None,
            },
            scope: scope.clone(),
            input_digest: Digest::sha256(b"publish-once"),
            created_ms: 10,
        })
        .unwrap();
    ledger.mark_dispatched(&effect_id).unwrap();
    drop(ledger);

    let routes = EffectRoutes::open(&path, 20).unwrap();
    let provider = AuthenticatedSession {
        accepted: SessionAccepted {
            kind: "accepted".into(),
            protocol: PROTOCOL.into(),
            session_id: id("effect-provider-session"),
            principal: Principal {
                id: id("effect-provider-principal"),
                kind: PrincipalKind::SupervisedModule,
                scopes: vec!["effects.provider.report".into()],
                module_id: Some(module_id),
                spawn_generation: Some(1),
            },
            limits: ConnectionLimits::default(),
            server_time_ms: 21,
        },
        peer: PeerEvidence::UnixOwner { uid: 1_000 },
        bound_scope: scope,
    };
    routes
        .record_provider_proof(
            &provider,
            id("effect-provider-proof"),
            &effect_id,
            AuthoritativeEffectOutcome::Committed {
                result: b"provider-commit-receipt".to_vec(),
            },
            30,
        )
        .unwrap();
}
