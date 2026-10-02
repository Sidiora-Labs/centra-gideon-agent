-- Hypermid memory schema design artifact. Do not execute this file directly.
-- Schema version 1. Runtime migrations must carry equivalent statements through
-- crates/hypermid-memory and must update both current_version and compatibility_floor.
--
-- Transactional invariants:
-- 1. A mutation begins IMMEDIATE, proves owner-or-exact-grant authorization for
--    its actor scope, checks expected revision, and commits the revision,
--    provenance/lineage, invalidations, mutation event, and scope cursor together.
-- 2. Maintenance has no privileged write path. It performs the same authorization
--    check at claim and publish; grant revocation between those steps blocks publish.
-- 3. Provider/repository calls and filesystem scans occur outside write transactions.
-- 4. Embeddings and indexes publish only while their input digest and registration
--    fingerprint match authoritative rows.
-- 5. Tombstones are authoritative. Purge is separate, retention-gated, and audited.

PRAGMA foreign_keys = ON;

CREATE TABLE hypermid_schema_version (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    current_version INTEGER NOT NULL CHECK (current_version >= 1),
    compatibility_floor INTEGER NOT NULL CHECK (compatibility_floor >= 1),
    schema_digest TEXT NOT NULL CHECK (length(schema_digest) = 64),
    updated_at_ms INTEGER NOT NULL CHECK (updated_at_ms >= 0),
    CHECK (compatibility_floor <= current_version)
) STRICT;

CREATE TABLE hypermid_migration_journal (
    version INTEGER PRIMARY KEY CHECK (version >= 1),
    migration_digest TEXT NOT NULL CHECK (length(migration_digest) = 64),
    started_at_ms INTEGER NOT NULL CHECK (started_at_ms >= 0),
    finished_at_ms INTEGER CHECK (finished_at_ms IS NULL OR finished_at_ms >= started_at_ms),
    backup_manifest_digest TEXT CHECK (backup_manifest_digest IS NULL OR length(backup_manifest_digest) = 64),
    state TEXT NOT NULL CHECK (state IN ('started', 'finished', 'failed')),
    error_code TEXT
) STRICT;

-- A scope_digest is the canonical digest of the shared Scope document. The
-- decomposed shared identity fields are intentionally not duplicated here.
CREATE TABLE memory_scopes (
    scope_digest TEXT PRIMARY KEY CHECK (length(scope_digest) = 64),
    scope_json TEXT NOT NULL CHECK (json_valid(scope_json)),
    epoch INTEGER NOT NULL DEFAULT 1 CHECK (epoch >= 1),
    sequence INTEGER NOT NULL DEFAULT 0 CHECK (sequence >= 0),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
    updated_at_ms INTEGER NOT NULL CHECK (updated_at_ms >= created_at_ms)
) STRICT;

CREATE TABLE memory_share_grants (
    grant_id TEXT PRIMARY KEY,
    owner_scope_digest TEXT NOT NULL REFERENCES memory_scopes(scope_digest) ON DELETE CASCADE,
    grantee_scope_digest TEXT NOT NULL REFERENCES memory_scopes(scope_digest) ON DELETE CASCADE,
    operations_json TEXT NOT NULL CHECK (json_valid(operations_json) AND json_type(operations_json) = 'array'),
    categories_json TEXT CHECK (categories_json IS NULL OR (json_valid(categories_json) AND json_type(categories_json) = 'array')),
    granted_at_ms INTEGER NOT NULL CHECK (granted_at_ms >= 0),
    expires_at_ms INTEGER CHECK (expires_at_ms IS NULL OR expires_at_ms > granted_at_ms),
    revoked_at_ms INTEGER CHECK (revoked_at_ms IS NULL OR revoked_at_ms >= granted_at_ms),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    UNIQUE (owner_scope_digest, grantee_scope_digest, grant_id)
) STRICT;

CREATE INDEX idx_memory_share_grants_grantee
    ON memory_share_grants(grantee_scope_digest, owner_scope_digest, revoked_at_ms, expires_at_ms);

