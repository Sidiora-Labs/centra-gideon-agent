use std::{
    collections::{BTreeMap, BTreeSet, HashMap, HashSet},
    fs,
    path::{Path, PathBuf},
    sync::{Arc, RwLock},
};

use sha2::{Digest as _, Sha256};
use thiserror::Error;

use crate::manifest::{ManifestError, ModuleManifest, Operation, ValidatedManifest};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ModuleRegistrationState {
    Configured,
    Starting,
    Registered,
    Ready,
    Draining,
    Failed,
}

#[derive(Clone, Debug)]
pub struct RegistryEntry {
    pub manifest: Arc<ValidatedManifest>,
    pub state: ModuleRegistrationState,
    pub spawn_generation: u64,
}

#[derive(Clone, Debug)]
pub struct OperationTarget {
    pub module_id: String,
    pub spawn_generation: u64,
    pub operation: Operation,
}

#[derive(Clone, Debug)]
pub struct RegistrySnapshot {
    pub generation: u64,
    pub entries: BTreeMap<String, RegistryEntry>,
}

#[derive(Clone, Debug)]
pub struct RegistryPreview {
    pub current_generation: u64,
    pub candidate_generation: u64,
    pub added: Vec<String>,
    pub removed: Vec<String>,
    pub changed: Vec<String>,
    candidates: BTreeMap<String, RegistryEntry>,
}

#[derive(Clone, Debug)]
struct LaunchProof {
    generation: u64,
    secret_digest: [u8; 32],
}

#[derive(Debug, Default)]
struct RegistryState {
    generation: u64,
    entries: BTreeMap<String, RegistryEntry>,
    launch_proofs: HashMap<String, LaunchProof>,
}

#[derive(Clone, Debug)]
pub struct Registry {
    roots: Arc<Vec<PathBuf>>,
    inner: Arc<RwLock<RegistryState>>,
}

#[derive(Debug, Error)]
pub enum RegistryError {
    #[error(transparent)]
    Manifest(#[from] ManifestError),
    #[error("registry lock is poisoned")]
    Poisoned,
    #[error("configured manifest root is unavailable: {0}")]
    InvalidRoot(PathBuf),
    #[error("manifest discovery failed for {path}: {source}")]
    Discovery {
        path: PathBuf,
        source: std::io::Error,
    },
    #[error("duplicate module id {0}")]
    DuplicateModule(String),
    #[error("operation capability {0} has conflicting providers")]
    CapabilityConflict(String),
    #[error("required capability {capability} for module {module_id} has no provider")]
    MissingCapability {
        module_id: String,
        capability: String,
    },
    #[error("module dependency graph contains a cycle")]
    DependencyCycle,
    #[error("registry preview is stale")]
    StalePreview,
    #[error("unknown module {0}")]
    UnknownModule(String),
    #[error("launch identity was refused")]
    LaunchIdentityRefused,
    #[error("operation catalog does not match the validated manifest")]
    CatalogMismatch,
    #[error("module {0} is not ready")]
    NotReady(String),
    #[error("unknown operation {0}")]
    UnknownOperation(String),
}

impl Registry {
    pub fn new(roots: Vec<PathBuf>) -> Result<Self, RegistryError> {
        let mut canonical = Vec::with_capacity(roots.len());
        for root in roots {
            canonical.push(
                root.canonicalize()
                    .map_err(|_| RegistryError::InvalidRoot(root))?,
            );
        }
        Ok(Self {
            roots: Arc::new(canonical),
            inner: Arc::new(RwLock::new(RegistryState::default())),
        })
    }

    pub fn empty() -> Self {
        Self {
            roots: Arc::new(Vec::new()),
            inner: Arc::new(RwLock::new(RegistryState::default())),
        }
    }

    pub fn snapshot(&self) -> Result<RegistrySnapshot, RegistryError> {
        let state = self.inner.read().map_err(|_| RegistryError::Poisoned)?;
        Ok(RegistrySnapshot {
            generation: state.generation,
            entries: state.entries.clone(),
        })
    }

