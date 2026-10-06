use crate::model::scope_digest;
use crate::{error, MemoryResult};
use hypermid_contracts::{Digest, EffectState, Id, Scope};
use rusqlite::{params, Connection, OptionalExtension, Transaction};
use serde::{Deserialize, Serialize};
use sha2::{Digest as _, Sha256};
use std::cmp::Ordering;
use std::str::FromStr;
use thiserror::Error;

#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, PartialEq, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum EmbeddingMode {
    Off,
    Local,
    RemoteCompatible,
    ManagedService,
}

impl EmbeddingMode {
    fn wire_name(self) -> &'static str {
        match self {
            Self::Off => "off",
            Self::Local => "local",
            Self::RemoteCompatible => "remote-compatible",
            Self::ManagedService => "managed-service",
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RegistrationState {
    Active,
    Retired,
    Failed,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct EmbeddingRegistration {
    pub registration_id: Id,
    pub owner_scope: Scope,
    pub mode: EmbeddingMode,
    pub provider_identity: String,
    pub model_id: String,
    pub dimensions: usize,
    pub normalized: bool,
    pub fingerprint: Digest,
    pub state: RegistrationState,
}

impl EmbeddingRegistration {
    pub fn new(
        registration_id: Id,
        owner_scope: Scope,
        mode: EmbeddingMode,
        provider_identity: impl Into<String>,
        model_id: impl Into<String>,
        dimensions: usize,
        normalized: bool,
    ) -> Result<Self, EmbeddingError> {
        let provider_identity = provider_identity.into();
        let model_id = model_id.into();
        if provider_identity.is_empty() || provider_identity.len() > 512 {
            return Err(EmbeddingError::InvalidRegistration);
        }
        if model_id.is_empty() || model_id.len() > 512 || dimensions == 0 {
            return Err(EmbeddingError::InvalidRegistration);
        }
        let fingerprint =
            registration_fingerprint(mode, &provider_identity, &model_id, dimensions, normalized);
        Ok(Self {
            registration_id,
            owner_scope,
            mode,
            provider_identity,
            model_id,
            dimensions,
            normalized,
            fingerprint,
            state: RegistrationState::Active,
        })
    }

    pub fn retire(&mut self) {
        self.state = RegistrationState::Retired;
    }

    pub fn validate_fingerprint(&self) -> Result<(), EmbeddingError> {
        let expected = registration_fingerprint(
            self.mode,
            &self.provider_identity,
            &self.model_id,
            self.dimensions,
            self.normalized,
        );
        if expected != self.fingerprint {
            return Err(EmbeddingError::FingerprintMismatch);
        }
        Ok(())
    }
}

pub fn registration_fingerprint(
    mode: EmbeddingMode,
    provider_identity: &str,
    model_id: &str,
    dimensions: usize,
    normalized: bool,
) -> Digest {
    let material = format!(
        "hypermid.embedding.v1\0{}\0{}\0{}\0{}\0cosine\0{}",
        mode.wire_name(),
        provider_identity,
        model_id,
        dimensions,
        if normalized { "true" } else { "false" }
    );
    Digest::from_bytes(Sha256::digest(material.as_bytes()).into())
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ProviderEmbedding {
    pub provider_identity: String,
    pub model_id: String,
    pub vector: Vec<f32>,
    pub input_tokens: Option<u64>,
    pub cost_units: Option<f64>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct ValidatedEmbedding {
    pub vector: Vec<f32>,
    pub dimensions: usize,
    pub norm: f64,
    pub registration_id: Id,
    pub fingerprint: Digest,
    pub input_digest: Digest,
    pub input_tokens: Option<u64>,
    pub cost_units: Option<f64>,
}

pub fn validate_provider_embedding(
    registration: &EmbeddingRegistration,
    input_digest: Digest,
    response: ProviderEmbedding,
) -> Result<ValidatedEmbedding, EmbeddingError> {
    registration.validate_fingerprint()?;
    if registration.state != RegistrationState::Active {
        return Err(EmbeddingError::RegistrationRetired);
    }
    if registration.mode == EmbeddingMode::Off {
        return Err(EmbeddingError::EmbeddingDisabled);
    }
    if response.provider_identity != registration.provider_identity
        || response.model_id != registration.model_id
    {
        return Err(EmbeddingError::ProviderSubstitution);
    }
    if response.vector.len() != registration.dimensions {
        return Err(EmbeddingError::DimensionMismatch {
            expected: registration.dimensions,
            actual: response.vector.len(),
        });
    }
    if response
        .vector
        .iter()
        .any(|component| !component.is_finite())
    {
        return Err(EmbeddingError::NonFiniteVector);
    }
    let norm_squared = response
        .vector
        .iter()
        .map(|component| f64::from(*component).powi(2))
        .sum::<f64>();
    if !norm_squared.is_finite() || norm_squared <= f64::EPSILON {
        return Err(EmbeddingError::ZeroNormVector);
    }
    let norm = norm_squared.sqrt();
    if registration.normalized && (norm - 1.0).abs() > 1.0e-3 {
        return Err(EmbeddingError::NormalizationMismatch { norm });
    }
    Ok(ValidatedEmbedding {
        dimensions: response.vector.len(),
        vector: response.vector,
        norm,
        registration_id: registration.registration_id.clone(),
        fingerprint: registration.fingerprint,
        input_digest,
        input_tokens: response.input_tokens,
        cost_units: response.cost_units,
    })
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PublicationGuard {
    pub record_id: Id,
    pub revision_digest: Digest,
    pub content_digest: Digest,
    pub registration_id: Id,
    pub registration_fingerprint: Digest,
}

#[derive(Clone, Debug, PartialEq)]
pub struct StoredEmbedding {
    pub record_id: Id,
    pub revision_digest: Digest,
    pub input_digest: Digest,
    pub registration_id: Id,
    pub fingerprint: Digest,
    pub vector: Vec<f32>,
    pub norm: f64,
}

#[derive(Clone, Debug, PartialEq)]
pub enum PublicationDecision {
    Replace(StoredEmbedding),
    Reject {
        reason: PublicationRefusal,
        retained: Option<StoredEmbedding>,
    },
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PublicationRefusal {
    ContentChanged,
    RevisionChanged,
    RegistrationChanged,
    RegistrationRetired,
    CandidateMismatch,
}

pub fn guarded_replacement(
    existing: Option<StoredEmbedding>,
    expected: &PublicationGuard,
    current: &PublicationGuard,
    current_registration: &EmbeddingRegistration,
    candidate: ValidatedEmbedding,
) -> PublicationDecision {
    let reject = |reason| PublicationDecision::Reject {
        reason,
        retained: existing.clone(),
    };
    if current_registration.state != RegistrationState::Active {
        return reject(PublicationRefusal::RegistrationRetired);
    }
    if expected.record_id != current.record_id {
        return reject(PublicationRefusal::CandidateMismatch);
    }
    if expected.revision_digest != current.revision_digest {
        return reject(PublicationRefusal::RevisionChanged);
    }
    if expected.content_digest != current.content_digest {
        return reject(PublicationRefusal::ContentChanged);
    }
    if expected.registration_id != current.registration_id
        || expected.registration_id != current_registration.registration_id
        || expected.registration_fingerprint != current.registration_fingerprint
        || expected.registration_fingerprint != current_registration.fingerprint
    {
        return reject(PublicationRefusal::RegistrationChanged);
    }
    if candidate.registration_id != expected.registration_id
        || candidate.fingerprint != expected.registration_fingerprint
        || candidate.input_digest != expected.content_digest
    {
        return reject(PublicationRefusal::CandidateMismatch);
    }
    PublicationDecision::Replace(StoredEmbedding {
        record_id: current.record_id.clone(),
        revision_digest: current.revision_digest,
        input_digest: candidate.input_digest,
        registration_id: candidate.registration_id,
        fingerprint: candidate.fingerprint,
        vector: candidate.vector,
        norm: candidate.norm,
    })
}

pub fn register_embedding(
    transaction: &Transaction<'_>,
    registration: &EmbeddingRegistration,
    now_ms: u64,
) -> MemoryResult<()> {
    registration
        .validate_fingerprint()
        .map_err(|_| invalid_registration())?;
    let scope = scope_digest(&registration.owner_scope).to_hex();
    let scope_exists: bool = transaction
        .query_row(
            "SELECT EXISTS(SELECT 1 FROM memory_scopes WHERE scope_digest=?1)",
            [&scope],
            |row| row.get(0),
        )
        .map_err(write_error)?;
    if !scope_exists {
        return Err(error(
            "SCOPE_NOT_FOUND",
            "the embedding scope is not registered",
            EffectState::NotStarted,
        ));
    }
    let existing: Option<(String, String)> = transaction
        .query_row(
            "SELECT fingerprint, state FROM embedding_registrations WHERE registration_id=?1",
            [registration.registration_id.as_str()],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .optional()
        .map_err(write_error)?;
    if let Some((fingerprint, state)) = existing {
        if fingerprint == registration.fingerprint.to_hex() && state == "active" {
            return Ok(());
        }
        return Err(error(
            "EMBEDDING_REGISTRATION_CONFLICT",
            "the embedding registration id was already used",
            EffectState::NotStarted,
        ));
    }
    if registration.state == RegistrationState::Active {
        transaction
            .execute(
                "UPDATE embedding_registrations SET state='retired', retired_at_ms=?1
                 WHERE owner_scope_digest=?2 AND state='active'",
                params![now_ms, scope],
            )
            .map_err(write_error)?;
    }
    transaction
        .execute(
            "INSERT INTO embedding_registrations(
                registration_id, owner_scope_digest, mode, provider_identity,
                model_id, dimensions, metric, normalized, fingerprint, state,
                created_at_ms, retired_at_ms
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, 'cosine', ?7, ?8, ?9, ?10, ?11)",
            params![
                registration.registration_id.as_str(),
                scope,
                registration.mode.wire_name(),
                registration.provider_identity,
                registration.model_id,
                registration.dimensions,
                registration.normalized,
                registration.fingerprint.to_hex(),
                registration_state_name(registration.state),
                now_ms,
                (registration.state != RegistrationState::Active).then_some(now_ms),
            ],
        )
        .map_err(write_error)?;
    Ok(())
}

pub fn retire_embedding(
    transaction: &Transaction<'_>,
    owner_scope: &Scope,
    registration_id: &Id,
    now_ms: u64,
) -> MemoryResult<bool> {
    let changed = transaction
        .execute(
            "UPDATE embedding_registrations SET state='retired', retired_at_ms=?1
             WHERE registration_id=?2 AND owner_scope_digest=?3 AND state='active'",
            params![
                now_ms,
                registration_id.as_str(),
                scope_digest(owner_scope).to_hex()
            ],
        )
        .map_err(write_error)?;
    Ok(changed == 1)
}

pub fn active_embedding(
    connection: &Connection,
    owner_scope: &Scope,
) -> MemoryResult<Option<EmbeddingRegistration>> {
    let row = connection
        .query_row(
            "SELECT registration_id, mode, provider_identity, model_id,
                    dimensions, normalized, fingerprint, state
             FROM embedding_registrations
             WHERE owner_scope_digest=?1 AND state='active'",
            [scope_digest(owner_scope).to_hex()],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, usize>(4)?,
                    row.get::<_, bool>(5)?,
                    row.get::<_, String>(6)?,
                    row.get::<_, String>(7)?,
                ))
            },
        )
        .optional()
        .map_err(read_error)?;
    row.map(|row| registration_from_row(owner_scope.clone(), row))
        .transpose()
}

pub fn publication_guard(
    connection: &Connection,
    record_id: &Id,
    registration: &EmbeddingRegistration,
) -> MemoryResult<PublicationGuard> {
    let row: Option<(String, String)> = connection
        .query_row(
            "SELECT r.current_revision_digest, v.content_digest
             FROM memory_records r
             JOIN memory_revisions v ON v.record_id=r.record_id
                                    AND v.revision=r.current_revision
             WHERE r.record_id=?1 AND r.owner_scope_digest=?2
               AND r.status NOT IN ('tombstoned','stale')",
            params![
                record_id.as_str(),
                scope_digest(&registration.owner_scope).to_hex()
            ],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .optional()
        .map_err(read_error)?;
    let (revision, content) = row.ok_or_else(|| {
        error(
            "EMBEDDING_TARGET_UNAVAILABLE",
            "the embedding target is missing or not current",
            EffectState::NotStarted,
        )
    })?;
    Ok(PublicationGuard {
        record_id: record_id.clone(),
        revision_digest: parse_digest(revision)?,
        content_digest: parse_digest(content)?,
        registration_id: registration.registration_id.clone(),
        registration_fingerprint: registration.fingerprint,
    })
}

pub fn publish_embedding(
    transaction: &Transaction<'_>,
    expected: &PublicationGuard,
    candidate: ValidatedEmbedding,
    now_ms: u64,
) -> MemoryResult<StoredEmbedding> {
    let owner_scope: String = transaction
        .query_row(
            "SELECT owner_scope_digest FROM memory_records WHERE record_id=?1",
            [expected.record_id.as_str()],
            |row| row.get(0),
        )
        .optional()
        .map_err(write_error)?
        .ok_or_else(|| stale_publication("the embedding record no longer exists"))?;
    let registration = registration_by_id(transaction, &expected.registration_id)?
        .ok_or_else(|| stale_publication("the embedding registration no longer exists"))?;
    if scope_digest(&registration.owner_scope).to_hex() != owner_scope {
        return Err(stale_publication(
            "the embedding registration no longer owns the record scope",
        ));
    }
    let current = publication_guard(transaction, &expected.record_id, &registration)?;
    let existing = read_stored_embedding(transaction, &expected.record_id)?;
    let replacement = guarded_replacement(existing, expected, &current, &registration, candidate);
    let PublicationDecision::Replace(stored) = replacement else {
        return Err(stale_publication(
            "the record content or embedding registration changed during inference",
        ));
    };
    let vector = encode_vector(&stored.vector);
    transaction
        .execute(
            "INSERT INTO memory_embeddings(
                record_id, revision_digest, registration_id, vector_f32,
                dimensions, norm, created_at_ms
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)
             ON CONFLICT(record_id, registration_id) DO UPDATE SET
                revision_digest=excluded.revision_digest,
                vector_f32=excluded.vector_f32,
                dimensions=excluded.dimensions,
                norm=excluded.norm,
                created_at_ms=excluded.created_at_ms",
            params![
                stored.record_id.as_str(),
                stored.revision_digest.to_hex(),
                stored.registration_id.as_str(),
                vector,
                stored.vector.len(),
                stored.norm,
                now_ms,
            ],
        )
        .map_err(write_error)?;
    Ok(stored)
}

pub fn read_compatible_embedding(
    connection: &Connection,
    record_id: &Id,
    fingerprint: Digest,
) -> MemoryResult<Option<StoredEmbedding>> {
    let stored = read_stored_embedding(connection, record_id)?;
    Ok(stored.filter(|embedding| embedding.fingerprint == fingerprint))
}

fn read_stored_embedding(
    connection: &Connection,
    record_id: &Id,
) -> MemoryResult<Option<StoredEmbedding>> {
    let row: Option<(String, String, String, String, Vec<u8>, f64)> = connection
        .query_row(
            "SELECT e.revision_digest, v.content_digest, e.registration_id,
                    g.fingerprint, e.vector_f32, e.norm
             FROM memory_embeddings e
             JOIN embedding_registrations g ON g.registration_id=e.registration_id
             JOIN memory_revisions v ON v.record_id=e.record_id
                                    AND v.revision_digest=e.revision_digest
             WHERE e.record_id=?1
             ORDER BY e.created_at_ms DESC, e.registration_id ASC LIMIT 1",
            [record_id.as_str()],
            |row| {
                Ok((
                    row.get(0)?,
                    row.get(1)?,
                    row.get(2)?,
                    row.get(3)?,
                    row.get(4)?,
                    row.get(5)?,
                ))
            },
        )
        .optional()
        .map_err(read_error)?;
    row.map(
        |(revision, input, registration, fingerprint, bytes, norm)| {
            Ok(StoredEmbedding {
                record_id: record_id.clone(),
                revision_digest: parse_digest(revision)?,
                input_digest: parse_digest(input)?,
                registration_id: Id::new(registration).map_err(|_| corrupt())?,
                fingerprint: parse_digest(fingerprint)?,
                vector: decode_vector(&bytes)?,
                norm,
            })
        },
    )
    .transpose()
}

fn registration_by_id(
    connection: &Connection,
    registration_id: &Id,
) -> MemoryResult<Option<EmbeddingRegistration>> {
    let row = connection
        .query_row(
            "SELECT s.scope_json, g.mode, g.provider_identity, g.model_id,
                    g.dimensions, g.normalized, g.fingerprint, g.state
             FROM embedding_registrations g
             JOIN memory_scopes s ON s.scope_digest=g.owner_scope_digest
             WHERE g.registration_id=?1",
            [registration_id.as_str()],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, usize>(4)?,
                    row.get::<_, bool>(5)?,
                    row.get::<_, String>(6)?,
                    row.get::<_, String>(7)?,
                ))
            },
        )
        .optional()
        .map_err(read_error)?;
    row.map(
        |(scope, mode, provider, model, dimensions, normalized, fingerprint, state)| {
            let scope: Scope = serde_json::from_str(&scope).map_err(|_| corrupt())?;
            registration_from_row(
                scope,
                (
                    registration_id.to_string(),
                    mode,
                    provider,
                    model,
                    dimensions,
                    normalized,
                    fingerprint,
                    state,
                ),
            )
        },
    )
    .transpose()
}

type RegistrationRow = (String, String, String, String, usize, bool, String, String);

fn registration_from_row(
    owner_scope: Scope,
    row: RegistrationRow,
) -> MemoryResult<EmbeddingRegistration> {
    let (id, mode, provider, model, dimensions, normalized, fingerprint, state) = row;
    let mode = match mode.as_str() {
        "off" => EmbeddingMode::Off,
        "local" => EmbeddingMode::Local,
        "remote-compatible" => EmbeddingMode::RemoteCompatible,
        "managed-service" => EmbeddingMode::ManagedService,
        _ => return Err(corrupt()),
    };
    let state = match state.as_str() {
        "active" => RegistrationState::Active,
        "retired" => RegistrationState::Retired,
        "failed" => RegistrationState::Failed,
        _ => return Err(corrupt()),
    };
    let registration = EmbeddingRegistration {
        registration_id: Id::new(id).map_err(|_| corrupt())?,
        owner_scope,
        mode,
        provider_identity: provider,
        model_id: model,
        dimensions,
        normalized,
        fingerprint: parse_digest(fingerprint)?,
        state,
    };
    registration.validate_fingerprint().map_err(|_| corrupt())?;
    Ok(registration)
}

fn registration_state_name(state: RegistrationState) -> &'static str {
    match state {
        RegistrationState::Active => "active",
        RegistrationState::Retired => "retired",
        RegistrationState::Failed => "failed",
    }
}

