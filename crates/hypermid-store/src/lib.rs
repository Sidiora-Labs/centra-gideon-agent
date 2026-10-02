pub mod authorization;
pub mod backup;
pub mod budget;
pub mod migrate;
pub mod sqlite;

use sha2::{Digest as _, Sha256};
use thiserror::Error;

pub use sqlite::SQLiteStore;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Migration {
    pub version: u64,
    pub name: String,
    pub sql: String,
}

impl Migration {
    pub fn new(version: u64, name: impl Into<String>, sql: impl Into<String>) -> Self {
        Self {
            version,
            name: name.into(),
            sql: sql.into(),
        }
    }

    pub fn digest(&self) -> String {
        hex::encode(Sha256::digest(self.sql.as_bytes()))
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum StoreStatus {
    Ready {
        version: u64,
    },
    StoreAhead {
        stored_version: u64,
        supported_version: u64,
    },
}

impl StoreStatus {
    pub fn is_writable(&self) -> bool {
        matches!(self, Self::Ready { .. })
    }
}

#[derive(Debug, Error)]
pub enum StoreError {
    #[error(transparent)]
    Lease(#[from] hypermid_leases::LeaseError),
    #[error("store path is not a regular file: {0}")]
    NotRegular(std::path::PathBuf),
    #[error("migration versions must be consecutive and begin at one")]
    InvalidMigrationOrder,
    #[error("stored migration chain is malformed")]
    MalformedMigrationChain,
    #[error("migration {version} differs from the recorded migration")]
    MigrationMismatch { version: u64 },
    #[error("store schema {stored_version} is newer than supported schema {supported_version}")]
    StoreAhead {
        stored_version: u64,
        supported_version: u64,
    },
    #[error("write fence is stale")]
    StaleFence,
    #[error("SQLite operation failed: {0}")]
    Sqlite(#[from] rusqlite::Error),
    #[error("store I/O failed: {0}")]
    Io(#[from] std::io::Error),
}

pub fn validate_migrations(migrations: &[Migration]) -> Result<(), StoreError> {
    for (index, migration) in migrations.iter().enumerate() {
        let expected = u64::try_from(index).unwrap_or(u64::MAX).saturating_add(1);
        if migration.version != expected || migration.name.is_empty() {
            return Err(StoreError::InvalidMigrationOrder);
        }
    }
    Ok(())
}

pub fn validate_supported_version(
    supported_version: u64,
    migrations: &[Migration],
) -> Result<(), StoreError> {
    if supported_version != migrations.len() as u64 {
        return Err(StoreError::InvalidMigrationOrder);
    }
    Ok(())
}
