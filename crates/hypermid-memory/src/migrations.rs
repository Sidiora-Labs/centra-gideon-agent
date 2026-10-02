use hypermid_contracts::Digest;
use hypermid_store::authorization::AUTHORIZATION_SCHEMA_SQL;
use hypermid_store::Migration;

pub const MEMORY_SCHEMA_VERSION: u64 = 2;
pub const MEMORY_COMPATIBILITY_FLOOR: u64 = 1;

const MEMORY_SCHEMA_V1: u64 = 1;

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

const SHARING_JUDGMENTS_SCHEMA_SQL: &str = r#"
CREATE UNIQUE INDEX uq_memory_records_owner_record
    ON memory_records(owner_scope_digest, record_id);

CREATE TABLE memory_sharing_judgments (
    judgment_id TEXT PRIMARY KEY,
    owner_scope_digest TEXT NOT NULL,
    record_id TEXT NOT NULL,
    revision_digest TEXT NOT NULL CHECK (length(revision_digest) = 64),
    classification TEXT NOT NULL CHECK (classification IN ('private', 'shared')),
    trust_decision TEXT NOT NULL CHECK (trust_decision IN ('allow', 'deny')),
    policy_id TEXT NOT NULL CHECK (length(policy_id) BETWEEN 1 AND 128),
    policy_version INTEGER NOT NULL CHECK (policy_version >= 1),
    policy_digest TEXT NOT NULL CHECK (length(policy_digest) = 64),
    provider_id TEXT CHECK (provider_id IS NULL OR length(provider_id) BETWEEN 1 AND 256),
    model_id TEXT CHECK (model_id IS NULL OR length(model_id) BETWEEN 1 AND 256),
    evidence_digest TEXT NOT NULL CHECK (length(evidence_digest) = 64),
    trace_json TEXT NOT NULL CHECK (json_valid(trace_json) AND json_type(trace_json) = 'object'),
    decided_at_ms INTEGER NOT NULL CHECK (decided_at_ms >= 0),
    invalidated_at_ms INTEGER CHECK (invalidated_at_ms IS NULL OR invalidated_at_ms >= decided_at_ms),
    invalidation_reason TEXT CHECK (
        invalidation_reason IS NULL OR invalidation_reason IN (
            'content_edited', 'relocated', 'deleted', 'policy_changed', 'evaluator_retired'
        )
    ),
    FOREIGN KEY (owner_scope_digest, record_id)
        REFERENCES memory_records(owner_scope_digest, record_id)
        ON UPDATE CASCADE ON DELETE CASCADE,
    CHECK (
        (provider_id IS NULL AND model_id IS NULL)
        OR (provider_id IS NOT NULL AND model_id IS NOT NULL)
    ),
    CHECK (
        (invalidated_at_ms IS NULL AND invalidation_reason IS NULL)
        OR (invalidated_at_ms IS NOT NULL AND invalidation_reason IS NOT NULL)
    )
) STRICT;

CREATE UNIQUE INDEX uq_memory_sharing_judgments_live
    ON memory_sharing_judgments(
        owner_scope_digest, record_id, revision_digest, policy_digest
    )
    WHERE invalidated_at_ms IS NULL;

CREATE INDEX idx_memory_sharing_judgments_active
    ON memory_sharing_judgments(
        owner_scope_digest, classification, trust_decision, record_id, decided_at_ms DESC
    )
    WHERE invalidated_at_ms IS NULL;
"#;

fn v1_schema_digest() -> Digest {
    Digest::sha256(KNOWLEDGE_SCHEMA_SQL.as_bytes())
}

pub fn schema_digest() -> Digest {
    let mut schema = b"hypermid-memory-schema-v2\0".to_vec();
    schema.extend_from_slice(KNOWLEDGE_SCHEMA_SQL.as_bytes());
    schema.push(0);
    schema.extend_from_slice(SHARING_JUDGMENTS_SCHEMA_SQL.as_bytes());
    Digest::sha256(&schema)
}

pub fn memory_migrations() -> Vec<Migration> {
    let v1_digest = v1_schema_digest().to_hex();
    let v1_metadata = format!(
        "\nINSERT INTO hypermid_schema_version(\
             singleton, current_version, compatibility_floor, schema_digest, updated_at_ms\
         ) VALUES (1, {MEMORY_SCHEMA_V1}, {MEMORY_COMPATIBILITY_FLOOR}, '{v1_digest}', \
             CAST(strftime('%s', 'now') AS INTEGER) * 1000);\
         INSERT INTO hypermid_migration_journal(\
             version, migration_digest, started_at_ms, finished_at_ms, state\
         ) VALUES (\
             {MEMORY_SCHEMA_V1}, '{v1_digest}',\
             CAST(strftime('%s', 'now') AS INTEGER) * 1000,\
             CAST(strftime('%s', 'now') AS INTEGER) * 1000, 'finished'\
         );"
    );
    let current_digest = schema_digest().to_hex();
    let v2_metadata = format!(
        "\nUPDATE hypermid_schema_version SET \
             current_version={MEMORY_SCHEMA_VERSION},\
             compatibility_floor={MEMORY_COMPATIBILITY_FLOOR},\
             schema_digest='{current_digest}',\
             updated_at_ms=CAST(strftime('%s', 'now') AS INTEGER) * 1000 \
         WHERE singleton=1;\
         INSERT INTO hypermid_migration_journal(\
             version, migration_digest, started_at_ms, finished_at_ms, state\
         ) VALUES (\
             {MEMORY_SCHEMA_VERSION}, '{current_digest}',\
             CAST(strftime('%s', 'now') AS INTEGER) * 1000,\
             CAST(strftime('%s', 'now') AS INTEGER) * 1000, 'finished'\
         );"
    );
    vec![
        Migration::new(
            MEMORY_SCHEMA_V1,
            "hypermid_memory_v1",
            format!("{AUTHORIZATION_SCHEMA_SQL}{KNOWLEDGE_SCHEMA_SQL}{v1_metadata}"),
        ),
        Migration::new(
            MEMORY_SCHEMA_VERSION,
            "hypermid_memory_v2_sharing_judgments",
            format!("{SHARING_JUDGMENTS_SCHEMA_SQL}{v2_metadata}"),
        ),
    ]
}