fn encode_vector(vector: &[f32]) -> Vec<u8> {
    vector
        .iter()
        .flat_map(|component| component.to_le_bytes())
        .collect()
}

fn decode_vector(bytes: &[u8]) -> MemoryResult<Vec<f32>> {
    if bytes.is_empty() || bytes.len() % 4 != 0 {
        return Err(corrupt());
    }
    let values = bytes
        .chunks_exact(4)
        .map(|chunk| f32::from_le_bytes(chunk.try_into().expect("four-byte chunk")))
        .collect::<Vec<_>>();
    if values.iter().any(|value| !value.is_finite()) {
        return Err(corrupt());
    }
    Ok(values)
}

fn parse_digest(value: String) -> MemoryResult<Digest> {
    Digest::from_str(&value).map_err(|_| corrupt())
}

fn invalid_registration() -> hypermid_contracts::Error {
    error(
        "INVALID_EMBEDDING_REGISTRATION",
        "the embedding registration is invalid",
        EffectState::NotStarted,
    )
}

fn stale_publication(message: &'static str) -> hypermid_contracts::Error {
    error("EMBEDDING_STALE_RESULT", message, EffectState::NotStarted)
}

fn corrupt() -> hypermid_contracts::Error {
    error(
        "STORE_CORRUPT",
        "embedding state violates the durable contract",
        EffectState::Unknown,
    )
}

