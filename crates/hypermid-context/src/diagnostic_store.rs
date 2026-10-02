use hypermid_core::diagnostics::{
    ContextDiagnostics, ContextDiagnosticsSnapshot, DiagnosticFailure, DiagnosticViolation,
};
use std::collections::VecDeque;
use std::sync::{Arc, Mutex};

pub const DEFAULT_EVIDENCE_RETENTION: usize = 64;
pub const MAX_EVIDENCE_RETENTION: usize = 256;

#[derive(Clone)]
pub struct DiagnosticStore {
    inner: Arc<Mutex<DiagnosticStoreState>>,
}

struct DiagnosticStoreState {
    retention: usize,
    latest: ContextDiagnosticsSnapshot,
    history: VecDeque<ContextDiagnosticsSnapshot>,
}

impl DiagnosticStore {
    pub fn new(retention: usize, observed_at_ms: u64) -> Result<Self, DiagnosticStoreError> {
        if retention == 0 || retention > MAX_EVIDENCE_RETENTION {
            return Err(DiagnosticStoreError::InvalidRetention);
        }
        Ok(Self {
            inner: Arc::new(Mutex::new(DiagnosticStoreState {
                retention,
                latest: ContextDiagnosticsSnapshot::unsupported(observed_at_ms),
                history: VecDeque::with_capacity(retention),
            })),
        })
    }

    pub fn record_good(
        &self,
        observed_at_ms: u64,
        value: ContextDiagnostics,
    ) -> Result<ContextDiagnosticsSnapshot, DiagnosticStoreError> {
        let snapshot = ContextDiagnosticsSnapshot::available(observed_at_ms, value)?;
        self.record(snapshot)
    }

    pub fn record_failure(
        &self,
        observed_at_ms: u64,
        failure: DiagnosticFailure,
        parked: bool,
    ) -> Result<ContextDiagnosticsSnapshot, DiagnosticStoreError> {
        let last_good = self.lock()?.latest.last_good.clone();
        let snapshot =
            ContextDiagnosticsSnapshot::unavailable(observed_at_ms, failure, last_good, parked)?;
        self.record(snapshot)
    }

    pub fn snapshot(&self) -> Result<ContextDiagnosticsSnapshot, DiagnosticStoreError> {
        Ok(self.lock()?.latest.clone())
    }

    pub fn retained(&self) -> Result<Vec<ContextDiagnosticsSnapshot>, DiagnosticStoreError> {
        Ok(self.lock()?.history.iter().cloned().collect())
    }

    fn record(
        &self,
        snapshot: ContextDiagnosticsSnapshot,
    ) -> Result<ContextDiagnosticsSnapshot, DiagnosticStoreError> {
        let mut state = self.lock()?;
        if state.history.len() == state.retention {
            state.history.pop_front();
        }
        state.history.push_back(snapshot.clone());
        state.latest = snapshot.clone();
        Ok(snapshot)
    }

    fn lock(
        &self,
    ) -> Result<std::sync::MutexGuard<'_, DiagnosticStoreState>, DiagnosticStoreError> {
        self.inner
            .lock()
            .map_err(|_| DiagnosticStoreError::Poisoned)
    }
}

impl Default for DiagnosticStore {
    fn default() -> Self {
        Self::new(DEFAULT_EVIDENCE_RETENTION, 0).expect("default retention is valid")
    }
}

#[derive(Debug, thiserror::Error)]
pub enum DiagnosticStoreError {
    #[error("diagnostic retention must be between 1 and 256")]
    InvalidRetention,
    #[error("diagnostic store lock is poisoned")]
    Poisoned,
    #[error(transparent)]
    InvalidDiagnostic(#[from] DiagnosticViolation),
}
