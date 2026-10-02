use hypermid_contracts::Digest;
use hypermid_core::reclaim::{
    plan_reclaim, PressureInput, PressurePolicy, ReclaimCandidate, ReclaimPlan, ReclaimViolation,
};
use hypermid_core::reduction::ReductionBoundary;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ReclaimLatch {
    pub decision_digest: Digest,
    pub plan: ReclaimPlan,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ReclaimerState {
    pub schema_version: u32,
    pub latches: Vec<ReclaimLatch>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReclaimDecision {
    pub plan: ReclaimPlan,
    pub replayed: bool,
}

#[derive(Clone, Debug, Default)]
pub struct Reclaimer {
    latches: BTreeMap<Digest, ReclaimPlan>,
}

impl Reclaimer {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn decide(
        &mut self,
        input: &PressureInput,
        policy: &PressurePolicy,
        boundary: ReductionBoundary,
        candidates: &[ReclaimCandidate],
    ) -> Result<ReclaimDecision, ReclaimerError> {
        let planned = plan_reclaim(input, policy, boundary, candidates)?;
        if let Some(latched) = self.latches.get(&planned.decision_digest) {
            return Ok(ReclaimDecision {
                plan: latched.clone(),
                replayed: true,
            });
        }
        self.latches
            .insert(planned.decision_digest, planned.clone());
        Ok(ReclaimDecision {
            plan: planned,
            replayed: false,
        })
    }

    pub fn state(&self) -> ReclaimerState {
        ReclaimerState {
            schema_version: 1,
            latches: self
                .latches
                .iter()
                .map(|(decision_digest, plan)| ReclaimLatch {
                    decision_digest: *decision_digest,
                    plan: plan.clone(),
                })
                .collect(),
        }
    }

    pub fn from_state(state: ReclaimerState) -> Result<Self, ReclaimerError> {
        if state.schema_version != 1 {
            return Err(ReclaimerError::UnsupportedState);
        }
        let mut latches = BTreeMap::new();
        for latch in state.latches {
            if latch.decision_digest != latch.plan.decision_digest
                || latches.insert(latch.decision_digest, latch.plan).is_some()
            {
                return Err(ReclaimerError::CorruptState);
            }
        }
        Ok(Self { latches })
    }
}

#[derive(Debug, thiserror::Error)]
pub enum ReclaimerError {
    #[error("reclaim plan is invalid: {0}")]
    Plan(#[from] ReclaimViolation),
    #[error("reclaimer state version is unsupported")]
    UnsupportedState,
    #[error("reclaimer state is internally inconsistent")]
    CorruptState,
}
