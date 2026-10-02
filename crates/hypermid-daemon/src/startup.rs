use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};

use hypermid_bus::{DurableEffectLedger, DurableEventBus, EffectStatus};
use hypermid_contracts::{Cursor, Digest, Id, Scope};
use hypermid_memory::{
    recovery::{inspect_store, stage_repaired_restore},
    MemoryRecoveryReport, MEMORY_SCHEMA_VERSION,
};
use hypermid_store::backup::SnapshotManifest;
use rusqlite::{Connection, OpenFlags};
use serde::{Deserialize, Serialize};
use sha2::{Digest as _, Sha256};

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum StartupRecoveryMode {
    Ready,
    ReadOnlyRecovery,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct StartupRecoveryIssue {
    pub code: String,
    pub message: String,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct StartupUnknownEffect {
    pub effect_id: Id,
    pub module_id: Id,
    pub operation: String,
    pub scope: Scope,
    pub input_digest: Digest,
    pub created_ms: u64,
    pub reason: String,
    pub settled_ms: u64,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct StartupRecoveryReport {
    pub observed_at_ms: u64,
    pub mode: StartupRecoveryMode,
    pub memories: Vec<MemoryRecoveryReport>,
    pub unfinished_migration_versions: Vec<u64>,
    pub outbox_cursor: Option<Cursor>,
    pub unknown_effects: Vec<StartupUnknownEffect>,
    pub issues: Vec<StartupRecoveryIssue>,
}

impl StartupRecoveryReport {
    pub fn is_ready(&self) -> bool {
        self.mode == StartupRecoveryMode::Ready
    }
}

#[derive(Debug, thiserror::Error)]
pub enum StartupRecoveryError {
    #[error("startup could not open the durable memory store: {0}")]
    OpenMemory(#[source] rusqlite::Error),
}

pub struct StartupReconciler;

impl StartupReconciler {
    pub fn run(
        memory_path: impl AsRef<Path>,
        now_ms: u64,
    ) -> Result<StartupRecoveryReport, StartupRecoveryError> {
        let memory_path = memory_path.as_ref();
        let state_root = memory_path.parent().unwrap_or_else(|| Path::new("."));
        let connection = Connection::open_with_flags(
            memory_path,
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )
        .map_err(StartupRecoveryError::OpenMemory)?;
        connection
            .pragma_update(None, "query_only", true)
            .map_err(StartupRecoveryError::OpenMemory)?;

        let mut issues = Vec::new();
        let unfinished_migration_versions = unfinished_migrations(&connection, &mut issues);
        let memories = inspect_memories(&connection, &mut issues);
        let outbox_cursor = inspect_outbox(state_root.join("events.sqlite3"), &mut issues);
        let unknown_effects =
            reconcile_effects(state_root.join("effects.journal"), now_ms, &mut issues);
        let mode = if issues.is_empty() && unfinished_migration_versions.is_empty() {
            StartupRecoveryMode::Ready
        } else {
            StartupRecoveryMode::ReadOnlyRecovery
        };

        Ok(StartupRecoveryReport {
            observed_at_ms: now_ms,
            mode,
            memories,
            unfinished_migration_versions,
            outbox_cursor,
            unknown_effects,
            issues,
        })
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PendingRestoreIntent {
    pub intent_id: Id,
    pub artifact_id: Id,
    pub scope: Scope,
    pub manifest_digest: Digest,
    pub expected_active_digest: Digest,
    pub created_at_ms: u64,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PendingRestoreReceipt {
    pub intent: PendingRestoreIntent,
    pub state: String,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct StartupRestoreReceipt {
    pub intent_id: Id,
    pub artifact_id: Id,
    pub scope: Scope,
    pub cursor: Cursor,
    pub manifest_digest: Digest,
    pub active_digest: Digest,
    pub bytes: u64,
    pub recovered_at_ms: u64,
    pub replayed: bool,
}

#[derive(Debug, thiserror::Error)]
pub enum RecoveryAuthorityError {
    #[error("recovery authority I/O failed: {0}")]
    Io(#[from] std::io::Error),
    #[error("recovery intent is invalid: {0}")]
    InvalidIntent(String),
    #[error("recovery intent serialization failed: {0}")]
    Json(#[from] serde_json::Error),
    #[error("recovery validation failed: {0}")]
    Memory(String),
    #[error("recovery activation failed: {0}")]
    Activation(String),
}

pub struct RecoveryAuthority {
    state_root: PathBuf,
}

impl RecoveryAuthority {
    pub fn new(state_root: impl AsRef<Path>) -> Self {
        Self {
            state_root: state_root.as_ref().to_path_buf(),
        }
    }

    pub fn queue_restore(
        &self,
        intent: PendingRestoreIntent,
    ) -> Result<PendingRestoreReceipt, RecoveryAuthorityError> {
        let recovery_root = self.recovery_root();
        fs::create_dir_all(&recovery_root)?;
        private_directory(&recovery_root)?;
        let artifact = self.artifact_path(&intent.artifact_id);
        stage_repaired_restore(
            self.memory_path(),
            &artifact,
            intent.manifest_digest,
            &intent.scope,
        )
        .map_err(|error| RecoveryAuthorityError::Memory(error.to_string()))?;

        let intent_path = self.intent_path();
        if intent_path.exists() {
            let existing = self.load_intent()?.ok_or_else(|| {
                RecoveryAuthorityError::InvalidIntent("pending restore disappeared".into())
            })?;
            if existing == intent {
                return Ok(PendingRestoreReceipt {
                    intent,
                    state: "pending_restart".into(),
                });
            }
            return Err(RecoveryAuthorityError::InvalidIntent(
                "another restore is already pending".into(),
            ));
        }
        write_atomic(&intent_path, &serde_json::to_vec(&intent)?)?;
        Ok(PendingRestoreReceipt {
            intent,
            state: "pending_restart".into(),
        })
    }

    pub fn recover_pending(
        &self,
        now_ms: u64,
    ) -> Result<Option<StartupRestoreReceipt>, RecoveryAuthorityError> {
        let Some(intent) = self.load_intent()? else {
            return Ok(None);
        };
        let artifact = self.artifact_path(&intent.artifact_id);
        let manifest = load_manifest(&artifact, intent.manifest_digest)?;
        if manifest.scope != intent.scope {
            return Err(RecoveryAuthorityError::InvalidIntent(
                "pending restore scope does not match its manifest".into(),
            ));
        }

        if active_matches_manifest(&self.memory_path(), &manifest) {
            let receipt = StartupRestoreReceipt {
                intent_id: intent.intent_id,
                artifact_id: intent.artifact_id,
                scope: manifest.scope,
                cursor: manifest.cursor,
                manifest_digest: intent.manifest_digest,
                active_digest: file_digest(&self.memory_path())?,
                bytes: fs::metadata(self.memory_path())?.len(),
                recovered_at_ms: now_ms,
                replayed: true,
            };
            self.clear_intent()?;
            return Ok(Some(receipt));
        }

        let staged = stage_repaired_restore(
            self.memory_path(),
            &artifact,
            intent.manifest_digest,
            &intent.scope,
        )
        .map_err(|error| RecoveryAuthorityError::Memory(error.to_string()))?;
        let activated = staged
            .activate(Some(intent.expected_active_digest))
            .map_err(|error| RecoveryAuthorityError::Activation(error.to_string()))?;
        let receipt = StartupRestoreReceipt {
            intent_id: intent.intent_id,
            artifact_id: intent.artifact_id,
            scope: activated.scope,
            cursor: activated.cursor,
            manifest_digest: activated.manifest_digest,
            active_digest: activated.active_digest,
            bytes: activated.bytes,
            recovered_at_ms: now_ms,
            replayed: false,
        };
        self.clear_intent()?;
        Ok(Some(receipt))
    }

    pub fn pending(&self) -> Result<Option<PendingRestoreIntent>, RecoveryAuthorityError> {
        self.load_intent()
    }

    fn memory_path(&self) -> PathBuf {
        self.state_root.join("memory.sqlite3")
    }

    fn recovery_root(&self) -> PathBuf {
        self.state_root.join("recovery")
    }

    fn artifact_path(&self, artifact_id: &Id) -> PathBuf {
        self.recovery_root().join(artifact_id.as_str())
    }

    fn intent_path(&self) -> PathBuf {
        self.recovery_root().join("pending-restore.json")
    }

    fn load_intent(&self) -> Result<Option<PendingRestoreIntent>, RecoveryAuthorityError> {
        let path = self.intent_path();
        let bytes = match fs::read(path) {
            Ok(bytes) => bytes,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(error.into()),
        };
        serde_json::from_slice(&bytes)
            .map(Some)
            .map_err(|error| RecoveryAuthorityError::InvalidIntent(error.to_string()))
    }

    fn clear_intent(&self) -> Result<(), RecoveryAuthorityError> {
        fs::remove_file(self.intent_path())?;
        sync_directory(&self.recovery_root())?;
        Ok(())
    }
}

fn unfinished_migrations(
    connection: &Connection,
    issues: &mut Vec<StartupRecoveryIssue>,
) -> Vec<u64> {
    let mut statement = match connection.prepare(
        "SELECT version FROM hypermid_migration_journal \
         WHERE state<>'finished' OR finished_at_ms IS NULL ORDER BY version",
    ) {
        Ok(statement) => statement,
        Err(error) => {
            issues.push(issue(
                "MIGRATION_JOURNAL_UNAVAILABLE",
                format!("migration journal could not be inspected: {error}"),
            ));
            return Vec::new();
        }
    };
    let rows = statement.query_map([], |row| row.get::<_, u64>(0));
    match rows {
        Ok(rows) => rows
            .filter_map(|row| match row {
                Ok(version) => Some(version),
                Err(error) => {
                    issues.push(issue(
                        "MIGRATION_JOURNAL_INVALID",
                        format!("migration journal row is invalid: {error}"),
                    ));
                    None
                }
            })
            .collect(),
        Err(error) => {
            issues.push(issue(
                "MIGRATION_JOURNAL_UNAVAILABLE",
                format!("migration journal could not be read: {error}"),
            ));
            Vec::new()
        }
    }
}

fn inspect_memories(
    connection: &Connection,
    issues: &mut Vec<StartupRecoveryIssue>,
) -> Vec<MemoryRecoveryReport> {
    let version = connection.query_row(
        "SELECT current_version FROM hypermid_schema_version WHERE singleton=1",
        [],
        |row| row.get::<_, u64>(0),
    );
    match version {
        Ok(version) if version > MEMORY_SCHEMA_VERSION => {
            issues.push(issue(
                "MEMORY_SCHEMA_FUTURE",
                format!(
                    "memory schema version {version} is newer than supported version {MEMORY_SCHEMA_VERSION}"
                ),
            ));
            return Vec::new();
        }
        Ok(_) => {}
        Err(error) => {
            issues.push(issue(
                "MEMORY_SCHEMA_UNAVAILABLE",
                format!("memory schema evidence could not be read: {error}"),
            ));
            return Vec::new();
        }
    }

    let mut statement =
        match connection.prepare("SELECT scope_json FROM memory_scopes ORDER BY scope_digest") {
            Ok(statement) => statement,
            Err(error) => {
                issues.push(issue(
                    "MEMORY_SCOPES_UNAVAILABLE",
                    format!("memory scopes could not be inspected: {error}"),
                ));
                return Vec::new();
            }
        };
    let scopes = match statement.query_map([], |row| row.get::<_, String>(0)) {
        Ok(rows) => rows
            .filter_map(|row| match row {
                Ok(raw) => match serde_json::from_str::<Scope>(&raw) {
                    Ok(scope) => Some(scope),
                    Err(error) => {
                        issues.push(issue(
                            "MEMORY_SCOPE_INVALID",
                            format!("memory scope document is invalid: {error}"),
                        ));
                        None
                    }
                },
                Err(error) => {
                    issues.push(issue(
                        "MEMORY_SCOPE_INVALID",
                        format!("memory scope row is invalid: {error}"),
                    ));
                    None
                }
            })
            .collect::<Vec<_>>(),
        Err(error) => {
            issues.push(issue(
                "MEMORY_SCOPES_UNAVAILABLE",
                format!("memory scopes could not be read: {error}"),
            ));
            return Vec::new();
        }
    };
    scopes
        .into_iter()
        .filter_map(|scope| match inspect_store(connection, &scope) {
            Ok(report) => Some(report),
            Err(error) => {
                issues.push(issue(error.code.as_str(), error.message));
                None
            }
        })
        .collect()
}

fn inspect_outbox(path: PathBuf, issues: &mut Vec<StartupRecoveryIssue>) -> Option<Cursor> {
    match DurableEventBus::open(path, 1, 10_000, 5).and_then(|bus| bus.head_cursor()) {
        Ok(cursor) => Some(cursor),
        Err(error) => {
            issues.push(issue(
                "OUTBOX_RECOVERY_FAILED",
                format!("durable event outbox could not be recovered: {error}"),
            ));
            None
        }
    }
}

fn reconcile_effects(
    path: PathBuf,
    now_ms: u64,
    issues: &mut Vec<StartupRecoveryIssue>,
) -> Vec<StartupUnknownEffect> {
    let ledger = match DurableEffectLedger::open(path, now_ms) {
        Ok(ledger) => ledger,
        Err(error) => {
            issues.push(issue(
                "EFFECT_RECOVERY_FAILED",
                format!("durable effect ledger could not be recovered: {error}"),
            ));
            return Vec::new();
        }
    };
    ledger
        .unsettled()
        .into_iter()
        .filter_map(|record| {
            let EffectStatus::Unknown { reason, settled_ms } = record.status else {
                return None;
            };
            Some(StartupUnknownEffect {
                effect_id: record.intent.effect_id,
                module_id: record.intent.module_id,
                operation: record.intent.operation,
                scope: record.intent.scope,
                input_digest: record.intent.input_digest,
                created_ms: record.intent.created_ms,
                reason,
                settled_ms,
            })
        })
        .collect()
}

fn issue(code: impl Into<String>, message: impl Into<String>) -> StartupRecoveryIssue {
    StartupRecoveryIssue {
        code: code.into(),
        message: message.into(),
    }
}

fn load_manifest(
    artifact_path: &Path,
    expected_digest: Digest,
) -> Result<SnapshotManifest, RecoveryAuthorityError> {
    let bytes = fs::read(artifact_path.join("manifest.json"))?;
    if Digest::sha256(&bytes) != expected_digest {
        return Err(RecoveryAuthorityError::InvalidIntent(
            "pending restore manifest digest changed".into(),
        ));
    }
    serde_json::from_slice(&bytes).map_err(RecoveryAuthorityError::from)
}

fn active_matches_manifest(active_path: &Path, manifest: &SnapshotManifest) -> bool {
    let Ok(connection) = Connection::open_with_flags(
        active_path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    ) else {
        return false;
    };
    inspect_store(&connection, &manifest.scope).is_ok_and(|report| {
        report.cursor == manifest.cursor && report.authoritative_digest == manifest.source_digest
    })
}

fn file_digest(path: &Path) -> Result<Digest, std::io::Error> {
    let mut file = File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }
    Ok(Digest::from_bytes(hasher.finalize().into()))
}

fn write_atomic(path: &Path, bytes: &[u8]) -> Result<(), std::io::Error> {
    let parent = path
        .parent()
        .ok_or_else(|| std::io::Error::other("recovery intent has no parent"))?;
    let temporary = parent.join(format!(".pending-{}", Digest::sha256(bytes).to_hex()));
    if temporary.exists() {
        fs::remove_file(&temporary)?;
    }
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(&temporary)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    fs::rename(&temporary, path)?;
    sync_directory(parent)
}

fn private_directory(path: &Path) -> Result<(), std::io::Error> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o700))?;
    }
    Ok(())
}

fn sync_directory(path: &Path) -> Result<(), std::io::Error> {
    #[cfg(unix)]
    {
        File::open(path)?.sync_all()?;
    }
    Ok(())
}
