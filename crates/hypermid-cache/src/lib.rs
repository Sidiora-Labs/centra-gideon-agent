use fs2::FileExt;
use hypermid_contracts::Id;
use serde::{Deserialize, Serialize};
use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use thiserror::Error;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Durability {
    Memory,
    Process,
    Durable,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct FrozenPass {
    pub boundary_id: Id,
    pub rendered: Vec<u8>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct CacheState {
    pub version: u64,
    pub durability: Durability,
    pub frozen: Option<FrozenPass>,
    pub deferred_passes: u64,
    pub reconciliation_pending: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum DeferOutcome {
    Empty,
    Replay(Vec<u8>),
    ReconciliationPending,
}

#[derive(Debug, Error)]
pub enum CacheError {
    #[error("cache version is stale: expected {expected}, current {current}")]
    StaleVersion { expected: u64, current: u64 },
    #[error("cache version overflow")]
    VersionOverflow,
    #[error("cache persistence failed: {0}")]
    Io(#[from] io::Error),
    #[error("cache state is malformed: {0}")]
    Malformed(#[from] serde_json::Error),
}

impl CacheState {
    pub fn new(durability: Durability) -> Self {
        Self {
            version: 0,
            durability,
            frozen: None,
            deferred_passes: 0,
            reconciliation_pending: false,
        }
    }

    fn check(&self, expected: u64) -> Result<(), CacheError> {
        if self.version != expected {
            return Err(CacheError::StaleVersion {
                expected,
                current: self.version,
            });
        }
        Ok(())
    }

    fn advance(&mut self) -> Result<(), CacheError> {
        self.version = self
            .version
            .checked_add(1)
            .ok_or(CacheError::VersionOverflow)?;
        Ok(())
    }

    pub fn rebuild_local(
        &mut self,
        expected: u64,
        boundary_id: Id,
        rendered: Vec<u8>,
    ) -> Result<(), CacheError> {
        self.check(expected)?;
        self.frozen = Some(FrozenPass {
            boundary_id,
            rendered,
        });
        self.reconciliation_pending = false;
        self.advance()
    }

    pub fn defer(
        &mut self,
        expected: u64,
        boundary_present: bool,
    ) -> Result<DeferOutcome, CacheError> {
        self.check(expected)?;
        let outcome = match (&self.frozen, boundary_present) {
            (None, _) => DeferOutcome::Empty,
            (Some(frozen), true) => {
                self.deferred_passes = self.deferred_passes.saturating_add(1);
                DeferOutcome::Replay(frozen.rendered.clone())
            }
            (Some(_), false) => {
                self.reconciliation_pending = true;
                DeferOutcome::ReconciliationPending
            }
        };
        self.advance()?;
        Ok(outcome)
    }

    pub fn rebuild_full(
        &mut self,
        expected: u64,
        boundary_id: Id,
        rendered: Vec<u8>,
    ) -> Result<(), CacheError> {
        self.check(expected)?;
        self.frozen = Some(FrozenPass {
            boundary_id,
            rendered,
        });
        self.deferred_passes = 0;
        self.reconciliation_pending = false;
        self.advance()
    }

    pub fn set_durability(
        &mut self,
        expected: u64,
        durability: Durability,
    ) -> Result<(), CacheError> {
        self.check(expected)?;
        if self.durability != durability {
            self.durability = durability;
            self.frozen = None;
            self.deferred_passes = 0;
            self.reconciliation_pending = false;
        }
        self.advance()
    }
}

#[derive(Clone, Debug)]
pub struct FileCacheStore {
    path: PathBuf,
}

impl FileCacheStore {
    pub fn new(path: impl Into<PathBuf>) -> Self {
        Self { path: path.into() }
    }

    fn lock_path(&self) -> PathBuf {
        self.path.with_extension("lock")
    }

    pub fn load(&self) -> Result<Option<CacheState>, CacheError> {
        match fs::read(&self.path) {
            Ok(bytes) => Ok(Some(serde_json::from_slice(&bytes)?)),
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
            Err(error) => Err(error.into()),
        }
    }

    pub fn compare_and_swap(
        &self,
        expected_version: Option<u64>,
        state: &CacheState,
    ) -> Result<(), CacheError> {
        if let Some(parent) = self.path.parent() {
            fs::create_dir_all(parent)?;
        }
        let lock_path = self.lock_path();
        let lock = OpenOptions::new()
            .create(true)
            .read(true)
            .write(true)
            .open(&lock_path)?;
        lock.lock_exclusive()?;
        let current = self.load()?;
        let current_version = current.as_ref().map(|value| value.version);
        if current_version != expected_version {
            let result = Err(CacheError::StaleVersion {
                expected: expected_version.unwrap_or(0),
                current: current_version.unwrap_or(0),
            });
            let _ = lock.unlock();
            return result;
        }
        let temporary = temporary_path(&self.path);
        let result = write_atomic(&temporary, &self.path, state);
        let _ = lock.unlock();
        result
    }
}

fn temporary_path(path: &Path) -> PathBuf {
    let name = path
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("cache");
    path.with_file_name(format!(".{name}.{}.tmp", std::process::id()))
}

fn write_atomic(
    temporary: &Path,
    destination: &Path,
    state: &CacheState,
) -> Result<(), CacheError> {
    let mut options = OpenOptions::new();
    options.create(true).truncate(true).write(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(temporary)?;
    let encoded = serde_json::to_vec(state)?;
    file.write_all(&encoded)?;
    file.write_all(b"\n")?;
    file.sync_all()?;
    fs::rename(temporary, destination)?;
    if let Some(parent) = destination.parent() {
        File::open(parent)?.sync_all()?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn persisted_state_replays_exact_bytes_and_rejects_stale_writes() {
        let root = tempfile::tempdir().unwrap();
        let store = FileCacheStore::new(root.path().join("state.json"));
        let mut state = CacheState::new(Durability::Durable);
        state
            .rebuild_local(0, Id::new("message:42").unwrap(), vec![0, 255, b'\n'])
            .unwrap();
        store.compare_and_swap(None, &state).unwrap();
        let mut loaded = store.load().unwrap().unwrap();
        assert_eq!(
            loaded.defer(1, true).unwrap(),
            DeferOutcome::Replay(vec![0, 255, b'\n'])
        );
        store.compare_and_swap(Some(1), &loaded).unwrap();
        assert!(matches!(
            store.compare_and_swap(Some(1), &loaded),
            Err(CacheError::StaleVersion { .. })
        ));
        assert_eq!(
            loaded.defer(2, false).unwrap(),
            DeferOutcome::ReconciliationPending
        );
        assert!(loaded.frozen.is_some());
        loaded
            .rebuild_full(3, Id::new("message:99").unwrap(), b"fresh".to_vec())
            .unwrap();
        assert_eq!(loaded.deferred_passes, 0);
        loaded.set_durability(4, Durability::Process).unwrap();
        assert!(loaded.frozen.is_none());
    }
}
