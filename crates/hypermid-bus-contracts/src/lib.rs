use hypermid_contracts::{Cursor, Digest, Id, Scope, Trace, MAX_SAFE_INTEGER};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::time::Duration;

pub mod conformance;
pub mod register;
pub mod stream;
pub mod work_queue;

pub use register::{Register, RegisterEntry, RegisterSnapshot, RegisterUpdate};
pub use stream::{Delivery, DeliveryDisposition, Stream};
pub use work_queue::{QueueItem, QueuePull, WorkQueue};

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct BusMessage {
    pub subject: String,
    pub id: Id,
    pub digest: Digest,
    pub headers: BTreeMap<String, String>,
    pub scope: Scope,
    pub trace: Trace,
}

impl BusMessage {
    pub fn validate(&self) -> Result<(), BusError> {
        if self.subject.is_empty() || self.subject.len() > 512 {
            return Err(BusError::Invalid(
                "subject length is outside 1..=512".into(),
            ));
        }
        if self.headers.len() > 64
            || self.headers.iter().any(|(key, value)| {
                key.is_empty() || key.len() > 128 || value.len() > 4096 || !key.is_ascii()
            })
        {
            return Err(BusError::Invalid(
                "headers exceed the bounded wire contract".into(),
            ));
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq, thiserror::Error)]
pub enum BusError {
    #[error("bus resource is missing")]
    Missing,
    #[error("bus operation is denied")]
    Denied,
    #[error("bus backend is unavailable")]
    Unavailable,
    #[error("bus backend is disconnected")]
    Disconnected,
    #[error("invalid bus operation: {0}")]
    Invalid(String),
    #[error("bus revision or identity conflict")]
    Conflict,
    #[error("bus operation timed out")]
    Timeout,
    #[error("bus internal failure: {0}")]
    Internal(String),
}

pub fn checked_sequence(value: u64) -> Result<u64, BusError> {
    if value == 0 || value > MAX_SAFE_INTEGER {
        return Err(BusError::Invalid(
            "sequence is outside the Hypermid wire range".into(),
        ));
    }
    Ok(value)
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PullRequest {
    pub durable: Id,
    pub filter: String,
    pub ack_wait: Duration,
    pub max_deliveries: u32,
}

impl PullRequest {
    pub fn validate(&self) -> Result<(), BusError> {
        if self.filter.is_empty()
            || self.filter.len() > 512
            || self.ack_wait.is_zero()
            || self.max_deliveries == 0
        {
            return Err(BusError::Invalid("invalid durable pull request".into()));
        }
        Ok(())
    }
}

pub fn cursor(sequence: u64) -> Result<Cursor, BusError> {
    Cursor::new(1, checked_sequence(sequence)?)
        .map_err(|error| BusError::Invalid(error.to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use register::{CensusGuard, RegisterSnapshot};

    #[test]
    fn digest_cursor_and_census_contracts_are_stable() {
        let message = BusMessage {
            subject: "hm.owner.project.module-events.memory.changed".into(),
            id: Id::new("event-1").unwrap(),
            digest: Digest::sha256(b"payload"),
            headers: BTreeMap::new(),
            scope: Scope::new(Id::new("owner").unwrap(), Id::new("project").unwrap(), None),
            trace: Trace::new(Id::new("trace-1").unwrap(), Id::new("request-1").unwrap()),
        };
        message.validate().unwrap();
        assert_eq!(cursor(1).unwrap(), Cursor::new(1, 1).unwrap());
        let mut guard = CensusGuard::default();
        assert!(!guard.proven_absent());
        guard.observe_snapshot(
            &RegisterSnapshot {
                revision: 1,
                entries: vec![],
            },
            "agent-1",
        );
        assert!(guard.proven_absent());
        guard.disconnect();
        assert!(!guard.proven_absent());
    }
}