fn read_error(_: rusqlite::Error) -> hypermid_contracts::Error {
    error(
        "STORE_READ_FAILED",
        "embedding state could not be read",
        EffectState::Unknown,
    )
}

fn write_error(_: rusqlite::Error) -> hypermid_contracts::Error {
    error(
        "STORE_WRITE_FAILED",
        "embedding state could not be written",
        EffectState::Unknown,
    )
}

pub fn cosine_similarity(left: &[f32], right: &[f32]) -> Option<f64> {
    if left.len() != right.len() || left.is_empty() {
        return None;
    }
    let mut dot = 0.0_f64;
    let mut left_norm = 0.0_f64;
    let mut right_norm = 0.0_f64;
    for (left, right) in left.iter().zip(right) {
        if !left.is_finite() || !right.is_finite() {
            return None;
        }
        let left = f64::from(*left);
        let right = f64::from(*right);
        dot += left * right;
        left_norm += left * left;
        right_norm += right * right;
    }
    if left_norm <= f64::EPSILON || right_norm <= f64::EPSILON {
        return None;
    }
    let score = dot / (left_norm.sqrt() * right_norm.sqrt());
    score.is_finite().then_some(score.clamp(-1.0, 1.0))
}

impl PartialOrd for ValidatedEmbedding {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        self.norm.partial_cmp(&other.norm)
    }
}

