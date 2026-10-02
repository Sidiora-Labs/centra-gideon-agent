use std::{
    collections::VecDeque,
    sync::{Arc, RwLock},
};

use hypermid_contracts::{Error, Id, Scope};
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct StderrRecord {
    pub module_id: Id,
    pub at_ms: u64,
    pub line: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub scope: Option<Scope>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct TerminalRecord {
    pub module_id: Id,
    pub at_ms: u64,
    pub spawn_generation: u64,
    pub reason: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<Error>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SpawnEvent {
    pub sequence: u64,
    pub module_id: Id,
    pub at_ms: u64,
    pub spawn_generation: u64,
    pub state: String,
    pub provenance: Value,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RouteDiagnostic {
    pub route_id: String,
    pub route_epoch: u64,
    pub module_id: String,
    pub spawn_generation: u64,
    pub operation: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub scope: Option<Scope>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct DiagnosticSnapshot {
    pub captured_at_ms: u64,
    pub stderr: Vec<StderrRecord>,
    pub terminals: Vec<TerminalRecord>,
    pub spawn_events: Vec<SpawnEvent>,
    pub routes: Vec<RouteDiagnostic>,
    pub resources: Value,
}

#[derive(Default)]
struct DiagnosticState {
    stderr: VecDeque<StderrRecord>,
    terminals: VecDeque<TerminalRecord>,
    spawn_events: VecDeque<SpawnEvent>,
    routes: Vec<RouteDiagnostic>,
    resources: Value,
    secrets: Vec<String>,
    next_spawn_sequence: u64,
}

#[derive(Clone)]
pub struct DiagnosticsStore {
    inner: Arc<RwLock<DiagnosticState>>,
    stderr_limit: usize,
    terminal_limit: usize,
    spawn_limit: usize,
}

impl Default for DiagnosticsStore {
    fn default() -> Self {
        Self::new(512, 256, 512)
    }
}

impl DiagnosticsStore {
    pub fn new(stderr_limit: usize, terminal_limit: usize, spawn_limit: usize) -> Self {
        Self {
            inner: Arc::new(RwLock::new(DiagnosticState::default())),
            stderr_limit: stderr_limit.max(1),
            terminal_limit: terminal_limit.max(1),
            spawn_limit: spawn_limit.max(1),
        }
    }

    pub fn register_secret(&self, secret: impl Into<String>) {
        let secret = secret.into();
        if !secret.is_empty() {
            if let Ok(mut state) = self.inner.write() {
                state.secrets.push(secret);
            }
        }
    }

    pub fn record_stderr(&self, mut record: StderrRecord) {
        if let Ok(mut state) = self.inner.write() {
            record.line = redact(&record.line, &state.secrets);
            push_bounded(&mut state.stderr, record, self.stderr_limit);
        }
    }

    pub fn record_terminal(&self, record: TerminalRecord) {
        if let Ok(mut state) = self.inner.write() {
            push_bounded(&mut state.terminals, record, self.terminal_limit);
        }
    }

    pub fn record_spawn(
        &self,
        module_id: Id,
        at_ms: u64,
        spawn_generation: u64,
        state_name: impl Into<String>,
        provenance: Value,
    ) {
        if let Ok(mut state) = self.inner.write() {
            state.next_spawn_sequence = state.next_spawn_sequence.saturating_add(1);
            let event = SpawnEvent {
                sequence: state.next_spawn_sequence,
                module_id,
                at_ms,
                spawn_generation,
                state: state_name.into(),
                provenance,
            };
            push_bounded(&mut state.spawn_events, event, self.spawn_limit);
        }
    }

    pub fn set_routes(&self, routes: Vec<RouteDiagnostic>) {
        if let Ok(mut state) = self.inner.write() {
            state.routes = routes;
        }
    }

    pub fn set_resources(&self, resources: Value) {
        if let Ok(mut state) = self.inner.write() {
            state.resources = resources;
        }
    }

    pub fn snapshot(&self, captured_at_ms: u64, scope: Option<&Scope>) -> DiagnosticSnapshot {
        let Ok(state) = self.inner.read() else {
            return DiagnosticSnapshot {
                captured_at_ms,
                stderr: Vec::new(),
                terminals: Vec::new(),
                spawn_events: Vec::new(),
                routes: Vec::new(),
                resources: serde_json::json!({
                    "state": "unavailable",
                    "reason": "diagnostic state lock is unavailable"
                }),
            };
        };
        DiagnosticSnapshot {
            captured_at_ms,
            stderr: state
                .stderr
                .iter()
                .filter(|record| visible(record.scope.as_ref(), scope))
                .cloned()
                .collect(),
            terminals: state.terminals.iter().cloned().collect(),
            spawn_events: state.spawn_events.iter().cloned().collect(),
            routes: state
                .routes
                .iter()
                .filter(|route| visible(route.scope.as_ref(), scope))
                .cloned()
                .collect(),
            resources: state.resources.clone(),
        }
    }
}

fn visible(record: Option<&Scope>, requested: Option<&Scope>) -> bool {
    record.is_none() || record == requested
}

fn push_bounded<T>(queue: &mut VecDeque<T>, value: T, limit: usize) {
    queue.push_back(value);
    while queue.len() > limit {
        queue.pop_front();
    }
}

fn redact(value: &str, secrets: &[String]) -> String {
    let mut redacted = value.chars().take(8_192).collect::<String>();
    for secret in secrets {
        redacted = redacted.replace(secret, "<redacted>");
    }
    for marker in ["Authorization: Bearer ", "api_key=", "token="] {
        let mut offset = 0;
        while let Some(relative_start) = redacted[offset..].find(marker) {
            let start = offset + relative_start;
            let value_start = start + marker.len();
            let value_end = redacted[value_start..]
                .find(char::is_whitespace)
                .map_or(redacted.len(), |offset| value_start + offset);
            redacted.replace_range(value_start..value_end, "<redacted>");
            offset = value_start + "<redacted>".len();
        }
    }
    redacted
}
