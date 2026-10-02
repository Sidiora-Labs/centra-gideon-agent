use std::{
    collections::BTreeMap,
    fs::{self, File, OpenOptions},
    io::{BufRead, BufReader, Write},
    path::{Path, PathBuf},
};

use serde::{Deserialize, Serialize};
use sha2::{Digest as _, Sha256};
use thiserror::Error;

use crate::containment::ProcessIdentity;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum ChildEvent {
    Spawned {
        module_id: String,
        spawn_generation: u64,
        identity: ProcessIdentity,
        observed_ms: u64,
    },
    Terminal {
        module_id: String,
        spawn_generation: u64,
        exit_code: Option<i32>,
        reason: String,
        observed_ms: u64,
    },
    Retired {
        module_id: String,
        spawn_generation: u64,
        observed_ms: u64,
    },
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
struct JournalLine {
    sequence: u64,
    event: ChildEvent,
    digest: String,
}

#[derive(Clone, Debug, Default)]
pub struct ChildJournalSnapshot {
    pub sequence: u64,
    pub live: BTreeMap<(String, u64), ProcessIdentity>,
    pub terminals: Vec<ChildEvent>,
}

#[derive(Clone, Debug)]
pub struct ChildJournal {
    path: PathBuf,
}

#[derive(Debug, Error)]
pub enum ChildJournalError {
    #[error("child journal I/O failed: {0}")]
    Io(#[from] std::io::Error),
    #[error("child journal record is invalid: {0}")]
    Invalid(String),
    #[error("child journal sequence is not monotonic")]
    Sequence,
    #[error("child journal digest mismatch")]
    Digest,
}

impl ChildJournal {
    pub fn open(path: impl AsRef<Path>) -> Result<Self, ChildJournalError> {
        let path = path.as_ref().to_path_buf();
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        OpenOptions::new().create(true).append(true).open(&path)?;
        Ok(Self { path })
    }

    pub fn append(&self, event: ChildEvent) -> Result<u64, ChildJournalError> {
        let snapshot = self.load()?;
        let sequence = snapshot.sequence.saturating_add(1);
        let digest = digest_event(sequence, &event)?;
        let line = JournalLine {
            sequence,
            event,
            digest,
        };
        let mut file = OpenOptions::new().append(true).open(&self.path)?;
        serde_json::to_writer(&mut file, &line)
            .map_err(|error| ChildJournalError::Invalid(error.to_string()))?;
        file.write_all(b"\n")?;
        file.sync_data()?;
        Ok(sequence)
    }

    pub fn load(&self) -> Result<ChildJournalSnapshot, ChildJournalError> {
        let file = File::open(&self.path)?;
        let mut snapshot = ChildJournalSnapshot::default();
        let mut reader = BufReader::new(file);
        let mut line = Vec::new();
        loop {
            line.clear();
            let bytes = reader.read_until(b'\n', &mut line)?;
            if bytes == 0 {
                break;
            }
            let terminated = line.last() == Some(&b'\n');
            if terminated {
                line.pop();
            }
            if line.is_empty() {
                continue;
            }
            let record: JournalLine = match serde_json::from_slice(&line) {
                Ok(record) => record,
                Err(_) if !terminated => break,
                Err(error) => return Err(ChildJournalError::Invalid(error.to_string())),
            };
            if record.sequence != snapshot.sequence.saturating_add(1) {
                return Err(ChildJournalError::Sequence);
            }
            if record.digest != digest_event(record.sequence, &record.event)? {
                return Err(ChildJournalError::Digest);
            }
            snapshot.sequence = record.sequence;
            match &record.event {
                ChildEvent::Spawned {
                    module_id,
                    spawn_generation,
                    identity,
                    ..
                } => {
                    snapshot
                        .live
                        .insert((module_id.clone(), *spawn_generation), identity.clone());
                }
                ChildEvent::Terminal {
                    module_id,
                    spawn_generation,
                    ..
                } => {
                    snapshot
                        .live
                        .remove(&(module_id.clone(), *spawn_generation));
                    snapshot.terminals.push(record.event.clone());
                }
                ChildEvent::Retired {
                    module_id,
                    spawn_generation,
                    ..
                } => {
                    snapshot
                        .live
                        .remove(&(module_id.clone(), *spawn_generation));
                }
            }
        }
        Ok(snapshot)
    }
}

fn digest_event(sequence: u64, event: &ChildEvent) -> Result<String, ChildJournalError> {
    let bytes = serde_json::to_vec(&(sequence, event))
        .map_err(|error| ChildJournalError::Invalid(error.to_string()))?;
    Ok(format!("{:x}", Sha256::digest(bytes)))
}
