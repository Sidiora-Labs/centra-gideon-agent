use std::{collections::BTreeSet, net::IpAddr, path::Path, time::Duration};

use hypermid_client::open_tls;
use hypermid_contracts::{Digest, Id, Scope, Trace};
use hypermid_core::{capability::CapabilityOperation, provenance::MemoryProvenance};
use hypermid_daemon::{
    egress::{ConnectionAttempt, Destination, EgressGrantAuthority, EgressRequestContext},
    Daemon, DaemonConfig,
};
use hypermid_protocol::{
    authentication_proof, ClientAuthentication, ClientHello, ConnectionClass, Envelope,
    MessageKind, ServerChallenge, SessionAccepted, PROTOCOL,
};
use hypermid_transport::{
    read_json_frame, write_json_frame, AuthenticatedSession, ConnectionRecord, FrameCodec,
    PeerEvidence,
};
use serde_json::{json, Value};
use tokio::time::{sleep, timeout};

fn id(value: &str) -> Id {
    Id::new(value).unwrap()
}

fn scope() -> Scope {
    Scope::new(id("owner-1"), id("project-1"), Some(id("workspace-1")))
}

fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_millis() as u64
}

fn request(message_id: &str, operation: &str, payload: Value, with_trace: bool) -> Envelope {
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
        trace: with_trace.then(|| Trace::new(id("trace-1"), id(message_id))),
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
        local_credential_id: id("credential-1"),
        local_owner_id: id("owner-1"),
        local_project_id: id("project-1"),
        local_workspace_id: Some(id("workspace-1")),
        local_capability_id: id("capability-1"),
        local_capability_operations: BTreeSet::from([
            CapabilityOperation::Administer,
            CapabilityOperation::NetworkUse,
        ]),
        local_capability_resources: BTreeSet::from([id("grant-1")]),
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
    .expect("daemon did not publish its connection record")
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
    let accepted: SessionAccepted = read_json_frame(&mut stream).await.unwrap();
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
        .expect("daemon did not answer the security RPC")
        .unwrap();
    assert_eq!(inbound.kind, MessageKind::Response);
    assert_eq!(inbound.reply_to.as_ref(), Some(&message_id));
    inbound
}

fn grant_payload() -> Value {
    json!({
        "grant": {
            "grantId": "grant-1",
            "principalId": "credential-1",
            "operation": "model.invoke",
            "scheme": "https",
            "hostname": "api.example.com",
            "ports": [443],
            "addressClasses": ["public"],
            "proxyPolicy": "direct",
            "redirectLimit": 2,
            "byteLimit": 1048576,
            "expiresAtMs": now_ms().saturating_add(30_000)
        }
    })
}

