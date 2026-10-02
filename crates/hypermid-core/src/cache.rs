use hypermid_contracts::{Digest, Id};
use serde::{Deserialize, Serialize};
use std::error::Error;
use std::fmt;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum HardBoundaryReason {
    CacheExpiry,
    ProviderProfileChange,
    IncompatiblePolicyRevision,
    IdentityRebind,
    ExplicitFlush,
    TailPressure,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CachedChangeKind {
    None,
    Reduction,
    TierDecay,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CacheOutcomeKind {
    Applied,
    Deferred,
    PressureRefused,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct CachedRegion {
    pub digest: Digest,
    pub bytes: Vec<u8>,
    pub item_ids: Vec<Id>,
    pub summary_ids: Vec<Id>,
    pub token_mass: u64,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct CacheGeneration {
    pub generation: u64,
    pub provider_profile_digest: Digest,
    pub policy_revision: u64,
    pub baseline: CachedRegion,
    pub delta: CachedRegion,
    pub live_tail: CachedRegion,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub boundary_reason: Option<HardBoundaryReason>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct CacheTransition {
    pub next: CacheGeneration,
    pub cached_change: CachedChangeKind,
    pub boundary: Option<HardBoundaryReason>,
    pub overflow_requires_cached_change: bool,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct CacheOutcome {
    pub kind: CacheOutcomeKind,
    pub generation: u64,
    pub reason_code: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CachePolicyError {
    InvalidGeneration,
    InvalidPolicyRevision,
    GenerationChangedWithoutBoundary,
    GenerationNotAdvanced,
    BaselineChangedWithoutBoundary,
    DeltaNotEmptyAfterFold,
    ProfileChangedWithoutBoundary,
    PolicyChangedWithoutBoundary,
}

impl fmt::Display for CachePolicyError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "{self:?}")
    }
}
impl Error for CachePolicyError {}

pub fn evaluate_transition(
    current: Option<&CacheGeneration>,
    transition: &CacheTransition,
) -> Result<CacheOutcome, CachePolicyError> {
    if transition.next.generation == 0 {
        return Err(CachePolicyError::InvalidGeneration);
    }
    if transition.next.policy_revision == 0 {
        return Err(CachePolicyError::InvalidPolicyRevision);
    }
    let Some(current) = current else {
        return Ok(CacheOutcome {
            kind: CacheOutcomeKind::Applied,
            generation: transition.next.generation,
            reason_code: "cache_initialized".to_owned(),
        });
    };
    if transition.cached_change != CachedChangeKind::None && transition.boundary.is_none() {
        return Ok(CacheOutcome {
            kind: if transition.overflow_requires_cached_change {
                CacheOutcomeKind::PressureRefused
            } else {
                CacheOutcomeKind::Deferred
            },
            generation: current.generation,
            reason_code: if transition.overflow_requires_cached_change {
                "cached_change_pressure_refused"
            } else {
                "cached_change_deferred"
            }
            .to_owned(),
        });
    }
    match transition.boundary {
        None => {
            if transition.next.generation != current.generation {
                return Err(CachePolicyError::GenerationChangedWithoutBoundary);
            }
            if transition.next.provider_profile_digest != current.provider_profile_digest {
                return Err(CachePolicyError::ProfileChangedWithoutBoundary);
            }
            if transition.next.policy_revision != current.policy_revision {
                return Err(CachePolicyError::PolicyChangedWithoutBoundary);
            }
            if transition.next.baseline.bytes != current.baseline.bytes
                || transition.next.baseline.digest != current.baseline.digest
            {
                return Err(CachePolicyError::BaselineChangedWithoutBoundary);
            }
            Ok(CacheOutcome {
                kind: CacheOutcomeKind::Applied,
                generation: current.generation,
                reason_code: "delta_refreshed".to_owned(),
            })
        }
        Some(_) => {
            if transition.next.generation <= current.generation {
                return Err(CachePolicyError::GenerationNotAdvanced);
            }
            if !is_empty_region(&transition.next.delta) {
                return Err(CachePolicyError::DeltaNotEmptyAfterFold);
            }
            Ok(CacheOutcome {
                kind: CacheOutcomeKind::Applied,
                generation: transition.next.generation,
                reason_code: "baseline_folded".to_owned(),
            })
        }
    }
}

fn is_empty_region(region: &CachedRegion) -> bool {
    let canonical_empty = region.bytes.is_empty() || region.bytes.as_slice() == b"[]";
    canonical_empty
        && region.digest == Digest::sha256([])
        && region.item_ids.is_empty()
        && region.summary_ids.is_empty()
        && region.token_mass == 0
}
