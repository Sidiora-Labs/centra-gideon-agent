use crate::journal::{Journal, JournalError};
use hypermid_contracts::{Digest, Error};
use hypermid_core::history::{JournalRange, SourceAdapter};
use hypermid_core::projection::Projection;
use hypermid_core::recovery::{
    replay_refusal, LastKnownGood, RebuiltState, RecoveryViolation, ReplayRequest,
};
use serde::{Deserialize, Serialize};
use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

const ACTIVE_FILE: &str = "last_known_good.json";
const QUARANTINE_DIR: &str = "quarantine";

pub struct RecoveryStore {
    root: PathBuf,
    lock: Mutex<()>,
}

impl RecoveryStore {
    pub fn open(root: impl AsRef<Path>) -> Result<Self, RecoveryStoreError> {
        let root = root.as_ref().to_path_buf();
        fs::create_dir_all(root.join(QUARANTINE_DIR))?;
        Ok(Self {
            root,
            lock: Mutex::new(()),
        })
    }

    pub fn publish(&self, record: &LastKnownGood) -> Result<(), RecoveryStoreError> {
        record.validate()?;
        let _guard = self.lock.lock().map_err(|_| RecoveryStoreError::Poisoned)?;
        write_atomic(&self.root.join(ACTIVE_FILE), &serde_json::to_vec(record)?)
    }

    pub fn load(&self) -> Result<Option<LastKnownGood>, RecoveryStoreError> {
        let _guard = self.lock.lock().map_err(|_| RecoveryStoreError::Poisoned)?;
        self.load_locked()
    }

    pub fn replay(&self, request: &ReplayRequest) -> Result<Projection, Error> {
        if let Err(violation) = request.validate_fit() {
            return Err(replay_refusal(violation));
        }
        let record = self
            .load()
            .map_err(|_| replay_refusal(RecoveryViolation::CorruptRecord))?
            .ok_or_else(|| replay_refusal(RecoveryViolation::CorruptRecord))?;
        if record.binding != request.binding {
            return Err(replay_refusal(RecoveryViolation::BindingMismatch));
        }
        record.validate().map_err(replay_refusal)
    }

    pub fn quarantine_entries(&self) -> Result<Vec<QuarantineEntry>, RecoveryStoreError> {
        let _guard = self.lock.lock().map_err(|_| RecoveryStoreError::Poisoned)?;
        let mut paths = fs::read_dir(self.root.join(QUARANTINE_DIR))?
            .filter_map(Result::ok)
            .map(|entry| entry.path())
            .filter(|path| path.extension().is_some_and(|value| value == "meta"))
            .collect::<Vec<_>>();
        paths.sort();
        paths
            .into_iter()
            .map(|path| Ok(serde_json::from_slice(&fs::read(path)?)?))
            .collect()
    }

    fn load_locked(&self) -> Result<Option<LastKnownGood>, RecoveryStoreError> {
        let active = self.root.join(ACTIVE_FILE);
        if !active.exists() {
            return Ok(None);
        }
        let bytes = fs::read(&active)?;
        let parsed = serde_json::from_slice::<LastKnownGood>(&bytes)
            .map_err(|_| RecoveryViolation::CorruptRecord)
            .and_then(|record| record.validate().map(|_| record));
        match parsed {
            Ok(record) => Ok(Some(record)),
            Err(violation) => {
                self.quarantine_locked(&active, &bytes, violation)?;
                Ok(None)
            }
        }
    }

    fn quarantine_locked(
        &self,
        active: &Path,
        bytes: &[u8],
        violation: RecoveryViolation,
    ) -> Result<(), RecoveryStoreError> {
        let content_digest = Digest::sha256(bytes);
        let quarantined_at_ms = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis() as u64;
        let stem = format!("{}-{quarantined_at_ms}", content_digest);
        let payload_path = self.root.join(QUARANTINE_DIR).join(format!("{stem}.bin"));
        let metadata_path = self.root.join(QUARANTINE_DIR).join(format!("{stem}.meta"));
        fs::rename(active, &payload_path)?;
        let entry = QuarantineEntry {
            content_digest,
            reason: violation.to_string(),
            quarantined_at_ms,
            byte_length: bytes.len() as u64,
        };
        write_atomic(&metadata_path, &serde_json::to_vec(&entry)?)?;
        sync_directory(self.root.join(QUARANTINE_DIR).as_path())?;
        Ok(())
    }
}

pub fn rebuild_from_journal<A: SourceAdapter>(
    journal: &Journal,
    adapter: &A,
) -> Result<RebuiltState, RecoveryStoreError> {
    let cursor = journal.current_cursor();
    if cursor.sequence == 0 {
        return Ok(RebuiltState::from_recovered(
            journal.scope().clone(),
            journal.session_id().clone(),
            cursor,
            Vec::new(),
        )?);
    }
    let range = JournalRange::new(
        hypermid_contracts::Cursor::new(cursor.epoch, 1)
            .map_err(|_| RecoveryViolation::JournalGap)?,
        cursor,
    )?;
    let items = journal.recover_range(range, adapter)?;
    Ok(RebuiltState::from_recovered(
        journal.scope().clone(),
        journal.session_id().clone(),
        cursor,
        items,
    )?)
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct QuarantineEntry {
    pub content_digest: Digest,
    pub reason: String,
    pub quarantined_at_ms: u64,
    pub byte_length: u64,
}

fn write_atomic(path: &Path, bytes: &[u8]) -> Result<(), RecoveryStoreError> {
    let parent = path
        .parent()
        .ok_or(RecoveryStoreError::Corrupt("missing parent"))?;
    let temporary = parent.join(format!(".pending-{}", Digest::sha256(bytes)));
    if temporary.exists() {
        fs::remove_file(&temporary)?;
    }
    let mut file = OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(&temporary)?;
    if let Err(error) = (|| -> std::io::Result<()> {
        file.write_all(bytes)?;
        file.sync_all()?;
        fs::rename(&temporary, path)?;
        sync_directory(parent)?;
        Ok(())
    })() {
        let _ = fs::remove_file(&temporary);
        return Err(error.into());
    }
    Ok(())
}

fn sync_directory(path: &Path) -> std::io::Result<()> {
    File::open(path)?.sync_all()
}

#[derive(Debug, thiserror::Error)]
pub enum RecoveryStoreError {
    #[error(transparent)]
    Recovery(#[from] RecoveryViolation),
    #[error(transparent)]
    History(#[from] hypermid_core::history::HistoryViolation),
    #[error(transparent)]
    Journal(#[from] JournalError),
    #[error(transparent)]
    Io(#[from] std::io::Error),
    #[error(transparent)]
    Json(#[from] serde_json::Error),
    #[error("recovery store lock is poisoned")]
    Poisoned,
    #[error("recovery store is corrupt: {0}")]
    Corrupt(&'static str),
}
