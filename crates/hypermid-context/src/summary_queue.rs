use crate::journal::Journal;
use hypermid_contracts::{Digest, Id, Scope};
use hypermid_core::history::JournalRange;
use hypermid_core::summary::{
    SummaryCandidate, SummaryContractError, SummaryJob, SummaryJobSpec, SummaryOutcome,
    SummaryOutcomeKind, SummaryRecord, SummaryUsage,
};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};
use thiserror::Error;

const DEFAULT_BACKOFF_MS: u64 = 1_000;

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "snake_case", tag = "state", content = "value")]
enum JobState {
    Pending { available_at_ms: u64 },
    Leased(SummaryJob),
    Committed(SummaryRecord),
    Terminal(SummaryOutcomeKind),
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
struct QueueEntry {
    spec: SummaryJobSpec,
    attempt: u8,
    fence: u64,
    state: JobState,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SummaryQueueState {
    schema_version: u32,
    global_concurrency: usize,
    max_attempts: u8,
    entries: Vec<QueueEntry>,
    outcomes: Vec<SummaryOutcome>,
    usage: Vec<(Id, SummaryUsage)>,
}

#[derive(Clone, Debug)]
pub struct SummaryQueue {
    global_concurrency: usize,
    max_attempts: u8,
    entries: BTreeMap<Id, QueueEntry>,
    source_jobs: BTreeMap<SourceKey, Id>,
    outcomes: Vec<SummaryOutcome>,
    usage: BTreeMap<Id, SummaryUsage>,
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
struct SourceKey {
    owner_id: Id,
    project_id: Id,
    workspace_id: Option<Id>,
    session_id: Id,
    start_epoch: u64,
    start_sequence: u64,
    end_epoch: u64,
    end_sequence: u64,
    digest: Digest,
}

impl SourceKey {
    fn from_spec(spec: &SummaryJobSpec) -> Self {
        Self {
            owner_id: spec.source.scope.owner_id.clone(),
            project_id: spec.source.scope.project_id.clone(),
            workspace_id: spec.source.scope.workspace_id.clone(),
            session_id: spec.source.session_id.clone(),
            start_epoch: spec.source.source_start.epoch,
            start_sequence: spec.source.source_start.sequence,
            end_epoch: spec.source.source_end.epoch,
            end_sequence: spec.source.source_end.sequence,
            digest: spec.source.source_digest,
        }
    }
}

impl SummaryQueue {
    pub fn new(global_concurrency: usize, max_attempts: u8) -> Result<Self, SummaryQueueError> {
        if global_concurrency == 0 || max_attempts == 0 {
            return Err(SummaryQueueError::InvalidConfiguration);
        }
        Ok(Self {
            global_concurrency,
            max_attempts,
            entries: BTreeMap::new(),
            source_jobs: BTreeMap::new(),
            outcomes: Vec::new(),
            usage: BTreeMap::new(),
        })
    }

    pub fn schedule(
        &mut self,
        spec: SummaryJobSpec,
        now_ms: u64,
    ) -> Result<bool, SummaryQueueError> {
        spec.validate()?;
        if let Some(existing) = self.entries.get(&spec.job_id) {
            return if existing.spec == spec {
                Ok(false)
            } else {
                Err(SummaryQueueError::JobConflict)
            };
        }
        let source_key = SourceKey::from_spec(&spec);
        if self.source_jobs.contains_key(&source_key) {
            return Ok(false);
        }
        self.source_jobs.insert(source_key, spec.job_id.clone());
        self.entries.insert(
            spec.job_id.clone(),
            QueueEntry {
                spec,
                attempt: 0,
                fence: 0,
                state: JobState::Pending {
                    available_at_ms: now_ms,
                },
            },
        );
        Ok(true)
    }

    #[allow(clippy::too_many_arguments)]
    pub fn schedule_from_journal(
        &mut self,
        journal: &Journal,
        range: JournalRange,
        job_id: Id,
        input_tokens: u64,
        locale: impl Into<String>,
        max_input_tokens: u64,
        max_output_tokens: u64,
        now_ms: u64,
    ) -> Result<bool, SummaryQueueError> {
        let source_digest = journal
            .source_digest(range)
            .map_err(|_| SummaryQueueError::JournalUnavailable)?;
        self.schedule(
            SummaryJobSpec {
                job_id,
                source: hypermid_core::summary::SummarySource {
                    scope: journal.scope().clone(),
                    session_id: journal.session_id().clone(),
                    source_start: range.start,
                    source_end: range.end,
                    source_digest,
                    input_tokens,
                },
                locale: locale.into(),
                max_input_tokens,
                max_output_tokens,
            },
            now_ms,
        )
    }