#[derive(Clone, Debug, Error, PartialEq)]
pub enum EmbeddingError {
    #[error("embedding registration is invalid")]
    InvalidRegistration,
    #[error("embedding registration fingerprint does not match its configuration")]
    FingerprintMismatch,
    #[error("embedding registration is retired")]
    RegistrationRetired,
    #[error("embedding mode is off")]
    EmbeddingDisabled,
    #[error("embedding provider or model was substituted")]
    ProviderSubstitution,
    #[error("embedding dimensions differ: expected {expected}, received {actual}")]
    DimensionMismatch { expected: usize, actual: usize },
    #[error("embedding contains a non-finite component")]
    NonFiniteVector,
    #[error("embedding has zero norm")]
    ZeroNormVector,
    #[error("embedding was declared normalized but its norm is {norm}")]
    NormalizationMismatch { norm: f64 },
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::MemoryStore;

    fn scope() -> Scope {
        Scope::new(Id::new("owner").unwrap(), Id::new("project").unwrap(), None)
    }

    fn registration() -> EmbeddingRegistration {
        EmbeddingRegistration::new(
            Id::new("embedding-1").unwrap(),
            scope(),
            EmbeddingMode::ManagedService,
            "centra",
            "embedding-large-v1",
            3,
            false,
        )
        .unwrap()
    }

