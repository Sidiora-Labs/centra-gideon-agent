use std::collections::BTreeSet;
use std::path::Path;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use hypermid_client::open_tls;
use hypermid_contracts::{EffectState, Id, Scope, Trace};
use hypermid_core::capability::CapabilityOperation;
use hypermid_daemon::{Daemon, DaemonConfig};
use hypermid_memory::{MemoryStore, MEMORY_SCHEMA_VERSION};
use hypermid_protocol::{
    authentication_proof, ClientAuthentication, ClientHello, ConnectionClass, Envelope,
    MessageKind, ServerChallenge, SessionAccepted, PROTOCOL,
};
use hypermid_transport::{read_json_frame, write_json_frame, ConnectionRecord, FrameCodec};
use rusqlite::Connection;
use serde_json::{json, Value};
use tokio::time::{sleep, timeout};

fn id(value: &str) -> Id {
    Id::new(value).unwrap()
}

fn scope() -> Scope {
    Scope::new(id("recovery-owner"), id("recovery-project"), None)
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_millis() as u64
}

fn request(message_id: &str, operation: &str, payload: Value) -> Envelope {
    Envelope {
        protocol: PROTOCOL.into(),
        kind: MessageKind::Request,
        message_id: id(message_id),
        sequence: 0,
        reply_to: None,
        route_id: Some(id("control")),
        route_epoch: Some(1),
        operation: Some(operation.into()),
        scope: Some(scope()),
        trace: Some(Trace::new(id("recovery-trace"), id(message_id))),
        deadline_ms: Some(now_ms().saturating_add(30_000)),
        payload: Some(payload),
        error: None,
    }
}

fn config(root: &Path) -> DaemonConfig {
    DaemonConfig {
        socket: root.join("hypermid.sock"),
        connection_record: root.join("connection.json"),
        tls_directory: None,
        client_crl: None,
        mcp_config: None,
        local_credential_id: id("recovery-credential"),
        local_owner_id: id("recovery-owner"),
        local_project_id: id("recovery-project"),
        local_workspace_id: None,
        local_capability_id: id("recovery-capability"),
        local_capability_operations: BTreeSet::from([CapabilityOperation::Read]),
        local_capability_resources: BTreeSet::from([id("memory")]),
        local_capability_expires_ms: now_ms().saturating_add(60_000),
        remote: None,
    }
}

