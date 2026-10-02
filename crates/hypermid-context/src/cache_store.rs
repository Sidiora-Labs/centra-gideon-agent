use hypermid_core::cache::{
    evaluate_transition, CacheGeneration, CacheOutcome, CacheOutcomeKind, CachePolicyError,
    CacheTransition,
};
use std::sync::Mutex;

#[derive(Debug, Default)]
pub struct CacheStore {
    state: Mutex<Option<CacheGeneration>>,
}

impl CacheStore {
    pub fn apply(&self, transition: CacheTransition) -> Result<CacheOutcome, CachePolicyError> {
        let mut state = self.state.lock().expect("cache store lock poisoned");
        let outcome = evaluate_transition(state.as_ref(), &transition)?;
        if outcome.kind == CacheOutcomeKind::Applied {
            *state = Some(transition.next);
        }
        Ok(outcome)
    }
    pub fn snapshot(&self) -> Option<CacheGeneration> {
        self.state
            .lock()
            .expect("cache store lock poisoned")
            .clone()
    }
    pub fn replay_prefix(&self) -> Option<(Vec<u8>, Vec<u8>)> {
        self.snapshot()
            .map(|state| (state.baseline.bytes, state.delta.bytes))
    }
}
