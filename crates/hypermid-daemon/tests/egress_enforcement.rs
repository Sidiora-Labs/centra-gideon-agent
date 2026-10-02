use std::collections::BTreeSet;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use hypermid_contracts::{Id, Scope};
use hypermid_core::capability::CapabilityOperation;
use hypermid_daemon::egress::{EgressRequestContext, GuardedHttpClient};
use hypermid_daemon::enrollment::{install_local_operator_grant, LocalOperatorEnrollment};
use hypermid_daemon::security_routes::SecurityRoutes;
use hypermid_memory::MemoryStore;
use hypermid_protocol::{
    ConnectionLimits, Envelope, MessageKind, Principal, PrincipalKind, SessionAccepted, PROTOCOL,
};
use hypermid_transport::{AuthenticatedSession, PeerEvidence};
use serde_json::{json, Value};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;

fn id(value: &str) -> Id {
    Id::new(value).unwrap()
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_millis() as u64
}

fn request(operation: &str, scope: &Scope, payload: Value) -> Envelope {
    Envelope {
        protocol: PROTOCOL.into(),
        kind: MessageKind::Request,
        message_id: id("egress-test-request"),
        sequence: 1,
        reply_to: None,
        route_id: Some(id("control")),
        route_epoch: Some(1),
        operation: Some(operation.into()),
        scope: Some(scope.clone()),
        trace: None,
        deadline_ms: Some(now_ms() + 5_000),
        payload: Some(payload),
        error: None,
    }
}

fn session(scope: Scope) -> AuthenticatedSession {
    AuthenticatedSession {
        accepted: SessionAccepted {
            kind: "accepted".into(),
            protocol: PROTOCOL.into(),
            session_id: id("session-egress"),
            principal: Principal {
                id: id("credential-egress"),
                kind: PrincipalKind::LocalUser,
                scopes: vec!["model.invoke".into()],
                module_id: None,
                spawn_generation: None,
            },
            limits: ConnectionLimits::default(),
            server_time_ms: now_ms(),
        },
        peer: PeerEvidence::UnixOwner { uid: 1_000 },
        bound_scope: scope,
    }
}

fn grant_payload(grant_id: &str, port: u16, expires_at_ms: u64) -> Value {
    json!({
        "grant": {
            "grantId": grant_id,
            "principalId": "credential-egress",
            "operation": "model.invoke",
            "scheme": "http",
            "hostname": "localhost",
            "ports": [port],
            "addressClasses": ["loopback"],
            "proxyPolicy": "direct",
            "proxyConfiguration": null,
            "redirectLimit": 1,
            "byteLimit": 4096,
            "expiresAtMs": expires_at_ms
        }
    })
}

async fn serve_until_idle(listener: TcpListener, response: String, connections: Arc<AtomicUsize>) {
    loop {
        let accepted = tokio::time::timeout(Duration::from_millis(400), listener.accept()).await;
        let Ok(Ok((mut stream, _))) = accepted else {
            return;
        };
        connections.fetch_add(1, Ordering::SeqCst);
        let mut request = [0_u8; 1024];
        let _ = stream.read(&mut request).await;
        stream.write_all(response.as_bytes()).await.unwrap();
        stream.shutdown().await.unwrap();
    }
}

#[tokio::test]
async fn durable_grant_controls_real_dns_socket_redirect_and_revocation_boundaries() {
    let root = tempfile::tempdir().unwrap();
    let capability_path = root.path().join("memory.sqlite3");
    drop(MemoryStore::open(&capability_path).unwrap());
    let scope = Scope::new(id("owner-egress"), id("project-egress"), None);
    let capability_id = id("capability-egress");
    let enrollment = LocalOperatorEnrollment {
        credential_id: id("credential-egress"),
        scope: scope.clone(),
        capability_id: capability_id.clone(),
        operations: BTreeSet::from([CapabilityOperation::NetworkUse]),
        resources: BTreeSet::from([id("network-direct"), id("network-redirect")]),
        expires_at_ms: now_ms() + 60_000,
    };
    install_local_operator_grant(&capability_path, &enrollment).unwrap();
    let routes =
        SecurityRoutes::open(root.path().join("security.sqlite3"), &capability_path).unwrap();
    let session = session(scope.clone());

    let direct_listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let direct_port = direct_listener.local_addr().unwrap().port();
    let direct_connections = Arc::new(AtomicUsize::new(0));
    let direct_server = tokio::spawn(serve_until_idle(
        direct_listener,
        "HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok".into(),
        Arc::clone(&direct_connections),
    ));
    let put = routes.dispatch(
        &session,
        &request(
            "security.network.grant.put",
            &scope,
            grant_payload("network-direct", direct_port, now_ms() + 30_000),
        ),
        now_ms(),
    );
    assert!(put.error.is_none());
    let context =
        EgressRequestContext::from_session(&session, capability_id.clone(), "model.invoke");
    let client = GuardedHttpClient::new(&routes, context);
    let response = client
        .get(&format!("http://localhost:{direct_port}/allowed"))
        .await
        .unwrap();
    assert_eq!(response.status, 200);
    assert_eq!(response.body, b"ok");

    let revoked = routes.dispatch(
        &session,
        &request(
            "security.network.revoke",
            &scope,
            json!({"grantId": "network-direct"}),
        ),
        now_ms(),
    );
    assert!(revoked.error.is_none());
    let denied = client
        .get(&format!("http://localhost:{direct_port}/after-revoke"))
        .await
        .unwrap_err();
    assert_eq!(denied.rule(), Some("grant.missing"));
    direct_server.await.unwrap();
    assert_eq!(direct_connections.load(Ordering::SeqCst), 1);

    let target_listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let target_port = target_listener.local_addr().unwrap().port();
    let target_connections = Arc::new(AtomicUsize::new(0));
    let target_server = tokio::spawn(serve_until_idle(
        target_listener,
        "HTTP/1.1 200 OK\r\nContent-Length: 6\r\nConnection: close\r\n\r\ntarget".into(),
        Arc::clone(&target_connections),
    ));
    let redirect_listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let redirect_port = redirect_listener.local_addr().unwrap().port();
    let redirect_connections = Arc::new(AtomicUsize::new(0));
    let redirect_server = tokio::spawn(serve_until_idle(
        redirect_listener,
        format!(
            "HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:{target_port}/forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
        ),
        Arc::clone(&redirect_connections),
    ));
    let put = routes.dispatch(
        &session,
        &request(
            "security.network.grant.put",
            &scope,
            grant_payload("network-redirect", redirect_port, now_ms() + 30_000),
        ),
        now_ms(),
    );
    assert!(put.error.is_none());
    let context = EgressRequestContext::from_session(&session, capability_id, "model.invoke");
    let client = GuardedHttpClient::new(&routes, context);
    let denied = client
        .get(&format!("http://localhost:{redirect_port}/redirect"))
        .await
        .unwrap_err();
    assert_eq!(denied.rule(), Some("grant.missing"));
    redirect_server.await.unwrap();
    target_server.await.unwrap();
    assert_eq!(redirect_connections.load(Ordering::SeqCst), 1);
    assert_eq!(target_connections.load(Ordering::SeqCst), 0);
}
