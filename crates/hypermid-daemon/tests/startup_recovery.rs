use std::collections::BTreeSet;
use std::fs::File;
use std::io::Read;
use std::path::Path;
use std::process::Command;

use hypermid_bus::{BeginEffect, DurableEffectLedger, DurableEventBus, EventDraft};
use hypermid_contracts::{Cursor, Digest, Id, Scope};
use hypermid_core::capability::{
    AuthenticatedPrincipal, CapabilityGrant, CapabilityOperation,
    PrincipalKind as CapabilityPrincipalKind,
};
use hypermid_daemon::enrollment::LocalOperatorEnrollment;
use hypermid_daemon::startup::{
    PendingRestoreIntent, RecoveryAuthority, StartupReconciler, StartupRecoveryMode,
};
use hypermid_daemon::ServerState;
use hypermid_memory::{scope_digest, snapshot::create_memory_snapshot, MemoryStore};
use hypermid_protocol::{Principal, PrincipalKind};
use hypermid_store::authorization::put_grant;
use rusqlite::Connection;
use sha2::{Digest as _, Sha256};

fn id(value: &str) -> Id {
    Id::new(value).unwrap()
}

fn scope() -> Scope {
    Scope::new(id("startup-owner"), id("startup-project"), None)
}

#[test]
fn crash_cut_helper() {
    let Some(root) = std::env::var_os("HYPERMID_STARTUP_CRASH_ROOT") else {
        return;
    };
    let root = Path::new(&root);
    std::fs::create_dir_all(root).unwrap();
    let memory_path = root.join("memory.sqlite3");
    drop(MemoryStore::open(&memory_path).unwrap());
    let scope = scope();
    let connection = Connection::open(&memory_path).unwrap();
    connection
        .execute(
            "INSERT INTO memory_scopes(\
                 scope_digest,scope_json,epoch,sequence,created_at_ms,updated_at_ms\
             ) VALUES (?1,?2,1,0,1,1)",
            rusqlite::params![
                scope_digest(&scope).to_hex(),
                serde_json::to_string(&scope).unwrap()
            ],
        )
        .unwrap();
    drop(connection);

    let artifact_id = id("startup-artifact");
    let artifact_path = root.join("recovery").join(artifact_id.as_str());
    let store = MemoryStore::open(&memory_path).unwrap();
    let snapshot = create_memory_snapshot(&store, &artifact_path, scope.clone()).unwrap();
    drop(store);
    let connection = Connection::open(&memory_path).unwrap();
    let owner = scope_digest(&scope).to_hex();
    connection
        .execute(
            "UPDATE memory_scopes SET sequence=1,updated_at_ms=2 WHERE scope_digest=?1",
            [&owner],
        )
        .unwrap();
    connection
        .execute(
            "INSERT INTO memory_mutation_events(\
                 event_id,owner_scope_digest,epoch,sequence,operation,record_id,\
                 previous_revision_digest,result_revision_digest,actor_scope_digest,grant_id,\
                 authorization_basis,trace_json,created_at_ms\
             ) VALUES ('startup-mutation',?1,1,1,'export',NULL,NULL,NULL,?1,NULL,'owner','{}',2)",
            [&owner],
        )
        .unwrap();
    drop(connection);
    RecoveryAuthority::new(root)
        .queue_restore(PendingRestoreIntent {
            intent_id: id("startup-restore"),
            artifact_id,
            scope: scope.clone(),
            manifest_digest: snapshot.manifest_digest,
            expected_active_digest: digest_file(&memory_path),
            created_at_ms: 10,
        })
        .unwrap();

    let producer = Principal {
        id: id("startup-producer"),
        kind: PrincipalKind::Service,
        scopes: vec!["events.publish:*".into()],
        module_id: None,
        spawn_generation: None,
    };
    let mut outbox = DurableEventBus::open(root.join("events.sqlite3"), 1, 10_000, 5).unwrap();
    outbox
        .publish(
            &producer,
            EventDraft {
                event_id: id("startup-event"),
                topic: "memory.committed".into(),
                scope: scope.clone(),
                at_ms: 10,
                schema_name: "memory.committed".into(),
                schema_version: 1,
                trace: None,
                payload: serde_json::json!({"cursor": {"epoch": 1, "sequence": 0}}),
            },
        )
        .unwrap();
    drop(outbox);

    let mut effects = DurableEffectLedger::open(root.join("effects.journal"), 10).unwrap();
    effects
        .begin(BeginEffect {
            effect_id: id("startup-effect"),
            module_id: id("startup-module"),
            operation: "external.send".into(),
            principal: producer,
            scope,
            input_digest: Digest::sha256(b"send exactly once"),
            created_ms: 10,
        })
        .unwrap();
    effects.mark_dispatched(&id("startup-effect")).unwrap();
    std::process::abort();
}