    #[test]
    fn invalid_vectors_never_replace_a_valid_vector() {
        let registration = registration();
        let digest = Digest::sha256("current");
        let invalid = validate_provider_embedding(
            &registration,
            digest,
            ProviderEmbedding {
                provider_identity: "centra".into(),
                model_id: "embedding-large-v1".into(),
                vector: vec![0.0, f32::NAN, 1.0],
                input_tokens: None,
                cost_units: None,
            },
        );
        assert_eq!(invalid, Err(EmbeddingError::NonFiniteVector));
    }

    #[test]
    fn content_and_registration_changes_retain_the_previous_vector() {
        let registration = registration();
        let old_digest = Digest::sha256("old");
        let new_digest = Digest::sha256("new");
        let revision = Digest::sha256("revision");
        let guard = PublicationGuard {
            record_id: Id::new("record-1").unwrap(),
            revision_digest: revision,
            content_digest: old_digest,
            registration_id: registration.registration_id.clone(),
            registration_fingerprint: registration.fingerprint,
        };
        let existing = StoredEmbedding {
            record_id: guard.record_id.clone(),
            revision_digest: revision,
            input_digest: old_digest,
            registration_id: registration.registration_id.clone(),
            fingerprint: registration.fingerprint,
            vector: vec![1.0, 0.0, 0.0],
            norm: 1.0,
        };
        let candidate = validate_provider_embedding(
            &registration,
            old_digest,
            ProviderEmbedding {
                provider_identity: "centra".into(),
                model_id: "embedding-large-v1".into(),
                vector: vec![0.5, 0.5, 0.5],
                input_tokens: Some(1),
                cost_units: Some(1.0),
            },
        )
        .unwrap();
        let mut current = guard.clone();
        current.content_digest = new_digest;
        assert_eq!(
            guarded_replacement(
                Some(existing.clone()),
                &guard,
                &current,
                &registration,
                candidate
            ),
            PublicationDecision::Reject {
                reason: PublicationRefusal::ContentChanged,
                retained: Some(existing),
            }
        );
    }

