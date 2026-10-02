use crate::reduction::{ContextRegion, ReductionBoundary};
use hypermid_contracts::{Digest, Id};
use serde::{Deserialize, Serialize};
use std::cmp::Ordering;
use std::collections::BTreeSet;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PressureBand {
    Normal,
    Advisory,
    Action,
    Emergency,
    HardWall,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ReclaimClass {
    ExplicitReduction,
    SupersededEdit,
    ObsoleteToolOutput,
    CompletedToolOutput,
    HistoricalDetail,
    UnprotectedProse,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ReclaimCandidate {
    pub candidate_id: Id,
    pub item_ids: Vec<Id>,
    pub reclaim_tags: Vec<u64>,
    pub class: ReclaimClass,
    pub region: ContextRegion,
    pub recoverable_mass: u64,
    pub recoverable_bytes: u64,
    pub age_rank: u64,
    pub dependency_safe: bool,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub call_ids: Vec<Id>,
    pub tool_arcs_complete: bool,
    #[serde(default)]
    pub protected_reason_codes: Vec<String>,
    pub requires_rewrite: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub rewrite_cost_nanodollars: Option<u64>,
}

impl ReclaimCandidate {
    pub fn validate(&self) -> Result<(), ReclaimViolation> {
        if self.item_ids.is_empty()
            || self.item_ids.len() > 4_096
            || self.reclaim_tags.is_empty()
            || self.reclaim_tags.len() > 4_096
            || self.recoverable_mass == 0
            || self.reclaim_tags.contains(&0)
            || self.item_ids.iter().collect::<BTreeSet<_>>().len() != self.item_ids.len()
            || self.reclaim_tags.iter().collect::<BTreeSet<_>>().len() != self.reclaim_tags.len()
            || self.call_ids.is_empty() && !self.tool_arcs_complete
            || self.requires_rewrite && self.rewrite_cost_nanodollars == Some(0)
            || !self.requires_rewrite && self.rewrite_cost_nanodollars.is_some()
        {
            return Err(ReclaimViolation::InvalidCandidate);
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PressurePolicy {
    pub advisory_basis_points: u16,
    pub action_basis_points: u16,
    pub emergency_basis_points: u16,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub max_rewrite_cost_nanodollars: Option<u64>,
}

impl Default for PressurePolicy {
    fn default() -> Self {
        Self {
            advisory_basis_points: 7_000,
            action_basis_points: 8_200,
            emergency_basis_points: 9_300,
            max_rewrite_cost_nanodollars: None,
        }
    }
}

impl PressurePolicy {
    pub fn validate(&self) -> Result<(), ReclaimViolation> {
        if self.advisory_basis_points == 0
            || self.advisory_basis_points >= self.action_basis_points
            || self.action_basis_points >= self.emergency_basis_points
            || self.emergency_basis_points >= 10_000
        {
            return Err(ReclaimViolation::InvalidPolicy);
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PressureInput {
    pub calibrated_mass: u64,
    pub safe_input_mass: u64,
    pub cache_generation: u64,
    pub projection_digest: Digest,
    pub policy_revision: u64,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ReclaimDisposition {
    Diagnostic,
    Applied,
    Deferred,
    Refused,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ReclaimChoice {
    pub candidate_id: Id,
    pub item_ids: Vec<Id>,
    pub reclaim_tags: Vec<u64>,
    pub class: ReclaimClass,
    pub recoverable_mass: u64,
    pub recoverable_bytes: u64,
    pub reason_code: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub rewrite_cost_nanodollars: Option<u64>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ReclaimExclusion {
    pub candidate_id: Id,
    pub reason_code: String,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ReclaimPlan {
    pub decision_digest: Digest,
    pub band: PressureBand,
    pub disposition: ReclaimDisposition,
    pub input_mass: u64,
    pub target_mass: u64,
    pub required_savings: u64,
    pub estimated_savings: u64,
    pub estimated_bytes: u64,
    pub rewrite_cost_nanodollars: u64,
    pub chosen: Vec<ReclaimChoice>,
    pub exclusions: Vec<ReclaimExclusion>,
    pub reason_codes: Vec<String>,
    pub boundary: ReductionBoundary,
}

impl ReclaimPlan {
    pub fn dispatch_allowed(&self) -> bool {
        self.disposition != ReclaimDisposition::Refused
    }
}

pub fn pressure_band(
    input: &PressureInput,
    policy: &PressurePolicy,
) -> Result<PressureBand, ReclaimViolation> {
    policy.validate()?;
    if input.safe_input_mass == 0 {
        return Err(ReclaimViolation::InvalidBudget);
    }
    if input.calibrated_mass > input.safe_input_mass {
        return Ok(PressureBand::HardWall);
    }
    let usage = basis_points(input.calibrated_mass, input.safe_input_mass);
    Ok(if usage >= policy.emergency_basis_points as u64 {
        PressureBand::Emergency
    } else if usage >= policy.action_basis_points as u64 {
        PressureBand::Action
    } else if usage >= policy.advisory_basis_points as u64 {
        PressureBand::Advisory
    } else {
        PressureBand::Normal
    })
}

pub fn plan_reclaim(
    input: &PressureInput,
    policy: &PressurePolicy,
    boundary: ReductionBoundary,
    candidates: &[ReclaimCandidate],
) -> Result<ReclaimPlan, ReclaimViolation> {
    let band = pressure_band(input, policy)?;
    let target_mass = match band {
        PressureBand::Normal | PressureBand::Advisory => input.calibrated_mass,
        PressureBand::Action => threshold_mass(input.safe_input_mass, policy.advisory_basis_points),
        PressureBand::Emergency => {
            threshold_mass(input.safe_input_mass, policy.action_basis_points)
        }
        PressureBand::HardWall => input.safe_input_mass,
    };
    let required_savings = input.calibrated_mass.saturating_sub(target_mass);
    let mut ordered = candidates.to_vec();
    for candidate in &ordered {
        candidate.validate()?;
    }
    ordered.sort_by(candidate_order);

    let mut chosen = Vec::new();
    let mut exclusions = Vec::new();
    let mut estimated_savings = 0_u64;
    let mut estimated_bytes = 0_u64;
    let mut rewrite_cost = 0_u64;
    let mut selected_items = BTreeSet::new();
    let mut selected_tags = BTreeSet::new();

    for candidate in ordered {
        if required_savings == 0 {
            exclusions.push(exclusion(&candidate, "pressure_below_action"));
            continue;
        }
        if !candidate.protected_reason_codes.is_empty() {
            exclusions.push(exclusion(
                &candidate,
                candidate.protected_reason_codes[0].as_str(),
            ));
            continue;
        }
        if !candidate.dependency_safe {
            exclusions.push(exclusion(&candidate, "dependency_unsafe"));
            continue;
        }
        if !candidate.call_ids.is_empty() && !candidate.tool_arcs_complete {
            exclusions.push(exclusion(&candidate, "incomplete_tool_arc"));
            continue;
        }
        if candidate.region == ContextRegion::Tail
            && !matches!(band, PressureBand::Emergency | PressureBand::HardWall)
            && candidate.class != ReclaimClass::ExplicitReduction
        {
            exclusions.push(exclusion(&candidate, "tail_requires_emergency"));
            continue;
        }
        if !boundary.can_apply(candidate.region) {
            exclusions.push(exclusion(&candidate, "awaiting_cache_boundary"));
            continue;
        }
        if candidate
            .item_ids
            .iter()
            .any(|item_id| selected_items.contains(item_id))
            || candidate
                .reclaim_tags
                .iter()
                .any(|tag| selected_tags.contains(tag))
        {
            exclusions.push(exclusion(&candidate, "overlapping_candidate"));
            continue;
        }
        let candidate_cost = if candidate.requires_rewrite {
            let Some(cost) = candidate.rewrite_cost_nanodollars else {
                exclusions.push(exclusion(&candidate, "rewrite_price_unknown"));
                continue;
            };
            let Some(limit) = policy.max_rewrite_cost_nanodollars else {
                exclusions.push(exclusion(&candidate, "rewrite_not_authorized"));
                continue;
            };
            if rewrite_cost.saturating_add(cost) > limit {
                exclusions.push(exclusion(&candidate, "rewrite_budget_exceeded"));
                continue;
            }
            cost
        } else {
            0
        };
        if estimated_savings >= required_savings {
            exclusions.push(exclusion(&candidate, "not_needed"));
            continue;
        }
        estimated_savings = estimated_savings.saturating_add(candidate.recoverable_mass);
        estimated_bytes = estimated_bytes.saturating_add(candidate.recoverable_bytes);
        rewrite_cost = rewrite_cost.saturating_add(candidate_cost);
        selected_items.extend(candidate.item_ids.iter().cloned());
        selected_tags.extend(candidate.reclaim_tags.iter().copied());
        chosen.push(ReclaimChoice {
            candidate_id: candidate.candidate_id,
            item_ids: candidate.item_ids,
            reclaim_tags: candidate.reclaim_tags,
            class: candidate.class,
            recoverable_mass: candidate.recoverable_mass,
            recoverable_bytes: candidate.recoverable_bytes,
            reason_code: class_reason(candidate.class).to_owned(),
            rewrite_cost_nanodollars: candidate.rewrite_cost_nanodollars,
        });
    }

    let disposition = if band == PressureBand::HardWall && estimated_savings < required_savings {
        ReclaimDisposition::Refused
    } else if required_savings == 0 {
        ReclaimDisposition::Diagnostic
    } else if estimated_savings >= required_savings {
        ReclaimDisposition::Applied
    } else {
        ReclaimDisposition::Deferred
    };
    let mut reason_codes = vec![band_reason(band).to_owned()];
    if disposition == ReclaimDisposition::Refused {
        reason_codes.push("unsafe_overflow_refused".to_owned());
    } else if disposition == ReclaimDisposition::Deferred {
        reason_codes.push("insufficient_eligible_savings".to_owned());
    }
    let decision_digest = plan_digest(input, &boundary, &chosen, &exclusions, disposition);
    Ok(ReclaimPlan {
        decision_digest,
        band,
        disposition,
        input_mass: input.calibrated_mass,
        target_mass,
        required_savings,
        estimated_savings,
        estimated_bytes,
        rewrite_cost_nanodollars: rewrite_cost,
        chosen,
        exclusions,
        reason_codes,
        boundary,
    })
}

fn candidate_order(left: &ReclaimCandidate, right: &ReclaimCandidate) -> Ordering {
    left.class
        .cmp(&right.class)
        .then_with(|| right.recoverable_mass.cmp(&left.recoverable_mass))
        .then_with(|| right.age_rank.cmp(&left.age_rank))
        .then_with(|| left.candidate_id.cmp(&right.candidate_id))
}

fn exclusion(candidate: &ReclaimCandidate, reason_code: &str) -> ReclaimExclusion {
    ReclaimExclusion {
        candidate_id: candidate.candidate_id.clone(),
        reason_code: reason_code.to_owned(),
    }
}

fn class_reason(class: ReclaimClass) -> &'static str {
    match class {
        ReclaimClass::ExplicitReduction => "explicit_reduction",
        ReclaimClass::SupersededEdit => "superseded_edit",
        ReclaimClass::ObsoleteToolOutput => "obsolete_tool_output",
        ReclaimClass::CompletedToolOutput => "completed_tool_output",
        ReclaimClass::HistoricalDetail => "historical_detail",
        ReclaimClass::UnprotectedProse => "unprotected_prose",
    }
}

fn band_reason(band: PressureBand) -> &'static str {
    match band {
        PressureBand::Normal => "pressure_normal",
        PressureBand::Advisory => "pressure_advisory",
        PressureBand::Action => "pressure_action",
        PressureBand::Emergency => "pressure_emergency",
        PressureBand::HardWall => "pressure_hard_wall",
    }
}

fn basis_points(value: u64, capacity: u64) -> u64 {
    (u128::from(value) * 10_000 / u128::from(capacity))
        .try_into()
        .unwrap_or(u64::MAX)
}

fn threshold_mass(capacity: u64, threshold: u16) -> u64 {
    (u128::from(capacity) * u128::from(threshold) / 10_000)
        .try_into()
        .unwrap_or(u64::MAX)
}

fn plan_digest(
    input: &PressureInput,
    boundary: &ReductionBoundary,
    chosen: &[ReclaimChoice],
    exclusions: &[ReclaimExclusion],
    disposition: ReclaimDisposition,
) -> Digest {
    let mut bytes = Vec::new();
    bytes.extend_from_slice(input.projection_digest.as_bytes());
    bytes.extend_from_slice(&input.cache_generation.to_be_bytes());
    bytes.extend_from_slice(&input.policy_revision.to_be_bytes());
    bytes.extend_from_slice(&input.calibrated_mass.to_be_bytes());
    bytes.extend_from_slice(&input.safe_input_mass.to_be_bytes());
    bytes.extend_from_slice(&(boundary.kind as u8).to_be_bytes());
    bytes.extend_from_slice(boundary.reason_code.as_bytes());
    bytes.push(disposition as u8);
    for choice in chosen {
        bytes.extend_from_slice(choice.candidate_id.as_str().as_bytes());
        bytes.extend_from_slice(&choice.recoverable_mass.to_be_bytes());
    }
    for exclusion in exclusions {
        bytes.extend_from_slice(exclusion.candidate_id.as_str().as_bytes());
        bytes.extend_from_slice(exclusion.reason_code.as_bytes());
    }
    Digest::sha256(bytes)
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, thiserror::Error)]
pub enum ReclaimViolation {
    #[error("pressure policy thresholds are invalid")]
    InvalidPolicy,
    #[error("safe provider input budget must be positive")]
    InvalidBudget,
    #[error("reclaim candidate is structurally invalid")]
    InvalidCandidate,
}
