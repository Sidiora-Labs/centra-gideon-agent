use std::{thread, time::Duration};

use hypermid_contracts::Digest;
use thiserror::Error;

use crate::{
    manifest::OverlapPolicy,
    supervisor::{SupervisedModuleSpec, Supervisor, SupervisorError, SupervisorState},
};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ReplacementMode {
    OverlapSafe,
    Exclusive,
}

#[derive(Clone, Debug)]
pub struct ReplacementPlan {
    pub module_id: String,
    pub incumbent_generation: u64,
    pub incumbent_digest: Digest,
    pub candidate_digest: Digest,
    pub mode: ReplacementMode,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ReplacementOutcome {
    Applied { generation: u64 },
    Refused { reason: String },
}

#[derive(Clone, Debug, Default)]
pub struct ReplacementCoordinator;

#[derive(Debug, Error)]
pub enum ReplacementError {
    #[error(transparent)]
    Supervisor(#[from] SupervisorError),
    #[error("replacement candidate process failed: {0}")]
    CandidateIo(#[from] std::io::Error),
    #[error("module {0} has no incumbent")]
    NoIncumbent(String),
    #[error("replacement plan is stale")]
    StalePlan,
}

impl ReplacementCoordinator {
    pub fn preview(
        supervisor: &Supervisor,
        candidate: &SupervisedModuleSpec,
    ) -> Result<ReplacementPlan, ReplacementError> {
        let incumbent = supervisor
            .snapshot()?
            .into_iter()
            .find(|module| module.module_id == candidate.module_id)
            .ok_or_else(|| ReplacementError::NoIncumbent(candidate.module_id.clone()))?;
        Ok(ReplacementPlan {
            module_id: candidate.module_id.clone(),
            incumbent_generation: incumbent.spawn_generation,
            incumbent_digest: incumbent.artifact_digest,
            candidate_digest: candidate.artifact_digest,
            mode: if candidate.overlap == OverlapPolicy::Safe {
                ReplacementMode::OverlapSafe
            } else {
                ReplacementMode::Exclusive
            },
        })
    }

    pub fn apply(
        supervisor: &Supervisor,
        plan: &ReplacementPlan,
        candidate: SupervisedModuleSpec,
        now_ms: u64,
        warmup_ms: u64,
    ) -> Result<ReplacementOutcome, ReplacementError> {
        let current = supervisor
            .snapshot()?
            .into_iter()
            .find(|module| module.module_id == plan.module_id)
            .ok_or_else(|| ReplacementError::NoIncumbent(plan.module_id.clone()))?;
        if current.spawn_generation != plan.incumbent_generation
            || current.artifact_digest != plan.incumbent_digest
            || candidate.artifact_digest != plan.candidate_digest
        {
            return Err(ReplacementError::StalePlan);
        }
        match plan.mode {
            ReplacementMode::OverlapSafe => {
                let mut warmed = supervisor.warm_candidate(candidate, now_ms)?;
                let steps = warmup_ms.div_ceil(5).max(1);
                for _ in 0..steps {
                    if !warmed.is_running()? {
                        supervisor.discard_candidate(
                            warmed,
                            now_ms.saturating_add(warmup_ms),
                            "warmup_failed",
                        )?;
                        return Ok(ReplacementOutcome::Refused {
                            reason: "candidate exited during warmup".into(),
                        });
                    }
                    thread::sleep(Duration::from_millis(5));
                }
                let generation = warmed.generation();
                supervisor.promote_candidate(warmed, now_ms.saturating_add(warmup_ms))?;
                Ok(ReplacementOutcome::Applied { generation })
            }
            ReplacementMode::Exclusive => {
                supervisor.set_enabled(&plan.module_id, false, now_ms)?;
                let ceiling = now_ms.saturating_add(warmup_ms.max(1));
                let mut observed = now_ms;
                loop {
                    supervisor.tick(observed)?;
                    let snapshot = supervisor
                        .snapshot()?
                        .into_iter()
                        .find(|module| module.module_id == plan.module_id)
                        .ok_or_else(|| ReplacementError::NoIncumbent(plan.module_id.clone()))?;
                    if matches!(
                        snapshot.state,
                        SupervisorState::Disabled | SupervisorState::Failed
                    ) && snapshot.pid.is_none()
                    {
                        break;
                    }
                    if observed >= ceiling {
                        return Ok(ReplacementOutcome::Refused {
                            reason: "incumbent did not stop within the replacement ceiling".into(),
                        });
                    }
                    thread::sleep(Duration::from_millis(5));
                    observed = observed.saturating_add(5);
                }
                supervisor.configure(candidate)?;
                let generation = supervisor.spawn(&plan.module_id, observed)?;
                Ok(ReplacementOutcome::Applied { generation })
            }
        }
    }
}