async fn wait_for_record(path: &Path) -> ConnectionRecord {
    timeout(Duration::from_secs(10), async {
        loop {
            if let Ok(record) = ConnectionRecord::load(path) {
                return record;
            }
            sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .expect("recovery daemon did not publish its connection record")
}

async fn connect(
    record_path: &Path,
) -> (
    hypermid_client::LocalTlsStream,
    FrameCodec<Envelope>,
    SessionAccepted,
) {
    let record = wait_for_record(record_path).await;
    let secret = record.secret_bytes().unwrap();
    let mut stream = open_tls(&record).await.unwrap();
    let hello = ClientHello {
        kind: "hello".into(),
        protocols: vec![PROTOCOL.into()],
        client_nonce: "22".repeat(32),
        connection_class: ConnectionClass::Client,
        scope: scope(),
    };
    write_json_frame(&mut stream, &hello).await.unwrap();
    let challenge: ServerChallenge = read_json_frame(&mut stream).await.unwrap();
    let proof = authentication_proof(&secret, &hello, &challenge).unwrap();
    write_json_frame(
        &mut stream,
        &ClientAuthentication {
            kind: "authenticate".into(),
            proof,
            token: None,
            module_id: None,
            spawn_generation: None,
        },
    )
    .await
    .unwrap();
    let accepted = read_json_frame(&mut stream).await.unwrap();
    (stream, FrameCodec::default(), accepted)
}

async fn exchange(
    stream: &mut hypermid_client::LocalTlsStream,
    codec: &mut FrameCodec<Envelope>,
    outbound: Envelope,
) -> Envelope {
    let message_id = outbound.message_id.clone();
    codec.write(stream, outbound).await.unwrap();
    let inbound = timeout(Duration::from_secs(5), codec.read(stream))
        .await
        .expect("recovery daemon did not answer")
        .unwrap();
    assert_eq!(inbound.kind, MessageKind::Response);
    assert_eq!(inbound.reply_to.as_ref(), Some(&message_id));
    inbound
}

#[derive(Clone, Copy)]
enum Damage {
    Future,
    Corrupt,
}

async fn exercise(damage: Damage) {
    let root = tempfile::tempdir().unwrap();
    let state = root.path().join("state");
    std::fs::create_dir_all(&state).unwrap();
    let memory_path = state.join("memory.sqlite3");
    drop(MemoryStore::open(&memory_path).unwrap());
    let connection = Connection::open(&memory_path).unwrap();
    match damage {
        Damage::Future => {
            connection
                .execute(
                    "UPDATE hypermid_schema_version SET current_version=?1 WHERE singleton=1",
                    [MEMORY_SCHEMA_VERSION + 1],
                )
                .unwrap();
        }
        Damage::Corrupt => {}
    }
    connection
        .query_row("PRAGMA wal_checkpoint(TRUNCATE)", [], |_| Ok(()))
        .unwrap();
    drop(connection);
    if matches!(damage, Damage::Corrupt) {
        std::fs::write(&memory_path, b"corrupt sqlite authority").unwrap();
    }
    let pristine = std::fs::read(&memory_path).unwrap();

    let daemon = Daemon::new(config(root.path())).unwrap();
    assert!(daemon.state().read_only_recovery());
    assert!(!daemon.state().startup_recovery().is_ready());
    assert_eq!(std::fs::read(&memory_path).unwrap(), pristine);
    let advertised = daemon.state().operations();
    assert!(advertised.contains(&"server.describe".to_owned()));
    assert!(advertised.contains(&"lifecycle.restore.plan".to_owned()));
    assert!(advertised.contains(&"lifecycle.status".to_owned()));
    assert!(!advertised.contains(&"memory.health".to_owned()));
    assert!(!advertised.contains(&"passthrough".to_owned()));

    let record = root.path().join("connection.json");
    let server = tokio::spawn(daemon.run());
    let (mut stream, mut codec, accepted) = connect(&record).await;
    assert_eq!(accepted.principal.scopes, advertised);

    let described = exchange(
        &mut stream,
        &mut codec,
        request("describe-recovery", "server.describe", json!({})),
    )
    .await;
    assert!(described.error.is_none());
    assert_eq!(described.payload.as_ref().unwrap()["recovery_mode"], true);
    assert_eq!(
        described.payload.as_ref().unwrap()["operations"],
        json!(advertised)
    );

    let blocked = exchange(
        &mut stream,
        &mut codec,
        request("blocked-memory", "memory.health", json!({})),
    )
    .await;
    let blocked_error = blocked.error.unwrap();
    assert_eq!(blocked_error.code, "READ_ONLY_RECOVERY");
    assert_eq!(blocked_error.effect_state, Some(EffectState::NotStarted));

    let status = exchange(
        &mut stream,
        &mut codec,
        request(
            "recovery-status",
            "lifecycle.status",
            json!({"job_id": "absent-recovery-job"}),
        ),
    )
    .await;
    assert_ne!(
        status.error.as_ref().map(|error| error.code.as_str()),
        Some("READ_ONLY_RECOVERY")
    );
    assert_eq!(std::fs::read(&memory_path).unwrap(), pristine);

    drop(stream);
    server.abort();
    let _ = server.await;
}

#[tokio::test]
async fn corrupt_and_future_memory_start_authenticated_read_only_recovery_without_mutation() {
    exercise(Damage::Future).await;
    exercise(Damage::Corrupt).await;
}
