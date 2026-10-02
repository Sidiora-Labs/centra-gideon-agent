use hypermid_protocol::{
    authentication_proof, chunk_event, parse_json, verify_authentication_proof,
    AuthenticationMethod, ClientAuthentication, ClientHello, ConnectionClass, ConnectionLimits,
    Digest, EventReassembler, Id, Principal, PrincipalKind, ProtocolError, Scope, ServerChallenge,
    MAX_CHUNK_BYTES, MAX_FRAME_BYTES, PROTOCOL,
};
use hypermid_transport::{
    provision_local_tls, read_json_frame, PeerEvidence, ServerHandshake, ServerHandshakeConfig,
    SessionSequence, TlsServerPolicy,
};
use serde_json::Value;

#[tokio::test]
async fn length_boundaries_refuse_before_body_allocation() {
    let mut empty = &0u32.to_be_bytes()[..];
    assert!(matches!(
        read_json_frame::<_, Value>(&mut empty).await,
        Err(hypermid_transport::TransportError::Protocol(
            ProtocolError::EmptyFrame
        ))
    ));

    let mut oversized = &((MAX_FRAME_BYTES as u32) + 1).to_be_bytes()[..];
    assert!(matches!(
        read_json_frame::<_, Value>(&mut oversized).await,
        Err(hypermid_transport::TransportError::Protocol(
            ProtocolError::FrameTooLarge { .. }
        ))
    ));
}

