use crate::{validate_migrations, validate_supported_version, Migration, StoreError, StoreStatus};
use hypermid_contracts::storage::{BackendKind, Fence, LeaseKey};
use hypermid_leases::LocalLease;
use rusqlite::{params, Connection, Transaction, TransactionBehavior};
use std::fs;
use std::path::{Path, PathBuf};

const METADATA_SQL: &str = r#"
CREATE TABLE IF NOT EXISTS hypermid_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    digest TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS hypermid_lease_epochs (
    module_id TEXT NOT NULL,
    backend TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    epoch INTEGER NOT NULL CHECK(epoch > 0),
    PRIMARY KEY(module_id, backend, scope_key)
);
"#;

pub struct SQLiteStore {
    path: PathBuf,
    connection: Connection,
    lease: LocalLease,
    status: StoreStatus,
}

impl SQLiteStore {
    pub fn open(
        path: impl AsRef<Path>,
        lease_key: LeaseKey,
        supported_version: u64,
        migrations: &[Migration],
    ) -> Result<Self, StoreError> {
        validate_migrations(migrations)?;
        validate_supported_version(supported_version, migrations)?;
        if lease_key.backend != BackendKind::Sqlite {
            return Err(StoreError::StaleFence);
        }
        let path = path.as_ref().to_path_buf();
        guard_store_paths(&path)?;
        prepare_parent(&path)?;
        if let Ok(metadata) = fs::symlink_metadata(&path) {
            if !metadata.file_type().is_file() {
                return Err(StoreError::NotRegular(path));
            }
        }
        let lease_path = lease_path(&path);
        let lease = LocalLease::acquire(&lease_path, lease_key)?;
        prepare_store_file(&path)?;
        tighten_store_files(&path)?;
        let mut connection = Connection::open(&path)?;
        connection.busy_timeout(std::time::Duration::from_secs(5))?;
        connection.pragma_update(None, "foreign_keys", "ON")?;
        let has_metadata: bool = connection.query_row(
            "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='hypermid_migrations')",
            [],
            |row| row.get(0),
        )?;
        let applied = if has_metadata {
            read_applied(&connection)?
        } else {
            Vec::new()
        };
        validate_applied(&applied, migrations)?;
        let stored_version = applied.last().map_or(0, |row| row.0);
        if stored_version > supported_version {
            return Ok(Self {
                path,
                connection,
                lease,
                status: StoreStatus::StoreAhead {
                    stored_version,
                    supported_version,
                },
            });
        }

        connection.pragma_update(None, "journal_mode", "WAL")?;
        connection.pragma_update(None, "synchronous", "FULL")?;
        {
            let transaction =
                connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
            transaction.execute_batch(METADATA_SQL)?;
            transaction.commit()?;
        }

        for migration in migrations
            .iter()
            .filter(|migration| migration.version > stored_version)
        {
            if migration.version > supported_version {
                break;
            }
            let transaction =
                connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
            transaction.execute_batch(&migration.sql)?;
            transaction.execute(
                "INSERT INTO hypermid_migrations(version, name, digest) VALUES (?1, ?2, ?3)",
                params![migration.version, migration.name, migration.digest()],
            )?;
            transaction.commit()?;
        }

        let fence = lease.fence();
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        transaction.execute(
            "INSERT INTO hypermid_lease_epochs(module_id, backend, scope_key, epoch) \
             VALUES (?1, 'sqlite', ?2, ?3) \
             ON CONFLICT(module_id, backend, scope_key) DO UPDATE SET epoch=excluded.epoch",
            params![
                fence.lease.module_id.as_str(),
                fence.lease.scope_key.as_str(),
                fence.epoch
            ],
        )?;
        transaction.commit()?;
        tighten_store_files(&path)?;

        Ok(Self {
            path,
            connection,
            lease,
            status: StoreStatus::Ready {
                version: supported_version,
            },
        })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn status(&self) -> &StoreStatus {
        &self.status
    }

    pub fn current_fence(&self) -> &Fence {
        self.lease.fence()
    }

    pub fn read<T>(
        &self,
        operation: impl FnOnce(&Connection) -> Result<T, StoreError>,
    ) -> Result<T, StoreError> {
        operation(&self.connection)
    }

    pub fn with_fenced_transaction<T, E>(
        &mut self,
        fence: &Fence,
        operation: impl FnOnce(&Transaction<'_>) -> Result<T, E>,
    ) -> Result<Result<T, E>, StoreError> {
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
            });
        }
        if fence.lease != self.current_fence().lease {
            return Err(StoreError::StaleFence);
        }
        let transaction = self
            .connection
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        let recorded: Option<u64> = transaction
            .query_row(
                "SELECT epoch FROM hypermid_lease_epochs \
                 WHERE module_id=?1 AND backend='sqlite' AND scope_key=?2",
                params![
                    fence.lease.module_id.as_str(),
                    fence.lease.scope_key.as_str()
                ],
                |row| row.get(0),
            )
            .optional()?;
        if recorded != Some(fence.epoch) {
            return Err(StoreError::StaleFence);
        }
        match operation(&transaction) {
            Ok(result) => {
                transaction.commit()?;
                Ok(Ok(result))
            }
            Err(error) => Ok(Err(error)),
        }
    }
}

