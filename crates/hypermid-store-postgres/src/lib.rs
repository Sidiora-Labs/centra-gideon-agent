use hypermid_contracts::storage::{BackendKind, Fence, LeaseKey};
use hypermid_store::{
    validate_migrations, validate_supported_version, Migration, StoreError, StoreStatus,
};
use postgres::{Client, NoTls, Transaction};
use sha2::{Digest as _, Sha256};
use thiserror::Error;

const METADATA_SQL: &str = r#"
CREATE TABLE IF NOT EXISTS hypermid_migrations (
    namespace TEXT NOT NULL,
    version BIGINT NOT NULL,
    name TEXT NOT NULL,
    digest TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(namespace, version)
);
CREATE TABLE IF NOT EXISTS hypermid_lease_epochs (
    namespace TEXT PRIMARY KEY,
    module_id TEXT NOT NULL,
    backend TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    epoch BIGINT NOT NULL CHECK(epoch > 0)
);
"#;

#[derive(Debug, Error)]
pub enum PostgresStoreError {
    #[error(transparent)]
    Contract(#[from] StoreError),
    #[error("PostgreSQL operation failed: {0}")]
    Postgres(#[from] postgres::Error),
    #[error("PostgreSQL writer lease is held by another session")]
    Contended,
    #[error("connected database {actual} does not match descriptor database {expected}")]
    WrongDatabase { expected: String, actual: String },
    #[error("write fence is stale")]
    StaleFence,
    #[error("lease epoch is exhausted")]
    EpochOverflow,
}

pub struct PostgresStore {
    client: Client,
    namespace: String,
    advisory_key: i64,
    fence: Fence,
    status: StoreStatus,
}

impl PostgresStore {
    pub fn connect(
        dsn: &str,
        expected_database: &str,
        namespace: impl Into<String>,
        lease_key: LeaseKey,
        supported_version: u64,
        migrations: &[Migration],
    ) -> Result<Self, PostgresStoreError> {
        validate_migrations(migrations)?;
        validate_supported_version(supported_version, migrations)?;
        if lease_key.backend != BackendKind::Postgres {
            return Err(PostgresStoreError::StaleFence);
        }
        let namespace = namespace.into();
        let mut client = Client::connect(dsn, NoTls)?;
        let actual: String = client.query_one("SELECT current_database()", &[])?.get(0);
        if actual != expected_database {
            return Err(PostgresStoreError::WrongDatabase {
                expected: expected_database.to_owned(),
                actual,
            });
        }
        let advisory_key = advisory_key(&lease_key);
        let acquired: bool = client
            .query_one("SELECT pg_try_advisory_lock($1)", &[&advisory_key])?
            .get(0);
        if !acquired {
            return Err(PostgresStoreError::Contended);
        }

        let result = Self::initialize(
            client,
            namespace,
            advisory_key,
            lease_key,
            supported_version,
            migrations,
        );
        result
    }

    fn initialize(
        mut client: Client,
        namespace: String,
        advisory_key: i64,
        lease_key: LeaseKey,
        supported_version: u64,
        migrations: &[Migration],
    ) -> Result<Self, PostgresStoreError> {
        let has_metadata: bool = client
            .query_one("SELECT to_regclass('hypermid_migrations') IS NOT NULL", &[])?
            .get(0);
        let rows = if has_metadata {
            client.query(
                "SELECT version, name, digest FROM hypermid_migrations WHERE namespace=$1 ORDER BY version ASC",
                &[&namespace],
            )?
        } else {
            Vec::new()
        };
        let applied: Vec<(u64, String, String)> = rows
            .into_iter()
            .map(|row| {
                let version: i64 = row.get(0);
                (version as u64, row.get(1), row.get(2))
            })
            .collect();
        validate_applied(&applied, migrations)?;
        let stored_version = applied.last().map_or(0, |row| row.0);
        if stored_version > supported_version {
            return Ok(Self {
                client,
                namespace,
                advisory_key,
                fence: Fence {
                    lease: lease_key,
                    epoch: 1,
                },
                status: StoreStatus::StoreAhead {
                    stored_version,
                    supported_version,
                },
            });
        }

        client.batch_execute(METADATA_SQL)?;

        for migration in migrations
            .iter()
            .filter(|migration| migration.version > stored_version)
        {
            if migration.version > supported_version {
                break;
            }
            let mut transaction = client.transaction()?;
            transaction.batch_execute(&migration.sql)?;
            transaction.execute(
                "INSERT INTO hypermid_migrations(namespace, version, name, digest) VALUES ($1, $2, $3, $4)",
                &[&namespace, &(migration.version as i64), &migration.name, &migration.digest()],
            )?;
            transaction.commit()?;
        }

        let mut epoch_transaction = client.transaction()?;
        let previous = epoch_transaction
            .query_opt(
                "SELECT epoch FROM hypermid_lease_epochs WHERE namespace=$1 FOR UPDATE",
                &[&namespace],
            )?
            .map(|row| row.get::<_, i64>(0) as u64)
            .unwrap_or(0);
        let epoch = previous
            .checked_add(1)
            .ok_or(PostgresStoreError::EpochOverflow)?;
        epoch_transaction.execute(
            "INSERT INTO hypermid_lease_epochs(namespace, module_id, backend, scope_key, epoch) \
             VALUES ($1, $2, 'postgres', $3, $4) \
             ON CONFLICT(namespace) DO UPDATE SET module_id=EXCLUDED.module_id, backend='postgres', \
             scope_key=EXCLUDED.scope_key, epoch=EXCLUDED.epoch",
            &[
                &namespace,
                &lease_key.module_id.as_str(),
                &lease_key.scope_key.as_str(),
                &(epoch as i64),
            ],
        )?;
        epoch_transaction.commit()?;

        Ok(Self {
            client,
            namespace,
            advisory_key,
            fence: Fence {
                lease: lease_key,
                epoch,
            },
            status: StoreStatus::Ready {
                version: supported_version,
            },
        })
    }

    pub fn status(&self) -> &StoreStatus {
        &self.status
    }

    pub fn current_fence(&self) -> &Fence {
        &self.fence
    }

    pub fn with_fenced_transaction<T, E>(
        &mut self,
        fence: &Fence,
        operation: impl FnOnce(&mut Transaction<'_>) -> Result<T, E>,
    ) -> Result<Result<T, E>, PostgresStoreError> {
        if !self.status.is_writable() {
            let StoreStatus::StoreAhead {
                stored_version,
                supported_version,
            } = self.status
            else {
                unreachable!()
            };
            return Err(StoreError::StoreAhead {
                stored_version,
                supported_version,
            }
            .into());
        }
        if *fence != self.fence {
            return Err(PostgresStoreError::StaleFence);
        }
        let mut transaction = self.client.transaction()?;
        let recorded = transaction
            .query_opt(
                "SELECT module_id, backend, scope_key, epoch FROM hypermid_lease_epochs WHERE namespace=$1 FOR UPDATE",
                &[&self.namespace],
            )?
            .map(|row| {
                (
                    row.get::<_, String>(0),
                    row.get::<_, String>(1),
                    row.get::<_, String>(2),
                    row.get::<_, i64>(3) as u64,
                )
            });
        let expected = (
            fence.lease.module_id.as_str().to_owned(),
            "postgres".to_owned(),
            fence.lease.scope_key.as_str().to_owned(),
            fence.epoch,
        );
        if recorded != Some(expected) {
            return Err(PostgresStoreError::StaleFence);
        }
        match operation(&mut transaction) {
            Ok(result) => {
                transaction.commit()?;
                Ok(Ok(result))
            }
            Err(error) => Ok(Err(error)),
        }
    }
}

impl Drop for PostgresStore {
    fn drop(&mut self) {
        let _ = self
            .client
            .execute("SELECT pg_advisory_unlock($1)", &[&self.advisory_key]);
    }
}

pub fn database_name(module_id: &str) -> String {
    let mut slug: String = module_id
        .chars()
        .map(|character| {
            let lower = character.to_ascii_lowercase();
            if lower.is_ascii_alphanumeric() || lower == '_' {
                lower
            } else {
                '_'
            }
        })
        .collect();
    if slug.is_empty() || slug.as_bytes()[0].is_ascii_digit() {
        slug.insert_str(0, "m_");
    }
    slug.truncate(48);
    let digest = hex::encode(Sha256::digest(module_id.as_bytes()));
    format!("{slug}_{}", &digest[..12])
}

fn advisory_key(key: &LeaseKey) -> i64 {
    let material = format!(
        "{}\0postgres\0{}",
        key.module_id.as_str(),
        key.scope_key.as_str()
    );
    let digest = Sha256::digest(material.as_bytes());
    i64::from_be_bytes(digest[..8].try_into().expect("SHA-256 prefix length"))
}

fn validate_applied(
    applied: &[(u64, String, String)],
    migrations: &[Migration],
) -> Result<(), StoreError> {
    for (index, (version, name, digest)) in applied.iter().enumerate() {
        let expected = index as u64 + 1;
        if *version != expected {
            return Err(StoreError::MalformedMigrationChain);
        }
        if let Some(migration) = migrations.get(index) {
            if migration.version != *version
                || migration.name != *name
                || migration.digest() != *digest
            {
                return Err(StoreError::MigrationMismatch { version: *version });
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use hypermid_contracts::Id;

    #[test]
    fn readable_slug_collisions_keep_distinct_database_names() {
        assert_ne!(database_name("module-a"), database_name("module_a"));
        assert!(database_name("9-Memory.Module").len() <= 63);
    }

    #[test]
    fn live_session_lock_reclaims_and_fences_an_old_epoch() {
        let Ok(dsn) = std::env::var("HYPERMID_TEST_POSTGRES_DSN") else {
            return;
        };
        let Ok(database) = std::env::var("HYPERMID_TEST_POSTGRES_DATABASE") else {
            return;
        };
        let namespace = format!("base02_{}", uuid::Uuid::new_v4().simple());
        let key = LeaseKey {
            module_id: Id::new("memory").unwrap(),
            backend: BackendKind::Postgres,
            scope_key: Id::new(namespace.clone()).unwrap(),
        };
        let migrations = [Migration::new(1, "foundation", "SELECT 1;")];
        let first =
            PostgresStore::connect(&dsn, &database, &namespace, key.clone(), 1, &migrations)
                .unwrap();
        let stale = first.current_fence().clone();
        assert!(matches!(
            PostgresStore::connect(&dsn, &database, &namespace, key.clone(), 1, &migrations),
            Err(PostgresStoreError::Contended)
        ));
        drop(first);
        let mut replacement =
            PostgresStore::connect(&dsn, &database, &namespace, key, 1, &migrations).unwrap();
        assert_eq!(replacement.current_fence().epoch, stale.epoch + 1);
        assert!(matches!(
            replacement.with_fenced_transaction(&stale, |_| Ok::<_, ()>(())),
            Err(PostgresStoreError::StaleFence)
        ));
    }
}
