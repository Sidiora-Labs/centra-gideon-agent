use hypermid_protocol::{
    verify_authentication_proof, AuthenticationMethod, ClientAuthentication, ClientHello,
    ConnectionLimits, Digest, Id, Principal, Scope, ServerChallenge, SessionAccepted, PROTOCOL,
};

use crate::TransportError;

#[derive(Clone, Debug)]
pub struct ServerHandshakeConfig {
    pub daemon_instance_id: Id,
    pub authorized_scope: Scope,
    pub auth_method: AuthenticationMethod,
    pub challenge_ttl_ms: u64,
    pub limits: ConnectionLimits,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum PeerEvidence {
    UnixOwner { uid: u32 },
    WindowsOwner { sid: String },
    TlsIdentity { certificate_sha256: Digest },
}

#[derive(Clone, Debug)]
pub struct AuthenticatedSession {
    pub accepted: SessionAccepted,
    pub peer: PeerEvidence,
    pub bound_scope: Scope,
}

pub struct ServerHandshake {
    hello: ClientHello,
    challenge: ServerChallenge,
}

impl ServerHandshake {
    pub fn challenge(
        config: &ServerHandshakeConfig,
        hello: ClientHello,
        server_nonce: [u8; 32],
        now_ms: u64,
    ) -> Result<(Self, ServerChallenge), TransportError> {
        hello.validate()?;
        if hello.scope != config.authorized_scope {
            return Err(TransportError::Authentication(
                "requested scope is not authorized by this credential",
            ));
        }
        let challenge = ServerChallenge {
            kind: "challenge".into(),
            protocol: PROTOCOL.into(),
            server_nonce: encode_hex(&server_nonce),
            daemon_instance_id: config.daemon_instance_id.clone(),
            auth_method: config.auth_method,
            expires_ms: now_ms.saturating_add(config.challenge_ttl_ms),
        };
        Ok((
            Self {
                hello,
                challenge: challenge.clone(),
            },
            challenge,
        ))
    }

    pub fn authenticate(
        self,
        config: &ServerHandshakeConfig,
        authentication: ClientAuthentication,
        secret: &[u8],
        principal: Principal,
        session_id: Id,
        peer: PeerEvidence,
        now_ms: u64,
    ) -> Result<AuthenticatedSession, TransportError> {
        if now_ms > self.challenge.expires_ms {
            return Err(TransportError::ChallengeExpired);
        }
        if authentication.kind != "authenticate" {
            return Err(TransportError::Authentication("expected authenticate"));
        }
        verify_authentication_proof(secret, &self.hello, &self.challenge, &authentication.proof)?;
        let accepted = SessionAccepted {
            kind: "accepted".into(),
            protocol: PROTOCOL.into(),
            session_id,
            principal,
            limits: config.limits.clone(),
            server_time_ms: now_ms,
        };
        Ok(AuthenticatedSession {
            accepted,
            peer,
            bound_scope: self.hello.scope,
        })
    }
}

fn encode_hex(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut result = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        result.push(HEX[(byte >> 4) as usize] as char);
        result.push(HEX[(byte & 0x0f) as usize] as char);
    }
    result
}
