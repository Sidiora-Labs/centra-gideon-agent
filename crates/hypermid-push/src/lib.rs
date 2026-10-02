use hpke::{
    aead::ChaCha20Poly1305,
    kdf::HkdfSha256,
    kem::{Kem as _, X25519HkdfSha256},
    single_shot_open, single_shot_seal, Deserializable, OpModeR, OpModeS, Serializable,
};
use sha2::{Digest as _, Sha256};
use thiserror::Error;

type Kem = X25519HkdfSha256;
type PublicKey = <Kem as hpke::Kem>::PublicKey;
type PrivateKey = <Kem as hpke::Kem>::PrivateKey;
type EncappedKey = <Kem as hpke::Kem>::EncappedKey;

const DOMAIN: &[u8] = b"hypermid.push.v1\0";
const ENCAP_SIZE: usize = 32;
const HEADER_SIZE: usize = 1 + 8 + ENCAP_SIZE;
pub const MAX_PLAINTEXT: usize = 2_048;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct KeyPair {
    pub private: [u8; 32],
    pub public: [u8; 32],
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum EnvelopeVersion {
    Anonymous = 1,
    Authenticated = 2,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Error)]
pub enum InternalOpenError {
    #[error("push envelope is malformed")]
    Malformed,
    #[error("push envelope version is unsupported")]
    UnsupportedVersion,
    #[error("push envelope uses the wrong authentication mode")]
    WrongMode,
    #[error("push envelope sender routing id does not match the pinned key")]
    WrongSenderRouting,
    #[error("push envelope contains invalid key material")]
    InvalidKey,
    #[error("push envelope authentication failed")]
    Authentication,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Error)]
#[error("push envelope was rejected")]
pub struct WireOpenError;

impl InternalOpenError {
    pub fn wire(self) -> WireOpenError {
        WireOpenError
    }
}

#[derive(Debug, Error)]
pub enum SealError {
    #[error("push plaintext exceeds {MAX_PLAINTEXT} bytes")]
    PlaintextTooLarge,
    #[error("push key material is invalid")]
    InvalidKey,
    #[error("push encryption failed")]
    Encryption,
}

pub fn generate_keypair() -> KeyPair {
    let mut rng = rand::rng();
    let (private, public) = Kem::gen_keypair(&mut rng);
    let mut private_bytes = [0_u8; 32];
    let mut public_bytes = [0_u8; 32];
    private_bytes.copy_from_slice(private.to_bytes().as_slice());
    public_bytes.copy_from_slice(public.to_bytes().as_slice());
    KeyPair {
        private: private_bytes,
        public: public_bytes,
    }
}

pub fn routing_id(sender_public: &[u8; 32]) -> [u8; 8] {
    let digest = Sha256::digest(sender_public);
    let mut route = [0_u8; 8];
    route.copy_from_slice(&digest[..8]);
    route
}

pub fn seal_anonymous(recipient_public: &[u8; 32], plaintext: &[u8]) -> Result<Vec<u8>, SealError> {
    seal(
        EnvelopeVersion::Anonymous,
        recipient_public,
        None,
        plaintext,
    )
}

pub fn seal_authenticated(
    recipient_public: &[u8; 32],
    sender: &KeyPair,
    plaintext: &[u8],
) -> Result<Vec<u8>, SealError> {
    seal(
        EnvelopeVersion::Authenticated,
        recipient_public,
        Some(sender),
        plaintext,
    )
}

fn seal(
    version: EnvelopeVersion,
    recipient_public: &[u8; 32],
    sender: Option<&KeyPair>,
    plaintext: &[u8],
) -> Result<Vec<u8>, SealError> {
    if plaintext.len() > MAX_PLAINTEXT {
        return Err(SealError::PlaintextTooLarge);
    }
    let recipient = PublicKey::from_bytes(recipient_public).map_err(|_| SealError::InvalidKey)?;
    let route = sender
        .map(|value| routing_id(&value.public))
        .unwrap_or([0_u8; 8]);
    let aad = associated_data(version, route);
    let mut rng = rand::rng();
    let sealed = match sender {
        None => single_shot_seal::<ChaCha20Poly1305, HkdfSha256, Kem, _>(
            &OpModeS::Base,
            &recipient,
            DOMAIN,
            plaintext,
            &aad,
            &mut rng,
        ),
        Some(sender) => {
            let private =
                PrivateKey::from_bytes(&sender.private).map_err(|_| SealError::InvalidKey)?;
            let public =
                PublicKey::from_bytes(&sender.public).map_err(|_| SealError::InvalidKey)?;
            single_shot_seal::<ChaCha20Poly1305, HkdfSha256, Kem, _>(
                &OpModeS::Auth((private, public)),
                &recipient,
                DOMAIN,
                plaintext,
                &aad,
                &mut rng,
            )
        }
    }
    .map_err(|_| SealError::Encryption)?;
    let mut envelope = Vec::with_capacity(HEADER_SIZE + sealed.1.len());
    envelope.push(version as u8);
    envelope.extend_from_slice(&route);
    envelope.extend_from_slice(sealed.0.to_bytes().as_slice());
    envelope.extend_from_slice(&sealed.1);
    Ok(envelope)
}

