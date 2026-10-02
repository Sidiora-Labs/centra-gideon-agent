use crate::ownership::{OwnedPath, OwnershipError, OwnershipLedger, UninstallResult};
use hypermid_artifacts::{ArtifactStore, PinnedArtifact, VerifiedArtifact};
use std::fs;
use std::path::{Path, PathBuf};
use thiserror::Error;

#[derive(Clone, Debug)]
pub struct RegistryLifecycle {
    root: PathBuf,
    artifacts: ArtifactStore,
}

impl RegistryLifecycle {
    pub fn open(root: impl Into<PathBuf>) -> Result<Self, RegistryError> {
        let root = root.into();
        fs::create_dir_all(&root)?;
        let artifacts = ArtifactStore::open(root.join("artifacts"))?;
        Ok(Self { root, artifacts })
    }

    pub fn activate(&self, artifact: VerifiedArtifact) -> Result<PinnedArtifact, RegistryError> {
        let pinned = self.artifacts.activate(artifact)?;
        let mut ledger = self.ledger()?;
        for file in walk_regular_files(&pinned.generation_path)? {
            let relative = file
                .strip_prefix(&pinned.generation_path)
                .map_err(|_| RegistryError::InvalidGeneration)?
                .to_string_lossy()
                .replace('\\', "/");
            ledger.record(OwnedPath {
                relative_path: format!(
                    "artifacts/generations/{}/{}",
                    generation_name(&pinned)?,
                    relative
                ),
                sha256: crate::ownership::digest_file(&file)?,
                generation: generation_name(&pinned)?.to_owned(),
            })?;
        }
        ledger.save_atomic(&self.ledger_path())?;
        Ok(pinned)
    }

    pub fn uninstall_generation(&self, generation: &str) -> Result<UninstallResult, RegistryError> {
        let mut ledger = self.ledger()?;
        let result = ledger.uninstall(&self.root, generation)?;
        ledger.save_atomic(&self.ledger_path())?;
        let generation_root = self.root.join("artifacts/generations").join(generation);
        remove_empty_dirs(&generation_root)?;
        Ok(result)
    }

    pub fn deactivate(&self, pinned: &PinnedArtifact) -> Result<(), RegistryError> {
        self.artifacts.deactivate(pinned)?;
        Ok(())
    }

    pub fn ownership(&self) -> Result<OwnershipLedger, RegistryError> {
        self.ledger()
    }

    fn ledger(&self) -> Result<OwnershipLedger, RegistryError> {
        Ok(OwnershipLedger::load(&self.ledger_path())?)
    }

    fn ledger_path(&self) -> PathBuf {
        self.root.join("ownership.json")
    }
}

fn generation_name(pinned: &PinnedArtifact) -> Result<&str, RegistryError> {
    pinned
        .generation_path
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or(RegistryError::InvalidGeneration)
}

fn walk_regular_files(root: &Path) -> Result<Vec<PathBuf>, RegistryError> {
    let mut pending = vec![root.to_path_buf()];
    let mut files = Vec::new();
    while let Some(directory) = pending.pop() {
        for item in fs::read_dir(directory)? {
            let path = item?.path();
            let metadata = fs::symlink_metadata(&path)?;
            if metadata.is_dir() {
                pending.push(path);
            } else if metadata.is_file() {
                files.push(path);
            }
        }
    }
    Ok(files)
}

fn remove_empty_dirs(root: &Path) -> Result<(), RegistryError> {
    if !root.exists() {
        return Ok(());
    }
    let mut directories = vec![root.to_path_buf()];
    let mut index = 0;
    while index < directories.len() {
        for item in fs::read_dir(&directories[index])? {
            let path = item?.path();
            if path.is_dir() {
                directories.push(path);
            }
        }
        index += 1;
    }
    for directory in directories.into_iter().rev() {
        if fs::read_dir(&directory)?.next().is_none() {
            fs::remove_dir(directory)?;
        }
    }
    Ok(())
}

#[derive(Debug, Error)]
pub enum RegistryError {
    #[error("registry generation path is invalid")]
    InvalidGeneration,
    #[error(transparent)]
    Activation(#[from] hypermid_artifacts::ActivationError),
    #[error(transparent)]
    Ownership(#[from] OwnershipError),
    #[error(transparent)]
    Io(#[from] std::io::Error),
}