    #[test]
    fn durable_publication_rechecks_content_and_preserves_the_valid_row() {
        let directory = tempfile::tempdir().unwrap();
        let mut store = MemoryStore::open(directory.path().join("memory.sqlite3")).unwrap();
        let scope = scope();
        store.ensure_scope(&scope, 1).unwrap();
        let registration = registration();
        let record_id = Id::new("record-1").unwrap();
        let first_revision = Digest::sha256("revision-1");
        let first_content = Digest::sha256("content-1");
        store
            .immediate(|transaction| {
                transaction
                    .raw()
                    .execute(
                        "INSERT INTO memory_records(
                        record_id, owner_scope_digest, kind, category, status,
                        current_revision, current_revision_digest,
                        normalized_content_digest, created_at_ms, updated_at_ms
                     ) VALUES (?1, ?2, 'fact', 'test', 'active', 1, ?3, ?4, 1, 1)",
                        params![
                            record_id.as_str(),
                            scope_digest(&scope).to_hex(),
                            first_revision.to_hex(),
                            first_content.to_hex()
                        ],
                    )
                    .map_err(write_error)?;
                transaction
                    .raw()
                    .execute(
                        "INSERT INTO memory_revisions(
                        record_id, revision, revision_digest, parent_revision_digest,
                        content, content_digest, metadata_json, author_scope_digest,
                        authored_at_ms, immutable_anchor
                     ) VALUES (?1, 1, ?2, NULL, 'content-1', ?3, '{}', ?4, 1, 0)",
                        params![
                            record_id.as_str(),
                            first_revision.to_hex(),
                            first_content.to_hex(),
                            scope_digest(&scope).to_hex()
                        ],
                    )
                    .map_err(write_error)?;
                register_embedding(transaction.raw(), &registration, 1)
            })
            .unwrap();
        let guard = store
            .read(|connection| publication_guard(connection, &record_id, &registration))
            .unwrap();
        let first = validate_provider_embedding(
            &registration,
            first_content,
            ProviderEmbedding {
                provider_identity: "centra".into(),
                model_id: "embedding-large-v1".into(),
                vector: vec![1.0, 0.0, 0.0],
                input_tokens: Some(1),
                cost_units: Some(1.0),
            },
        )
        .unwrap();
        store
            .immediate(|transaction| {
                publish_embedding(transaction.raw(), &guard, first.clone(), 2).map(|_| ())
            })
            .unwrap();

        let second_revision = Digest::sha256("revision-2");
        let second_content = Digest::sha256("content-2");
        store
            .immediate(|transaction| {
                transaction
                    .raw()
                    .execute(
                        "INSERT INTO memory_revisions(
                        record_id, revision, revision_digest, parent_revision_digest,
                        content, content_digest, metadata_json, author_scope_digest,
                        authored_at_ms, immutable_anchor
                     ) VALUES (?1, 2, ?2, ?3, 'content-2', ?4, '{}', ?5, 3, 0)",
                        params![
                            record_id.as_str(),
                            second_revision.to_hex(),
                            first_revision.to_hex(),
                            second_content.to_hex(),
                            scope_digest(&scope).to_hex()
                        ],
                    )
                    .map_err(write_error)?;
                transaction
                    .raw()
                    .execute(
                        "UPDATE memory_records SET current_revision=2,
                        current_revision_digest=?2, normalized_content_digest=?3,
                        updated_at_ms=3 WHERE record_id=?1",
                        params![
                            record_id.as_str(),
                            second_revision.to_hex(),
                            second_content.to_hex()
                        ],
                    )
                    .map_err(write_error)?;
                Ok(())
            })
            .unwrap();
        let failure = store
            .immediate(|transaction| {
                publish_embedding(transaction.raw(), &guard, first, 4).map(|_| ())
            })
            .unwrap_err();
        assert_eq!(failure.code, "EMBEDDING_STALE_RESULT");
        let retained = store
            .read(|connection| {
                read_compatible_embedding(connection, &record_id, registration.fingerprint)
            })
            .unwrap()
            .unwrap();
        assert_eq!(retained.revision_digest, first_revision);
        assert_eq!(retained.input_digest, first_content);
        assert_eq!(retained.vector, vec![1.0, 0.0, 0.0]);
    }
}