#[tokio::test]
async fn security_routes_persist_reviewed_trust_and_revocation_changes_egress() {
    let root = tempfile::tempdir().unwrap();
    let daemon_config = config(root.path());
    let daemon = Daemon::new(daemon_config.clone()).unwrap();
    let initial = MemoryProvenance::new(
        "conversation",
        id("source-1"),
        id("author-1"),
        Trace::new(id("trace-create"), id("request-create")),
        b"review this instruction",
    )
    .unwrap();
    daemon
        .state()
        .security_routes()
        .record_provenance(&scope(), &id("memory-1"), &initial, true)
        .unwrap();
    let authority = daemon.state().clone();
    let first_server = tokio::spawn(daemon.run());
    let (mut stream, mut codec, accepted) = connect(&daemon_config.connection_record).await;

    let put = exchange(
        &mut stream,
        &mut codec,
        request(
            "grant-put-1",
            "security.network.grant.put",
            grant_payload(),
            false,
        ),
    )
    .await;
    assert!(put.error.is_none());
    assert_eq!(
        put.payload.as_ref().unwrap()["grants"][0]["grantId"],
        "grant-1"
    );

    let authenticated = AuthenticatedSession {
        accepted,
        peer: PeerEvidence::UnixOwner { uid: 0 },
        bound_scope: scope(),
    };
    let context =
        EgressRequestContext::from_session(&authenticated, id("capability-1"), "model.invoke");
    let destination = Destination::normalized("https", "api.example.com", 443).unwrap();
    let resolved = vec!["8.8.8.8".parse::<IpAddr>().unwrap()];
    let authorized = authority
        .security_routes()
        .authorize(
            &context,
            ConnectionAttempt {
                principal_id: context.principal_id().as_str(),
                operation: context.operation(),
                destination: &destination,
                resolved_addresses: &resolved,
                selected_proxy: None,
                redirect_hops: 0,
                now_ms: now_ms(),
            },
        )
        .unwrap();
    assert_eq!(authorized.grant_id, "grant-1");

    let lookup = exchange(
        &mut stream,
        &mut codec,
        request(
            "lookup-1",
            "security.memory.provenance",
            json!({"memoryId": "memory-1"}),
            false,
        ),
    )
    .await;
    assert!(lookup.error.is_none());
    assert_eq!(lookup.payload.as_ref().unwrap()["trustClass"], "data");
    let digest = lookup.payload.as_ref().unwrap()["contentDigest"]
        .as_str()
        .unwrap()
        .to_owned();

    let promoted = exchange(
        &mut stream,
        &mut codec,
        request(
            "promote-1",
            "security.memory.promote",
            json!({
                "memoryId": "memory-1",
                "expectedRevision": 1,
                "expectedDigest": digest,
            }),
            true,
        ),
    )
    .await;
    assert!(promoted.error.is_none());
    assert_eq!(
        promoted.payload.as_ref().unwrap()["trustClass"],
        "privileged_instruction"
    );
    assert_eq!(
        promoted.payload.as_ref().unwrap()["reviewerPrincipalId"],
        "credential-1"
    );

    let revoked = exchange(
        &mut stream,
        &mut codec,
        request(
            "revoke-1",
            "security.network.revoke",
            json!({"grantId": "grant-1"}),
            false,
        ),
    )
    .await;
    assert!(revoked.error.is_none());
    assert_eq!(revoked.payload.as_ref().unwrap()["grants"], json!([]));
    let denied = authority
        .security_routes()
        .authorize(
            &context,
            ConnectionAttempt {
                principal_id: context.principal_id().as_str(),
                operation: context.operation(),
                destination: &destination,
                resolved_addresses: &resolved,
                selected_proxy: None,
                redirect_hops: 0,
                now_ms: now_ms(),
            },
        )
        .unwrap_err();
    assert_eq!(denied.rule, "grant.missing");
    drop(stream);
    first_server.abort();
    let _ = first_server.await;
    drop(authority);

    let restarted = Daemon::new(daemon_config.clone()).unwrap();
    let edited = MemoryProvenance::new(
        "conversation",
        id("source-1"),
        id("author-1"),
        Trace::new(id("trace-edit"), id("request-edit")),
        b"changed instruction",
    )
    .unwrap();
    let updated = restarted
        .state()
        .security_routes()
        .record_provenance(&scope(), &id("memory-1"), &edited, true)
        .unwrap();
    assert_eq!(updated.revision, 2);
    assert!(!updated.is_privileged());
    std::fs::remove_file(&daemon_config.connection_record).unwrap();
    let second_server = tokio::spawn(restarted.run());
    let (mut stream, mut codec, _) = connect(&daemon_config.connection_record).await;
    let status = exchange(
        &mut stream,
        &mut codec,
        request("status-2", "security.network.status", json!({}), false),
    )
    .await;
    assert!(status.error.is_none());
    assert_eq!(status.payload.as_ref().unwrap()["grants"], json!([]));
    assert_eq!(
        status.payload.as_ref().unwrap()["decisions"]
            .as_array()
            .unwrap()
            .len(),
        2
    );

    let stale = exchange(
        &mut stream,
        &mut codec,
        request(
            "promote-stale",
            "security.memory.promote",
            json!({
                "memoryId": "memory-1",
                "expectedRevision": 1,
                "expectedDigest": Digest::sha256(b"review this instruction"),
            }),
            true,
        ),
    )
    .await;
    assert_eq!(stale.error.unwrap().code, "STALE_MEMORY_REVISION");
    drop(stream);
    second_server.abort();
    let _ = second_server.await;
}
