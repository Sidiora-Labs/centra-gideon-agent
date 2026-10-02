use hypermid_contracts::{Cursor, Digest, Id, Scope};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
use thiserror::Error;

pub const SUMMARY_TIER_COUNT: usize = 4;
pub const MAX_SUMMARY_LOCALE_BYTES: usize = 64;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SummarySource {
    pub scope: Scope,
    pub session_id: Id,
    pub source_start: Cursor,
    pub source_end: Cursor,
    pub source_digest: Digest,
    pub input_tokens: u64,
}

impl SummarySource {
    pub fn validate(&self) -> Result<(), SummaryContractError> {
        if self.source_start.epoch != self.source_end.epoch || self.source_start > self.source_end {
            return Err(SummaryContractError::InvalidSourceRange);
        }
        if self.input_tokens == 0 {
            return Err(SummaryContractError::InvalidTokenLimit);
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SummaryJobSpec {
    pub job_id: Id,
    pub source: SummarySource,
    pub locale: String,
    pub max_input_tokens: u64,
    pub max_output_tokens: u64,
}

impl SummaryJobSpec {
    pub fn validate(&self) -> Result<(), SummaryContractError> {
        self.source.validate()?;
        if self.locale.len() < 2 || self.locale.len() > MAX_SUMMARY_LOCALE_BYTES {
            return Err(SummaryContractError::InvalidLocale);
        }
        if self.max_input_tokens == 0
            || self.max_output_tokens == 0
            || self.source.input_tokens > self.max_input_tokens
        {
            return Err(SummaryContractError::InvalidTokenLimit);
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SummaryJob {
    pub job_id: Id,
    pub source: SummarySource,
    pub lease_id: Id,
    pub fence: u64,
    pub lease_expires_at_ms: u64,
    pub tier_levels: [u8; SUMMARY_TIER_COUNT],
    pub locale: String,
    pub max_input_tokens: u64,
    pub max_output_tokens: u64,
    pub attempt: u8,
}

impl SummaryJob {
    pub fn validate(&self) -> Result<(), SummaryContractError> {
        self.source.validate()?;
        if self.tier_levels != [0, 1, 2, 3] {
            return Err(SummaryContractError::InvalidTierSet);
        }
        if self.locale.len() < 2 || self.locale.len() > MAX_SUMMARY_LOCALE_BYTES {
            return Err(SummaryContractError::InvalidLocale);
        }
        if self.max_input_tokens == 0
            || self.max_output_tokens == 0
            || self.source.input_tokens > self.max_input_tokens
        {
            return Err(SummaryContractError::InvalidTokenLimit);
        }
        if self.fence == 0 || self.lease_expires_at_ms == 0 || self.attempt == 0 {
            return Err(SummaryContractError::InvalidLease);
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SummaryTier {
    pub level: u8,
    pub content: String,
    pub content_digest: Digest,
    pub token_mass: u64,
}

impl SummaryTier {
    pub fn new(
        level: u8,
        content: impl Into<String>,
        token_mass: u64,
    ) -> Result<Self, SummaryContractError> {
        let content = content.into();
        if level >= SUMMARY_TIER_COUNT as u8 || content.is_empty() || token_mass == 0 {
            return Err(SummaryContractError::InvalidTier);
        }
        let content_digest = Digest::sha256(content.as_bytes());
        Ok(Self {
            level,
            content,
            content_digest,
            token_mass,
        })
    }

    pub fn validate(&self) -> Result<(), SummaryContractError> {
        if self.level >= SUMMARY_TIER_COUNT as u8
            || self.content.is_empty()
            || self.token_mass == 0
            || self.content_digest != Digest::sha256(self.content.as_bytes())
        {
            return Err(SummaryContractError::InvalidTier);
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SummaryCandidate {
    pub job_id: Id,
    pub lease_id: Id,
    pub fence: u64,
    pub source_digest: Digest,
    pub tiers: Vec<SummaryTier>,
    pub importance: f64,
}

impl SummaryCandidate {
    pub fn validate(&self, max_output_tokens: u64) -> Result<(), SummaryContractError> {
        if self.fence == 0
            || !self.importance.is_finite()
            || !(0.0..=1.0).contains(&self.importance)
        {
            return Err(SummaryContractError::InvalidCandidate);
        }
        if self.tiers.len() != SUMMARY_TIER_COUNT {
            return Err(SummaryContractError::InvalidTierSet);
        }
        let mut seen = BTreeSet::new();
        let mut total_tokens = 0_u64;
        let mut previous_token_mass = u64::MAX;
        for (expected, tier) in self.tiers.iter().enumerate() {
            tier.validate()?;
            if tier.level != expected as u8
                || !seen.insert(tier.level)
                || tier.token_mass > previous_token_mass
            {
                return Err(SummaryContractError::InvalidTierSet);
            }
            previous_token_mass = tier.token_mass;
            total_tokens = total_tokens
                .checked_add(tier.token_mass)
                .ok_or(SummaryContractError::InvalidTokenLimit)?;
        }
        if total_tokens > max_output_tokens {
            return Err(SummaryContractError::InvalidTokenLimit);
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SummaryRecord {
    pub summary_id: Id,
    pub scope: Scope,
    pub session_id: Id,
    pub source_start: Cursor,
    pub source_end: Cursor,
    pub source_digest: Digest,
    pub tiers: Vec<SummaryTier>,
    pub importance: f64,
    pub created_at_ms: u64,
}

impl SummaryRecord {
    pub fn validate(&self) -> Result<(), SummaryContractError> {
        SummarySource {
            scope: self.scope.clone(),
            session_id: self.session_id.clone(),
            source_start: self.source_start,
            source_end: self.source_end,
            source_digest: self.source_digest,
            input_tokens: 1,
        }
        .validate()?;
        SummaryCandidate {
            job_id: self.summary_id.clone(),
            lease_id: self.summary_id.clone(),
            fence: 1,
            source_digest: self.source_digest,
            tiers: self.tiers.clone(),
            importance: self.importance,
        }
        .validate(u64::MAX)?;
        if self.created_at_ms == 0 {
            return Err(SummaryContractError::InvalidCandidate);
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SummaryUsage {
    pub provider: String,
    pub model: String,
    pub input_tokens: Option<u64>,
    pub output_tokens: Option<u64>,
    pub duration_ms: u64,
}

impl SummaryUsage {
    pub fn validate(&self) -> Result<(), SummaryContractError> {
        if self.provider.len() > 160 || self.model.len() > 256 {
            return Err(SummaryContractError::InvalidUsage);
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SummaryOutcomeKind {
    Committed,
    Cancelled,
    TimedOut,
    StaleSource,
    Malformed,
    Superseded,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SummaryOutcome {
    pub job_id: Id,
    pub attempt: u8,
    pub kind: SummaryOutcomeKind,
    pub at_ms: u64,
}

#[derive(Clone, Copy, Debug, Eq, Error, PartialEq)]
pub enum SummaryContractError {
    #[error("summary source range is invalid")]
    InvalidSourceRange,
    #[error("summary locale is invalid")]
    InvalidLocale,
    #[error("summary token limit is invalid")]
    InvalidTokenLimit,
    #[error("summary lease is invalid")]
    InvalidLease,
    #[error("summary tier is invalid")]
    InvalidTier,
    #[error("summary tiers must be exactly levels zero through three")]
    InvalidTierSet,
    #[error("summary candidate is invalid")]
    InvalidCandidate,
    #[error("summary usage is invalid")]
    InvalidUsage,
}
