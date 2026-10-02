use std::collections::BTreeSet;
use std::path::Path;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use hypermid_client::open_tls;
use hypermid_contracts::{Cursor, Id, Scope};
use hypermid_core::capability::CapabilityOperation;
use hypermid_daemon::{Daemon, DaemonConfig};
use hypermid_protocol::{
    authentication_proof, ClientAuthentication, ClientHello, ConnectionClass, Envelope,
    MessageKind, ServerChallenge, SessionAccepted, PROTOCOL,
};
use hypermid_transport::{read_json_frame, write_json_frame, ConnectionRecord, FrameCodec};
use serde_json::{json, Value};
use tokio::time::{sleep, timeout};

fn id(value: &str) -> Id {
    Id::new(value).unwrap()
}

fn scope(workspace: &str) -> Scope {
    Scope::new(id("owner-1"), id("project-1"), Some(id(workspace)))
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_millis() as u64
}

fn request(message_id: &str, operation: &str, scope: Scope, payload: Value) -> Envelope {
    Envelope {
        protocol: PROTOCOL.into(),
        kind: MessageKind::Request,
        message_id: id(message_id),
        sequence: 0,
        reply_to: None,
        route_id: Some(id("control")),
        route_epoch: Some(1),
        operation: Some(operation.into()),
        scope: Some(scope),
        trace: None,
        deadline_ms: Some(now_ms().saturating_add(30_000)),
        payload: Some(payload),
        error: None,
    }
}

fn cancel(message_id: &str, reply_to: &str) -> Envelope {
    Envelope {
        protocol: PROTOCOL.into(),
        kind: MessageKind::Cancel,
        message_id: id(message_id),
        sequence: 0,
        reply_to: Some(id(reply_to)),
        route_id: None,
        route_epoch: None,
        operation: None,
        scope: None,
        trace: None,
        deadline_ms: None,
        payload: None,
        error: None,
    }
}

fn event_draft(event_id: &str, event_scope: Scope, ordinal: u64) -> Value {
    json!({
        "event_id": event_id,
        "topic": "jobs.completed",
        "scope": event_scope,
        "at_ms": now_ms(),
        "schema_name": "jobs.completed",
        "schema_version": 1,
        "payload": {"ordinal": ordinal}
    })
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
    bound_scope: Scope,
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
        client_nonce: "11".repeat(32),
        connection_class: ConnectionClass::Client,
        scope: bound_scope,
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
        .expect("daemon did not answer the RPC")
        .unwrap();
    assert_eq!(inbound.kind, MessageKind::Response);
    assert_eq!(inbound.reply_to.as_ref(), Some(&message_id));
    inbound
}

async fn next_event(
    stream: &mut hypermid_client::LocalTlsStream,
    codec: &mut FrameCodec<Envelope>,
) -> Envelope {
    let inbound = timeout(Duration::from_secs(5), codec.read(stream))
        .await
        .expect("daemon did not push the subscribed event")
        .unwrap();
    assert_eq!(inbound.kind, MessageKind::Event);
    inbound
}

fn event_cursor(envelope: &Envelope) -> Cursor {
    serde_json::from_value(envelope.payload.as_ref().unwrap()["event"]["cursor"].clone()).unwrap()
}

fn event_id(envelope: &Envelope) -> &str {
    envelope.payload.as_ref().unwrap()["event"]["event_id"]
        .as_str()
        .unwrap()
}

