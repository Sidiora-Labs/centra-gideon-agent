use fs2::FileExt;
use hypermid_contracts::{
    storage::{Fence, LeaseKey},
    MAX_SAFE_INTEGER,
};
use serde::{Deserialize, Serialize};
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use thiserror::Error;

#[derive(Debug, Error)]
pub enum LeaseError {
    #[error("lease is held by another live writer")]
    Contended,
    #[error("lease path is not a regular file: {0}")]
    NotRegular(PathBuf),
    #[error("lease belongs to a different storage scope")]
    KeyMismatch,
    #[error("lease epoch is malformed")]
    MalformedEpoch,
    #[error("lease epoch is exhausted")]
    EpochOverflow,
    #[error("lease I/O failed: {0}")]
    Io(#[from] std::io::Error),
    #[error("lease record is malformed: {0}")]
    Record(#[from] serde_json::Error),
}

#[derive(Debug, Serialize, Deserialize)]
struct LeaseRecord {
    key: LeaseKey,
    epoch: u64,
}

#[derive(Debug)]
pub struct LocalLease {
    path: PathBuf,
    file: File,
    fence: Fence,
}

impl LocalLease {
    pub fn acquire(path: impl AsRef<Path>, key: LeaseKey) -> Result<Self, LeaseError> {
        let path = path.as_ref().to_path_buf();
        if let Ok(metadata) = fs::symlink_metadata(&path) {
            if !metadata.file_type().is_file() {
                return Err(LeaseError::NotRegular(path));
            }
        }

        let mut options = OpenOptions::new();
        options.read(true).write(true).create(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600).custom_flags(libc::O_NOFOLLOW);
        }
        let mut file = options.open(&path)?;
        if !file.metadata()?.is_file() {
            return Err(LeaseError::NotRegular(path));
        }
        tighten_file_permissions(&path)?;
        match file.try_lock_exclusive() {
            Ok(()) => {}
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                return Err(LeaseError::Contended)
            }
            Err(error) => return Err(LeaseError::Io(error)),
        }

        let mut bytes = Vec::new();
        file.read_to_end(&mut bytes)?;
        let previous = if bytes.is_empty() {
            0
        } else {
            let record: LeaseRecord =
                serde_json::from_slice(&bytes).map_err(|_| LeaseError::MalformedEpoch)?;
            if record.key != key {
                return Err(LeaseError::KeyMismatch);
            }
            if record.epoch == 0 {
                return Err(LeaseError::MalformedEpoch);
            }
            record.epoch
        };
        if previous >= MAX_SAFE_INTEGER {
            return Err(LeaseError::EpochOverflow);
        }
        let epoch = previous + 1;
        let record = LeaseRecord {
            key: key.clone(),
            epoch,
        };
        let encoded = serde_json::to_vec(&record)?;
        file.seek(SeekFrom::Start(0))?;
        file.set_len(0)?;
        file.write_all(&encoded)?;
        file.sync_all()?;

        Ok(Self {
            path,
            file,
            fence: Fence { lease: key, epoch },
        })
    }

    pub fn fence(&self) -> &Fence {
        &self.fence
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn is_live(&self) -> bool {
        self.file.metadata().is_ok()
    }
}

#[cfg(unix)]
fn tighten_file_permissions(path: &Path) -> Result<(), std::io::Error> {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(path, fs::Permissions::from_mode(0o600))
}

#[cfg(not(unix))]
fn tighten_file_permissions(_path: &Path) -> Result<(), std::io::Error> {
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use hypermid_contracts::storage::BackendKind;

    fn key() -> LeaseKey {
        LeaseKey {
            module_id: "memory".parse().unwrap(),
            backend: BackendKind::Sqlite,
            scope_key: "owner:project".parse().unwrap(),
        }
    }

    #[test]
    fn contention_and_reclamation_advance_the_epoch() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("writer.lease");
        let first = LocalLease::acquire(&path, key()).unwrap();
        assert_eq!(first.fence().epoch, 1);
        assert!(matches!(
            LocalLease::acquire(&path, key()),
            Err(LeaseError::Contended)
        ));
        drop(first);
        let replacement = LocalLease::acquire(&path, key()).unwrap();
        assert_eq!(replacement.fence().epoch, 2);
    }

    #[test]
    fn a_non_regular_path_is_refused() {
        let directory = tempfile::tempdir().unwrap();
        assert!(matches!(
            LocalLease::acquire(directory.path(), key()),
            Err(LeaseError::NotRegular(_))
        ));
    }
}