#[test]
fn real_crash_restart_reconciles_cursors_and_surfaces_unknown_effect_once() {
    let root = tempfile::tempdir().unwrap();
    let crashed = Command::new(std::env::current_exe().unwrap())
        .arg("--exact")
        .arg("crash_cut_helper")
        .arg("--nocapture")
        .env("HYPERMID_STARTUP_CRASH_ROOT", root.path())
        .status()
        .unwrap();
    assert!(!crashed.success());

    let recovered = RecoveryAuthority::new(root.path())
        .recover_pending(20)
        .unwrap()
        .unwrap();
    assert_eq!(recovered.intent_id, id("startup-restore"));
    assert_eq!(recovered.cursor, Cursor::new(1, 0).unwrap());
    assert_eq!(
        recovered.source_manifest_digest,
        digest_file(
            &root
                .path()
                .join("recovery")
                .join("startup-artifact")
                .join("manifest.json")
        )
    );
    assert!(!recovered.replayed);
    assert!(RecoveryAuthority::new(root.path())
        .pending()
        .unwrap()
        .is_none());

    let first = StartupReconciler::run(root.path().join("memory.sqlite3"), 21).unwrap();
    assert_eq!(first.mode, StartupRecoveryMode::Ready);
    assert_eq!(first.memories.len(), 1);
    assert_eq!(first.memories[0].scope, scope());
    assert_eq!(first.memories[0].cursor, Cursor::new(1, 0).unwrap());
    assert_eq!(first.outbox_cursor, Some(Cursor::new(1, 1).unwrap()));
    assert_eq!(first.unknown_effects.len(), 1);
    assert_eq!(first.unknown_effects[0].effect_id, id("startup-effect"));
    assert_eq!(first.unknown_effects[0].settled_ms, 21);

    let restarted = StartupReconciler::run(root.path().join("memory.sqlite3"), 30).unwrap();
    assert_eq!(
        restarted.memories[0].authoritative_digest,
        first.memories[0].authoritative_digest
    );
    assert_eq!(restarted.memories[0].cursor, first.memories[0].cursor);
    assert_eq!(restarted.outbox_cursor, first.outbox_cursor);
    assert_eq!(restarted.unknown_effects, first.unknown_effects);
}

