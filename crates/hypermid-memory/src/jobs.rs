use crate::embedding::PublicationRefusal;
use hypermid_contracts::{Digest, Id};
use std::cmp::Ordering;
use std::time::Duration;
use thiserror::Error;

#[derive(Clone, Debug, PartialEq)]
pub struct EmbeddingBudget {
    pub max_items: u64,
    pub max_input_tokens: u64,
    pub max_requests: u64,
    pub max_cost_units: f64,
    pub max_retries: u64,
    pub max_wall_time: Duration,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct EmbeddingUsage {
    pub items: u64,
    pub input_tokens: u64,
    pub requests: u64,
    pub cost_units: f64,
    pub retries: u64,
    pub input_tokens_unknown: bool,
    pub cost_unknown: bool,
}

#[derive(Clone, Debug)]
pub struct BudgetTracker {
    budget: EmbeddingBudget,
    usage: EmbeddingUsage,
}

impl BudgetTracker {
    pub fn new(budget: EmbeddingBudget) -> Result<Self, JobError> {
        if budget.max_items == 0
            || budget.max_requests == 0
            || !budget.max_cost_units.is_finite()
            || budget.max_cost_units < 0.0
            || budget.max_wall_time.is_zero()
        {
            return Err(JobError::InvalidBudget);
        }
        Ok(Self {
            budget,
            usage: EmbeddingUsage::default(),
        })
    }

    pub fn usage(&self) -> &EmbeddingUsage {
        &self.usage
    }

    pub fn reserve(
        &mut self,
        estimated_input_tokens: u64,
        estimated_cost_units: f64,
        elapsed: Duration,
    ) -> Result<(), JobError> {
        if !estimated_cost_units.is_finite() || estimated_cost_units < 0.0 {
            return Err(JobError::InvalidBudget);
        }
        if elapsed >= self.budget.max_wall_time
            || self.usage.items.saturating_add(1) > self.budget.max_items
            || self.usage.requests.saturating_add(1) > self.budget.max_requests
            || self
                .usage
                .input_tokens
                .saturating_add(estimated_input_tokens)
                > self.budget.max_input_tokens
            || self.usage.cost_units + estimated_cost_units > self.budget.max_cost_units
        {
            return Err(JobError::BudgetExhausted);
        }
        self.usage.items += 1;
        self.usage.requests += 1;
        self.usage.input_tokens += estimated_input_tokens;
        self.usage.cost_units += estimated_cost_units;
        Ok(())
    }

    pub fn settle(
        &mut self,
        estimated_input_tokens: u64,
        estimated_cost_units: f64,
        actual_input_tokens: Option<u64>,
        actual_cost_units: Option<f64>,
    ) -> Result<(), JobError> {
        match actual_input_tokens {
            Some(actual) => {
                self.usage.input_tokens = self
                    .usage
                    .input_tokens
                    .saturating_sub(estimated_input_tokens)
                    .saturating_add(actual);
            }
            None => self.usage.input_tokens_unknown = true,
        }
        match actual_cost_units {
            Some(actual) if actual.is_finite() && actual >= 0.0 => {
                self.usage.cost_units =
                    (self.usage.cost_units - estimated_cost_units).max(0.0) + actual;
            }
            Some(_) => return Err(JobError::InvalidUsage),
            None => self.usage.cost_unknown = true,
        }
        Ok(())
    }

