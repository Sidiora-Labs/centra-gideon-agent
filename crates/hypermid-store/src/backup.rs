use hypermid_contracts::{Cursor, Digest, Scope};
use rusqlite::{backup::Backup, Connection};
use serde::{Deserialize, Serialize};
use sha2::{Digest as _, Sha256};
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::time::Duration;
use tempfile::{NamedTempFile, TempDir};
use thiserror::Error;

const FORMAT_VERSION: u64 = 1;
const STORE_ENTRY: &str = "store.sqlite3";
const MANIFEST_ENTRY: &str = "manifest.json";

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SnapshotEntry {
    pub path: String,
    pub bytes: u64,
    pub digest: Digest,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SnapshotManifest {
    pub format_version: u64,
    pub schema_version: u64,
    pub scope: Scope,
    pub cursor: Cursor,
    pub source_digest: Digest,
    pub entries: Vec<SnapshotEntry>,
}

impl SnapshotManifest {
    pub fn canonical_bytes(&self) -> Result<Vec<u8>, LifecycleError> {
        Ok(serde_json::to_vec(self)?)
    }

    pub fn digest(&self) -> Result<Digest, LifecycleError> {
        Ok(Digest::sha256(self.canonical_bytes()?))
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SnapshotReceipt {
    pub scope: Scope,
    pub cursor: Cursor,
    pub manifest_digest: Digest,
    pub image_digest: Digest,
    pub bytes: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RestoreReceipt {
    pub scope: Scope,
    pub cursor: Cursor,
    pub manifest_digest: Digest,
    pub active_digest: Digest,
    pub bytes: u64,
}

#[derive(Debug, Error)]
pub enum LifecycleError {
    #[error("snapshot manifest is invalid")]
    InvalidManifest,
    #[error("snapshot manifest digest does not match")]
    ManifestDigestMismatch,
    #[error("snapshot entry digest does not match")]
    EntryDigestMismatch,
    #[error("snapshot scope does not match the requested scope")]
    ScopeMismatch,
    #[error("snapshot schema {stored} is newer than supported schema {supported}")]
    SchemaAhead { stored: u64, supported: u64 },
    #[error("snapshot schema metadata does not match its database")]
    SchemaMismatch,
    #[error("snapshot SQLite integrity check failed")]
    Integrity,
    #[error("active store changed after restore was staged")]
    ActiveStoreChanged,
    #[error("lifecycle validation failed: {0}")]
    DomainValidation(String),
    #[error("store replacement committed but directory durability is unknown")]
    ActivationOutcomeUnknown,
    #[error("lifecycle I/O failed: {0}")]
    Io(#[from] io::Error),
    #[error("lifecycle SQLite operation failed: {0}")]
    Sqlite(#[from] rusqlite::Error),
    #[error("lifecycle manifest parsing failed: {0}")]
    Json(#[from] serde_json::Error),
}

pub struct StagedRestore {
    active_path: PathBuf,
    staged: NamedTempFile,
    manifest: SnapshotManifest,
    manifest_digest: Digest,
    image_digest: Digest,
    bytes: u64,
}

impl StagedRestore {
    pub fn manifest(&self) -> &SnapshotManifest {
        &self.manifest
    }

    pub fn staging_path(&self) -> &Path {
        self.staged.path()
    }

    pub fn activate(
        self,
        expected_active_digest: Option<Digest>,
    ) -> Result<RestoreReceipt, LifecycleError> {
        let Self {
            active_path,
            mut staged,
            manifest,
            manifest_digest,
            image_digest,
            bytes,
        } = self;
        if active_path.exists() {
            require_regular(&active_path)?;
        }
        if sqlite_sidecars(&active_path)
            .iter()
            .any(|path| path.exists())
        {
            return Err(LifecycleError::ActiveStoreChanged);
        }
        let actual = if active_path.exists() {
            Some(file_digest(&active_path)?)
        } else {
            None
        };
        if actual != expected_active_digest {
            return Err(LifecycleError::ActiveStoreChanged);
        }
        staged.as_file_mut().sync_all()?;
        let persisted = staged.persist(&active_path).map_err(|error| error.error)?;
        persisted.sync_all()?;
        if sync_parent(&active_path).is_err() {
            return Err(LifecycleError::ActivationOutcomeUnknown);
        }
        Ok(RestoreReceipt {
            scope: manifest.scope,
            cursor: manifest.cursor,
            manifest_digest,
            active_digest: image_digest,
            bytes,
        })
    }

    pub(crate) fn from_staged(
        active_path: PathBuf,
        staged: NamedTempFile,
        manifest: SnapshotManifest,
    ) -> Result<Self, LifecycleError> {
        let bytes = staged.as_file().metadata()?.len();
        let image_digest = file_digest(staged.path())?;
        if manifest.entries.len() != 1
            || manifest.entries[0].bytes != bytes
            || manifest.entries[0].digest != image_digest
        {
            return Err(LifecycleError::EntryDigestMismatch);
        }
        let manifest_digest = manifest.digest()?;
        Ok(Self {
            active_path,
            staged,
            manifest,
            manifest_digest,
            image_digest,
            bytes,
        })
    }
}

pub fn create_sqlite_snapshot(
    source: &Connection,
    artifact_path: impl AsRef<Path>,
    scope: Scope,
    cursor: Cursor,
    schema_version: u64,
    source_digest: Digest,
) -> Result<SnapshotReceipt, LifecycleError> {
    create_sqlite_snapshot_with_transform(
        source,
        artifact_path,
        scope,
        cursor,
        schema_version,
        source_digest,
        |_| Ok(()),
    )
}

pub fn create_sqlite_snapshot_with_transform(
    source: &Connection,
    artifact_path: impl AsRef<Path>,
    scope: Scope,
    cursor: Cursor,
    schema_version: u64,
    source_digest: Digest,
    transform: impl FnOnce(&Connection) -> Result<(), String>,
) -> Result<SnapshotReceipt, LifecycleError> {
    let artifact_path = artifact_path.as_ref();
    if artifact_path.exists() {
        return Err(LifecycleError::InvalidManifest);
    }
    let parent = artifact_path
        .parent()
        .ok_or(LifecycleError::InvalidManifest)?;
    if recorded_schema_version(source)? != schema_version {
        return Err(LifecycleError::SchemaMismatch);
    }
    prepare_private_directory(parent)?;
    let staging = tempfile::tempdir_in(parent)?;
    let image_path = staging.path().join(STORE_ENTRY);
    let mut destination = Connection::open(&image_path)?;
    {
        let backup = Backup::new(source, &mut destination)?;
        backup.run_to_completion(64, Duration::from_millis(5), None)?;
    }
    transform(&destination).map_err(LifecycleError::DomainValidation)?;
    verify_integrity(&destination)?;
    if recorded_schema_version(&destination)? != schema_version {
        return Err(LifecycleError::SchemaMismatch);
    }
    drop(destination);
    private_file(&image_path)?;
    File::open(&image_path)?.sync_all()?;
    let bytes = fs::metadata(&image_path)?.len();
    let image_digest = file_digest(&image_path)?;
    let manifest = SnapshotManifest {
        format_version: FORMAT_VERSION,
        schema_version,
        scope: scope.clone(),
        cursor,
        source_digest,
        entries: vec![SnapshotEntry {
            path: STORE_ENTRY.to_owned(),
            bytes,
            digest: image_digest,
        }],
    };
    let manifest_bytes = manifest.canonical_bytes()?;
    let manifest_digest = Digest::sha256(&manifest_bytes);
    write_private(&staging.path().join(MANIFEST_ENTRY), &manifest_bytes)?;
    sync_directory(staging.path())?;
    persist_directory(staging, artifact_path)?;
    sync_parent(artifact_path)?;
    Ok(SnapshotReceipt {
        scope,
        cursor,
        manifest_digest,
        image_digest,
        bytes,
    })
}

pub fn stage_restore(
    active_path: impl AsRef<Path>,
    artifact_path: impl AsRef<Path>,
    expected_manifest_digest: Digest,
    expected_scope: &Scope,
    supported_schema: u64,
    validate: impl FnOnce(&Connection, &SnapshotManifest) -> Result<(), String>,
) -> Result<StagedRestore, LifecycleError> {
    let active_path = active_path.as_ref().to_path_buf();
    let artifact_path = artifact_path.as_ref();
    let manifest_path = artifact_path.join(MANIFEST_ENTRY);
    require_regular(&manifest_path)?;
    let manifest_bytes = fs::read(&manifest_path)?;
    if Digest::sha256(&manifest_bytes) != expected_manifest_digest {
        return Err(LifecycleError::ManifestDigestMismatch);
    }
    let manifest: SnapshotManifest = serde_json::from_slice(&manifest_bytes)?;
    validate_manifest(&manifest, expected_scope, supported_schema)?;
    let entry = &manifest.entries[0];
    let image_path = artifact_path.join(&entry.path);
    require_regular(&image_path)?;
    if fs::metadata(&image_path)?.len() != entry.bytes || file_digest(&image_path)? != entry.digest
    {
        return Err(LifecycleError::EntryDigestMismatch);
    }

    let parent = active_path
        .parent()
        .ok_or(LifecycleError::InvalidManifest)?;
    prepare_private_directory(parent)?;
    let mut staged = tempfile::Builder::new()
        .prefix(".hypermid-restore-")
        .tempfile_in(parent)?;
    let mut source = File::open(&image_path)?;
    io::copy(&mut source, staged.as_file_mut())?;
    staged.as_file_mut().sync_all()?;
    private_file(staged.path())?;
    let connection = Connection::open(staged.path())?;
    verify_integrity(&connection)?;
    if recorded_schema_version(&connection)? != manifest.schema_version {
        return Err(LifecycleError::SchemaMismatch);
    }
    validate(&connection, &manifest).map_err(LifecycleError::DomainValidation)?;
    drop(connection);
    StagedRestore::from_staged(active_path, staged, manifest)
}

pub(crate) fn verify_integrity(connection: &Connection) -> Result<(), LifecycleError> {
    let result: String = connection.query_row("PRAGMA integrity_check", [], |row| row.get(0))?;
    if result != "ok" {
        return Err(LifecycleError::Integrity);
    }
    Ok(())
}

pub(crate) fn recorded_schema_version(connection: &Connection) -> Result<u64, LifecycleError> {
    let exists: bool = connection.query_row(
        "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='hypermid_migrations')",
        [],
        |row| row.get(0),
    )?;
    if !exists {
        return Ok(0);
    }
    let version: Option<u64> =
        connection.query_row("SELECT MAX(version) FROM hypermid_migrations", [], |row| {
            row.get(0)
        })?;
    Ok(version.unwrap_or(0))
}

pub(crate) fn file_digest(path: &Path) -> Result<Digest, LifecycleError> {
    let mut file = File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        hasher.update(&buffer[..count]);
    }
    Ok(Digest::from_bytes(hasher.finalize().into()))
}

fn validate_manifest(
    manifest: &SnapshotManifest,
    expected_scope: &Scope,
    supported_schema: u64,
) -> Result<(), LifecycleError> {
    if manifest.format_version != FORMAT_VERSION
        || manifest.entries.len() != 1
        || manifest.entries[0].path != STORE_ENTRY
    {
        return Err(LifecycleError::InvalidManifest);
    }
    if &manifest.scope != expected_scope {
        return Err(LifecycleError::ScopeMismatch);
    }
    if manifest.schema_version > supported_schema {
        return Err(LifecycleError::SchemaAhead {
            stored: manifest.schema_version,
            supported: supported_schema,
        });
    }
    Ok(())
}

fn require_regular(path: &Path) -> Result<(), LifecycleError> {
    if !fs::symlink_metadata(path)?.file_type().is_file() {
        return Err(LifecycleError::InvalidManifest);
    }
    Ok(())
}

fn prepare_private_directory(path: &Path) -> Result<(), LifecycleError> {
    fs::create_dir_all(path)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o700))?;
    }
    Ok(())
}

fn private_file(path: &Path) -> Result<(), LifecycleError> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o600))?;
    }
    Ok(())
}

fn write_private(path: &Path, bytes: &[u8]) -> Result<(), LifecycleError> {
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(path)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    Ok(())
}

fn persist_directory(staging: TempDir, destination: &Path) -> Result<(), LifecycleError> {
    let staging_path = staging.keep();
    match fs::rename(&staging_path, destination) {
        Ok(()) => Ok(()),
        Err(error) => {
            let _ = fs::remove_dir_all(staging_path);
            Err(error.into())
        }
    }
}

fn sync_parent(path: &Path) -> Result<(), LifecycleError> {
    let parent = path.parent().ok_or(LifecycleError::InvalidManifest)?;
    sync_directory(parent)
}

fn sqlite_sidecars(path: &Path) -> [PathBuf; 2] {
    [
        PathBuf::from(format!("{}-wal", path.display())),
        PathBuf::from(format!("{}-shm", path.display())),
    ]
}

#[cfg(unix)]
fn sync_directory(path: &Path) -> Result<(), LifecycleError> {
    File::open(path)?.sync_all()?;
    Ok(())
}

#[cfg(not(unix))]
fn sync_directory(_path: &Path) -> Result<(), LifecycleError> {
    Ok(())
}