    pub fn claim_next(
        &mut self,
        now_ms: u64,
        lease_ttl_ms: u64,
    ) -> Result<Option<SummaryJob>, SummaryQueueError> {
        if lease_ttl_ms == 0 {
            return Err(SummaryQueueError::InvalidLease);
        }
        self.expire_leases(now_ms);
        let active = self
            .entries
            .values()
            .filter(|entry| matches!(&entry.state, JobState::Leased(_)))
            .count();
        if active >= self.global_concurrency {
            return Ok(None);
        }
        let active_sessions = self
            .entries
            .values()
            .filter_map(|entry| match &entry.state {
                JobState::Leased(job) => Some(job.source.session_id.clone()),
                _ => None,
            })
            .collect::<BTreeSet<_>>();
        let selected = self.entries.iter().find_map(|(job_id, entry)| {
            let JobState::Pending { available_at_ms } = &entry.state else {
                return None;
            };
            (*available_at_ms <= now_ms
                && entry.attempt < self.max_attempts
                && !active_sessions.contains(&entry.spec.source.session_id))
            .then(|| job_id.clone())
        });
        let Some(job_id) = selected else {
            return Ok(None);
        };
        let entry = self.entries.get_mut(&job_id).expect("selected queue entry");
        entry.attempt = entry
            .attempt
            .checked_add(1)
            .ok_or(SummaryQueueError::AttemptsExhausted)?;
        entry.fence = entry
            .fence
            .checked_add(1)
            .ok_or(SummaryQueueError::FenceExhausted)?;
        let lease_expires_at_ms = now_ms
            .checked_add(lease_ttl_ms)
            .ok_or(SummaryQueueError::InvalidLease)?;
        let lease_id = Id::new(format!(
            "summary-lease:{}:{}",
            entry.spec.job_id, entry.fence
        ))
        .map_err(|_| SummaryQueueError::InvalidLease)?;
        let job = SummaryJob {
            job_id: entry.spec.job_id.clone(),
            source: entry.spec.source.clone(),
            lease_id,
            fence: entry.fence,
            lease_expires_at_ms,
            tier_levels: [0, 1, 2, 3],
            locale: entry.spec.locale.clone(),
            max_input_tokens: entry.spec.max_input_tokens,
            max_output_tokens: entry.spec.max_output_tokens,
            attempt: entry.attempt,
        };
        job.validate()?;
        entry.state = JobState::Leased(job.clone());
        Ok(Some(job))
    }

    pub fn publish(
        &mut self,
        candidate: SummaryCandidate,
        current_scope: &Scope,
        current_source_digest: Digest,
        usage: SummaryUsage,
        now_ms: u64,
    ) -> Result<SummaryRecord, SummaryQueueError> {
        let entry = self
            .entries
            .get(&candidate.job_id)
            .ok_or(SummaryQueueError::JobNotFound)?;
        let JobState::Leased(job) = &entry.state else {
            return Err(SummaryQueueError::LeaseNotActive);
        };
        let job = job.clone();
        if candidate.lease_id != job.lease_id || candidate.fence != job.fence {
            return Err(SummaryQueueError::StaleFence);
        }
        if now_ms >= job.lease_expires_at_ms {
            self.reject_active(&candidate.job_id, SummaryOutcomeKind::TimedOut, now_ms)?;
            return Err(SummaryQueueError::LeaseExpired);
        }
        if current_scope != &job.source.scope {
            return Err(SummaryQueueError::ScopeMismatch);
        }
        if candidate.source_digest != job.source.source_digest
            || current_source_digest != job.source.source_digest
        {
            self.reject_active(&candidate.job_id, SummaryOutcomeKind::StaleSource, now_ms)?;
            return Err(SummaryQueueError::StaleSource);
        }
        if candidate.validate(job.max_output_tokens).is_err() || usage.validate().is_err() {
            self.reject_active(&candidate.job_id, SummaryOutcomeKind::Malformed, now_ms)?;
            return Err(SummaryQueueError::MalformedCandidate);
        }

        let record = SummaryRecord {
            summary_id: Id::new(format!("summary:{}:{}", job.job_id, job.fence))
                .map_err(|_| SummaryQueueError::MalformedCandidate)?,
            scope: job.source.scope.clone(),
            session_id: job.source.session_id.clone(),
            source_start: job.source.source_start,
            source_end: job.source.source_end,
            source_digest: job.source.source_digest,
            tiers: candidate.tiers,
            importance: candidate.importance,
            created_at_ms: now_ms,
        };
        record.validate()?;
        let outcome = SummaryOutcome {
            job_id: job.job_id.clone(),
            attempt: job.attempt,
            kind: SummaryOutcomeKind::Committed,
            at_ms: now_ms,
        };

        let entry = self
            .entries
            .get_mut(&candidate.job_id)
            .expect("validated queue entry");
        entry.state = JobState::Committed(record.clone());
        self.usage.insert(record.summary_id.clone(), usage);
        self.outcomes.push(outcome);
        Ok(record)
    }