CREATE TABLE memory_records (
    record_id TEXT PRIMARY KEY,
    owner_scope_digest TEXT NOT NULL REFERENCES memory_scopes(scope_digest) ON DELETE RESTRICT,
    kind TEXT NOT NULL CHECK (kind IN ('fact', 'episode', 'note', 'smart_note', 'anchor', 'summary')),
    category TEXT NOT NULL CHECK (length(category) BETWEEN 1 AND 128),
    status TEXT NOT NULL CHECK (status IN ('active', 'archived', 'stale', 'tombstoned')),
    current_revision INTEGER NOT NULL CHECK (current_revision >= 1),
    current_revision_digest TEXT NOT NULL CHECK (length(current_revision_digest) = 64),
    normalized_content_digest TEXT NOT NULL CHECK (length(normalized_content_digest) = 64),
    importance REAL NOT NULL DEFAULT 0.5 CHECK (importance BETWEEN 0.0 AND 1.0),
    confidence REAL NOT NULL DEFAULT 0.5 CHECK (confidence BETWEEN 0.0 AND 1.0),
    verification_state TEXT NOT NULL DEFAULT 'unverified'
        CHECK (verification_state IN ('unverified', 'supported', 'disputed', 'refuted', 'unknown')),
    valid_from_ms INTEGER,
    valid_to_ms INTEGER,
    observed_from_ms INTEGER,
    observed_to_ms INTEGER,
    expires_at_ms INTEGER,
    retention_until_ms INTEGER,
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
    updated_at_ms INTEGER NOT NULL CHECK (updated_at_ms >= created_at_ms),
    deleted_at_ms INTEGER,
    CHECK (valid_to_ms IS NULL OR valid_from_ms IS NULL OR valid_to_ms >= valid_from_ms),
    CHECK (observed_to_ms IS NULL OR observed_from_ms IS NULL OR observed_to_ms >= observed_from_ms),
    CHECK (kind <> 'anchor' OR expires_at_ms IS NULL),
    UNIQUE (owner_scope_digest, record_id)
) STRICT;

CREATE UNIQUE INDEX uq_memory_active_exact_content
    ON memory_records(owner_scope_digest, kind, category, normalized_content_digest)
    WHERE status IN ('active', 'stale');
CREATE INDEX idx_memory_records_scope_state
    ON memory_records(owner_scope_digest, status, kind, category, updated_at_ms DESC);
CREATE INDEX idx_memory_records_expiry
    ON memory_records(owner_scope_digest, expires_at_ms)
    WHERE expires_at_ms IS NOT NULL AND status = 'active';

CREATE TABLE memory_revisions (
    record_id TEXT NOT NULL REFERENCES memory_records(record_id) ON DELETE RESTRICT,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    revision_digest TEXT NOT NULL CHECK (length(revision_digest) = 64),
    parent_revision_digest TEXT CHECK (parent_revision_digest IS NULL OR length(parent_revision_digest) = 64),
    content TEXT NOT NULL CHECK (length(content) > 0),
    content_digest TEXT NOT NULL CHECK (length(content_digest) = 64),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata_json) AND json_type(metadata_json) = 'object'),
    smart_predicate_json TEXT CHECK (smart_predicate_json IS NULL OR (json_valid(smart_predicate_json) AND json_type(smart_predicate_json) = 'object')),
    author_scope_digest TEXT NOT NULL REFERENCES memory_scopes(scope_digest) ON DELETE RESTRICT,
    authored_at_ms INTEGER NOT NULL CHECK (authored_at_ms >= 0),
    immutable_anchor INTEGER NOT NULL DEFAULT 0 CHECK (immutable_anchor IN (0, 1)),
    PRIMARY KEY (record_id, revision),
    UNIQUE (revision_digest),
    CHECK (revision = 1 OR parent_revision_digest IS NOT NULL)
) STRICT;

