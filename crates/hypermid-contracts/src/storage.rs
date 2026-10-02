use crate::{ContractViolation, Id, MAX_SAFE_INTEGER};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum StorageIsolation {
    PerModule,
    PerProject,
}

#[derive(Clone, Debug, Deserialize, Eq, Serialize, PartialEq)]
#[serde(untagged)]
pub enum StorageBackend {
    Sqlite(SqliteBackend),
    Postgres(PostgresBackend),
}

#[derive(Clone, Debug, Deserialize, Eq, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct SqliteBackend {
    pub kind: SqliteKind,
    pub path: PathBuf,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Serialize, PartialEq)]
pub enum SqliteKind {
    #[serde(rename = "sqlite")]
    Sqlite,
}

#[derive(Clone, Debug, Deserialize, Eq, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct PostgresBackend {
    pub kind: PostgresKind,
    pub dsn_ref: Id,
    pub database: String,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Serialize, PartialEq)]
pub enum PostgresKind {
    #[serde(rename = "postgres")]
    Postgres,
}

impl StorageBackend {
    pub fn sqlite(path: impl Into<PathBuf>) -> Result<Self, StorageViolation> {
        let path = path.into();
        if path.as_os_str().is_empty() {
            return Err(StorageViolation::EmptyPath);
        }
        Ok(Self::Sqlite(SqliteBackend {
            kind: SqliteKind::Sqlite,
            path,
        }))
    }

    pub fn postgres(dsn_ref: Id, database: impl Into<String>) -> Result<Self, StorageViolation> {
        let database = database.into();
        validate_database_name(&database)?;
        Ok(Self::Postgres(PostgresBackend {
            kind: PostgresKind::Postgres,
            dsn_ref,
            database,
        }))
    }

    pub fn sqlite_path(&self) -> Option<&Path> {
        match self {
            Self::Sqlite(backend) => Some(&backend.path),
            Self::Postgres(_) => None,
        }
    }
}

#[derive(Clone, Debug, Deserialize, Eq, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct StorageDescriptor {
    pub module_id: Id,
    pub isolation: StorageIsolation,
    pub backend: StorageBackend,
}

impl StorageDescriptor {
    pub fn new(module_id: Id, isolation: StorageIsolation, backend: StorageBackend) -> Self {
        Self {
            module_id,
            isolation,
            backend,
        }
    }

    pub fn require_resolved(&self) -> Result<(), StorageViolation> {
        if let Some(path) = self.backend.sqlite_path() {
            if !path.is_absolute() {
                return Err(StorageViolation::RelativePath(path.to_path_buf()));
            }
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, Serialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum BackendKind {
    Sqlite,
    Postgres,
}

#[derive(Clone, Debug, Deserialize, Eq, Hash, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct LeaseKey {
    pub module_id: Id,
    pub backend: BackendKind,
    pub scope_key: Id,
}

#[derive(Clone, Debug, Deserialize, Eq, Hash, Serialize, PartialEq)]
#[serde(try_from = "FenceWire", into = "FenceWire")]
pub struct Fence {
    pub lease: LeaseKey,
    pub epoch: u64,
}

impl Fence {
    pub fn new(lease: LeaseKey, epoch: u64) -> Result<Self, StorageViolation> {
        if epoch == 0 || epoch > MAX_SAFE_INTEGER {
            return Err(StorageViolation::InvalidEpoch);
        }
        Ok(Self { lease, epoch })
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct FenceWire {
    lease: LeaseKey,
    epoch: u64,
}

impl TryFrom<FenceWire> for Fence {
    type Error = StorageViolation;

    fn try_from(value: FenceWire) -> Result<Self, Self::Error> {
        Self::new(value.lease, value.epoch)
    }
}

impl From<Fence> for FenceWire {
    fn from(value: Fence) -> Self {
        Self {
            lease: value.lease,
            epoch: value.epoch,
        }
    }
}

fn validate_database_name(value: &str) -> Result<(), StorageViolation> {
    let bytes = value.as_bytes();
    if bytes.is_empty()
        || bytes.len() > 63
        || !(bytes[0].is_ascii_lowercase() || bytes[0] == b'_')
        || !bytes[1..]
            .iter()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || *byte == b'_')
    {
        return Err(StorageViolation::InvalidDatabaseName);
    }
    Ok(())
}

#[derive(Debug, thiserror::Error)]
pub enum StorageViolation {
    #[error("storage path is empty")]
    EmptyPath,
    #[error("storage path is relative: {0}")]
    RelativePath(PathBuf),
    #[error("PostgreSQL database name is invalid")]
    InvalidDatabaseName,
    #[error("fence epoch is outside the Hypermid wire range")]
    InvalidEpoch,
    #[error(transparent)]
    Contract(#[from] ContractViolation),
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn storage_descriptor_preserves_backend_shape() {
        let descriptor = StorageDescriptor::new(
            Id::new("memory").unwrap(),
            StorageIsolation::PerProject,
            StorageBackend::sqlite("relative.sqlite").unwrap(),
        );
        assert!(matches!(
            descriptor.require_resolved(),
            Err(StorageViolation::RelativePath(_))
        ));
        assert_eq!(
            serde_json::to_value(&descriptor).unwrap()["backend"]["kind"],
            "sqlite"
        );
    }
}