    pub fn reserve_retry(&mut self) -> Result<(), JobError> {
        if self.usage.retries >= self.budget.max_retries {
            return Err(JobError::BudgetExhausted);
        }
        self.usage.retries += 1;
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CandidateState {
    Missing,
    Stale,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BackfillCandidate {
    pub record_id: Id,
    pub revision_digest: Digest,
    pub content_digest: Digest,
    pub state: CandidateState,
}

impl Ord for BackfillCandidate {
    fn cmp(&self, other: &Self) -> Ordering {
        priority(self.state)
            .cmp(&priority(other.state))
            .then_with(|| self.record_id.cmp(&other.record_id))
    }
}

impl PartialOrd for BackfillCandidate {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

fn priority(state: CandidateState) -> u8 {
    match state {
        CandidateState::Missing => 0,
        CandidateState::Stale => 1,
    }
}

pub fn order_backfill(mut candidates: Vec<BackfillCandidate>) -> Vec<BackfillCandidate> {
    candidates.sort();
    candidates
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum EmbeddingFailureClass {
    Transport,
    Refusal,
    MissingCredentials,
    RateLimited,
    InvalidVector,
    ContentChanged,
    RegistrationRetired,
    Budget,
}

impl From<PublicationRefusal> for EmbeddingFailureClass {
    fn from(value: PublicationRefusal) -> Self {
        match value {
            PublicationRefusal::ContentChanged | PublicationRefusal::RevisionChanged => {
                Self::ContentChanged
            }
            PublicationRefusal::RegistrationChanged | PublicationRefusal::RegistrationRetired => {
                Self::RegistrationRetired
            }
            PublicationRefusal::CandidateMismatch => Self::InvalidVector,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum EmbeddingJobPhase {
    Missing,
    Stale,
    Complete,
    Cooldown,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EmbeddingJobProgress {
    pub job_id: Id,
    pub phase: EmbeddingJobPhase,
    pub checkpoint_record_id: Option<Id>,
    pub attempted_count: u64,
    pub accepted_count: u64,
    pub rejected_count: u64,
    pub consecutive_stalls: u32,
    pub cooldown_until_ms: Option<u64>,
}

impl EmbeddingJobProgress {
    pub fn new(job_id: Id) -> Self {
        Self {
            job_id,
            phase: EmbeddingJobPhase::Missing,
            checkpoint_record_id: None,
            attempted_count: 0,
            accepted_count: 0,
            rejected_count: 0,
            consecutive_stalls: 0,
            cooldown_until_ms: None,
        }
    }

    pub fn can_run(&self, now_ms: u64) -> bool {
        self.cooldown_until_ms.is_none_or(|until| now_ms >= until)
    }

    pub fn accepted(&mut self, record_id: Id) {
        self.attempted_count += 1;
        self.accepted_count += 1;
        self.consecutive_stalls = 0;
        self.checkpoint_record_id = Some(record_id);
        self.cooldown_until_ms = None;
    }

    pub fn rejected(
        &mut self,
        record_id: Id,
        class: EmbeddingFailureClass,
        now_ms: u64,
        stall_threshold: u32,
        cooldown_ms: u64,
    ) {
        self.attempted_count += 1;
        self.rejected_count += 1;
        self.checkpoint_record_id = Some(record_id);
        if matches!(
            class,
            EmbeddingFailureClass::Transport
                | EmbeddingFailureClass::MissingCredentials
                | EmbeddingFailureClass::RateLimited
                | EmbeddingFailureClass::Refusal
        ) {
            self.consecutive_stalls = self.consecutive_stalls.saturating_add(1);
            if stall_threshold > 0 && self.consecutive_stalls >= stall_threshold {
                self.phase = EmbeddingJobPhase::Cooldown;
                self.cooldown_until_ms = Some(now_ms.saturating_add(cooldown_ms));
            }
        }
    }
}

#[derive(Clone, Debug, Error, Eq, PartialEq)]
pub enum JobError {
    #[error("embedding budget is invalid")]
    InvalidBudget,
    #[error("embedding budget is exhausted")]
    BudgetExhausted,
    #[error("provider usage is invalid")]
    InvalidUsage,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bounded_jobs_prioritize_missing_and_persist_cooldown() {
        let missing = BackfillCandidate {
            record_id: Id::new("missing").unwrap(),
            revision_digest: Digest::sha256("r1"),
            content_digest: Digest::sha256("c1"),
            state: CandidateState::Missing,
        };
        let stale = BackfillCandidate {
            record_id: Id::new("stale").unwrap(),
            revision_digest: Digest::sha256("r2"),
            content_digest: Digest::sha256("c2"),
            state: CandidateState::Stale,
        };
        assert_eq!(order_backfill(vec![stale, missing.clone()])[0], missing);

        let mut job = EmbeddingJobProgress::new(Id::new("job-1").unwrap());
        job.rejected(
            Id::new("record-1").unwrap(),
            EmbeddingFailureClass::RateLimited,
            100,
            2,
            500,
        );
        assert!(job.can_run(100));
        job.rejected(
            Id::new("record-2").unwrap(),
            EmbeddingFailureClass::RateLimited,
            200,
            2,
            500,
        );
        assert_eq!(job.phase, EmbeddingJobPhase::Cooldown);
        assert!(!job.can_run(699));
        assert!(job.can_run(700));
    }

    #[test]
    fn usage_unknown_is_distinct_from_zero() {
        let mut tracker = BudgetTracker::new(EmbeddingBudget {
            max_items: 2,
            max_input_tokens: 100,
            max_requests: 2,
            max_cost_units: 10.0,
            max_retries: 1,
            max_wall_time: Duration::from_secs(1),
        })
        .unwrap();
        tracker.reserve(10, 1.0, Duration::ZERO).unwrap();
        tracker.settle(10, 1.0, None, None).unwrap();
        assert!(tracker.usage().input_tokens_unknown);
        assert!(tracker.usage().cost_unknown);
        assert_eq!(tracker.usage().input_tokens, 10);
        assert_eq!(tracker.usage().cost_units, 1.0);
    }
}