use rusqlite::OptionalExtension;

fn read_applied(connection: &Connection) -> Result<Vec<(u64, String, String)>, StoreError> {
    let mut statement = connection
        .prepare("SELECT version, name, digest FROM hypermid_migrations ORDER BY version ASC")?;
    let rows = statement
        .query_map([], |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)))?
        .collect::<Result<Vec<_>, _>>()?;
    Ok(rows)
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

fn lease_path(path: &Path) -> PathBuf {
    let mut value = path.as_os_str().to_os_string();
    value.push(".lease");
    PathBuf::from(value)
}

fn store_paths(path: &Path) -> Vec<PathBuf> {
    let mut paths = vec![path.to_path_buf(), lease_path(path)];
    for suffix in ["-journal", "-wal", "-shm"] {
        let mut value = path.as_os_str().to_os_string();
        value.push(suffix);
        paths.push(PathBuf::from(value));
    }
    paths
}

fn guard_store_paths(path: &Path) -> Result<(), StoreError> {
    for ancestor in path.ancestors().skip(1) {
        match fs::symlink_metadata(ancestor) {
            Ok(info) if !info.is_dir() => return Err(StoreError::NotRegular(ancestor.into())),
            Ok(_) => {},
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {},
            Err(error) => return Err(error.into()),
        }
    }
    for candidate in store_paths(path) {
        match fs::symlink_metadata(&candidate) {
            Ok(info) => {
                if !info.is_file() { return Err(StoreError::NotRegular(candidate)); }
                #[cfg(unix)] {
                    use std::os::unix::fs::MetadataExt;
                    if info.nlink() != 1 { return Err(StoreError::NotRegular(candidate)); }
                }
            },
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {},
            Err(error) => return Err(error.into()),
        }
    }
    Ok(())
}

fn prepare_parent(path: &Path) -> Result<(), StoreError> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(parent, fs::Permissions::from_mode(0o700))?;
        }
    }
    Ok(())
}

fn prepare_store_file(path: &Path) -> Result<(), StoreError> {
    let mut options = fs::OpenOptions::new();
    options.read(true).write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    match options.open(path) {
        Ok(file) => {
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                file.set_permissions(fs::Permissions::from_mode(0o600))?;
            }
        }
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
        Err(error) => return Err(error.into()),
    }
    Ok(())
}