#[tokio::test]
async fn durable_bus_rpc_orders_delivery_ack_cancel_and_restart_resume() {
    let root = tempfile::tempdir().unwrap();
    let socket = root.path().join("hypermid.sock");
    let record = root.path().join("connection.json");
    let config = DaemonConfig {
        socket: socket.clone(),
        connection_record: record.clone(),
        tls_directory: None,
        client_crl: None,
        mcp_config: None,
        local_credential_id: id("credential-1"),
        local_owner_id: id("owner-1"),
        local_project_id: id("project-1"),
        local_workspace_id: Some(id("workspace-1")),
        local_capability_id: id("capability-1"),
        local_capability_operations: BTreeSet::from([
            CapabilityOperation::Append,
            CapabilityOperation::Read,
        ]),
        local_capability_resources: BTreeSet::from([id("events")]),
        local_capability_expires_ms: now_ms().saturating_add(60_000),
        remote: None,
    };
    let daemon = Daemon::new(config.clone()).unwrap();
    let first_server = tokio::spawn(daemon.run());
    let event_scope = scope("workspace-1");
    let (mut subscriber_stream, mut subscriber_codec, accepted) =
        connect(&record, event_scope.clone()).await;
    let (mut publisher_stream, mut publisher_codec, _) =
        connect(&record, event_scope.clone()).await;
    for required_grant in [
        "events.publish",
        "events.subscribe",
        "events.ack",
        "events.unsubscribe",
        "events.publish:*",
        "events.subscribe:*",
    ] {
        assert!(accepted
            .principal
            .scopes
            .iter()
            .any(|grant| grant == required_grant));
    }

    let subscribe = exchange(
        &mut subscriber_stream,
        &mut subscriber_codec,
        request(
            "subscribe-1",
            "events.subscribe",
            event_scope.clone(),
            json!({
                "consumer_id": "consumer-1",
                "scope": event_scope,
                "topic_filter": "jobs.*"
            }),
        ),
    )
    .await;
    assert!(subscribe.error.is_none());
    assert_eq!(subscribe.payload.as_ref().unwrap()["replay"], json!([]));

    let publish_one = exchange(
        &mut publisher_stream,
        &mut publisher_codec,
        request(
            "publish-1",
            "events.publish",
            event_scope.clone(),
            event_draft("event-1", event_scope.clone(), 1),
        ),
    )
    .await;
    assert!(publish_one.error.is_none());
    let first = next_event(&mut subscriber_stream, &mut subscriber_codec).await;
    assert_eq!(event_id(&first), "event-1");
    let first_cursor = event_cursor(&first);
    assert_eq!(
        first.payload.as_ref().unwrap()["event"]["delivery_count"],
        1
    );

    let publish_two = exchange(
        &mut publisher_stream,
        &mut publisher_codec,
        request(
            "publish-2",
            "events.publish",
            event_scope.clone(),
            event_draft("event-2", event_scope.clone(), 2),
        ),
    )
    .await;
    assert!(publish_two.error.is_none());
    let ack_one = exchange(
        &mut subscriber_stream,
        &mut subscriber_codec,
        request(
            "ack-1",
            "events.ack",
            event_scope.clone(),
            json!({"consumer_id":"consumer-1","event_id":"event-1"}),
        ),
    )
    .await;
    assert!(ack_one.error.is_none());
    let second = next_event(&mut subscriber_stream, &mut subscriber_codec).await;
    assert_eq!(event_id(&second), "event-2");

    let cancelled = exchange(
        &mut subscriber_stream,
        &mut subscriber_codec,
        cancel("cancel-1", "subscribe-1"),
    )
    .await;
    assert_eq!(cancelled.payload.as_ref().unwrap()["cancelled"], true);
    assert_eq!(
        cancelled.payload.as_ref().unwrap()["classification"],
        "cancelled"
    );
    let publish_three = exchange(
        &mut publisher_stream,
        &mut publisher_codec,
        request(
            "publish-3",
            "events.publish",
            event_scope.clone(),
            event_draft("event-3", event_scope.clone(), 3),
        ),
    )
    .await;
    assert!(publish_three.error.is_none());
    let denied = exchange(
        &mut publisher_stream,
        &mut publisher_codec,
        request(
            "publish-denied",
            "events.publish",
            event_scope.clone(),
            event_draft("event-denied", scope("workspace-2"), 99),
        ),
    )
    .await;
    assert_eq!(denied.error.as_ref().unwrap().code, "SCOPE_DENIED");

    drop(subscriber_stream);
    drop(publisher_stream);
    first_server.abort();
    let _ = first_server.await;
    sleep(Duration::from_millis(50)).await;
    std::fs::remove_file(&record).unwrap();

    let daemon = Daemon::new(config).unwrap();
    let second_server = tokio::spawn(daemon.run());
    sleep(Duration::from_millis(50)).await;
    let (mut stream, mut codec, _) = connect(&record, event_scope.clone()).await;
    let resumed = exchange(
        &mut stream,
        &mut codec,
        request(
            "subscribe-2",
            "events.subscribe",
            event_scope.clone(),
            json!({
                "consumer_id": "consumer-1",
                "scope": event_scope,
                "topic_filter": "jobs.*",
                "after": first_cursor
            }),
        ),
    )
    .await;
    assert!(resumed.error.is_none());
    let replayed = next_event(&mut stream, &mut codec).await;
    assert_eq!(event_id(&replayed), "event-2");
    assert_eq!(
        replayed.payload.as_ref().unwrap()["event"]["delivery_count"],
        2
    );
    let ack_two = exchange(
        &mut stream,
        &mut codec,
        request(
            "ack-2",
            "events.ack",
            event_scope.clone(),
            json!({"consumer_id":"consumer-1","event_id":"event-2"}),
        ),
    )
    .await;
    assert!(ack_two.error.is_none());
    let third = next_event(&mut stream, &mut codec).await;
    assert_eq!(event_id(&third), "event-3");
    assert_eq!(
        third.payload.as_ref().unwrap()["event"]["delivery_count"],
        1
    );

    drop(stream);
    second_server.abort();
    let _ = second_server.await;
}
