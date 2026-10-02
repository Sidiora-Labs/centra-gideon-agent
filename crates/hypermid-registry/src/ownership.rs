use serde::{Deserialize, Serialize};
use sha2::{Digest as _, Sha256};
use std::collections::BTreeMap;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::{Component, Path, PathBuf};
use thiserror::Error;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct OwnedPath {
    pub relative_path: String,
    pub sha256: String,
    pub generation: String,
}

#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct OwnershipLedger {
    pub paths: BTreeMap<String, OwnedPath>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct UninstallResult {
    pub removed: Vec<String>,
    pub preserved_modified: Vec<String>,
    pub missing: Vec<String>,
}

impl OwnershipLedger {
    pub fn load(path: &Path) -> Result<Self, OwnershipError> {
        match File::open(path) {
            Ok(file) => Ok(serde_json::from_reader(file)?),
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(Self::default()),
            Err(error) => Err(error.into()),
        }
    }

    pub fn save_atomic(&self, path: &Path) -> Result<(), OwnershipError> {
        let parent = path.parent().ok_or(OwnershipError::InvalidRoot)?;
        fs::create_dir_all(parent)?;
        let temporary = parent.join(".ownership.json.tmp");
        let bytes = serde_json::to_vec(self)?;
        let mut file = OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .open(&temporary)?;
        file.write_all(&bytes)?;
        file.sync_all()?;
        fs::rename(temporary, path)?;
        File::open(parent)?.sync_all()?;
        Ok(())
    }

    pub fn plan_claim(
        &self,
        install_root: &Path,
        candidate: &OwnedPath,
    ) -> Result<(), OwnershipError> {
        let relative = safe_relative(&candidate.relative_path)?;
        let destination = install_root.join(relative);
        if !destination.exists() {
            return Ok(());
        }
        let current_digest = digest_file(&destination)?;
        match self.paths.get(&candidate.relative_path) {
            Some(existing) if existing.sha256 == current_digest => Ok(()),
            Some(_) => Err(OwnershipError::OwnedPathModified(
                candidate.relative_path.clone(),
            )),
            None => Err(OwnershipError::UnownedCollision(
                candidate.relative_path.clone(),
            )),
        }
    }

    pub fn record(&mut self, owned: OwnedPath) -> Result<(), OwnershipError> {
        safe_relative(&owned.relative_path)?;
        validate_digest(&owned.sha256)?;
        self.paths.insert(owned.relative_path.clone(), owned);
        Ok(())
    }

    pub fn uninstall(
        &mut self,
        install_root: &Path,
        generation: &str,
    ) -> Result<UninstallResult, OwnershipError> {
        let selected: Vec<_> = self
            .paths
            .values()
            .filter(|entry| entry.generation == generation)
            .cloned()
            .collect();
        let mut result = UninstallResult::default();
        for owned in selected {
            let destination = install_root.join(safe_relative(&owned.relative_path)?);
            if !destination.exists() {
                result.missing.push(owned.relative_path.clone());
                self.paths.remove(&owned.relative_path);
                continue;
            }
            if digest_file(&destination)? != owned.sha256 {
                result.preserved_modified.push(owned.relative_path);
                continue;
            }
            fs::remove_file(&destination)?;
            result.removed.push(owned.relative_path.clone());
            self.paths.remove(&owned.relative_path);
        }
        Ok(result)
    }
}

pub fn digest_file(path: &Path) -> Result<String, OwnershipError> {
    let bytes = fs::read(path)?;
    Ok(hex::encode(Sha256::digest(bytes)))
}

fn validate_digest(value: &str) -> Result<(), OwnershipError> {
    if value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
    {
        Ok(())
    } else {
        Err(OwnershipError::InvalidDigest)
    }
}

fn safe_relative(value: &str) -> Result<PathBuf, OwnershipError> {
    let path = Path::new(value);
    if value.is_empty() || path.is_absolute() {
        return Err(OwnershipError::UnsafePath(value.to_owned()));
    }
    let mut output = PathBuf::new();
    for component in path.components() {
        match component {
            Component::Normal(value) => output.push(value),
            _ => return Err(OwnershipError::UnsafePath(value.to_owned())),
        }
    }
    Ok(output)
}

#[derive(Debug, Error)]
pub enum OwnershipError {
    #[error("ownership digest is invalid")]
    InvalidDigest,
    #[error("ownership ledger root is invalid")]
    InvalidRoot,
    #[error("owned path has been modified: {0}")]
    OwnedPathModified(String),
    #[error("install would replace an unowned path: {0}")]
    UnownedCollision(String),
    #[error("ownership path is unsafe: {0}")]
    UnsafePath(String),
    #[error(transparent)]
    Io(#[from] io::Error),
    #[error(transparent)]
    Json(#[from] serde_json::Error),
}