    pub fn preview_rescan(&self) -> Result<RegistryPreview, RegistryError> {
        let candidates = self.discover_candidates()?;
        validate_candidate_set(&candidates)?;
        let state = self.inner.read().map_err(|_| RegistryError::Poisoned)?;
        let old: BTreeSet<_> = state.entries.keys().cloned().collect();
        let new: BTreeSet<_> = candidates.keys().cloned().collect();
        let added = new.difference(&old).cloned().collect();
        let removed = old.difference(&new).cloned().collect();
        let changed = old
            .intersection(&new)
            .filter(|id| {
                state.entries[*id].manifest.manifest != candidates[*id].manifest.manifest
                    || state.entries[*id].manifest.executable_path
                        != candidates[*id].manifest.executable_path
            })
            .cloned()
            .collect();
        Ok(RegistryPreview {
            current_generation: state.generation,
            candidate_generation: state.generation.saturating_add(1),
            added,
            removed,
            changed,
            candidates,
        })
    }

    pub fn apply_rescan(&self, preview: RegistryPreview) -> Result<u64, RegistryError> {
        let mut state = self.inner.write().map_err(|_| RegistryError::Poisoned)?;
        if state.generation != preview.current_generation {
            return Err(RegistryError::StalePreview);
        }
        state.entries = preview.candidates;
        state.generation = preview.candidate_generation;
        state.launch_proofs.clear();
        Ok(state.generation)
    }

    pub fn rescan(&self) -> Result<u64, RegistryError> {
        let preview = self.preview_rescan()?;
        self.apply_rescan(preview)
    }

    pub fn prepare_spawn(
        &self,
        module_id: &str,
        one_use_secret: &[u8],
    ) -> Result<u64, RegistryError> {
        if one_use_secret.len() < 32 {
            return Err(RegistryError::LaunchIdentityRefused);
        }
        let mut state = self.inner.write().map_err(|_| RegistryError::Poisoned)?;
        let entry = state
            .entries
            .get_mut(module_id)
            .ok_or_else(|| RegistryError::UnknownModule(module_id.into()))?;
        entry.spawn_generation = entry.spawn_generation.saturating_add(1).max(1);
        entry.state = ModuleRegistrationState::Starting;
        let generation = entry.spawn_generation;
        state.launch_proofs.insert(
            module_id.into(),
            LaunchProof {
                generation,
                secret_digest: Sha256::digest(one_use_secret).into(),
            },
        );
        Ok(generation)
    }

    pub fn authenticate_module(
        &self,
        module_id: &str,
        spawn_generation: u64,
        one_use_secret: &[u8],
        operation_catalog: &[Operation],
    ) -> Result<(), RegistryError> {
        let mut state = self.inner.write().map_err(|_| RegistryError::Poisoned)?;
        let entry = state
            .entries
            .get(module_id)
            .ok_or_else(|| RegistryError::UnknownModule(module_id.into()))?;
        let proof = state
            .launch_proofs
            .get(module_id)
            .ok_or(RegistryError::LaunchIdentityRefused)?;
        let actual: [u8; 32] = Sha256::digest(one_use_secret).into();
        if proof.generation != spawn_generation
            || entry.spawn_generation != spawn_generation
            || actual != proof.secret_digest
        {
            return Err(RegistryError::LaunchIdentityRefused);
        }
        if operation_catalog != entry.manifest.manifest.operations {
            return Err(RegistryError::CatalogMismatch);
        }
        state.launch_proofs.remove(module_id);
        state
            .entries
            .get_mut(module_id)
            .expect("entry exists")
            .state = ModuleRegistrationState::Registered;
        Ok(())
    }

    pub fn mark_ready(&self, module_id: &str, spawn_generation: u64) -> Result<(), RegistryError> {
        let mut state = self.inner.write().map_err(|_| RegistryError::Poisoned)?;
        let entry = state
            .entries
            .get_mut(module_id)
            .ok_or_else(|| RegistryError::UnknownModule(module_id.into()))?;
        if entry.spawn_generation != spawn_generation
            || entry.state != ModuleRegistrationState::Registered
        {
            return Err(RegistryError::LaunchIdentityRefused);
        }
        entry.state = ModuleRegistrationState::Ready;
        Ok(())
    }

