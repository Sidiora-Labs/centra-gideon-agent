use hypermid_contracts::Digest;
use hypermid_store::authorization::AUTHORIZATION_SCHEMA_SQL;
use hypermid_store::Migration;

pub const MEMORY_SCHEMA_VERSION: u64 = 1;
pub const MEMORY_COMPATIBILITY_FLOOR: u64 = 1;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MigrationFence {
    pub source_version: u64,
    pub target_version: u64,
    pub destructive: bool,
    pub verified_backup_manifest: Option<Digest>,
}

impl MigrationFence {
    pub fn validate(&self) -> crate::MemoryResult<()> {
        if self.source_version >= self.target_version {
            return Err(crate::error(
                "INVALID_MIGRATION",
                "memory migration target must be newer than its source",
                hypermid_contracts::EffectState::NotStarted,
            ));
        }
        if self.destructive && self.verified_backup_manifest.is_none() {
            return Err(crate::error(
                "MIGRATION_BACKUP_REQUIRED",
                "destructive memory migration requires a verified backup manifest",
                hypermid_contracts::EffectState::NotStarted,
            ));
        }
        Ok(())
    }
}

const KNOWLEDGE_SCHEMA_SQL: &str = include_str!("../../../spec/hypermid/schemas/knowledge.sql");

pub fn schema_digest() -> Digest {
    Digest::sha256(KNOWLEDGE_SCHEMA_SQL.as_bytes())
}

pub fn memory_migrations() -> Vec<Migration> {
    let digest = schema_digest().to_hex();
    let metadata = format!(
        "\nINSERT INTO hypermid_schema_version(\
             singleton, current_version, compatibility_floor, schema_digest, updated_at_ms\
         ) VALUES (1, {MEMORY_SCHEMA_VERSION}, {MEMORY_COMPATIBILITY_FLOOR}, '{digest}', \
             CAST(strftime('%s', 'now') AS INTEGER) * 1000);\
         INSERT INTO hypermid_migration_journal(\
             version, migration_digest, started_at_ms, finished_at_ms, state\
         ) VALUES (\
             {MEMORY_SCHEMA_VERSION}, '{digest}',\
             CAST(strftime('%s', 'now') AS INTEGER) * 1000,\
             CAST(strftime('%s', 'now') AS INTEGER) * 1000, 'finished'\
         );"
    );
    vec![Migration::new(
        MEMORY_SCHEMA_VERSION,
        "hypermid_memory_v1",
        format!("{AUTHORIZATION_SCHEMA_SQL}{KNOWLEDGE_SCHEMA_SQL}{metadata}"),
    )]
}