    pub fn publish_from_journal(
        &mut self,
        candidate: SummaryCandidate,
        journal: &Journal,
        usage: SummaryUsage,
        now_ms: u64,
    ) -> Result<SummaryRecord, SummaryQueueError> {
        let entry = self
            .entries
            .get(&candidate.job_id)
            .ok_or(SummaryQueueError::JobNotFound)?;
        if &entry.spec.source.scope != journal.scope()
            || &entry.spec.source.session_id != journal.session_id()
        {
            return Err(SummaryQueueError::ScopeMismatch);
        }
        let range = JournalRange::new(entry.spec.source.source_start, entry.spec.source.source_end)
            .map_err(|_| SummaryQueueError::JournalUnavailable)?;
        let current_source_digest = journal
            .source_digest(range)
            .map_err(|_| SummaryQueueError::JournalUnavailable)?;
        self.publish(
            candidate,
            journal.scope(),
            current_source_digest,
            usage,
            now_ms,
        )
    }

    pub fn fail(
        &mut self,
        job_id: &Id,
        lease_id: &Id,
        fence: u64,
        retryable: bool,
        now_ms: u64,
    ) -> Result<(), SummaryQueueError> {
        let entry = self
            .entries
            .get(job_id)
            .ok_or(SummaryQueueError::JobNotFound)?;
        let JobState::Leased(job) = &entry.state else {
            return Err(SummaryQueueError::LeaseNotActive);
        };
        if &job.lease_id != lease_id || job.fence != fence {
            return Err(SummaryQueueError::StaleFence);
        }
        let kind = if retryable {
            SummaryOutcomeKind::TimedOut
        } else {
            SummaryOutcomeKind::Malformed
        };
        self.reject_active(job_id, kind, now_ms)
    }

    pub fn cancel(&mut self, job_id: &Id, now_ms: u64) -> Result<(), SummaryQueueError> {
        let entry = self
            .entries
            .get_mut(job_id)
            .ok_or(SummaryQueueError::JobNotFound)?;
        if matches!(&entry.state, JobState::Committed(_)) {
            return Err(SummaryQueueError::AlreadyCommitted);
        }
        entry.state = JobState::Terminal(SummaryOutcomeKind::Cancelled);
        self.outcomes.push(SummaryOutcome {
            job_id: job_id.clone(),
            attempt: entry.attempt,
            kind: SummaryOutcomeKind::Cancelled,
            at_ms: now_ms,
        });
        Ok(())
    }

    pub fn records_for_session(&self, scope: &Scope, session_id: &Id) -> Vec<SummaryRecord> {
        let mut records = self
            .entries
            .values()
            .filter_map(|entry| match &entry.state {
                JobState::Committed(record)
                    if &record.scope == scope && &record.session_id == session_id =>
                {
                    Some(record.clone())
                }
                _ => None,
            })
            .collect::<Vec<_>>();
        records.sort_by_key(|record| (record.source_start, record.source_end));
        records
    }

    pub fn usage_for(&self, summary_id: &Id) -> Option<&SummaryUsage> {
        self.usage.get(summary_id)
    }

    pub fn outcomes(&self) -> &[SummaryOutcome] {
        &self.outcomes
    }

    pub fn rebuild_from_sources(
        &mut self,
        sources: impl IntoIterator<Item = SummaryJobSpec>,
        now_ms: u64,
    ) -> Result<usize, SummaryQueueError> {
        let mut scheduled = 0;
        for source in sources {
            scheduled += usize::from(self.schedule(source, now_ms)?);
        }
        Ok(scheduled)
    }

    pub fn state(&self) -> SummaryQueueState {
        SummaryQueueState {
            schema_version: 1,
            global_concurrency: self.global_concurrency,
            max_attempts: self.max_attempts,
            entries: self.entries.values().cloned().collect(),
            outcomes: self.outcomes.clone(),
            usage: self
                .usage
                .iter()
                .map(|(summary_id, usage)| (summary_id.clone(), usage.clone()))
                .collect(),
        }
    }

