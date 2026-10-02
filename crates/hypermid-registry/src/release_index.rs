use hypermid_artifacts::manifest::{validate_digest, verify_detached};
use hypermid_artifacts::{Platform, TrustStore, VerificationError};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
use thiserror::Error;

pub const MAX_INDEX_LIFETIME_MS: u64 = 7 * 24 * 60 * 60 * 1000;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ReleaseEntry {
    pub artifact_id: String,
    pub version: String,
    pub platform: Platform,
    pub archive_sha256: String,
    pub manifest_sha256: String,
    pub source_uri: String,
    pub protocol_min: u32,
    pub protocol_max: u32,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ReleaseIndex {
    pub schema_version: u32,
    pub publisher_id: String,
    pub sequence: u64,
    pub issued_at_ms: u64,
    pub expires_at_ms: u64,
    pub releases: Vec<ReleaseEntry>,
}

impl ReleaseIndex {
    pub fn canonical_bytes(&self) -> Result<Vec<u8>, ReleaseIndexError> {
        serde_json::to_vec(self).map_err(ReleaseIndexError::Encoding)
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SignedReleaseIndex {
    pub index: ReleaseIndex,
    pub signature: String,
}

#[derive(Clone, Debug)]
pub struct VerifiedReleaseIndex(ReleaseIndex);

impl VerifiedReleaseIndex {
    pub fn verify(
        signed: SignedReleaseIndex,
        trust: &TrustStore,
        now_ms: u64,
    ) -> Result<Self, ReleaseIndexError> {
        let index = &signed.index;
        if index.schema_version != 1 || index.sequence == 0 {
            return Err(ReleaseIndexError::UnsupportedMetadata);
        }
        if index.issued_at_ms > now_ms
            || now_ms >= index.expires_at_ms
            || index.expires_at_ms.saturating_sub(index.issued_at_ms) > MAX_INDEX_LIFETIME_MS
        {
            return Err(ReleaseIndexError::StaleMetadata);
        }
        verify_detached(
            trust,
            &index.publisher_id,
            &index.canonical_bytes()?,
            &signed.signature,
        )?;
        let mut identities = BTreeSet::new();
        for release in &index.releases {
            validate_digest(&release.archive_sha256)?;
            validate_digest(&release.manifest_sha256)?;
            if release.protocol_min == 0
                || release.protocol_min > release.protocol_max
                || !(release.source_uri.starts_with("https://")
                    || release.source_uri.starts_with("file://"))
            {
                return Err(ReleaseIndexError::InvalidRelease(
                    release.artifact_id.clone(),
                ));
            }
            let identity = (
                release.artifact_id.clone(),
                release.version.clone(),
                release.platform.os.clone(),
                release.platform.arch.clone(),
            );
            if !identities.insert(identity) {
                return Err(ReleaseIndexError::DuplicateRelease);
            }
        }
        Ok(Self(signed.index))
    }

    pub fn select(
        &self,
        artifact_id: &str,
        version: &str,
        platform: &Platform,
        protocol: u32,
    ) -> Result<&ReleaseEntry, ReleaseIndexError> {
        self.0
            .releases
            .iter()
            .find(|entry| {
                entry.artifact_id == artifact_id
                    && entry.version == version
                    && &entry.platform == platform
                    && entry.protocol_min <= protocol
                    && protocol <= entry.protocol_max
            })
            .ok_or(ReleaseIndexError::IncompatibleRelease)
    }

    pub fn sequence(&self) -> u64 {
        self.0.sequence
    }
}

#[derive(Debug, Error)]
pub enum ReleaseIndexError {
    #[error("release index contains a duplicate artifact identity")]
    DuplicateRelease,
    #[error("release index could not be encoded: {0}")]
    Encoding(serde_json::Error),
    #[error("no compatible release exists")]
    IncompatibleRelease,
    #[error("release entry is invalid: {0}")]
    InvalidRelease(String),
    #[error("release index is not currently fresh")]
    StaleMetadata,
    #[error("release index metadata is unsupported")]
    UnsupportedMetadata,
    #[error(transparent)]
    Verification(#[from] VerificationError),
}