CREATE TRIGGER trg_memory_anchor_revision_immutable
BEFORE INSERT ON memory_revisions
WHEN EXISTS (SELECT 1 FROM memory_records r WHERE r.record_id = NEW.record_id AND r.kind = 'anchor')
     AND NEW.revision > 1
BEGIN
    SELECT RAISE(ABORT, 'anchor content is immutable; create a superseding anchor');
END;

CREATE TABLE episode_details (
    record_id TEXT PRIMARY KEY REFERENCES memory_records(record_id) ON DELETE CASCADE,
    observed_from_ms INTEGER NOT NULL CHECK (observed_from_ms >= 0),
    observed_to_ms INTEGER NOT NULL CHECK (observed_to_ms >= observed_from_ms),
    participants_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(participants_json) AND json_type(participants_json) = 'array'),
    episode_type TEXT NOT NULL CHECK (length(episode_type) BETWEEN 1 AND 128)
) STRICT;

CREATE TABLE smart_note_details (
    record_id TEXT PRIMARY KEY REFERENCES memory_records(record_id) ON DELETE CASCADE,
    predicate_json TEXT NOT NULL CHECK (json_valid(predicate_json) AND json_type(predicate_json) = 'object'),
    predicate_digest TEXT NOT NULL CHECK (length(predicate_digest) = 64),
    last_evaluated_cursor_json TEXT CHECK (last_evaluated_cursor_json IS NULL OR json_valid(last_evaluated_cursor_json)),
    last_result INTEGER CHECK (last_result IS NULL OR last_result IN (0, 1)),
    next_evaluation_at_ms INTEGER
) STRICT;

CREATE TABLE summary_details (
    record_id TEXT PRIMARY KEY REFERENCES memory_records(record_id) ON DELETE CASCADE,
    input_set_digest TEXT NOT NULL CHECK (length(input_set_digest) = 64),
    summary_level TEXT NOT NULL CHECK (summary_level IN ('brief', 'standard', 'detailed', 'exhaustive')),
    decay_half_life_ms INTEGER CHECK (decay_half_life_ms IS NULL OR decay_half_life_ms > 0),
    refreshed_at_ms INTEGER NOT NULL CHECK (refreshed_at_ms >= 0),
    stale_at_ms INTEGER
) STRICT;

CREATE TABLE memory_retrieval_stats (
    record_id TEXT PRIMARY KEY REFERENCES memory_records(record_id) ON DELETE CASCADE,
    explicit_retrieval_count INTEGER NOT NULL DEFAULT 0 CHECK (explicit_retrieval_count >= 0),
    last_explicit_retrieval_at_ms INTEGER,
    useful_count INTEGER NOT NULL DEFAULT 0 CHECK (useful_count >= 0),
    not_useful_count INTEGER NOT NULL DEFAULT 0 CHECK (not_useful_count >= 0),
    updated_at_ms INTEGER NOT NULL CHECK (updated_at_ms >= 0)
) STRICT;

CREATE TABLE memory_sources (
    source_id TEXT PRIMARY KEY,
    owner_scope_digest TEXT NOT NULL REFERENCES memory_scopes(scope_digest) ON DELETE RESTRICT,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('memory', 'message', 'file', 'git_commit', 'external')),
    source_digest TEXT NOT NULL CHECK (length(source_digest) = 64),
    locator TEXT,
    captured_content TEXT,
    capture_method TEXT NOT NULL,
    observed_at_ms INTEGER NOT NULL CHECK (observed_at_ms >= 0),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
    UNIQUE (owner_scope_digest, source_kind, source_digest, locator)
) STRICT;

