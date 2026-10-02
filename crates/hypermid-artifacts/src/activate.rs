use crate::install::VerifiedArtifact;
use serde::{Deserialize, Serialize};
use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use thiserror::Error;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ActivationBoundary {
    IntentDurable,
    GenerationDurable,
    PointerSwitched,
    IntentCleared,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct ActiveGeneration {
    artifact_id: String,
    version: String,
    generation: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct ActivationIntent {
    prior: Option<ActiveGeneration>,
    next: ActiveGeneration,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct DeactivationIntent {
    prior: ActiveGeneration,
}

#[derive(Clone, Debug)]
pub struct PinnedArtifact {
    pub artifact_id: String,
    pub version: String,
    pub generation_path: PathBuf,
}

#[derive(Clone, Debug)]
pub struct ArtifactStore {
    root: PathBuf,
}

impl ArtifactStore {
    pub fn open(root: impl Into<PathBuf>) -> Result<Self, ActivationError> {
        let store = Self { root: root.into() };
        fs::create_dir_all(store.generations())?;
        store.recover()?;
        Ok(store)
    }

    pub fn pin(&self) -> Result<Option<PinnedArtifact>, ActivationError> {
        let Some(active) = self.read_active()? else {
            return Ok(None);
        };
        let path = self.generations().join(&active.generation);
        if !path.is_dir() {
            return Err(ActivationError::MissingGeneration(active.generation));
        }
        Ok(Some(PinnedArtifact {
            artifact_id: active.artifact_id,
            version: active.version,
            generation_path: path,
        }))
    }

    pub fn activate(&self, artifact: VerifiedArtifact) -> Result<PinnedArtifact, ActivationError> {
        self.activate_with_hook(artifact, |_| Ok(()))
    }

    pub fn activate_with_hook<F>(
        &self,
        artifact: VerifiedArtifact,
        mut boundary: F,
    ) -> Result<PinnedArtifact, ActivationError>
    where
        F: FnMut(ActivationBoundary) -> Result<(), ActivationError>,
    {
        let generation = format!(
            "{}-{}-{}",
            artifact.manifest.artifact_id,
            artifact.manifest.version,
            &artifact.manifest.archive_sha256[..16]
        );
        let next = ActiveGeneration {
            artifact_id: artifact.manifest.artifact_id,
            version: artifact.manifest.version,
            generation: generation.clone(),
        };
        let intent = ActivationIntent {
            prior: self.read_active()?,
            next: next.clone(),
        };
        atomic_json(&self.intent_path(), &intent)?;
        boundary(ActivationBoundary::IntentDurable)?;

        let destination = self.generations().join(&generation);
        if !destination.exists() {
            fs::rename(&artifact.staging_path, &destination)?;
            sync_dir(self.generations())?;
        } else {
            fs::remove_dir_all(&artifact.staging_path)?;
        }
        boundary(ActivationBoundary::GenerationDurable)?;

        atomic_json(&self.active_path(), &next)?;
        boundary(ActivationBoundary::PointerSwitched)?;
        match fs::remove_file(self.intent_path()) {
            Ok(()) => sync_dir(&self.root)?,
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => return Err(error.into()),
        }
        boundary(ActivationBoundary::IntentCleared)?;
        Ok(PinnedArtifact {
            artifact_id: next.artifact_id,
            version: next.version,
            generation_path: destination,
        })
    }

    pub fn rollback(&self, pinned: &PinnedArtifact) -> Result<(), ActivationError> {
        if !pinned.generation_path.starts_with(self.generations())
            || !pinned.generation_path.is_dir()
        {
            return Err(ActivationError::UnverifiedRollback);
        }
        let generation = pinned
            .generation_path
            .file_name()
            .and_then(|name| name.to_str())
            .ok_or(ActivationError::UnverifiedRollback)?;
        atomic_json(
            &self.active_path(),
            &ActiveGeneration {
                artifact_id: pinned.artifact_id.clone(),
                version: pinned.version.clone(),
                generation: generation.to_owned(),
            },
        )
    }

    pub fn deactivate(&self, pinned: &PinnedArtifact) -> Result<(), ActivationError> {
        let active = self
            .read_active()?
            .ok_or(ActivationError::ActiveGenerationChanged)?;
        let generation = pinned
            .generation_path
            .file_name()
            .and_then(|name| name.to_str())
            .ok_or(ActivationError::UnverifiedRollback)?;
        if !pinned.generation_path.starts_with(self.generations())
            || !pinned.generation_path.is_dir()
            || active.artifact_id != pinned.artifact_id
            || active.version != pinned.version
            || active.generation != generation
        {
            return Err(ActivationError::ActiveGenerationChanged);
        }
        atomic_json(
            &self.deactivation_intent_path(),
            &DeactivationIntent { prior: active },
        )?;
        fs::remove_file(self.active_path())?;
        sync_dir(&self.root)?;
        fs::remove_file(self.deactivation_intent_path())?;
        sync_dir(&self.root)?;
        Ok(())
    }

    pub fn recover(&self) -> Result<(), ActivationError> {
        self.recover_deactivation()?;
        let intent = match read_json::<ActivationIntent>(&self.intent_path()) {
            Ok(value) => value,
            Err(ActivationError::Io(error)) if error.kind() == io::ErrorKind::NotFound => {
                return Ok(())
            }
            Err(error) => return Err(error),
        };
        let active = self.read_active()?;
        if active.as_ref().map(|value| &value.generation) == Some(&intent.next.generation)
            && self.generations().join(&intent.next.generation).is_dir()
        {
            fs::remove_file(self.intent_path())?;
            sync_dir(&self.root)?;
            return Ok(());
        }
        if let Some(prior) = intent.prior {
            if !self.generations().join(&prior.generation).is_dir() {
                return Err(ActivationError::MissingGeneration(prior.generation));
            }
            atomic_json(&self.active_path(), &prior)?;
        } else if self.active_path().exists() {
            fs::remove_file(self.active_path())?;
        }
        let staged_next = self.generations().join(intent.next.generation);
        if staged_next.exists() {
            fs::remove_dir_all(staged_next)?;
        }
        fs::remove_file(self.intent_path())?;
        sync_dir(&self.root)?;
        Ok(())
    }

    fn generations(&self) -> PathBuf {
        self.root.join("generations")
    }

    fn active_path(&self) -> PathBuf {
        self.root.join("active.json")
    }

    fn intent_path(&self) -> PathBuf {
        self.root.join("activation-intent.json")
    }

    fn deactivation_intent_path(&self) -> PathBuf {
        self.root.join("deactivation-intent.json")
    }

    fn recover_deactivation(&self) -> Result<(), ActivationError> {
        let intent = match read_json::<DeactivationIntent>(&self.deactivation_intent_path()) {
            Ok(value) => value,
            Err(ActivationError::Io(error)) if error.kind() == io::ErrorKind::NotFound => {
                return Ok(())
            }
            Err(error) => return Err(error),
        };
        if self.read_active()?.as_ref().is_some_and(|active| {
            active.artifact_id != intent.prior.artifact_id
                || active.version != intent.prior.version
                || active.generation != intent.prior.generation
        }) {
            return Err(ActivationError::ActiveGenerationChanged);
        }
        if self.active_path().exists() {
            fs::remove_file(self.active_path())?;
            sync_dir(&self.root)?;
        }
        fs::remove_file(self.deactivation_intent_path())?;
        sync_dir(&self.root)?;
        Ok(())
    }

    fn read_active(&self) -> Result<Option<ActiveGeneration>, ActivationError> {
        match read_json(&self.active_path()) {
            Ok(value) => Ok(Some(value)),
            Err(ActivationError::Io(error)) if error.kind() == io::ErrorKind::NotFound => Ok(None),
            Err(error) => Err(error),
        }
    }
}

fn atomic_json<T: Serialize>(path: &Path, value: &T) -> Result<(), ActivationError> {
    let bytes = serde_json::to_vec(value)?;
    let parent = path.parent().ok_or(ActivationError::InvalidStore)?;
    fs::create_dir_all(parent)?;
    let temporary = parent.join(format!(
        ".{}.tmp",
        path.file_name().and_then(|n| n.to_str()).unwrap_or("state")
    ));
    let mut file = OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .open(&temporary)?;
    file.write_all(&bytes)?;
    file.sync_all()?;
    fs::rename(temporary, path)?;
    sync_dir(parent)?;
    Ok(())
}

fn read_json<T: for<'de> Deserialize<'de>>(path: &Path) -> Result<T, ActivationError> {
    Ok(serde_json::from_reader(File::open(path)?)?)
}

fn sync_dir(path: impl AsRef<Path>) -> io::Result<()> {
    File::open(path)?.sync_all()
}

#[derive(Debug, Error)]
pub enum ActivationError {
    #[error("activation store path is invalid")]
    InvalidStore,
    #[error("active generation is missing: {0}")]
    MissingGeneration(String),
    #[error("active generation changed after review")]
    ActiveGenerationChanged,
    #[error("rollback target is not a verified generation")]
    UnverifiedRollback,
    #[error(transparent)]
    Io(#[from] io::Error),
    #[error(transparent)]
    Json(#[from] serde_json::Error),
}
