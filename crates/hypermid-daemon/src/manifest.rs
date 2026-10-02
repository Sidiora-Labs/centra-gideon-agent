use std::{
    collections::HashSet,
    fs,
    path::{Path, PathBuf},
};

use hypermid_contracts::{Digest, Id, Scope};
use hypermid_protocol::PROTOCOL;
use serde::{Deserialize, Serialize};
use sha2::{Digest as _, Sha256};
use thiserror::Error;

#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ModuleRole {
    ControlSurface,
    OperationProvider,
    PipelineStage,
    InternalService,
    EventPublisher,
    EventConsumer,
    FederationGateway,
    MaintenanceWorker,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum EffectClass {
    Query,
    Idempotent,
    Durable,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Operation {
    pub name: String,
    pub effect: EffectClass,
    pub remote: bool,
    pub required_scopes: Vec<String>,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ConcurrencyPolicy {
    Serial,
    Bounded,
    ModuleManaged,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum OverlapPolicy {
    Exclusive,
    Safe,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RestartMode {
    Never,
    OnFailure,
    Always,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RestartPolicy {
    pub mode: RestartMode,
    pub max_restarts: u32,
    pub window_ms: u64,
    pub base_backoff_ms: u64,
    pub max_backoff_ms: u64,
    pub drain_timeout_ms: u64,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum HealthAction {
    Report,
    Drain,
    Restart,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct HealthPolicy {
    pub cadence_ms: u64,
    pub deadline_ms: u64,
    pub failure_threshold: u32,
    pub action: HealthAction,
}

#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ResourceLimits {
    pub memory_bytes: Option<u64>,
    pub cpu_millis_per_second: Option<u64>,
    pub open_files: Option<u64>,
    pub processes: Option<u64>,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum StorageKind {
    Directory,
    Sqlite,
    Secret,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct StorageBinding {
    pub name: Id,
    pub kind: StorageKind,
    pub scope: Scope,
    pub writable: bool,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ModuleManifest {
    pub schema_version: u32,
    pub module_id: Id,
    pub version: String,
    pub executable: PathBuf,
    #[serde(default)]
    pub arguments: Vec<String>,
    #[serde(default)]
    pub environment: std::collections::BTreeMap<String, String>,
    pub artifact_digest: Digest,
    pub protocol: String,
    pub roles: Vec<ModuleRole>,
    pub operations: Vec<Operation>,
    pub requires: Vec<String>,
    pub concurrency: ConcurrencyPolicy,
    pub max_concurrency: Option<u16>,
    pub overlap: OverlapPolicy,
    pub restart: RestartPolicy,
    pub health: HealthPolicy,
    #[serde(default)]
    pub resource_limits: ResourceLimits,
    #[serde(default)]
    pub storage_bindings: Vec<StorageBinding>,
}

#[derive(Clone, Debug)]
pub struct ValidatedManifest {
    pub manifest_path: PathBuf,
    pub executable_path: PathBuf,
    pub manifest: ModuleManifest,
}

#[derive(Debug, Error)]
pub enum ManifestError {
    #[error("configured root is unavailable: {0}")]
    InvalidRoot(PathBuf),
    #[error("manifest path escapes its configured root: {0}")]
    PathEscape(PathBuf),
    #[error("manifest I/O failed for {path}: {source}")]
    Io {
        path: PathBuf,
        source: std::io::Error,
    },
    #[error("manifest JSON is invalid for {path}: {source}")]
    Json {
        path: PathBuf,
        source: serde_json::Error,
    },
    #[error("unsupported manifest schema version {0}")]
    SchemaVersion(u32),
    #[error("unsupported module protocol {0:?}")]
    Protocol(String),
    #[error("manifest field is invalid: {0}")]
    InvalidField(&'static str),
    #[error("artifact digest does not match executable")]
    DigestMismatch,
}

impl ValidatedManifest {
    pub fn load(manifest_path: &Path, configured_root: &Path) -> Result<Self, ManifestError> {
        let root = configured_root
            .canonicalize()
            .map_err(|_| ManifestError::InvalidRoot(configured_root.to_path_buf()))?;
        let path = manifest_path
            .canonicalize()
            .map_err(|source| ManifestError::Io {
                path: manifest_path.to_path_buf(),
                source,
            })?;
        if !path.starts_with(&root) {
            return Err(ManifestError::PathEscape(path));
        }
        let bytes = fs::read(&path).map_err(|source| ManifestError::Io {
            path: path.clone(),
            source,
        })?;
        let manifest: ModuleManifest =
            serde_json::from_slice(&bytes).map_err(|source| ManifestError::Json {
                path: path.clone(),
                source,
            })?;
        manifest.validate_fields()?;
        let declared = if manifest.executable.is_absolute() {
            manifest.executable.clone()
        } else {
            path.parent().unwrap_or(&root).join(&manifest.executable)
        };
        let executable_path = declared
            .canonicalize()
            .map_err(|source| ManifestError::Io {
                path: declared,
                source,
            })?;
        if !executable_path.starts_with(&root) {
            return Err(ManifestError::PathEscape(executable_path));
        }
        let artifact = fs::read(&executable_path).map_err(|source| ManifestError::Io {
            path: executable_path.clone(),
            source,
        })?;
        let actual = Digest::from_bytes(Sha256::digest(artifact).into());
        if actual != manifest.artifact_digest {
            return Err(ManifestError::DigestMismatch);
        }
        Ok(Self {
            manifest_path: path,
            executable_path,
            manifest,
        })
    }
}

impl ModuleManifest {
    pub fn validate_fields(&self) -> Result<(), ManifestError> {
        if self.schema_version != 1 {
            return Err(ManifestError::SchemaVersion(self.schema_version));
        }
        if self.protocol != PROTOCOL {
            return Err(ManifestError::Protocol(self.protocol.clone()));
        }
        if self.version.is_empty() || self.version.len() > 64 || self.roles.is_empty() {
            return Err(ManifestError::InvalidField("version or roles"));
        }
        if self.max_concurrency.is_some() != (self.concurrency == ConcurrencyPolicy::Bounded) {
            return Err(ManifestError::InvalidField("max_concurrency"));
        }
        if self.restart.window_ms == 0
            || self.restart.base_backoff_ms == 0
            || self.restart.max_backoff_ms < self.restart.base_backoff_ms
            || self.health.cadence_ms < 100
            || self.health.deadline_ms == 0
            || self.health.failure_threshold == 0
        {
            return Err(ManifestError::InvalidField("restart or health policy"));
        }
        let mut roles = HashSet::new();
        if self.roles.iter().any(|role| !roles.insert(*role)) {
            return Err(ManifestError::InvalidField("duplicate role"));
        }
        let mut operations = HashSet::new();
        for operation in &self.operations {
            if !valid_operation_name(&operation.name)
                || !operations.insert(operation.name.as_str())
                || operation.required_scopes.iter().any(|scope| {
                    scope.is_empty()
                        || scope.len() > 160
                        || operation
                            .required_scopes
                            .iter()
                            .filter(|item| *item == scope)
                            .count()
                            > 1
                })
            {
                return Err(ManifestError::InvalidField("operation catalog"));
            }
        }
        let mut requirements = HashSet::new();
        if self
            .requires
            .iter()
            .any(|value| value.is_empty() || value.len() > 160 || !requirements.insert(value))
        {
            return Err(ManifestError::InvalidField("required capabilities"));
        }
        Ok(())
    }
}

fn valid_operation_name(value: &str) -> bool {
    if value.is_empty() || value.len() > 160 || !value.as_bytes()[0].is_ascii_lowercase() {
        return false;
    }
    let mut separator = false;
    for byte in value.bytes().skip(1) {
        if matches!(byte, b'.' | b'_' | b'-') {
            if separator {
                return false;
            }
            separator = true;
        } else if byte.is_ascii_lowercase() || byte.is_ascii_digit() {
            separator = false;
        } else {
            return false;
        }
    }
    !separator
}