CREATE TABLE memory_provenance (
    record_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    source_id TEXT NOT NULL REFERENCES memory_sources(source_id) ON DELETE RESTRICT,
    span_start INTEGER CHECK (span_start IS NULL OR span_start >= 0),
    span_end INTEGER CHECK (span_end IS NULL OR span_end >= 0),
    quoted_digest TEXT CHECK (quoted_digest IS NULL OR length(quoted_digest) = 64),
    PRIMARY KEY (record_id, revision, source_id, span_start),
    FOREIGN KEY (record_id, revision) REFERENCES memory_revisions(record_id, revision) ON DELETE CASCADE,
    CHECK (span_end IS NULL OR span_start IS NULL OR span_end >= span_start)
) STRICT;

CREATE TABLE memory_lineage (
    child_record_id TEXT NOT NULL REFERENCES memory_records(record_id) ON DELETE RESTRICT,
    child_revision INTEGER NOT NULL,
    parent_record_id TEXT NOT NULL REFERENCES memory_records(record_id) ON DELETE RESTRICT,
    parent_revision_digest TEXT NOT NULL CHECK (length(parent_revision_digest) = 64),
    relation TEXT NOT NULL CHECK (relation IN ('derived_from', 'cites', 'supersedes', 'contradicts', 'merged_from', 'split_from', 'imported_from', 'verifies')),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
    PRIMARY KEY (child_record_id, child_revision, parent_record_id, relation),
    FOREIGN KEY (child_record_id, child_revision) REFERENCES memory_revisions(record_id, revision) ON DELETE CASCADE,
    CHECK (child_record_id <> parent_record_id OR relation IN ('cites', 'verifies'))
) STRICT;

CREATE INDEX idx_memory_lineage_parent ON memory_lineage(parent_record_id, relation);

CREATE TABLE memory_verification_events (
    event_id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL REFERENCES memory_records(record_id) ON DELETE RESTRICT,
    revision_digest TEXT NOT NULL CHECK (length(revision_digest) = 64),
    actor_scope_digest TEXT NOT NULL REFERENCES memory_scopes(scope_digest) ON DELETE RESTRICT,
    state TEXT NOT NULL CHECK (state IN ('unverified', 'supported', 'disputed', 'refuted', 'unknown')),
    evidence_source_id TEXT REFERENCES memory_sources(source_id) ON DELETE RESTRICT,
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0.0 AND 1.0),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0)
) STRICT;

CREATE INDEX idx_memory_verification_record
    ON memory_verification_events(record_id, created_at_ms DESC);

CREATE TABLE memory_mutation_events (
    event_id TEXT PRIMARY KEY,
    owner_scope_digest TEXT NOT NULL REFERENCES memory_scopes(scope_digest) ON DELETE RESTRICT,
    epoch INTEGER NOT NULL CHECK (epoch >= 1),
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    operation TEXT NOT NULL CHECK (operation IN ('create', 'update', 'archive', 'restore', 'merge', 'split', 'relocate', 'delete', 'purge', 'verify', 'embed', 'index', 'summarize', 'import', 'export')),
    record_id TEXT,
    previous_revision_digest TEXT CHECK (previous_revision_digest IS NULL OR length(previous_revision_digest) = 64),
    result_revision_digest TEXT CHECK (result_revision_digest IS NULL OR length(result_revision_digest) = 64),
    actor_scope_digest TEXT NOT NULL REFERENCES memory_scopes(scope_digest) ON DELETE RESTRICT,
    grant_id TEXT REFERENCES memory_share_grants(grant_id) ON DELETE RESTRICT,
    authorization_basis TEXT NOT NULL CHECK (authorization_basis IN ('owner', 'grant')),
    trace_json TEXT NOT NULL CHECK (json_valid(trace_json)),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
    UNIQUE (owner_scope_digest, epoch, sequence),
    CHECK ((authorization_basis = 'owner' AND grant_id IS NULL AND actor_scope_digest = owner_scope_digest)
        OR (authorization_basis = 'grant' AND grant_id IS NOT NULL))
) STRICT;

CREATE INDEX idx_memory_mutation_record ON memory_mutation_events(record_id, created_at_ms DESC);

