use std::net::{IpAddr, SocketAddr};

use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use ed25519_dalek::{Signature, Signer, SigningKey, Verifier, VerifyingKey};
use hypermid_protocol::{Digest, Id, Principal, Scope, Trace, PROTOCOL};
use serde::{Deserialize, Serialize};

const NOISE_PATTERN: &str = "Noise_IK_25519_ChaChaPoly_BLAKE2s";

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case", tag = "kind", content = "value")]
pub enum CandidateEndpoint {
    Local(SocketAddr),
    Public(SocketAddr),
    Relay(String),
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DiscoveryCandidate {
    pub peer_id: Id,
    pub incarnation: Id,
    pub generation: u64,
    pub expires_ms: u64,
    pub endpoints: Vec<CandidateEndpoint>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SignedDiscoveryCandidate {
    pub candidate: DiscoveryCandidate,
    pub signature: String,
}

impl SignedDiscoveryCandidate {
    pub fn sign(
        candidate: DiscoveryCandidate,
        signing_key: &SigningKey,
    ) -> Result<Self, FederationError> {
        validate_candidate(&candidate)?;
        let signature = signing_key.sign(&canonical_candidate(&candidate)?);
        Ok(Self {
            candidate,
            signature: URL_SAFE_NO_PAD.encode(signature.to_bytes()),
        })
    }

    pub fn verify(
        &self,
        pairing: &PairingRecord,
        now_ms: u64,
    ) -> Result<Vec<CandidateEndpoint>, FederationError> {
        validate_candidate(&self.candidate)?;
        if self.candidate.peer_id != pairing.peer_id {
            return Err(FederationError::WrongPeer);
        }
        if self.candidate.generation != pairing.discovery_generation {
            return Err(FederationError::StaleGeneration);
        }
        if now_ms >= self.candidate.expires_ms {
            return Err(FederationError::ExpiredCandidate);
        }
        let signature_bytes = URL_SAFE_NO_PAD
            .decode(&self.signature)
            .map_err(|_| FederationError::InvalidSignature)?;
        let signature = Signature::from_slice(&signature_bytes)
            .map_err(|_| FederationError::InvalidSignature)?;
        let verifying_key = VerifyingKey::from_bytes(&pairing.signing_public_key)
            .map_err(|_| FederationError::InvalidSignature)?;
        verifying_key
            .verify(&canonical_candidate(&self.candidate)?, &signature)
            .map_err(|_| FederationError::InvalidSignature)?;
        Ok(dial_ladder(&self.candidate.endpoints))
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PairingRecord {
    pub peer_id: Id,
    pub signing_public_key: [u8; 32],
    pub noise_static_public_key: [u8; 32],
    pub discovery_generation: u64,
    pub grants: Vec<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EffectClass {
    Query,
    Idempotent,
    Durable,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FederatedCall {
    pub kind: String,
    pub message_id: Id,
    pub operation: String,
    pub principal: Principal,
    pub scope: Scope,
    pub trace: Trace,
    pub deadline_ms: u64,
    pub effect: EffectClass,
    pub effect_id: Option<Id>,
    pub input_digest: Option<Digest>,
    pub payload: serde_json::Value,
}

impl FederatedCall {
    pub fn validate(&self, now_ms: u64) -> Result<(), FederationError> {
        if self.kind != "federated_call" {
            return Err(FederationError::InvalidCall("kind"));
        }
        if self.operation.is_empty() || self.operation.len() > 160 {
            return Err(FederationError::InvalidCall("operation"));
        }
        if now_ms >= self.deadline_ms {
            return Err(FederationError::DeadlineExpired);
        }
        if self.effect == EffectClass::Durable
            && (self.effect_id.is_none() || self.input_digest.is_none())
        {
            return Err(FederationError::InvalidCall(
                "durable effect identity is missing",
            ));
        }
        if self.effect != EffectClass::Durable
            && (self.effect_id.is_some() || self.input_digest.is_some())
        {
            return Err(FederationError::InvalidCall(
                "non-durable call carries durable effect identity",
            ));
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SessionHello {
    pub kind: String,
    pub protocol: String,
    pub peer_id: Id,
    pub incarnation: Id,
    pub features: Vec<String>,
    pub effect_ledger_epoch: Option<Id>,
    pub catalog_digest: Digest,
    pub max_record_bytes: u64,
    pub keepalive_ms: u64,
}

impl SessionHello {
    pub fn validate(&self) -> Result<(), FederationError> {
        if self.kind != "peer_hello" || self.protocol != PROTOCOL {
            return Err(FederationError::ProtocolMismatch);
        }
        if !(1024..=8 * 1024 * 1024).contains(&self.max_record_bytes) || self.keepalive_ms == 0 {
            return Err(FederationError::InvalidHello);
        }
        let mut sorted = self.features.clone();
        sorted.sort();
        sorted.dedup();
        if sorted.len() != self.features.len() {
            return Err(FederationError::InvalidHello);
        }
        Ok(())
    }
}

pub struct NoiseInitiator {
    handshake: snow::HandshakeState,
}

impl NoiseInitiator {
    pub fn start(
        local_private_key: &[u8; 32],
        remote_public_key: &[u8; 32],
    ) -> Result<(Self, Vec<u8>), FederationError> {
        let params = NOISE_PATTERN
            .parse()
            .map_err(|_| FederationError::NoiseConfiguration)?;
        let builder = snow::Builder::new(params)
            .local_private_key(local_private_key)
            .remote_public_key(remote_public_key);
        let mut handshake = builder
            .build_initiator()
            .map_err(|_| FederationError::NoiseConfiguration)?;
        let mut message = vec![0u8; 1024];
        let length = handshake
            .write_message(&[], &mut message)
            .map_err(|_| FederationError::NoiseHandshake)?;
        message.truncate(length);
        Ok((Self { handshake }, message))
    }

    pub fn finish(
        mut self,
        response: &[u8],
        max_record_bytes: usize,
    ) -> Result<NoiseSession, FederationError> {
        let mut payload = vec![0u8; 1024];
        self.handshake
            .read_message(response, &mut payload)
            .map_err(|_| FederationError::NoiseHandshake)?;
        let transport = self
            .handshake
            .into_transport_mode()
            .map_err(|_| FederationError::NoiseHandshake)?;
        Ok(NoiseSession {
            transport,
            max_record_bytes,
        })
    }
}

pub struct NoiseResponder {
    handshake: snow::HandshakeState,
}

impl NoiseResponder {
    pub fn new(
        local_private_key: &[u8; 32],
        remote_public_key: &[u8; 32],
    ) -> Result<Self, FederationError> {
        let params = NOISE_PATTERN
            .parse()
            .map_err(|_| FederationError::NoiseConfiguration)?;
        let handshake = snow::Builder::new(params)
            .local_private_key(local_private_key)
            .remote_public_key(remote_public_key)
            .build_responder()
            .map_err(|_| FederationError::NoiseConfiguration)?;
        Ok(Self { handshake })
    }

    pub fn accept(
        mut self,
        request: &[u8],
        max_record_bytes: usize,
    ) -> Result<(NoiseSession, Vec<u8>), FederationError> {
        let mut payload = vec![0u8; 1024];
        self.handshake
            .read_message(request, &mut payload)
            .map_err(|_| FederationError::NoiseHandshake)?;
        let mut response = vec![0u8; 1024];
        let length = self
            .handshake
            .write_message(&[], &mut response)
            .map_err(|_| FederationError::NoiseHandshake)?;
        response.truncate(length);
        let transport = self
            .handshake
            .into_transport_mode()
            .map_err(|_| FederationError::NoiseHandshake)?;
        Ok((
            NoiseSession {
                transport,
                max_record_bytes,
            },
            response,
        ))
    }
}

pub struct NoiseSession {
    transport: snow::TransportState,
    max_record_bytes: usize,
}

impl NoiseSession {
    pub fn encrypt(&mut self, plaintext: &[u8]) -> Result<Vec<u8>, FederationError> {
        if plaintext.is_empty() || plaintext.len() > self.max_record_bytes {
            return Err(FederationError::RecordBound);
        }
        let mut ciphertext = vec![0u8; plaintext.len() + 16];
        let length = self
            .transport
            .write_message(plaintext, &mut ciphertext)
            .map_err(|_| FederationError::NoiseRecord)?;
        ciphertext.truncate(length);
        Ok(ciphertext)
    }

    pub fn decrypt(&mut self, ciphertext: &[u8]) -> Result<Vec<u8>, FederationError> {
        if ciphertext.len() <= 16 || ciphertext.len() > self.max_record_bytes + 16 {
            return Err(FederationError::RecordBound);
        }
        let mut plaintext = vec![0u8; ciphertext.len()];
        let length = self
            .transport
            .read_message(ciphertext, &mut plaintext)
            .map_err(|_| FederationError::NoiseRecord)?;
        plaintext.truncate(length);
        Ok(plaintext)
    }
}

#[derive(Debug, thiserror::Error)]
pub enum FederationError {
    #[error("discovery candidate is invalid")]
    InvalidCandidate,
    #[error("discovery signature is invalid")]
    InvalidSignature,
    #[error("candidate names the wrong paired peer")]
    WrongPeer,
    #[error("candidate generation is stale")]
    StaleGeneration,
    #[error("candidate expired")]
    ExpiredCandidate,
    #[error("federation protocol does not overlap")]
    ProtocolMismatch,
    #[error("session hello is invalid")]
    InvalidHello,
    #[error("federated call is invalid: {0}")]
    InvalidCall(&'static str),
    #[error("federated call deadline expired")]
    DeadlineExpired,
    #[error("Noise IK configuration failed")]
    NoiseConfiguration,
    #[error("Noise IK authentication failed")]
    NoiseHandshake,
    #[error("encrypted record failed authentication")]
    NoiseRecord,
    #[error("encrypted record exceeds the negotiated bound")]
    RecordBound,
    #[error("candidate serialization failed")]
    Serialization,
}

fn canonical_candidate(candidate: &DiscoveryCandidate) -> Result<Vec<u8>, FederationError> {
    serde_json::to_vec(candidate).map_err(|_| FederationError::Serialization)
}

fn validate_candidate(candidate: &DiscoveryCandidate) -> Result<(), FederationError> {
    if candidate.generation == 0 || candidate.endpoints.is_empty() || candidate.endpoints.len() > 32
    {
        return Err(FederationError::InvalidCandidate);
    }
    for endpoint in &candidate.endpoints {
        match endpoint {
            CandidateEndpoint::Local(address) if !is_private(address.ip()) => {
                return Err(FederationError::InvalidCandidate)
            }
            CandidateEndpoint::Public(address) if !is_public(address.ip()) => {
                return Err(FederationError::InvalidCandidate)
            }
            CandidateEndpoint::Relay(url) if !url.starts_with("wss://") || url.len() > 2048 => {
                return Err(FederationError::InvalidCandidate)
            }
            _ => {}
        }
    }
    Ok(())
}

fn dial_ladder(endpoints: &[CandidateEndpoint]) -> Vec<CandidateEndpoint> {
    let mut endpoints = endpoints.to_vec();
    endpoints.sort_by_key(|endpoint| match endpoint {
        CandidateEndpoint::Local(_) => 0,
        CandidateEndpoint::Public(_) => 1,
        CandidateEndpoint::Relay(_) => 2,
    });
    endpoints
}

fn is_private(address: IpAddr) -> bool {
    match address {
        IpAddr::V4(address) => {
            address.is_private() || address.is_loopback() || address.is_link_local()
        }
        IpAddr::V6(address) => {
            address.is_loopback() || address.is_unique_local() || address.is_unicast_link_local()
        }
    }
}

fn is_public(address: IpAddr) -> bool {
    match address {
        IpAddr::V4(address) => {
            !address.is_private()
                && !address.is_loopback()
                && !address.is_link_local()
                && !address.is_broadcast()
                && !address.is_documentation()
                && !address.is_multicast()
                && !address.is_unspecified()
        }
        IpAddr::V6(address) => {
            !address.is_loopback()
                && !address.is_unique_local()
                && !address.is_unicast_link_local()
                && !address.is_multicast()
                && !address.is_unspecified()
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn noise_ik_authenticates_both_static_keys_and_encrypts_records() {
        let params = NOISE_PATTERN.parse().unwrap();
        let builder = snow::Builder::new(params);
        let initiator_keys = builder.generate_keypair().unwrap();
        let params = NOISE_PATTERN.parse().unwrap();
        let responder_keys = snow::Builder::new(params).generate_keypair().unwrap();
        let initiator_private: [u8; 32] = initiator_keys.private.as_slice().try_into().unwrap();
        let initiator_public: [u8; 32] = initiator_keys.public.as_slice().try_into().unwrap();
        let responder_private: [u8; 32] = responder_keys.private.as_slice().try_into().unwrap();
        let responder_public: [u8; 32] = responder_keys.public.as_slice().try_into().unwrap();

        let (initiator, request) =
            NoiseInitiator::start(&initiator_private, &responder_public).unwrap();
        let responder = NoiseResponder::new(&responder_private, &initiator_public).unwrap();
        let (mut responder, response) = responder.accept(&request, 4096).unwrap();
        let mut initiator = initiator.finish(&response, 4096).unwrap();
        let ciphertext = initiator.encrypt(b"scoped federation record").unwrap();
        assert_eq!(
            responder.decrypt(&ciphertext).unwrap(),
            b"scoped federation record"
        );
    }
}