    pub fn set_state(
        &self,
        module_id: &str,
        spawn_generation: u64,
        new_state: ModuleRegistrationState,
    ) -> Result<(), RegistryError> {
        let mut state = self.inner.write().map_err(|_| RegistryError::Poisoned)?;
        let entry = state
            .entries
            .get_mut(module_id)
            .ok_or_else(|| RegistryError::UnknownModule(module_id.into()))?;
        if entry.spawn_generation != spawn_generation {
            return Err(RegistryError::LaunchIdentityRefused);
        }
        entry.state = new_state;
        Ok(())
    }

    pub fn resolve_operation(&self, operation: &str) -> Result<OperationTarget, RegistryError> {
        let state = self.inner.read().map_err(|_| RegistryError::Poisoned)?;
        for (module_id, entry) in &state.entries {
            if let Some(operation) = entry
                .manifest
                .manifest
                .operations
                .iter()
                .find(|candidate| candidate.name == operation)
            {
                if entry.state != ModuleRegistrationState::Ready {
                    return Err(RegistryError::NotReady(module_id.clone()));
                }
                return Ok(OperationTarget {
                    module_id: module_id.clone(),
                    spawn_generation: entry.spawn_generation,
                    operation: operation.clone(),
                });
            }
        }
        Err(RegistryError::UnknownOperation(operation.into()))
    }

    fn discover_candidates(&self) -> Result<BTreeMap<String, RegistryEntry>, RegistryError> {
        let mut candidates = BTreeMap::new();
        for root in self.roots.iter() {
            let directory = fs::read_dir(root).map_err(|source| RegistryError::Discovery {
                path: root.clone(),
                source,
            })?;
            for item in directory {
                let item = item.map_err(|source| RegistryError::Discovery {
                    path: root.clone(),
                    source,
                })?;
                let path = item.path();
                if path.extension().and_then(|value| value.to_str()) != Some("json") {
                    continue;
                }
                let manifest = Arc::new(ValidatedManifest::load(&path, root)?);
                let id = manifest.manifest.module_id.to_string();
                if candidates
                    .insert(
                        id.clone(),
                        RegistryEntry {
                            manifest,
                            state: ModuleRegistrationState::Configured,
                            spawn_generation: 0,
                        },
                    )
                    .is_some()
                {
                    return Err(RegistryError::DuplicateModule(id));
                }
            }
        }
        Ok(candidates)
    }
}

fn validate_candidate_set(entries: &BTreeMap<String, RegistryEntry>) -> Result<(), RegistryError> {
    let mut providers = HashMap::<&str, &str>::new();
    for (module_id, entry) in entries {
        for operation in &entry.manifest.manifest.operations {
            if providers.insert(&operation.name, module_id).is_some() {
                return Err(RegistryError::CapabilityConflict(operation.name.clone()));
            }
        }
    }
    let mut edges = HashMap::<&str, Vec<&str>>::new();
    for (module_id, entry) in entries {
        let mut dependencies = Vec::new();
        for required in &entry.manifest.manifest.requires {
            let provider = providers.get(required.as_str()).ok_or_else(|| {
                RegistryError::MissingCapability {
                    module_id: module_id.clone(),
                    capability: required.clone(),
                }
            })?;
            dependencies.push(*provider);
        }
        edges.insert(module_id, dependencies);
    }
    let mut visiting = HashSet::new();
    let mut visited = HashSet::new();
    for module_id in entries.keys() {
        if has_cycle(module_id, &edges, &mut visiting, &mut visited) {
            return Err(RegistryError::DependencyCycle);
        }
    }
    Ok(())
}

fn has_cycle<'a>(
    module_id: &'a str,
    edges: &HashMap<&'a str, Vec<&'a str>>,
    visiting: &mut HashSet<&'a str>,
    visited: &mut HashSet<&'a str>,
) -> bool {
    if visited.contains(module_id) {
        return false;
    }
    if !visiting.insert(module_id) {
        return true;
    }
    if edges
        .get(module_id)
        .into_iter()
        .flatten()
        .any(|next| has_cycle(next, edges, visiting, visited))
    {
        return true;
    }
    visiting.remove(module_id);
    visited.insert(module_id);
    false
}

pub fn manifest_for_entry(entry: &RegistryEntry) -> &ModuleManifest {
    &entry.manifest.manifest
}

pub fn path_within(path: &Path, root: &Path) -> bool {
    path.starts_with(root)
}
