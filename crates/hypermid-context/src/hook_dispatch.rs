use hypermid_contracts::Id;
use hypermid_core::hooks::{
    HookDecision, HookEvent, HookFailure, HookKind, HookOutcome, HookOutcomeRecord, HookPhase,
    HookViolation, SyntheticBlockRef, MAX_SYNTHETIC_BLOCKS, MAX_SYNTHETIC_TOKENS,
};
use std::collections::{BTreeSet, VecDeque};
use std::sync::{Arc, Mutex};
use std::time::Instant;

pub const MAX_HOOK_RETENTION: usize = 4_096;
pub const DEFAULT_HOOK_RETENTION: usize = 256;

pub trait ContextHook: Send + Sync {
    fn hook_id(&self) -> &Id;
    fn kinds(&self) -> &BTreeSet<HookKind>;
    fn phases(&self) -> &BTreeSet<HookPhase>;
    fn timeout_ms(&self) -> u64;
    fn call(&self, event: &HookEvent) -> Result<HookDecision, HookFailure>;
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum FailurePolicy {
    Continue,
    Deny,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct DispatchResult {
    pub allowed: bool,
    pub reason_code: Option<String>,
    pub synthetic_blocks: Vec<SyntheticBlockRef>,
    pub outcomes: Vec<HookOutcomeRecord>,
}

#[derive(Clone)]
pub struct HookOutcomeStore {
    inner: Arc<Mutex<OutcomeState>>,
}

struct OutcomeState {
    next_sequence: u64,
    retention: usize,
    outcomes: VecDeque<HookOutcomeRecord>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OutcomeReplay {
    pub gap: bool,
    pub oldest_cursor: u64,
    pub next_cursor: u64,
    pub outcomes: Vec<HookOutcomeRecord>,
}

impl HookOutcomeStore {
    pub fn new(retention: usize) -> Result<Self, HookDispatchError> {
        if retention == 0 || retention > MAX_HOOK_RETENTION {
            return Err(HookDispatchError::InvalidRetention);
        }
        Ok(Self {
            inner: Arc::new(Mutex::new(OutcomeState {
                next_sequence: 1,
                retention,
                outcomes: VecDeque::with_capacity(retention),
            })),
        })
    }

    pub fn append(
        &self,
        mut record: HookOutcomeRecord,
    ) -> Result<HookOutcomeRecord, HookDispatchError> {
        let mut state = self.inner.lock().map_err(|_| HookDispatchError::Poisoned)?;
        record.sequence = state.next_sequence;
        state.next_sequence = state
            .next_sequence
            .checked_add(1)
            .ok_or(HookDispatchError::CursorExhausted)?;
        if state.outcomes.len() == state.retention {
            state.outcomes.pop_front();
        }
        state.outcomes.push_back(record.clone());
        Ok(record)
    }

    pub fn read_after(&self, cursor: u64) -> Result<OutcomeReplay, HookDispatchError> {
        let state = self.inner.lock().map_err(|_| HookDispatchError::Poisoned)?;
        let oldest = state
            .outcomes
            .front()
            .map(|record| record.sequence)
            .unwrap_or(state.next_sequence);
        let latest = state.next_sequence.saturating_sub(1);
        let gap = cursor.saturating_add(1) < oldest;
        let outcomes = state
            .outcomes
            .iter()
            .filter(|record| record.sequence > cursor)
            .cloned()
            .collect();
        Ok(OutcomeReplay {
            gap,
            oldest_cursor: oldest.saturating_sub(1),
            next_cursor: latest,
            outcomes,
        })
    }
}

impl Default for HookOutcomeStore {
    fn default() -> Self {
        Self::new(DEFAULT_HOOK_RETENTION).expect("default hook retention is valid")
    }
}

pub struct HookDispatcher {
    hooks: Vec<Arc<dyn ContextHook>>,
    outcomes: HookOutcomeStore,
    failure_policy: FailurePolicy,
}

impl HookDispatcher {
    pub fn new(outcomes: HookOutcomeStore, failure_policy: FailurePolicy) -> Self {
        Self {
            hooks: vec![],
            outcomes,
            failure_policy,
        }
    }

    pub fn register(&mut self, hook: Arc<dyn ContextHook>) {
        self.hooks.push(hook);
        self.hooks
            .sort_by(|left, right| left.hook_id().cmp(right.hook_id()));
    }

    pub fn dispatch_pre(
        &self,
        event: &HookEvent,
        recorded_at_ms: u64,
    ) -> Result<DispatchResult, HookDispatchError> {
        if event.phase != HookPhase::Pre {
            return Err(HookDispatchError::WrongPhase);
        }
        self.dispatch(event, recorded_at_ms, true)
    }

    pub fn dispatch_post(
        &self,
        event: &HookEvent,
        recorded_at_ms: u64,
    ) -> Result<DispatchResult, HookDispatchError> {
        if event.phase != HookPhase::Post {
            return Err(HookDispatchError::WrongPhase);
        }
        self.dispatch(event, recorded_at_ms, false)
    }

    fn dispatch(
        &self,
        event: &HookEvent,
        recorded_at_ms: u64,
        enforcing: bool,
    ) -> Result<DispatchResult, HookDispatchError> {
        let mut result = DispatchResult {
            allowed: true,
            reason_code: None,
            synthetic_blocks: vec![],
            outcomes: vec![],
        };
        for hook in self.hooks.iter().filter(|hook| {
            hook.kinds().contains(&event.kind) && hook.phases().contains(&event.phase)
        }) {
            let started = Instant::now();
            let decision = hook.call(event);
            let duration_ms = started.elapsed().as_millis().min(u64::MAX as u128) as u64;
            let timed_out = duration_ms > hook.timeout_ms();
            let (outcome, reason_code, blocks) = if timed_out {
                (
                    HookOutcome::TimedOut,
                    Some("hook_timed_out".to_owned()),
                    vec![],
                )
            } else {
                match decision {
                    Ok(decision) => {
                        decision.validate()?;
                        (
                            decision.outcome,
                            decision.reason_code,
                            decision.synthetic_blocks,
                        )
                    }
                    Err(failure) => (HookOutcome::Failed, Some(failure.reason_code), vec![]),
                }
            };
            let record = self.outcomes.append(HookOutcomeRecord {
                sequence: 0,
                event_id: event.event_id.clone(),
                hook_id: hook.hook_id().clone(),
                kind: event.kind,
                phase: event.phase,
                trace: event.trace.clone(),
                outcome,
                reason_code: reason_code.clone(),
                duration_ms,
                synthetic_blocks: blocks.clone(),
                recorded_at_ms,
            })?;
            result.outcomes.push(record);

            if !enforcing {
                continue;
            }
            if outcome == HookOutcome::Inject {
                if result.synthetic_blocks.len() + blocks.len() > MAX_SYNTHETIC_BLOCKS
                    || result
                        .synthetic_blocks
                        .iter()
                        .chain(blocks.iter())
                        .map(|block| block.token_mass)
                        .sum::<u64>()
                        > MAX_SYNTHETIC_TOKENS
                {
                    result.allowed = false;
                    result.reason_code = Some("synthetic_block_limit".to_owned());
                    break;
                }
                result.synthetic_blocks.extend(blocks);
            } else if outcome == HookOutcome::Deny
                || ((outcome == HookOutcome::Failed || outcome == HookOutcome::TimedOut)
                    && self.failure_policy == FailurePolicy::Deny)
            {
                result.allowed = false;
                result.reason_code = reason_code;
                result.synthetic_blocks.clear();
                break;
            }
        }
        Ok(result)
    }
}

#[derive(Debug, thiserror::Error)]
pub enum HookDispatchError {
    #[error("hook retention must be between 1 and 4096")]
    InvalidRetention,
    #[error("hook outcome cursor is exhausted")]
    CursorExhausted,
    #[error("hook outcome store lock is poisoned")]
    Poisoned,
    #[error("hook event phase does not match dispatch method")]
    WrongPhase,
    #[error(transparent)]
    InvalidHook(#[from] HookViolation),
}