CREATE TABLE embedding_registrations (
    registration_id TEXT PRIMARY KEY,
    owner_scope_digest TEXT NOT NULL REFERENCES memory_scopes(scope_digest) ON DELETE CASCADE,
    mode TEXT NOT NULL CHECK (mode IN ('off', 'local', 'remote-compatible', 'managed-service')),
    provider_identity TEXT NOT NULL,
    model_id TEXT NOT NULL,
    dimensions INTEGER NOT NULL CHECK (dimensions > 0),
    metric TEXT NOT NULL CHECK (metric = 'cosine'),
    normalized INTEGER NOT NULL CHECK (normalized IN (0, 1)),
    fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 64),
    state TEXT NOT NULL CHECK (state IN ('active', 'retired', 'failed')),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
    retired_at_ms INTEGER,
    UNIQUE (owner_scope_digest, fingerprint)
) STRICT;

CREATE UNIQUE INDEX uq_embedding_active_scope
    ON embedding_registrations(owner_scope_digest)
    WHERE state = 'active';

CREATE TABLE memory_embeddings (
    record_id TEXT NOT NULL REFERENCES memory_records(record_id) ON DELETE CASCADE,
    revision_digest TEXT NOT NULL CHECK (length(revision_digest) = 64),
    registration_id TEXT NOT NULL REFERENCES embedding_registrations(registration_id) ON DELETE RESTRICT,
    vector_f32 BLOB NOT NULL,
    dimensions INTEGER NOT NULL CHECK (dimensions > 0),
    norm REAL NOT NULL CHECK (norm > 0.0),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
    PRIMARY KEY (record_id, registration_id),
    CHECK (length(vector_f32) = dimensions * 4)
) STRICT;

CREATE TABLE source_index_documents (
    document_id TEXT PRIMARY KEY,
    owner_scope_digest TEXT NOT NULL REFERENCES memory_scopes(scope_digest) ON DELETE CASCADE,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('message', 'file', 'git_commit')),
    source_key TEXT NOT NULL,
    content TEXT NOT NULL,
    content_digest TEXT NOT NULL CHECK (length(content_digest) = 64),
    source_time_ms INTEGER,
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata_json)),
    indexed_at_ms INTEGER NOT NULL CHECK (indexed_at_ms >= 0),
    tombstoned_at_ms INTEGER,
    UNIQUE (owner_scope_digest, source_kind, source_key)
) STRICT;

CREATE VIRTUAL TABLE memory_fts USING fts5(
    record_id UNINDEXED,
    owner_scope_digest UNINDEXED,
    category,
    content,
    content=''
);

CREATE TABLE memory_fts_rows (
    rowid INTEGER PRIMARY KEY,
    record_id TEXT NOT NULL UNIQUE REFERENCES memory_records(record_id) ON DELETE CASCADE,
    revision_digest TEXT NOT NULL CHECK (length(revision_digest) = 64)
) STRICT;

CREATE VIRTUAL TABLE source_fts USING fts5(
    document_id UNINDEXED,
    owner_scope_digest UNINDEXED,
    source_kind UNINDEXED,
    content,
    content=''
);

CREATE TABLE source_fts_rows (
    rowid INTEGER PRIMARY KEY,
    document_id TEXT NOT NULL UNIQUE REFERENCES source_index_documents(document_id) ON DELETE CASCADE,
    content_digest TEXT NOT NULL CHECK (length(content_digest) = 64)
) STRICT;

CREATE TABLE source_index_state (
    owner_scope_digest TEXT NOT NULL REFERENCES memory_scopes(scope_digest) ON DELETE CASCADE,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('message', 'file', 'git_commit')),
    cursor_json TEXT CHECK (cursor_json IS NULL OR json_valid(cursor_json)),
    dirty_floor_sequence INTEGER CHECK (dirty_floor_sequence IS NULL OR dirty_floor_sequence >= 0),
    repository_identity_digest TEXT CHECK (repository_identity_digest IS NULL OR length(repository_identity_digest) = 64),
    refs_digest TEXT CHECK (refs_digest IS NULL OR length(refs_digest) = 64),
    next_probe_at_ms INTEGER,
    updated_at_ms INTEGER NOT NULL CHECK (updated_at_ms >= 0),
    PRIMARY KEY (owner_scope_digest, source_kind)
) STRICT;