#[test]
fn malformed_and_ambiguous_json_is_refused() {
    assert!(parse_json::<Value>(b"{not-json}").is_err());
    assert!(matches!(
        parse_json::<Value>(br#"{"x":1,"x":2}"#),
        Err(ProtocolError::DuplicateKey(_))
    ));
    assert_eq!(
        parse_json::<Value>(br#"{"importance":0.625,"confidence":1e-3}"#).unwrap(),
        serde_json::json!({"importance": 0.625, "confidence": 1e-3})
    );
    for body in [
        br#"{"x":9007199254740992}"#.as_slice(),
        br#"{"x":-9007199254740992}"#.as_slice(),
        br#"{"x":1e400}"#.as_slice(),
        br#"{"x":NaN}"#.as_slice(),
    ] {
        assert!(parse_json::<Value>(body).is_err());
    }
}

#[test]
fn handshake_proof_is_bound_to_both_nonces_and_session_identity() {
    let secret = [0xa5; 32];
    let hello = ClientHello {
        kind: "hello".into(),
        protocols: vec![PROTOCOL.into()],
        client_nonce: "12".repeat(32),
        connection_class: ConnectionClass::Client,
        scope: Scope::new(
            Id::new("owner-1").unwrap(),
            Id::new("project-1").unwrap(),
            None,
        ),
    };
    let challenge = ServerChallenge {
        kind: "challenge".into(),
        protocol: PROTOCOL.into(),
        server_nonce: "34".repeat(32),
        daemon_instance_id: Id::new("daemon-1").unwrap(),
        auth_method: AuthenticationMethod::HmacSha256,
        expires_ms: 100,
    };
    let proof = authentication_proof(&secret, &hello, &challenge).unwrap();
    verify_authentication_proof(&secret, &hello, &challenge, &proof).unwrap();
    let mut changed = hello.clone();
    changed.client_nonce = "56".repeat(32);
    assert!(verify_authentication_proof(&secret, &changed, &challenge, &proof).is_err());
}

#[test]
fn mutual_tls_peer_evidence_combines_with_hmac_authentication() {
    let secret = [0x5a; 32];
    let scope = Scope::new(
        Id::new("owner-1").unwrap(),
        Id::new("project-1").unwrap(),
        None,
    );
    let config = ServerHandshakeConfig {
        daemon_instance_id: Id::new("daemon-1").unwrap(),
        authorized_scope: scope.clone(),
        auth_method: AuthenticationMethod::HmacSha256,
        challenge_ttl_ms: 1_000,
        limits: ConnectionLimits::default(),
    };
    let hello = ClientHello {
        kind: "hello".into(),
        protocols: vec![PROTOCOL.into()],
        client_nonce: "12".repeat(32),
        connection_class: ConnectionClass::Client,
        scope,
    };
    let (handshake, challenge) =
        ServerHandshake::challenge(&config, hello.clone(), [0x34; 32], 1_000).unwrap();
    let proof = authentication_proof(&secret, &hello, &challenge).unwrap();
    let session = handshake
        .authenticate(
            &config,
            ClientAuthentication {
                kind: "authenticate".into(),
                proof,
                token: None,
                module_id: None,
                spawn_generation: None,
            },
            &secret,
            Principal {
                id: Id::new("principal-1").unwrap(),
                kind: PrincipalKind::LocalUser,
                scopes: vec!["fabric.connect".into()],
                module_id: None,
                spawn_generation: None,
            },
            Id::new("session-1").unwrap(),
            PeerEvidence::TlsIdentity {
                certificate_sha256: Digest::sha256(b"peer certificate"),
            },
            1_001,
        )
        .unwrap();
    assert!(matches!(session.peer, PeerEvidence::TlsIdentity { .. }));
}

#[test]
fn chunk_vectors_enforce_order_size_and_digest() {
    let body = vec![0x6d; MAX_CHUNK_BYTES * 2 + 7];
    let chunks = chunk_event(Id::new("evt-1").unwrap(), &body).unwrap();
    assert_eq!(chunks.len(), 3);
    assert!(chunks
        .iter()
        .all(|chunk| chunk.decoded().unwrap().len() <= MAX_CHUNK_BYTES));
    let mut receiver = EventReassembler::default();
    assert!(receiver.push(chunks[0].clone()).unwrap().is_none());
    assert!(receiver.push(chunks[2].clone()).is_err());
}

#[test]
fn external_tls_policy_requires_enablement_and_every_scope() {
    let disabled = TlsServerPolicy {
        enabled: false,
        required_scopes: vec!["fabric.connect".into()],
    };
    assert!(disabled.authorize(["fabric.connect"]).is_err());
    let enabled = TlsServerPolicy {
        enabled: true,
        required_scopes: vec!["fabric.connect".into(), "project.read".into()],
    };
    assert!(enabled.authorize(["fabric.connect"]).is_err());
    enabled
        .authorize(["fabric.connect", "project.read"])
        .unwrap();
}

#[test]
fn authenticated_session_sequence_refuses_replay_and_reorder() {
    let mut sequence = SessionSequence::default();
    assert_eq!(sequence.next_outbound().unwrap(), 1);
    assert_eq!(sequence.next_outbound().unwrap(), 2);
    sequence.accept_inbound(1).unwrap();
    assert!(sequence.accept_inbound(1).is_err());
    assert!(sequence.accept_inbound(3).is_err());
    sequence.accept_inbound(2).unwrap();
}

#[test]
fn local_tls_material_is_mutual_tls_and_owner_protected() {
    let root = tempfile::tempdir().unwrap();
    let directory = root.path().join("tls");
    let material = provision_local_tls(&directory, "hypermid.local").unwrap();
    assert_eq!(material.server_name, "hypermid.local");
    assert_eq!(material.server_config.alpn_protocols, Vec::<Vec<u8>>::new());
    assert_eq!(material.client_config.alpn_protocols, Vec::<Vec<u8>>::new());
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(
            std::fs::metadata(&directory).unwrap().permissions().mode() & 0o777,
            0o700
        );
        for path in [
            material.ca_certificate,
            material.server_certificate,
            material.server_private_key,
            material.client_certificate,
            material.client_private_key,
        ] {
            assert_eq!(
                std::fs::metadata(path).unwrap().permissions().mode() & 0o777,
                0o600
            );
        }
    }
}
