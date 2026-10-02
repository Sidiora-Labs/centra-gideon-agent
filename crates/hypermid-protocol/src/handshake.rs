use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use sha2::Sha256;
use subtle::ConstantTimeEq;

use crate::{Error, Id, ProtocolError, Scope, PROTOCOL};

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ConnectionClass {
    Client,
    Module,
    Device,
    Service,
}

impl ConnectionClass {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Client => "client",
            Self::Module => "module",
            Self::Device => "device",
            Self::Service => "service",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AuthenticationMethod {
    HmacSha256,
    TlsCertificate,
    SignedToken,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ClientHello {
    pub kind: String,
    pub protocols: Vec<String>,
    pub client_nonce: String,
    pub connection_class: ConnectionClass,
    pub scope: Scope,
}

impl ClientHello {
    pub fn validate(&self) -> Result<(), ProtocolError> {
        if self.kind != "hello" {
            return Err(ProtocolError::InvalidJson("expected hello".into()));
        }
        if self.protocols.len() > 8 || !self.protocols.iter().any(|p| p == PROTOCOL) {
            return Err(ProtocolError::UnsupportedProtocol {
                received: self.protocols.join(","),
            });
        }
        decode_nonce(&self.client_nonce)?;
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ServerChallenge {
    pub kind: String,
    pub protocol: String,
    pub server_nonce: String,
    pub daemon_instance_id: Id,
    pub auth_method: AuthenticationMethod,
    pub expires_ms: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ClientAuthentication {
    pub kind: String,
    pub proof: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub token: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub module_id: Option<Id>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub spawn_generation: Option<u64>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PrincipalKind {
    LocalUser,
    SupervisedModule,
    Device,
    Service,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Principal {
    pub id: Id,
    pub kind: PrincipalKind,
    pub scopes: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub module_id: Option<Id>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub spawn_generation: Option<u64>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ConnectionLimits {
    pub max_frame_bytes: u64,
    pub event_chunk_bytes: u64,
    pub max_routes: u16,
    pub max_inflight_requests: u16,
}

impl Default for ConnectionLimits {
    fn default() -> Self {
        Self {
            max_frame_bytes: crate::MAX_FRAME_BYTES as u64,
            event_chunk_bytes: crate::MAX_CHUNK_BYTES as u64,
            max_routes: 1024,
            max_inflight_requests: 1024,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SessionAccepted {
    pub kind: String,
    pub protocol: String,
    pub session_id: Id,
    pub principal: Principal,
    pub limits: ConnectionLimits,
    pub server_time_ms: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CompatibilityRefusal {
    pub kind: String,
    pub error: Error,
    pub supported_protocol_min: String,
    pub supported_protocol_max: String,
}

impl CompatibilityRefusal {
    pub fn unsupported_protocol() -> Self {
        Self {
            kind: "close".into(),
            error: Error::new(
                "UNSUPPORTED_PROTOCOL",
                "client and daemon protocol ranges do not overlap",
                false,
                None,
                None,
            )
            .expect("static compatibility error is valid"),
            supported_protocol_min: PROTOCOL.into(),
            supported_protocol_max: PROTOCOL.into(),
        }
    }
}

pub fn authentication_proof(
    secret: &[u8],
    hello: &ClientHello,
    challenge: &ServerChallenge,
) -> Result<String, ProtocolError> {
    hello.validate()?;
    if challenge.protocol != PROTOCOL {
        return Err(ProtocolError::UnsupportedProtocol {
            received: challenge.protocol.clone(),
        });
    }
    let client_nonce = decode_nonce(&hello.client_nonce)?;
    let server_nonce = decode_nonce(&challenge.server_nonce)?;
    let transcript = transcript(
        &client_nonce,
        &server_nonce,
        &challenge.protocol,
        &challenge.daemon_instance_id,
        hello.connection_class,
        &hello.scope,
    );
    let mut mac = Hmac::<Sha256>::new_from_slice(secret)
        .map_err(|_| ProtocolError::InvalidJson("invalid authentication key".into()))?;
    mac.update(&transcript);
    Ok(URL_SAFE_NO_PAD.encode(mac.finalize().into_bytes()))
}

pub fn verify_authentication_proof(
    secret: &[u8],
    hello: &ClientHello,
    challenge: &ServerChallenge,
    proof: &str,
) -> Result<(), ProtocolError> {
    let expected = authentication_proof(secret, hello, challenge)?;
    let supplied = URL_SAFE_NO_PAD
        .decode(proof)
        .map_err(|_| ProtocolError::InvalidJson("invalid authentication proof".into()))?;
    let expected = URL_SAFE_NO_PAD
        .decode(expected)
        .expect("generated proof is valid");
    if supplied.len() != expected.len() || !bool::from(supplied.ct_eq(&expected)) {
        return Err(ProtocolError::InvalidJson(
            "authentication proof mismatch".into(),
        ));
    }
    Ok(())
}

fn decode_nonce(encoded: &str) -> Result<[u8; 32], ProtocolError> {
    if encoded.len() != 64
        || !encoded
            .bytes()
            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
    {
        return Err(ProtocolError::InvalidJson(
            "nonce must be 32 lowercase hexadecimal bytes".into(),
        ));
    }
    let mut result = [0; 32];
    for (index, pair) in encoded.as_bytes().chunks_exact(2).enumerate() {
        result[index] = (hex(pair[0])? << 4) | hex(pair[1])?;
    }
    Ok(result)
}

fn hex(value: u8) -> Result<u8, ProtocolError> {
    match value {
        b'0'..=b'9' => Ok(value - b'0'),
        b'a'..=b'f' => Ok(value - b'a' + 10),
        _ => Err(ProtocolError::InvalidJson("invalid nonce".into())),
    }
}

fn transcript(
    client_nonce: &[u8; 32],
    server_nonce: &[u8; 32],
    protocol: &str,
    daemon_instance_id: &Id,
    connection_class: ConnectionClass,
    scope: &Scope,
) -> Vec<u8> {
    let mut result = b"hypermid-auth-v1\0".to_vec();
    for field in [
        client_nonce.as_slice(),
        server_nonce.as_slice(),
        protocol.as_bytes(),
        daemon_instance_id.as_str().as_bytes(),
        connection_class.as_str().as_bytes(),
        scope.owner_id.as_str().as_bytes(),
        scope.project_id.as_str().as_bytes(),
        scope
            .workspace_id
            .as_ref()
            .map(Id::as_str)
            .unwrap_or("")
            .as_bytes(),
    ] {
        result.extend_from_slice(&(field.len() as u32).to_be_bytes());
        result.extend_from_slice(field);
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn proof_binds_every_transcript_field() {
        let hello = ClientHello {
            kind: "hello".into(),
            protocols: vec![PROTOCOL.into()],
            client_nonce: "11".repeat(32),
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
            server_nonce: "22".repeat(32),
            daemon_instance_id: Id::new("daemon-1").unwrap(),
            auth_method: AuthenticationMethod::HmacSha256,
            expires_ms: 9,
        };
        let proof = authentication_proof(&[3; 32], &hello, &challenge).unwrap();
        verify_authentication_proof(&[3; 32], &hello, &challenge, &proof).unwrap();
        let mut changed = challenge.clone();
        changed.daemon_instance_id = Id::new("daemon-2").unwrap();
        assert!(verify_authentication_proof(&[3; 32], &hello, &changed, &proof).is_err());
    }
}