fn tighten_store_files(path: &Path) -> Result<(), StoreError> {
    guard_store_paths(path)?;
    #[cfg(unix)] {
        use std::os::unix::fs::{MetadataExt, PermissionsExt};
        for candidate in store_paths(path).into_iter().filter(|candidate| *candidate != lease_path(path)) {
            let before = match fs::symlink_metadata(&candidate) {
                Ok(info) => info,
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
                Err(error) => return Err(error.into()),
            };
            let file = fs::File::open(&candidate)?;
            let opened = file.metadata()?;
            if !opened.is_file() || opened.nlink() != 1 || opened.dev() != before.dev() || opened.ino() != before.ino() {
                return Err(StoreError::NotRegular(candidate));
            }
            file.set_permissions(fs::Permissions::from_mode(0o600))?;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[cfg(unix)]
    #[test]
    fn linked_store_paths_are_refused_before_mode_changes() {
        use std::os::unix::fs::{PermissionsExt, symlink};
        for suffix in ["", "-journal", "-wal", "-shm", ".lease"] {
            let folder = tempfile::tempdir().unwrap();
            let outside = folder.path().join("outside");
            fs::write(&outside, b"unchanged").unwrap();
            fs::set_permissions(&outside, fs::Permissions::from_mode(0o644)).unwrap();
            let parent = folder.path().join("home");
            fs::create_dir(&parent).unwrap();
            fs::set_permissions(&parent, fs::Permissions::from_mode(0o755)).unwrap();
            let path = parent.join("store.db");
            fs::hard_link(&outside, PathBuf::from(format!("{}{suffix}", path.display()))).unwrap();
            assert!(SQLiteStore::open(&path, key(), 2, &migrations()).is_err());
            assert_eq!(fs::read(&outside).unwrap(), b"unchanged");
            assert_eq!(fs::metadata(&outside).unwrap().permissions().mode() & 0o777, 0o644);
            assert_eq!(fs::metadata(&parent).unwrap().permissions().mode() & 0o777, 0o755);
        }
        let folder = tempfile::tempdir().unwrap();
        let outside = folder.path().join("outside");
        fs::create_dir(&outside).unwrap();
        symlink(&outside, folder.path().join("linked")).unwrap();
        assert!(SQLiteStore::open(folder.path().join("linked/new/store.db"), key(), 2, &migrations()).is_err());
        assert!(!outside.join("new").exists());
    }

    #[cfg(unix)]
    #[test]
    fn database_is_private_before_sqlite_writes() {
        use std::os::unix::fs::PermissionsExt;
        let folder = tempfile::tempdir().unwrap();
        let path = folder.path().join("private").join("store.db");
        prepare_parent(&path).unwrap();
        prepare_store_file(&path).unwrap();
        assert_eq!(fs::metadata(&path).unwrap().len(), 0);
        assert_eq!(fs::metadata(&path).unwrap().permissions().mode() & 0o777, 0o600);
        let store = SQLiteStore::open(&path, key(), 2, &migrations()).unwrap();
        assert!(store.status().is_writable());
        for suffix in ["", "-wal", "-shm"] {
            let file = PathBuf::from(format!("{}{suffix}", path.display()));
            if file.exists() {
                assert_eq!(fs::metadata(file).unwrap().permissions().mode() & 0o777, 0o600);
            }
        }
    }

    fn key() -> LeaseKey {
        LeaseKey {
            module_id: "memory".parse().unwrap(),
            backend: BackendKind::Sqlite,
            scope_key: "owner:project".parse().unwrap(),
        }
    }

    fn migrations() -> Vec<Migration> {
        vec![
            Migration::new(1, "records", "CREATE TABLE records(value TEXT NOT NULL);"),
            Migration::new(2, "seed", "INSERT INTO records(value) VALUES ('ready');"),
        ]
    }

    #[test]
    fn migration_and_fence_are_transactional() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("store.sqlite3");
        let mut first = SQLiteStore::open(&path, key(), 2, &migrations()).unwrap();
        let stale = first.current_fence().clone();
        first
            .with_fenced_transaction(&stale, |tx| -> Result<_, rusqlite::Error> {
                tx.execute("INSERT INTO records(value) VALUES ('first')", [])?;
                Ok(())
            })
            .unwrap()
            .unwrap();
        drop(first);

        let mut replacement = SQLiteStore::open(&path, key(), 2, &migrations()).unwrap();
        assert!(matches!(
            replacement.with_fenced_transaction(&stale, |_| Ok::<_, rusqlite::Error>(())),
            Err(StoreError::StaleFence)
        ));
        assert_eq!(replacement.current_fence().epoch, stale.epoch + 1);
    }

    #[test]
    fn failed_migration_does_not_apply_its_schema_or_seed() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("store.sqlite3");
        let migrations = vec![Migration::new(
            1,
            "broken",
            "CREATE TABLE partial(value TEXT); INSERT INTO missing(value) VALUES ('x');",
        )];
        assert!(SQLiteStore::open(&path, key(), 1, &migrations).is_err());
        let connection = Connection::open(path).unwrap();
        let present: i64 = connection
            .query_row(
                "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='partial'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(present, 0);
    }
}
