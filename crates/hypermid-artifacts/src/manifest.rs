use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use serde::{Deserialize, Serialize};
use sha2::{Digest as _, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use thiserror::Error;

#[derive(Clone, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ArtifactCapability {
    FilesystemRead,
    FilesystemWrite,
    Network,
    Model,
    Secret,
    Mutation,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Platform {
    pub os: String,
    pub arch: String,
}

impl Platform {
    pub fn current() -> Self {
        Self {
            os: std::env::consts::OS.to_owned(),
            arch: std::env::consts::ARCH.to_owned(),
        }
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum FileKind {
    Regular,
    Symlink,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ArtifactFile {
    pub path: String,
    pub kind: FileKind,
    pub sha256: String,
    pub size: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub link_target: Option<String>,
    #[serde(default)]
    pub executable: bool,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ArtifactManifest {
    pub schema_version: u32,
    pub artifact_id: String,
    pub version: String,
    pub platform: Platform,
    pub archive_sha256: String,
    pub publisher_id: String,
    pub source_uri: String,
    pub source_sha256: String,
    pub capabilities: BTreeSet<ArtifactCapability>,
    pub entrypoint: String,
    pub files: Vec<ArtifactFile>,
}

impl ArtifactManifest {
    pub fn canonical_bytes(&self) -> Result<Vec<u8>, VerificationError> {
        serde_json::to_vec(self).map_err(VerificationError::Encoding)
    }

    pub fn validate_identity(&self) -> Result<(), VerificationError> {
        if self.schema_version != 1 {
            return Err(VerificationError::UnsupportedSchema(self.schema_version));
        }
        for (name, value) in [
            ("artifact_id", self.artifact_id.as_str()),
            ("version", self.version.as_str()),
            ("publisher_id", self.publisher_id.as_str()),
        ] {
            if value.is_empty()
                || value.len() > 160
                || !value
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || b"._:-".contains(&byte))
            {
                return Err(VerificationError::InvalidIdentity(name));
            }
        }
        validate_digest(&self.archive_sha256)?;
        validate_digest(&self.source_sha256)?;
        if !(self.source_uri.starts_with("https://") || self.source_uri.starts_with("file://")) {
            return Err(VerificationError::InvalidSource);
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SignedArtifactManifest {
    pub manifest: ArtifactManifest,
    pub signature: String,
}

#[derive(Clone, Debug, Default)]
pub struct TrustStore {
    publishers: BTreeMap<String, VerifyingKey>,
}

impl TrustStore {
    pub fn insert(
        &mut self,
        publisher_id: impl Into<String>,
        key_bytes: [u8; 32],
    ) -> Result<(), VerificationError> {
        let key = VerifyingKey::from_bytes(&key_bytes)
            .map_err(|_| VerificationError::InvalidPublisherKey)?;
        self.publishers.insert(publisher_id.into(), key);
        Ok(())
    }

    pub fn verify(&self, signed: &SignedArtifactManifest) -> Result<(), VerificationError> {
        signed.manifest.validate_identity()?;
        let payload = signed.manifest.canonical_bytes()?;
        verify_detached(
            self,
            &signed.manifest.publisher_id,
            &payload,
            &signed.signature,
        )
    }
}

pub fn verify_detached(
    trust: &TrustStore,
    publisher_id: &str,
    payload: &[u8],
    signature_hex: &str,
) -> Result<(), VerificationError> {
    let key = trust
        .publishers
        .get(publisher_id)
        .ok_or_else(|| VerificationError::UntrustedPublisher(publisher_id.to_owned()))?;
    let bytes = hex::decode(signature_hex).map_err(|_| VerificationError::InvalidSignature)?;
    let signature =
        Signature::from_slice(&bytes).map_err(|_| VerificationError::InvalidSignature)?;
    key.verify(payload, &signature)
        .map_err(|_| VerificationError::InvalidSignature)
}

pub fn sha256_hex(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

pub fn validate_digest(value: &str) -> Result<(), VerificationError> {
    if value.len() != 64
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
    {
        return Err(VerificationError::InvalidDigest);
    }
    Ok(())
}

#[derive(Debug, Error)]
pub enum VerificationError {
    #[error("artifact manifest could not be encoded: {0}")]
    Encoding(serde_json::Error),
    #[error("artifact digest must be lowercase SHA-256")]
    InvalidDigest,
    #[error("artifact identity field {0} is invalid")]
    InvalidIdentity(&'static str),
    #[error("publisher key is not valid Ed25519")]
    InvalidPublisherKey,
    #[error("publisher signature is invalid")]
    InvalidSignature,
    #[error("artifact provenance source must be an HTTPS or file URI")]
    InvalidSource,
    #[error("artifact publisher {0} is not trusted")]
    UntrustedPublisher(String),
    #[error("artifact manifest schema {0} is unsupported")]
    UnsupportedSchema(u32),
}