pub fn open_anonymous(
    recipient_private: &[u8; 32],
    envelope: &[u8],
) -> Result<Vec<u8>, InternalOpenError> {
    let (version, route, encapped, ciphertext) = parse(envelope)?;
    if version != EnvelopeVersion::Anonymous || route != [0_u8; 8] {
        return Err(InternalOpenError::WrongMode);
    }
    open(
        &OpModeR::Base,
        recipient_private,
        version,
        route,
        encapped,
        ciphertext,
    )
}

pub fn open_authenticated(
    recipient_private: &[u8; 32],
    pinned_sender_public: &[u8; 32],
    envelope: &[u8],
) -> Result<Vec<u8>, InternalOpenError> {
    let (version, route, encapped, ciphertext) = parse(envelope)?;
    if version != EnvelopeVersion::Authenticated {
        return Err(InternalOpenError::WrongMode);
    }
    if route != routing_id(pinned_sender_public) {
        return Err(InternalOpenError::WrongSenderRouting);
    }
    let sender =
        PublicKey::from_bytes(pinned_sender_public).map_err(|_| InternalOpenError::InvalidKey)?;
    open(
        &OpModeR::Auth(sender),
        recipient_private,
        version,
        route,
        encapped,
        ciphertext,
    )
}

fn open(
    mode: &OpModeR<Kem>,
    recipient_private: &[u8; 32],
    version: EnvelopeVersion,
    route: [u8; 8],
    encapped: &[u8],
    ciphertext: &[u8],
) -> Result<Vec<u8>, InternalOpenError> {
    let recipient =
        PrivateKey::from_bytes(recipient_private).map_err(|_| InternalOpenError::InvalidKey)?;
    let encapped = EncappedKey::from_bytes(encapped).map_err(|_| InternalOpenError::InvalidKey)?;
    single_shot_open::<ChaCha20Poly1305, HkdfSha256, Kem>(
        mode,
        &recipient,
        &encapped,
        DOMAIN,
        ciphertext,
        &associated_data(version, route),
    )
    .map_err(|_| InternalOpenError::Authentication)
}

fn parse(envelope: &[u8]) -> Result<(EnvelopeVersion, [u8; 8], &[u8], &[u8]), InternalOpenError> {
    if envelope.len() < HEADER_SIZE + 16 {
        return Err(InternalOpenError::Malformed);
    }
    let version = match envelope[0] {
        1 => EnvelopeVersion::Anonymous,
        2 => EnvelopeVersion::Authenticated,
        _ => return Err(InternalOpenError::UnsupportedVersion),
    };
    let mut route = [0_u8; 8];
    route.copy_from_slice(&envelope[1..9]);
    Ok((
        version,
        route,
        &envelope[9..HEADER_SIZE],
        &envelope[HEADER_SIZE..],
    ))
}

fn associated_data(version: EnvelopeVersion, route: [u8; 8]) -> Vec<u8> {
    let mut aad = Vec::with_capacity(DOMAIN.len() + 9);
    aad.extend_from_slice(DOMAIN);
    aad.push(version as u8);
    aad.extend_from_slice(&route);
    aad
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn authenticated_and_anonymous_modes_are_pinned_and_tamper_evident() {
        let recipient = generate_keypair();
        let sender = generate_keypair();
        let other = generate_keypair();
        let anonymous = seal_anonymous(&recipient.public, b"wake:opaque").unwrap();
        assert_eq!(
            open_anonymous(&recipient.private, &anonymous).unwrap(),
            b"wake:opaque"
        );
        assert_eq!(
            open_authenticated(&recipient.private, &sender.public, &anonymous).unwrap_err(),
            InternalOpenError::WrongMode
        );
        let authenticated = seal_authenticated(&recipient.public, &sender, b"wake:opaque").unwrap();
        assert_eq!(
            open_authenticated(&recipient.private, &sender.public, &authenticated).unwrap(),
            b"wake:opaque"
        );
        assert_eq!(
            open_authenticated(&recipient.private, &other.public, &authenticated).unwrap_err(),
            InternalOpenError::WrongSenderRouting
        );
        let mut tampered = authenticated.clone();
        *tampered.last_mut().unwrap() ^= 1;
        assert_eq!(
            open_authenticated(&recipient.private, &sender.public, &tampered).unwrap_err(),
            InternalOpenError::Authentication
        );
        assert!(matches!(
            seal_anonymous(&recipient.public, &[0; MAX_PLAINTEXT + 1]),
            Err(SealError::PlaintextTooLarge)
        ));
    }
}
