use crate::BusError;
use async_trait::async_trait;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RegisterEntry {
    pub key: String,
    pub value: Vec<u8>,
    pub revision: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RegisterSnapshot {
    pub revision: u64,
    pub entries: Vec<RegisterEntry>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum RegisterUpdate {
    Put(RegisterEntry),
    Delete { key: String, revision: u64 },
}

#[async_trait]
pub trait Register: Send + Sync {
    async fn put(
        &self,
        key: &str,
        value: Vec<u8>,
        expected_revision: Option<u64>,
    ) -> Result<RegisterEntry, BusError>;
    async fn get(&self, key: &str) -> Result<Option<RegisterEntry>, BusError>;
    async fn delete(&self, key: &str, expected_revision: u64) -> Result<u64, BusError>;
    async fn snapshot(&self) -> Result<RegisterSnapshot, BusError>;
    async fn watch_after(&self, revision: u64) -> Result<Vec<RegisterUpdate>, BusError>;
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct CensusGuard {
    revision: Option<u64>,
    present: bool,
}

impl CensusGuard {
    pub fn observe_snapshot(&mut self, snapshot: &RegisterSnapshot, key: &str) {
        self.revision = Some(snapshot.revision);
        self.present = snapshot.entries.iter().any(|entry| entry.key == key);
    }

    pub fn observe(&mut self, update: &RegisterUpdate, key: &str) {
        match update {
            RegisterUpdate::Put(entry) if entry.key == key => {
                self.revision = Some(entry.revision);
                self.present = true;
            }
            RegisterUpdate::Delete {
                key: deleted,
                revision,
            } if deleted == key => {
                self.revision = Some(*revision);
                self.present = false;
            }
            _ => {}
        }
    }

    pub fn disconnect(&mut self) {
        self.revision = None;
        self.present = false;
    }

    pub fn proven_absent(&self) -> bool {
        self.revision.is_some() && !self.present
    }
}