CREATE TABLE file_predicate_decisions (
    decision_id TEXT PRIMARY KEY,
    owner_scope_digest TEXT NOT NULL REFERENCES memory_scopes(scope_digest) ON DELETE CASCADE,
    canonical_path_digest TEXT NOT NULL CHECK (length(canonical_path_digest) = 64),
    root_digest TEXT NOT NULL CHECK (length(root_digest) = 64),
    content_digest TEXT CHECK (content_digest IS NULL OR length(content_digest) = 64),
    decision TEXT NOT NULL CHECK (decision IN ('allow', 'deny')),
    reason TEXT NOT NULL CHECK (reason IN ('inside_root', 'outside_root', 'symlink_escape', 'ignored', 'binary', 'too_large', 'generated', 'vendor', 'secret_policy', 'changed_during_read')),
    policy_digest TEXT NOT NULL CHECK (length(policy_digest) = 64),
    decided_at_ms INTEGER NOT NULL CHECK (decided_at_ms >= 0)
) STRICT;

CREATE TABLE maintenance_jobs (
    job_id TEXT PRIMARY KEY,
    owner_scope_digest TEXT NOT NULL REFERENCES memory_scopes(scope_digest) ON DELETE CASCADE,
    actor_scope_digest TEXT NOT NULL REFERENCES memory_scopes(scope_digest) ON DELETE RESTRICT,
    required_operation TEXT NOT NULL CHECK (required_operation IN ('create', 'update', 'archive', 'restore', 'merge', 'split', 'relocate', 'delete', 'purge', 'verify', 'embed', 'index', 'summarize', 'import', 'export')),
    claimed_grant_id TEXT REFERENCES memory_share_grants(grant_id) ON DELETE RESTRICT,
    kind TEXT NOT NULL CHECK (kind IN ('extract_facts', 'extract_episodes', 'verify_claims', 'evaluate_smart_notes', 'refresh_summaries', 'decay_summaries', 'embed_records', 'reembed_model', 'reconcile_fts', 'reconcile_sources', 'index_messages', 'index_git_commits', 'invalidate_lineage', 'sweep_orphans', 'compact_events', 'check_integrity', 'import_batch', 'export_batch', 'purge_tombstones')),
    state TEXT NOT NULL CHECK (state IN ('queued', 'claimed', 'running', 'checkpointed', 'succeeded', 'failed', 'abandoned')),
    input_cursor_json TEXT NOT NULL CHECK (json_valid(input_cursor_json)),
    checkpoint_cursor_json TEXT CHECK (checkpoint_cursor_json IS NULL OR json_valid(checkpoint_cursor_json)),
    input_digest TEXT NOT NULL CHECK (length(input_digest) = 64),
    config_digest TEXT NOT NULL CHECK (length(config_digest) = 64),
    budget_json TEXT NOT NULL CHECK (json_valid(budget_json)),
    usage_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(usage_json)),
    attempt INTEGER NOT NULL DEFAULT 1 CHECK (attempt >= 1),
    available_at_ms INTEGER NOT NULL CHECK (available_at_ms >= 0),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
    finished_at_ms INTEGER,
    last_error_code TEXT
) STRICT;

CREATE INDEX idx_maintenance_jobs_ready
    ON maintenance_jobs(state, available_at_ms, owner_scope_digest);

