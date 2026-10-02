use crate::budget::{BudgetError, HistoryBudget};
use crate::summary::SummaryTier;
use hypermid_contracts::{Digest, Id};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
use std::error::Error;
use std::fmt;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct HistoricalSpan {
    pub summary_id: Id,
    pub tiers: [SummaryTier; 4],
    pub age_rank: u64,
    pub importance_basis_points: u16,
    pub protected: bool,
    pub recurrence: u32,
    pub dependency_reach: u32,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct TierSelectionRequest {
    pub generation: u64,
    pub budget: HistoryBudget,
    pub spans: Vec<HistoricalSpan>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct TierDecision {
    pub summary_id: Id,
    pub level: u8,
    pub token_mass: u64,
    pub content_digest: Digest,
    pub reason_code: String,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct TierSelection {
    pub generation: u64,
    pub available_history: u64,
    pub selected_mass: u64,
    pub decisions: Vec<TierDecision>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DecayError {
    InvalidGeneration,
    DuplicateSummary,
    InvalidTierLevel,
    InvalidTierDigest,
    InvalidTierMass,
    InvalidImportance,
    MinimumTiersExceedBudget,
    ProtectedTiersExceedBudget,
    Budget(BudgetError),
    Serialization,
}
impl fmt::Display for DecayError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "{self:?}")
    }
}
impl Error for DecayError {}
impl From<BudgetError> for DecayError {
    fn from(value: BudgetError) -> Self {
        Self::Budget(value)
    }
}

fn validate_span(span: &HistoricalSpan) -> Result<(), DecayError> {
    if span.importance_basis_points > 10_000 {
        return Err(DecayError::InvalidImportance);
    }
    for (index, tier) in span.tiers.iter().enumerate() {
        if tier.level as usize != index {
            return Err(DecayError::InvalidTierLevel);
        }
        if tier.validate().is_err() {
            return Err(DecayError::InvalidTierDigest);
        }
        if tier.token_mass == 0 || (index > 0 && tier.token_mass > span.tiers[index - 1].token_mass)
        {
            return Err(DecayError::InvalidTierMass);
        }
    }
    Ok(())
}

pub fn select_tiers(request: &TierSelectionRequest) -> Result<TierSelection, DecayError> {
    if request.generation == 0 {
        return Err(DecayError::InvalidGeneration);
    }
    let available = request.budget.available_history()?;
    let mut seen = BTreeSet::new();
    for span in &request.spans {
        if !seen.insert(span.summary_id.clone()) {
            return Err(DecayError::DuplicateSummary);
        }
        validate_span(span)?;
    }

    let mut levels = vec![3_usize; request.spans.len()];
    let mut selected_mass = request.spans.iter().try_fold(0_u64, |total, span| {
        total
            .checked_add(span.tiers[3].token_mass)
            .ok_or(DecayError::MinimumTiersExceedBudget)
    })?;
    if selected_mass > available {
        return Err(DecayError::MinimumTiersExceedBudget);
    }

    let mut order = (0..request.spans.len()).collect::<Vec<_>>();
    order.sort_by(|left, right| {
        let left = &request.spans[*left];
        let right = &request.spans[*right];
        right
            .protected
            .cmp(&left.protected)
            .then_with(|| {
                right
                    .importance_basis_points
                    .cmp(&left.importance_basis_points)
            })
            .then_with(|| left.age_rank.cmp(&right.age_rank))
            .then_with(|| right.recurrence.cmp(&left.recurrence))
            .then_with(|| right.dependency_reach.cmp(&left.dependency_reach))
            .then_with(|| left.summary_id.cmp(&right.summary_id))
    });

    for index in order {
        let span = &request.spans[index];
        for next_level in (0..levels[index]).rev() {
            let extra = span.tiers[next_level].token_mass - span.tiers[next_level + 1].token_mass;
            if selected_mass
                .checked_add(extra)
                .is_some_and(|mass| mass <= available)
            {
                selected_mass += extra;
                levels[index] = next_level;
            } else if span.protected {
                return Err(DecayError::ProtectedTiersExceedBudget);
            } else {
                break;
            }
        }
    }

    let decisions = request
        .spans
        .iter()
        .zip(levels)
        .map(|(span, level)| {
            let tier = &span.tiers[level];
            TierDecision {
                summary_id: span.summary_id.clone(),
                level: tier.level,
                token_mass: tier.token_mass,
                content_digest: tier.content_digest,
                reason_code: if span.protected {
                    "protected_detail"
                } else if level == 3 {
                    "budget_decay"
                } else {
                    "priority_detail"
                }
                .to_owned(),
            }
        })
        .collect();
    Ok(TierSelection {
        generation: request.generation,
        available_history: available,
        selected_mass,
        decisions,
    })
}

pub fn selection_input_digest(request: &TierSelectionRequest) -> Result<Digest, DecayError> {
    let value = serde_json::to_value(request).map_err(|_| DecayError::Serialization)?;
    let bytes = serde_json::to_vec(&value).map_err(|_| DecayError::Serialization)?;
    Ok(Digest::sha256(bytes))
}