#[test]
fn restored_authority_free_store_reenrolls_only_the_configured_local_operator() {
    let directory = tempfile::tempdir().unwrap();
    let state_root = directory.path().join("state");
    std::fs::create_dir_all(&state_root).unwrap();
    let memory_path = state_root.join("memory.sqlite3");
    let target_scope = scope();
    drop(MemoryStore::open(&memory_path).unwrap());
    insert_scope(&memory_path, &target_scope);

    let foreign_capability = id("foreign-capability");
    let mut connection = Connection::open(&memory_path).unwrap();
    let transaction = connection.transaction().unwrap();
    put_grant(
        &transaction,
        &AuthenticatedPrincipal {
            principal_id: id("foreign-issuer"),
            owner_id: target_scope.owner_id.clone(),
            kind: CapabilityPrincipalKind::Foreground,
        },
        &CapabilityGrant {
            capability_id: foreign_capability.clone(),
            issuer_owner_id: target_scope.owner_id.clone(),
            principal_id: id("foreign-credential"),
            claimed_scope: target_scope.clone(),
            target_scope: target_scope.clone(),
            operations: BTreeSet::from([CapabilityOperation::Read]),
            resources: BTreeSet::from([id("memory")]),
            expires_at_ms: u64::MAX / 2,
        },
    )
    .unwrap();
    transaction.commit().unwrap();
    drop(connection);

    let sanitized_path = directory.path().join("sanitized.sqlite3");
    drop(MemoryStore::open(&sanitized_path).unwrap());
    insert_scope(&sanitized_path, &target_scope);
    let artifact_id = id("authority-free-artifact");
    let artifact_path = state_root.join("recovery").join(artifact_id.as_str());
    let sanitized = MemoryStore::open(&sanitized_path).unwrap();
    let snapshot =
        create_memory_snapshot(&sanitized, &artifact_path, target_scope.clone()).unwrap();
    drop(sanitized);
    let prior_active_digest = digest_file(&memory_path);
    RecoveryAuthority::new(&state_root)
        .queue_restore(PendingRestoreIntent {
            intent_id: id("authority-free-restore"),
            artifact_id: artifact_id.clone(),
            scope: target_scope.clone(),
            manifest_digest: snapshot.manifest_digest,
            expected_active_digest: prior_active_digest,
            created_at_ms: 10,
        })
        .unwrap();

    let local_capability = id("local-capability");
    let enrollment = LocalOperatorEnrollment {
        credential_id: id("local-credential"),
        scope: target_scope,
        capability_id: local_capability.clone(),
        operations: BTreeSet::from([CapabilityOperation::Append, CapabilityOperation::Read]),
        resources: BTreeSet::from([id("memory")]),
        expires_at_ms: u64::MAX / 2,
    };
    let state = ServerState::new(
        "authority-free-daemon".into(),
        &state_root,
        &enrollment,
        None,
    )
    .unwrap();
    assert!(!state.read_only_recovery());
    let startup_restore = state.startup_restore().unwrap();
    assert_eq!(startup_restore.intent_id, id("authority-free-restore"));
    assert_eq!(startup_restore.artifact_id, artifact_id);
    assert_eq!(
        startup_restore.source_manifest_digest,
        snapshot.manifest_digest
    );
    assert_ne!(startup_restore.active_digest, prior_active_digest);
    drop(state);

    let connection = Connection::open(&memory_path).unwrap();
    let capabilities = connection
        .prepare(
            "SELECT capability_id,principal_id FROM hypermid_capabilities ORDER BY capability_id",
        )
        .unwrap()
        .query_map([], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
        })
        .unwrap()
        .collect::<Result<Vec<_>, _>>()
        .unwrap();
    assert_eq!(
        capabilities,
        vec![(
            local_capability.to_string(),
            enrollment.credential_id.to_string()
        )]
    );
    let operation_count: u64 = connection
        .query_row(
            "SELECT count(*) FROM hypermid_capability_operations WHERE capability_id=?1",
            [local_capability.as_str()],
            |row| row.get(0),
        )
        .unwrap();
    let resource_count: u64 = connection
        .query_row(
            "SELECT count(*) FROM hypermid_capability_resources WHERE capability_id=?1",
            [local_capability.as_str()],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(operation_count, 2);
    assert_eq!(resource_count, 1);
    assert_eq!(
        connection
            .query_row(
                "SELECT count(*) FROM hypermid_capabilities WHERE capability_id=?1",
                [foreign_capability.as_str()],
                |row| row.get::<_, u64>(0),
            )
            .unwrap(),
        0
    );
}

fn insert_scope(path: &Path, target_scope: &Scope) {
    let connection = Connection::open(path).unwrap();
    connection
        .execute(
            "INSERT INTO memory_scopes(\
                 scope_digest,scope_json,epoch,sequence,created_at_ms,updated_at_ms\
             ) VALUES (?1,?2,1,0,1,1)",
            rusqlite::params![
                scope_digest(target_scope).to_hex(),
                serde_json::to_string(target_scope).unwrap()
            ],
        )
        .unwrap();
}

fn digest_file(path: &Path) -> Digest {
    let mut file = File::open(path).unwrap();
    let mut hasher = Sha256::new();
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let read = file.read(&mut buffer).unwrap();
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }
    Digest::from_bytes(hasher.finalize().into())
}