CREATE TABLE embedding_jobs (
    job_id TEXT PRIMARY KEY REFERENCES maintenance_jobs(job_id) ON DELETE CASCADE,
    registration_id TEXT NOT NULL REFERENCES embedding_registrations(registration_id) ON DELETE RESTRICT,
    phase TEXT NOT NULL CHECK (phase IN ('missing', 'stale', 'complete', 'cooldown')),
    checkpoint_record_id TEXT,
    attempted_count INTEGER NOT NULL DEFAULT 0 CHECK (attempted_count >= 0),
    accepted_count INTEGER NOT NULL DEFAULT 0 CHECK (accepted_count >= 0),
    rejected_count INTEGER NOT NULL DEFAULT 0 CHECK (rejected_count >= 0),
    cooldown_until_ms INTEGER,
    CHECK (accepted_count + rejected_count <= attempted_count)
) STRICT;

CREATE TABLE maintenance_leases (
    owner_scope_digest TEXT NOT NULL REFERENCES memory_scopes(scope_digest) ON DELETE CASCADE,
    lock_family TEXT NOT NULL,
    job_id TEXT NOT NULL REFERENCES maintenance_jobs(job_id) ON DELETE CASCADE,
    fencing_token_digest TEXT NOT NULL CHECK (length(fencing_token_digest) = 64),
    holder_id TEXT NOT NULL,
    acquired_at_ms INTEGER NOT NULL CHECK (acquired_at_ms >= 0),
    heartbeat_at_ms INTEGER NOT NULL CHECK (heartbeat_at_ms >= acquired_at_ms),
    expires_at_ms INTEGER NOT NULL CHECK (expires_at_ms > heartbeat_at_ms),
    PRIMARY KEY (owner_scope_digest, lock_family)
) STRICT;

CREATE INDEX idx_maintenance_leases_expiry ON maintenance_leases(expires_at_ms);

CREATE TABLE import_batches (
    batch_id TEXT PRIMARY KEY,
    source_digest TEXT NOT NULL CHECK (length(source_digest) = 64),
    target_scope_digest TEXT NOT NULL REFERENCES memory_scopes(scope_digest) ON DELETE RESTRICT,
    actor_scope_digest TEXT NOT NULL REFERENCES memory_scopes(scope_digest) ON DELETE RESTRICT,
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    manifest_json TEXT NOT NULL CHECK (json_valid(manifest_json)),
    state TEXT NOT NULL CHECK (state IN ('staged', 'validated', 'applying', 'applied', 'rejected', 'failed')),
    item_count INTEGER NOT NULL CHECK (item_count >= 0),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
    applied_at_ms INTEGER,
    UNIQUE (target_scope_digest, source_digest)
) STRICT;

CREATE TABLE import_items (
    batch_id TEXT NOT NULL REFERENCES import_batches(batch_id) ON DELETE CASCADE,
    item_key TEXT NOT NULL,
    item_digest TEXT NOT NULL CHECK (length(item_digest) = 64),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    state TEXT NOT NULL CHECK (state IN ('staged', 'valid', 'duplicate', 'applied', 'rejected', 'failed')),
    error_code TEXT,
    applied_record_id TEXT REFERENCES memory_records(record_id) ON DELETE SET NULL,
    PRIMARY KEY (batch_id, item_key)
) STRICT;

CREATE TABLE export_manifests (
    export_id TEXT PRIMARY KEY,
    owner_scope_digest TEXT NOT NULL REFERENCES memory_scopes(scope_digest) ON DELETE RESTRICT,
    actor_scope_digest TEXT NOT NULL REFERENCES memory_scopes(scope_digest) ON DELETE RESTRICT,
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    cursor_json TEXT NOT NULL CHECK (json_valid(cursor_json)),
    stream_digest TEXT NOT NULL CHECK (length(stream_digest) = 64),
    record_count INTEGER NOT NULL CHECK (record_count >= 0),
    include_grants INTEGER NOT NULL DEFAULT 0 CHECK (include_grants IN (0, 1)),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0)
) STRICT;

CREATE TABLE recovery_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    store_digest TEXT NOT NULL CHECK (length(store_digest) = 64),
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    integrity_state TEXT NOT NULL CHECK (integrity_state IN ('verified', 'failed')),
    manifest_json TEXT NOT NULL CHECK (json_valid(manifest_json)),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0)
) STRICT;