    pub fn from_state(state: SummaryQueueState) -> Result<Self, SummaryQueueError> {
        if state.schema_version != 1 || state.global_concurrency == 0 || state.max_attempts == 0 {
            return Err(SummaryQueueError::InvalidState);
        }
        let mut queue = Self::new(state.global_concurrency, state.max_attempts)?;
        for entry in state.entries {
            entry.spec.validate()?;
            if entry.attempt > state.max_attempts
                || entry.fence < u64::from(entry.attempt)
                || matches!(&entry.state, JobState::Leased(job) if job.job_id != entry.spec.job_id || job.source != entry.spec.source || job.attempt != entry.attempt || job.fence != entry.fence)
                || matches!(&entry.state, JobState::Committed(record) if record.scope != entry.spec.source.scope || record.session_id != entry.spec.source.session_id || record.source_start != entry.spec.source.source_start || record.source_end != entry.spec.source.source_end || record.source_digest != entry.spec.source.source_digest || record.validate().is_err())
            {
                return Err(SummaryQueueError::InvalidState);
            }
            let source_key = SourceKey::from_spec(&entry.spec);
            if queue
                .source_jobs
                .insert(source_key, entry.spec.job_id.clone())
                .is_some()
                || queue
                    .entries
                    .insert(entry.spec.job_id.clone(), entry)
                    .is_some()
            {
                return Err(SummaryQueueError::InvalidState);
            }
        }
        for (summary_id, usage) in state.usage {
            usage.validate()?;
            if queue.usage.insert(summary_id, usage).is_some() {
                return Err(SummaryQueueError::InvalidState);
            }
        }
        queue.outcomes = state.outcomes;
        Ok(queue)
    }

    fn expire_leases(&mut self, now_ms: u64) {
        let expired = self
            .entries
            .iter()
            .filter_map(|(job_id, entry)| match &entry.state {
                JobState::Leased(job) if now_ms >= job.lease_expires_at_ms => Some(job_id.clone()),
                _ => None,
            })
            .collect::<Vec<_>>();
        for job_id in expired {
            let _ = self.reject_active(&job_id, SummaryOutcomeKind::TimedOut, now_ms);
        }
    }

    fn reject_active(
        &mut self,
        job_id: &Id,
        kind: SummaryOutcomeKind,
        now_ms: u64,
    ) -> Result<(), SummaryQueueError> {
        let entry = self
            .entries
            .get_mut(job_id)
            .ok_or(SummaryQueueError::JobNotFound)?;
        if !matches!(&entry.state, JobState::Leased(_)) {
            return Err(SummaryQueueError::LeaseNotActive);
        }
        self.outcomes.push(SummaryOutcome {
            job_id: job_id.clone(),
            attempt: entry.attempt,
            kind,
            at_ms: now_ms,
        });
        if entry.attempt < self.max_attempts
            && matches!(
                kind,
                SummaryOutcomeKind::TimedOut | SummaryOutcomeKind::Malformed
            )
        {
            let exponent = u32::from(entry.attempt.saturating_sub(1).min(20));
            let delay = DEFAULT_BACKOFF_MS.saturating_mul(1_u64 << exponent);
            entry.state = JobState::Pending {
                available_at_ms: now_ms.saturating_add(delay),
            };
        } else {
            entry.state = JobState::Terminal(kind);
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, Error, PartialEq)]
pub enum SummaryQueueError {
    #[error("summary queue configuration is invalid")]
    InvalidConfiguration,
    #[error("summary job conflicts with an existing job id")]
    JobConflict,
    #[error("summary job was not found")]
    JobNotFound,
    #[error("summary lease is invalid")]
    InvalidLease,
    #[error("summary lease is not active")]
    LeaseNotActive,
    #[error("summary lease expired")]
    LeaseExpired,
    #[error("summary lease was superseded")]
    StaleFence,
    #[error("summary source changed before publication")]
    StaleSource,
    #[error("summary scope does not match the scheduled source")]
    ScopeMismatch,
    #[error("summary candidate is malformed")]
    MalformedCandidate,
    #[error("summary was already committed")]
    AlreadyCommitted,
    #[error("summary attempts are exhausted")]
    AttemptsExhausted,
    #[error("summary fence is exhausted")]
    FenceExhausted,
    #[error("summary queue state is invalid")]
    InvalidState,
    #[error("summary source journal is unavailable or the range is invalid")]
    JournalUnavailable,
    #[error(transparent)]
    Contract(#[from] SummaryContractError),
}
