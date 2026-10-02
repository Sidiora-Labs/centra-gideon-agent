use hypermid_contracts::{Cursor, Digest, EffectState, Id, Scope};
use rusqlite::{params, OptionalExtension, Row, Transaction};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use sha2::{Digest as _, Sha256};

use crate::invalidation::{invalidate_record_and_descendants, InvalidationReason};
use crate::lineage::{attach_edges, LineageEdge};
use crate::model::{
    scope_digest, Authorization, AuthorizationBasis, MutationRequest, Operation,
    RevisionPrecondition,
};
use crate::provenance::{
    attach_spans, error as domain_error, put_source, sql_error, ProvenanceSpan, SourceSnapshot,
};
use crate::smart_note::SmartPredicate;
use crate::summary::{put_summary_details, SummaryDetails};
use crate::{error, AuthContext, MemoryResult, MemoryStore, MemoryTransaction};

const MAX_CONTENT_BYTES: usize = 262_144;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RecordKind {
    Fact,
    Episode,
    Note,
    SmartNote,
    Anchor,
    Summary,
}

impl RecordKind {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Fact => "fact",
            Self::Episode => "episode",
            Self::Note => "note",
            Self::SmartNote => "smart_note",
            Self::Anchor => "anchor",
            Self::Summary => "summary",
        }
    }

    fn parse(value: &str) -> MemoryResult<Self> {
        match value {
            "fact" => Ok(Self::Fact),
            "episode" => Ok(Self::Episode),
            "note" => Ok(Self::Note),
            "smart_note" => Ok(Self::SmartNote),
            "anchor" => Ok(Self::Anchor),
            "summary" => Ok(Self::Summary),
            _ => Err(corrupt("record kind is invalid")),
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RecordStatus {
    Active,
    Archived,
    Stale,
    Tombstoned,
}

impl RecordStatus {
    const fn as_str(self) -> &'static str {
        match self {
            Self::Active => "active",
            Self::Archived => "archived",
            Self::Stale => "stale",
            Self::Tombstoned => "tombstoned",
        }
    }

    fn parse(value: &str) -> MemoryResult<Self> {
        match value {
            "active" => Ok(Self::Active),
            "archived" => Ok(Self::Archived),
            "stale" => Ok(Self::Stale),
            "tombstoned" => Ok(Self::Tombstoned),
            _ => Err(corrupt("record status is invalid")),
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum VerificationState {
    Unverified,
    Supported,
    Disputed,
    Refuted,
    Unknown,
}

impl VerificationState {
    fn parse(value: &str) -> MemoryResult<Self> {
        match value {
            "unverified" => Ok(Self::Unverified),
            "supported" => Ok(Self::Supported),
            "disputed" => Ok(Self::Disputed),
            "refuted" => Ok(Self::Refuted),
            "unknown" => Ok(Self::Unknown),
            _ => Err(corrupt("verification state is invalid")),
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RecordDraft {
    pub id: Id,
    pub scope: Scope,
    pub kind: RecordKind,
    pub category: String,
    pub content: String,
    #[serde(default)]
    pub metadata: Map<String, Value>,
    pub importance: f64,
    pub confidence: f64,
    pub expires_at_ms: Option<u64>,
    pub retention_until_ms: Option<u64>,
    #[serde(default)]
    pub provenance: Vec<ProvenanceSpan>,
    #[serde(default)]
    pub lineage: Vec<LineageEdge>,
    #[serde(default)]
    pub smart_predicate: Option<SmartPredicate>,
    pub summary: Option<SummaryDetails>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RecordRevision {
    pub number: u64,
    pub digest: Digest,
    pub parent_digest: Option<Digest>,
    pub content: String,
    pub content_digest: Digest,
    pub metadata: Map<String, Value>,
    pub author_scope_digest: Digest,
    pub authored_at_ms: u64,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct MemoryRecord {
    pub id: Id,
    pub owner_scope_digest: Digest,
    pub kind: RecordKind,
    pub category: String,
    pub status: RecordStatus,
    pub current: RecordRevision,
    pub normalized_content_digest: Digest,
    pub importance: f64,
    pub confidence: f64,
    pub verification: VerificationState,
    pub expires_at_ms: Option<u64>,
    pub retention_until_ms: Option<u64>,
    pub created_at_ms: u64,
    pub updated_at_ms: u64,
    pub deleted_at_ms: Option<u64>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct RecordMutation {
    pub record: MemoryRecord,
    pub cursor: Cursor,
    pub invalidated_ids: Vec<Id>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct SplitMutation {
    pub source: MemoryRecord,
    pub replacements: Vec<RecordMutation>,
    pub cursor: Cursor,
    pub invalidated_ids: Vec<Id>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct RelocationMutation {
    pub record: MemoryRecord,
    pub source_cursor: Cursor,
    pub destination_cursor: Cursor,
}

impl MemoryStore {
    pub fn create_record(
        &mut self,
        context: &AuthContext,
        request: &MutationRequest,
        draft: &RecordDraft,
        sources: &[SourceSnapshot],
        now_ms: u64,
    ) -> MemoryResult<RecordMutation> {
        require_auth_time(context, now_ms)?;
        require_request(request, Operation::Create, draft)?;
        if request.revision != RevisionPrecondition::MustNotExist {
            return Err(conflict("create requires a must-not-exist precondition"));
        }
        self.immediate_authorized(context, request, |transaction, authorization| {
            transaction.reauthorize(context, request, authorization)?;
            transaction.ensure_scope(&draft.scope, as_i64(now_ms)?)?;
            transaction.require_record_revision(&draft.id, &RevisionPrecondition::MustNotExist)?;
            create_in_transaction(transaction, request, authorization, draft, sources, now_ms)
        })
    }

    pub fn update_record(
        &mut self,
        context: &AuthContext,
        request: &MutationRequest,
        draft: &RecordDraft,
        sources: &[SourceSnapshot],
        now_ms: u64,
    ) -> MemoryResult<RecordMutation> {
        require_auth_time(context, now_ms)?;
        require_request(request, Operation::Update, draft)?;
        let RevisionPrecondition::Match(expected) = request.revision else {
            return Err(conflict("update requires an exact revision digest"));
        };
        self.immediate_authorized(context, request, |transaction, authorization| {
            transaction.reauthorize(context, request, authorization)?;
            transaction.require_record_revision(&draft.id, &request.revision)?;
            update_in_transaction(
                transaction,
                request,
                authorization,
                draft,
                sources,
                expected,
                now_ms,
            )
        })
    }

    pub fn archive_record(
        &mut self,
        context: &AuthContext,
        request: &MutationRequest,
        now_ms: u64,
    ) -> MemoryResult<RecordMutation> {
        require_auth_time(context, now_ms)?;
        self.set_record_status(
            context,
            request,
            Operation::Archive,
            RecordStatus::Archived,
            now_ms,
        )
    }

    pub fn restore_record(
        &mut self,
        context: &AuthContext,
        request: &MutationRequest,
        now_ms: u64,
    ) -> MemoryResult<RecordMutation> {
        require_auth_time(context, now_ms)?;
        self.set_record_status(
            context,
            request,
            Operation::Restore,
            RecordStatus::Active,
            now_ms,
        )
    }

    pub fn tombstone_record(
        &mut self,
        context: &AuthContext,
        request: &MutationRequest,
        now_ms: u64,
    ) -> MemoryResult<RecordMutation> {
        require_auth_time(context, now_ms)?;
        self.set_record_status(
            context,
            request,
            Operation::Delete,
            RecordStatus::Tombstoned,
            now_ms,
        )
    }

    pub fn purge_record(
        &mut self,
        context: &AuthContext,
        request: &MutationRequest,
        now_ms: u64,
    ) -> MemoryResult<Cursor> {
        require_auth_time(context, now_ms)?;
        require_record_request(request, Operation::Purge)?;
        self.immediate_authorized(context, request, |transaction, authorization| {
            transaction.reauthorize(context, request, authorization)?;
            if authorization.basis != AuthorizationBasis::Owner {
                return Err(denied("physical purge requires exact owner authority"));
            }
            let record_id = request.record_id.as_ref().expect("validated record id");
            transaction.require_record_revision(record_id, &request.revision)?;
            let state: Option<(String, Option<u64>, String, String, String)> = transaction
                .raw()
                .query_row(
                    "SELECT r.status, r.retention_until_ms, r.owner_scope_digest,
                            r.category, v.content
                     FROM memory_records r
                     JOIN memory_revisions v
                       ON v.record_id=r.record_id AND v.revision=r.current_revision
                     WHERE r.record_id=?1",
                    [record_id.as_str()],
                    |row| {
                        Ok((
                            row.get(0)?,
                            row.get(1)?,
                            row.get(2)?,
                            row.get(3)?,
                            row.get(4)?,
                        ))
                    },
                )
                .optional()
                .map_err(sql_error)?;
            let (status, retention_until_ms, scope, category, content) =
                state.ok_or_else(not_found)?;
            if status != "tombstoned" || retention_until_ms.is_none_or(|deadline| now_ms < deadline)
            {
                return Err(domain_error(
                    "RETENTION_NOT_ELAPSED",
                    "record is not tombstoned past its retention deadline",
                ));
            }
            let referenced: bool = transaction
                .raw()
                .query_row(
                    "SELECT EXISTS(SELECT 1 FROM memory_lineage WHERE parent_record_id=?1)",
                    [record_id.as_str()],
                    |row| row.get(0),
                )
                .map_err(sql_error)?;
            if referenced {
                return Err(domain_error(
                    "PURGE_REFERENCED",
                    "record remains referenced by lineage",
                ));
            }
            let source_ids = provenance_source_ids(transaction.raw(), record_id)?;
            crate::fts::remove_memory_record(
                transaction.raw(),
                record_id.as_str(),
                &scope,
                &category,
                &content,
            )?;
            transaction
                .raw()
                .execute(
                    "DELETE FROM memory_embeddings WHERE record_id=?1",
                    [record_id.as_str()],
                )
                .map_err(sql_error)?;
            transaction
                .raw()
                .execute(
                    "DELETE FROM memory_verification_events WHERE record_id=?1",
                    [record_id.as_str()],
                )
                .map_err(sql_error)?;
            transaction
                .raw()
                .execute(
                    "DELETE FROM memory_lineage WHERE child_record_id=?1",
                    [record_id.as_str()],
                )
                .map_err(sql_error)?;
            transaction
                .raw()
                .execute(
                    "DELETE FROM memory_revisions WHERE record_id=?1",
                    [record_id.as_str()],
                )
                .map_err(sql_error)?;
            transaction
                .raw()
                .execute(
                    "DELETE FROM memory_records WHERE record_id=?1",
                    [record_id.as_str()],
                )
                .map_err(sql_error)?;
            for source_id in source_ids {
                transaction
                    .raw()
                    .execute(
                        "DELETE FROM memory_sources WHERE source_id=?1
                         AND NOT EXISTS(SELECT 1 FROM memory_provenance WHERE source_id=?1)",
                        [source_id],
                    )
                    .map_err(sql_error)?;
            }
            let cursor = transaction.advance_cursor(&request.target_scope, as_i64(now_ms)?)?;
            insert_event(
                transaction,
                request,
                authorization,
                cursor,
                Some(record_id),
                expected_digest(request),
                None,
                now_ms,
            )?;
            Ok(cursor)
        })
    }

    pub fn verify_record(
        &mut self,
        context: &AuthContext,
        request: &MutationRequest,
        state: VerificationState,
        confidence: f64,
        evidence_source_id: Option<&Id>,
        now_ms: u64,
    ) -> MemoryResult<RecordMutation> {
        require_auth_time(context, now_ms)?;
        self.immediate_authorized(context, request, |transaction, authorization| {
            transaction.reauthorize(context, request, authorization)?;
            verify_record_in(
                transaction,
                authorization,
                request,
                state,
                confidence,
                evidence_source_id,
                now_ms,
            )
        })
    }

    pub fn merge_records(
        &mut self,
        context: &AuthContext,
        request: &MutationRequest,
        draft: &RecordDraft,
        source_revisions: &[(Id, Digest)],
        sources: &[SourceSnapshot],
        now_ms: u64,
    ) -> MemoryResult<RecordMutation> {
        require_auth_time(context, now_ms)?;
        require_request(request, Operation::Merge, draft)?;
        if request.revision != RevisionPrecondition::MustNotExist || source_revisions.len() < 2 {
            return Err(domain_error(
                "INVALID_MERGE",
                "merge requires a new result and at least two exact source revisions",
            ));
        }
        self.immediate_authorized(context, request, |transaction, authorization| {
            transaction.reauthorize(context, request, authorization)?;
            transaction.ensure_scope(&draft.scope, as_i64(now_ms)?)?;
            transaction.require_record_revision(&draft.id, &RevisionPrecondition::MustNotExist)?;
            let mut merged = draft.clone();
            for (source_id, expected) in source_revisions {
                let source = load_record(transaction.raw(), source_id)?;
                if source.owner_scope_digest != scope_digest(&draft.scope)
                    || source.current.digest != *expected
                    || source.status == RecordStatus::Tombstoned
                {
                    return Err(conflict("merge source revision precondition failed"));
                }
                merged.lineage.push(LineageEdge {
                    parent_id: source_id.clone(),
                    relation: crate::lineage::LineageRelation::MergedFrom,
                    parent_revision_digest: *expected,
                });
            }
            create_in_transaction(
                transaction,
                request,
                authorization,
                &merged,
                sources,
                now_ms,
            )
        })
    }

    pub fn split_record(
        &mut self,
        context: &AuthContext,
        request: &MutationRequest,
        replacements: &[RecordDraft],
        now_ms: u64,
    ) -> MemoryResult<SplitMutation> {
        require_auth_time(context, now_ms)?;
        require_record_request(request, Operation::Split)?;
        if replacements.len() < 2 {
            return Err(domain_error(
                "INVALID_SPLIT",
                "split requires at least two replacement records",
            ));
        }
        self.immediate_authorized(context, request, |transaction, authorization| {
            transaction.reauthorize(context, request, authorization)?;
            let source_id = request.record_id.as_ref().expect("validated record id");
            let source_digest = transaction
                .require_record_revision(source_id, &request.revision)?
                .expect("match precondition returns digest");
            let source = load_record(transaction.raw(), source_id)?;
            let mut created = Vec::with_capacity(replacements.len());
            for replacement in replacements {
                if replacement.scope != request.target_scope || replacement.id == *source_id {
                    return Err(domain_error(
                        "INVALID_SPLIT",
                        "split replacement scope or id is invalid",
                    ));
                }
                transaction.require_record_revision(
                    &replacement.id,
                    &RevisionPrecondition::MustNotExist,
                )?;
                let mut replacement = replacement.clone();
                replacement.lineage.push(LineageEdge {
                    parent_id: source_id.clone(),
                    relation: crate::lineage::LineageRelation::SplitFrom,
                    parent_revision_digest: source_digest,
                });
                created.push(create_in_transaction(
                    transaction,
                    request,
                    authorization,
                    &replacement,
                    &[],
                    now_ms,
                )?);
            }
            transaction
                .raw()
                .execute(
                    "UPDATE memory_records
                     SET status='tombstoned', deleted_at_ms=?2, updated_at_ms=?2
                     WHERE record_id=?1",
                    params![source_id.as_str(), now_ms],
                )
                .map_err(sql_error)?;
            let invalidated_ids = invalidate_record_and_descendants(
                transaction.raw(),
                source_id,
                now_ms,
                InvalidationReason::Deleted,
            )?;
            let cursor = transaction.advance_cursor(&request.target_scope, as_i64(now_ms)?)?;
            insert_event(
                transaction,
                request,
                authorization,
                cursor,
                Some(source_id),
                Some(source_digest),
                Some(source_digest),
                now_ms,
            )?;
            Ok(SplitMutation {
                source: MemoryRecord {
                    status: RecordStatus::Tombstoned,
                    updated_at_ms: now_ms,
                    deleted_at_ms: Some(now_ms),
                    ..source
                },
                replacements: created,
                cursor,
                invalidated_ids,
            })
        })
    }

    pub fn relocate_record(
        &mut self,
        source_context: &AuthContext,
        source_request: &MutationRequest,
        destination_context: &AuthContext,
        destination_request: &MutationRequest,
        now_ms: u64,
    ) -> MemoryResult<RelocationMutation> {
        require_auth_time(source_context, now_ms)?;
        require_auth_time(destination_context, now_ms)?;
        require_record_request(source_request, Operation::Relocate)?;
        require_record_request(destination_request, Operation::Relocate)?;
        if source_request.record_id != destination_request.record_id
            || source_request.actor_scope != destination_request.actor_scope
            || source_request.target_scope == destination_request.target_scope
            || source_request.revision != destination_request.revision
        {
            return Err(domain_error(
                "INVALID_RELOCATION",
                "relocation source and destination contracts do not align",
            ));
        }
        self.immediate(|transaction| {
            let source_authorization = transaction.authorize(source_context, source_request)?;
            let destination_authorization =
                transaction.authorize(destination_context, destination_request)?;
            transaction.reauthorize(source_context, source_request, &source_authorization)?;
            transaction.reauthorize(
                destination_context,
                destination_request,
                &destination_authorization,
            )?;
            transaction.ensure_scope(&destination_request.target_scope, as_i64(now_ms)?)?;
            let record_id = source_request
                .record_id
                .as_ref()
                .expect("validated record id");
            let revision = transaction
                .require_record_revision(record_id, &source_request.revision)?
                .expect("match precondition returns digest");
            let record = load_record(transaction.raw(), record_id)?;
            if record.owner_scope_digest != scope_digest(&source_request.target_scope) {
                return Err(conflict("record is no longer owned by the source scope"));
            }
            let duplicate: bool = transaction
                .raw()
                .query_row(
                    "SELECT EXISTS(
                        SELECT 1 FROM memory_records
                        WHERE owner_scope_digest=?1 AND kind=?2 AND category=?3
                          AND normalized_content_digest=?4
                          AND status IN ('active','stale') AND record_id<>?5
                     )",
                    params![
                        scope_digest(&destination_request.target_scope).to_string(),
                        record.kind.as_str(),
                        record.category,
                        record.normalized_content_digest.to_string(),
                        record_id.as_str()
                    ],
                    |row| row.get(0),
                )
                .map_err(sql_error)?;
            if duplicate {
                return Err(domain_error(
                    "DUPLICATE_MEMORY",
                    "destination already contains the same active memory",
                ));
            }
            transaction
                .raw()
                .execute(
                    "UPDATE memory_records SET owner_scope_digest=?2, updated_at_ms=?3
                     WHERE record_id=?1",
                    params![
                        record_id.as_str(),
                        scope_digest(&destination_request.target_scope).to_string(),
                        now_ms
                    ],
                )
                .map_err(sql_error)?;
            invalidate_record_and_descendants(
                transaction.raw(),
                record_id,
                now_ms,
                InvalidationReason::Relocated,
            )?;
            let source_cursor =
                transaction.advance_cursor(&source_request.target_scope, as_i64(now_ms)?)?;
            insert_event(
                transaction,
                source_request,
                &source_authorization,
                source_cursor,
                Some(record_id),
                Some(revision),
                Some(revision),
                now_ms,
            )?;
            let destination_cursor =
                transaction.advance_cursor(&destination_request.target_scope, as_i64(now_ms)?)?;
            insert_event(
                transaction,
                destination_request,
                &destination_authorization,
                destination_cursor,
                Some(record_id),
                Some(revision),
                Some(revision),
                now_ms,
            )?;
            Ok(RelocationMutation {
                record: load_record(transaction.raw(), record_id)?,
                source_cursor,
                destination_cursor,
            })
        })
    }

    pub(crate) fn record(&self, record_id: &Id) -> MemoryResult<Option<MemoryRecord>> {
        self.read(|connection| {
            connection
                .query_row(RECORD_SELECT, [record_id.as_str()], map_record)
                .optional()
                .map_err(sql_error)?
                .map_or(Ok(None), |row| row.map(Some))
        })
    }

    pub(crate) fn list_records(
        &self,
        scope: &Scope,
        category: Option<&str>,
        status: Option<RecordStatus>,
        limit: usize,
    ) -> MemoryResult<(Cursor, Vec<MemoryRecord>)> {
        if limit == 0 || limit > 1_000 {
            return Err(domain_error(
                "INVALID_ARGUMENT",
                "record list limit must be within 1..1000",
            ));
        }
        self.read(|connection| {
            let cursor = cursor_in(connection, scope)?;
            let records = list_records_on(connection, scope, category, status, limit)?;
            Ok((cursor, records))
        })
    }

    fn set_record_status(
        &mut self,
        context: &AuthContext,
        request: &MutationRequest,
        operation: Operation,
        status: RecordStatus,
        now_ms: u64,
    ) -> MemoryResult<RecordMutation> {
        require_record_request(request, operation)?;
        self.immediate_authorized(context, request, |transaction, authorization| {
            transaction.reauthorize(context, request, authorization)?;
            let record_id = request.record_id.as_ref().expect("validated record id");
            let previous = transaction
                .require_record_revision(record_id, &request.revision)?
                .expect("match precondition returns digest");
            let current_status: String = transaction
                .raw()
                .query_row(
                    "SELECT status FROM memory_records WHERE record_id=?1",
                    [record_id.as_str()],
                    |row| row.get(0),
                )
                .map_err(sql_error)?;
            let valid = match operation {
                Operation::Archive => matches!(current_status.as_str(), "active" | "stale"),
                Operation::Restore => current_status == "archived",
                Operation::Delete => current_status != "tombstoned",
                _ => false,
            };
            if !valid {
                return Err(domain_error(
                    "INVALID_RECORD_STATE",
                    "record status transition is not allowed",
                ));
            }
            transaction
                .raw()
                .execute(
                    "UPDATE memory_records
                     SET status=?2, updated_at_ms=?3,
                         deleted_at_ms=CASE WHEN ?2='tombstoned' THEN ?3 ELSE NULL END
                     WHERE record_id=?1",
                    params![record_id.as_str(), status.as_str(), now_ms],
                )
                .map_err(sql_error)?;
            let invalidated_ids = if status == RecordStatus::Tombstoned {
                invalidate_record_and_descendants(
                    transaction.raw(),
                    record_id,
                    now_ms,
                    InvalidationReason::Deleted,
                )?
            } else {
                Vec::new()
            };
            let cursor = transaction.advance_cursor(&request.target_scope, as_i64(now_ms)?)?;
            insert_event(
                transaction,
                request,
                authorization,
                cursor,
                Some(record_id),
                Some(previous),
                Some(previous),
                now_ms,
            )?;
            Ok(RecordMutation {
                record: load_record(transaction.raw(), record_id)?,
                cursor,
                invalidated_ids,
            })
        })
    }
}

fn create_in_transaction(
    transaction: &MemoryTransaction<'_>,
    request: &MutationRequest,
    authorization: &Authorization,
    draft: &RecordDraft,
    sources: &[SourceSnapshot],
    now_ms: u64,
) -> MemoryResult<RecordMutation> {
    validate_draft(draft)?;
    let owner_digest = scope_digest(&draft.scope);
    require_source_scope(sources, owner_digest)?;
    let normalized_digest = normalized_digest(&draft.content);
    refuse_duplicate(transaction.raw(), draft, normalized_digest, None)?;
    let content_digest = Digest::sha256(draft.content.as_bytes());
    let metadata_json = serde_json::to_string(&draft.metadata)
        .map_err(|_| corrupt("record metadata could not be encoded"))?;
    let smart_predicate_json = smart_predicate_json(draft)?;
    let revision_digest = revision_digest(
        &draft.id,
        1,
        None,
        content_digest,
        metadata_json.as_bytes(),
        smart_predicate_json.as_deref().map(str::as_bytes),
        authorization.actor_scope_digest,
        now_ms,
    );
    transaction
        .raw()
        .execute(
            "INSERT INTO memory_records(
                record_id, owner_scope_digest, kind, category, status,
                current_revision, current_revision_digest, normalized_content_digest,
                importance, confidence, verification_state, expires_at_ms,
                retention_until_ms, created_at_ms, updated_at_ms, deleted_at_ms
             ) VALUES (?1, ?2, ?3, ?4, 'active', 1, ?5, ?6, ?7, ?8,
                       'unverified', ?9, ?10, ?11, ?11, NULL)",
            params![
                draft.id.as_str(),
                owner_digest.to_string(),
                draft.kind.as_str(),
                draft.category,
                revision_digest.to_string(),
                normalized_digest.to_string(),
                draft.importance,
                draft.confidence,
                draft.expires_at_ms,
                draft.retention_until_ms,
                now_ms
            ],
        )
        .map_err(sql_error)?;
    insert_revision(
        transaction.raw(),
        draft,
        1,
        revision_digest,
        None,
        content_digest,
        &metadata_json,
        authorization.actor_scope_digest,
        now_ms,
    )?;
    attach_sources_and_edges(transaction.raw(), draft, sources, 1, now_ms)?;
    crate::fts::index_memory_record(
        transaction.raw(),
        draft.id.as_str(),
        &owner_digest.to_string(),
        &draft.category,
        &draft.content,
        &revision_digest.to_string(),
    )?;
    let cursor = transaction.advance_cursor(&draft.scope, as_i64(now_ms)?)?;
    insert_event(
        transaction,
        request,
        authorization,
        cursor,
        Some(&draft.id),
        None,
        Some(revision_digest),
        now_ms,
    )?;
    Ok(RecordMutation {
        record: load_record(transaction.raw(), &draft.id)?,
        cursor,
        invalidated_ids: Vec::new(),
    })
}

pub(crate) fn create_record_in(
    transaction: &MemoryTransaction<'_>,
    authorization: &Authorization,
    request: &MutationRequest,
    draft: &RecordDraft,
    sources: &[SourceSnapshot],
    now_ms: u64,
) -> MemoryResult<RecordMutation> {
    require_request(request, Operation::Create, draft)?;
    if request.revision != RevisionPrecondition::MustNotExist {
        return Err(conflict("create requires a must-not-exist precondition"));
    }
    transaction.ensure_scope(&draft.scope, as_i64(now_ms)?)?;
    transaction.require_record_revision(&draft.id, &RevisionPrecondition::MustNotExist)?;
    create_in_transaction(transaction, request, authorization, draft, sources, now_ms)
}

pub(crate) fn verify_record_in(
    transaction: &MemoryTransaction<'_>,
    authorization: &Authorization,
    request: &MutationRequest,
    state: VerificationState,
    confidence: f64,
    evidence_source_id: Option<&Id>,
    now_ms: u64,
) -> MemoryResult<RecordMutation> {
    require_record_request(request, Operation::Verify)?;
    if !confidence.is_finite() || !(0.0..=1.0).contains(&confidence) {
        return Err(domain_error(
            "INVALID_VERIFICATION",
            "verification confidence must be within 0..1",
        ));
    }
    let record_id = request.record_id.as_ref().expect("validated record id");
    let revision = transaction
        .require_record_revision(record_id, &request.revision)?
        .expect("match precondition returns digest");
    let record_owner: String = transaction
        .raw()
        .query_row(
            "SELECT owner_scope_digest FROM memory_records WHERE record_id=?1",
            [record_id.as_str()],
            |row| row.get(0),
        )
        .map_err(sql_error)?;
    if record_owner != authorization.target_scope_digest.to_hex() {
        return Err(denied(
            "verification record is outside the authorized scope",
        ));
    }
    if let Some(source_id) = evidence_source_id {
        let same_owner: bool = transaction
            .raw()
            .query_row(
                "SELECT EXISTS(
                    SELECT 1 FROM memory_sources
                    WHERE source_id=?1 AND owner_scope_digest=?2
                 )",
                params![
                    source_id.as_str(),
                    authorization.target_scope_digest.to_hex()
                ],
                |row| row.get(0),
            )
            .map_err(sql_error)?;
        if !same_owner {
            return Err(denied(
                "verification evidence is outside the authorized scope",
            ));
        }
    }
    let event_digest = Digest::sha256(
        format!(
            "verify:{}:{}:{}",
            record_id, revision, request.trace.request_id
        )
        .as_bytes(),
    );
    transaction
        .raw()
        .execute(
            "INSERT INTO memory_verification_events(
                event_id, record_id, revision_digest, actor_scope_digest,
                state, evidence_source_id, confidence, created_at_ms
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            params![
                format!("verification-{}", &event_digest.to_hex()[..32]),
                record_id.as_str(),
                revision.to_string(),
                authorization.actor_scope_digest.to_string(),
                verification_name(state),
                evidence_source_id.map(Id::as_str),
                confidence,
                now_ms
            ],
        )
        .map_err(sql_error)?;
    transaction
        .raw()
        .execute(
            "UPDATE memory_records
             SET verification_state=?2, confidence=?3, updated_at_ms=?4
             WHERE record_id=?1",
            params![
                record_id.as_str(),
                verification_name(state),
                confidence,
                now_ms
            ],
        )
        .map_err(sql_error)?;
    let cursor = transaction.advance_cursor(&request.target_scope, as_i64(now_ms)?)?;
    insert_event(
        transaction,
        request,
        authorization,
        cursor,
        Some(record_id),
        Some(revision),
        Some(revision),
        now_ms,
    )?;
    Ok(RecordMutation {
        record: load_record(transaction.raw(), record_id)?,
        cursor,
        invalidated_ids: Vec::new(),
    })
}

#[allow(clippy::too_many_arguments)]
fn update_in_transaction(
    transaction: &MemoryTransaction<'_>,
    request: &MutationRequest,
    authorization: &Authorization,
    draft: &RecordDraft,
    sources: &[SourceSnapshot],
    expected: Digest,
    now_ms: u64,
) -> MemoryResult<RecordMutation> {
    validate_draft(draft)?;
    let existing = load_record(transaction.raw(), &draft.id)?;
    if existing.kind == RecordKind::Anchor {
        return Err(domain_error(
            "IMMUTABLE_ANCHOR",
            "anchor content cannot be revised; create a superseding anchor",
        ));
    }
    if existing.owner_scope_digest != scope_digest(&draft.scope) || existing.kind != draft.kind {
        return Err(domain_error(
            "RECORD_IDENTITY_MISMATCH",
            "record scope and kind are immutable",
        ));
    }
    let normalized_digest = normalized_digest(&draft.content);
    refuse_duplicate(transaction.raw(), draft, normalized_digest, Some(&draft.id))?;
    require_source_scope(sources, existing.owner_scope_digest)?;
    let revision = existing.current.number + 1;
    let content_digest = Digest::sha256(draft.content.as_bytes());
    let metadata_json = serde_json::to_string(&draft.metadata)
        .map_err(|_| corrupt("record metadata could not be encoded"))?;
    let smart_predicate_json = smart_predicate_json(draft)?;
    let revision_digest = revision_digest(
        &draft.id,
        revision,
        Some(expected),
        content_digest,
        metadata_json.as_bytes(),
        smart_predicate_json.as_deref().map(str::as_bytes),
        authorization.actor_scope_digest,
        now_ms,
    );
    insert_revision(
        transaction.raw(),
        draft,
        revision,
        revision_digest,
        Some(expected),
        content_digest,
        &metadata_json,
        authorization.actor_scope_digest,
        now_ms,
    )?;
    transaction
        .raw()
        .execute(
            "UPDATE memory_records SET
                category=?2, status='active', current_revision=?3,
                current_revision_digest=?4, normalized_content_digest=?5,
                importance=?6, confidence=?7, verification_state='unverified',
                expires_at_ms=?8, retention_until_ms=?9,
                updated_at_ms=?10, deleted_at_ms=NULL
             WHERE record_id=?1 AND current_revision_digest=?11",
            params![
                draft.id.as_str(),
                draft.category,
                revision,
                revision_digest.to_string(),
                normalized_digest.to_string(),
                draft.importance,
                draft.confidence,
                draft.expires_at_ms,
                draft.retention_until_ms,
                now_ms,
                expected.to_string()
            ],
        )
        .map_err(sql_error)?;
    attach_sources_and_edges(transaction.raw(), draft, sources, revision, now_ms)?;
    let invalidated_ids = invalidate_record_and_descendants(
        transaction.raw(),
        &draft.id,
        now_ms,
        InvalidationReason::ContentEdited,
    )?;
    crate::fts::index_memory_record(
        transaction.raw(),
        draft.id.as_str(),
        &existing.owner_scope_digest.to_string(),
        &draft.category,
        &draft.content,
        &revision_digest.to_string(),
    )?;
    let cursor = transaction.advance_cursor(&draft.scope, as_i64(now_ms)?)?;
    insert_event(
        transaction,
        request,
        authorization,
        cursor,
        Some(&draft.id),
        Some(expected),
        Some(revision_digest),
        now_ms,
    )?;
    Ok(RecordMutation {
        record: load_record(transaction.raw(), &draft.id)?,
        cursor,
        invalidated_ids,
    })
}

fn attach_sources_and_edges(
    transaction: &Transaction<'_>,
    draft: &RecordDraft,
    sources: &[SourceSnapshot],
    revision: u64,
    now_ms: u64,
) -> MemoryResult<()> {
    for source in sources {
        let canonical_id = put_source(transaction, source, now_ms)?;
        if canonical_id != source.source_id
            && draft
                .provenance
                .iter()
                .any(|span| span.source_id == source.source_id)
        {
            return Err(domain_error(
                "DUPLICATE_SOURCE_ID",
                "provenance must use the canonical existing source id",
            ));
        }
    }
    attach_spans(transaction, &draft.id, revision, &draft.provenance)?;
    attach_edges(transaction, &draft.id, revision, &draft.lineage, now_ms)?;
    if let Some(predicate) = &draft.smart_predicate {
        let predicate_json = serde_json::to_string(predicate)
            .map_err(|_| corrupt("smart-note predicate could not be encoded"))?;
        let predicate_digest = predicate
            .digest()
            .map_err(|_| domain_error("INVALID_SMART_NOTE", "smart-note predicate is invalid"))?;
        transaction
            .execute(
                "INSERT INTO smart_note_details(
                    record_id,predicate_json,predicate_digest,last_evaluated_cursor_json,
                    last_result,next_evaluation_at_ms
                 ) VALUES (?1,?2,?3,NULL,NULL,NULL)
                 ON CONFLICT(record_id) DO UPDATE SET
                    predicate_json=excluded.predicate_json,
                    predicate_digest=excluded.predicate_digest,
                    last_evaluated_cursor_json=CASE
                        WHEN smart_note_details.predicate_digest=excluded.predicate_digest
                        THEN smart_note_details.last_evaluated_cursor_json ELSE NULL END,
                    last_result=CASE
                        WHEN smart_note_details.predicate_digest=excluded.predicate_digest
                        THEN smart_note_details.last_result ELSE NULL END,
                    next_evaluation_at_ms=CASE
                        WHEN smart_note_details.predicate_digest=excluded.predicate_digest
                        THEN smart_note_details.next_evaluation_at_ms ELSE NULL END",
                params![draft.id.as_str(), predicate_json, predicate_digest.to_hex()],
            )
            .map_err(sql_error)?;
    }
    if let Some(summary) = &draft.summary {
        if draft.kind != RecordKind::Summary {
            return Err(domain_error(
                "INVALID_SUMMARY",
                "summary details require a summary record",
            ));
        }
        put_summary_details(transaction, &draft.id, summary, now_ms)?;
    } else if draft.kind == RecordKind::Summary {
        return Err(domain_error(
            "INVALID_SUMMARY",
            "summary record requires input digest and level",
        ));
    }
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn insert_revision(
    transaction: &Transaction<'_>,
    draft: &RecordDraft,
    revision: u64,
    revision_digest: Digest,
    parent_digest: Option<Digest>,
    content_digest: Digest,
    metadata_json: &str,
    author_scope_digest: Digest,
    now_ms: u64,
) -> MemoryResult<()> {
    let smart_predicate_json = smart_predicate_json(draft)?;
    transaction
        .execute(
            "INSERT INTO memory_revisions(
                record_id, revision, revision_digest, parent_revision_digest,
                content, content_digest, metadata_json, smart_predicate_json,
                author_scope_digest, authored_at_ms, immutable_anchor
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)",
            params![
                draft.id.as_str(),
                revision,
                revision_digest.to_string(),
                parent_digest.map(Digest::to_hex),
                draft.content,
                content_digest.to_string(),
                metadata_json,
                smart_predicate_json,
                author_scope_digest.to_string(),
                now_ms,
                i64::from(draft.kind == RecordKind::Anchor)
            ],
        )
        .map_err(sql_error)?;
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn insert_event(
    transaction: &MemoryTransaction<'_>,
    request: &MutationRequest,
    authorization: &Authorization,
    cursor: Cursor,
    record_id: Option<&Id>,
    previous: Option<Digest>,
    result: Option<Digest>,
    now_ms: u64,
) -> MemoryResult<()> {
    let trace_json =
        serde_json::to_string(&request.trace).map_err(|_| corrupt("trace could not be encoded"))?;
    let event_digest = Digest::sha256(
        format!(
            "{}:{}:{}:{}:{}",
            request.trace.trace_id,
            request.trace.request_id,
            authorization.target_scope_digest,
            cursor.epoch,
            cursor.sequence
        )
        .as_bytes(),
    );
    let event_id = format!("event-{}", &event_digest.to_hex()[..32]);
    let (basis, grant_id) = match (&authorization.basis, &authorization.grant) {
        (AuthorizationBasis::Owner, None) => ("owner", None),
        (AuthorizationBasis::Grant, Some(grant)) => ("grant", Some(grant.id.as_str())),
        _ => return Err(corrupt("authorization basis is inconsistent")),
    };
    transaction
        .raw()
        .execute(
            "INSERT INTO memory_mutation_events(
                event_id, owner_scope_digest, epoch, sequence, operation,
                record_id, previous_revision_digest, result_revision_digest,
                actor_scope_digest, grant_id, authorization_basis,
                trace_json, created_at_ms
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13)",
            params![
                event_id,
                authorization.target_scope_digest.to_string(),
                cursor.epoch,
                cursor.sequence,
                request.operation.as_str(),
                record_id.map(Id::as_str),
                previous.map(Digest::to_hex),
                result.map(Digest::to_hex),
                authorization.actor_scope_digest.to_string(),
                grant_id,
                basis,
                trace_json,
                now_ms
            ],
        )
        .map_err(sql_error)?;
    Ok(())
}

const RECORD_SELECT: &str =
    "SELECT r.record_id, r.owner_scope_digest, r.kind, r.category, r.status,
            r.current_revision, r.current_revision_digest, r.normalized_content_digest,
            r.importance, r.confidence, r.verification_state, r.expires_at_ms,
            r.retention_until_ms, r.created_at_ms, r.updated_at_ms, r.deleted_at_ms,
            v.parent_revision_digest, v.content, v.content_digest, v.metadata_json,
            v.author_scope_digest, v.authored_at_ms
     FROM memory_records r
     JOIN memory_revisions v ON v.record_id=r.record_id AND v.revision=r.current_revision
     WHERE r.record_id=?1";

fn load_record(transaction: &Transaction<'_>, record_id: &Id) -> MemoryResult<MemoryRecord> {
    transaction
        .query_row(RECORD_SELECT, [record_id.as_str()], map_record)
        .optional()
        .map_err(sql_error)?
        .ok_or_else(not_found)?
}

pub(crate) fn record_in(
    transaction: &Transaction<'_>,
    record_id: &Id,
) -> MemoryResult<Option<MemoryRecord>> {
    transaction
        .query_row(RECORD_SELECT, [record_id.as_str()], map_record)
        .optional()
        .map_err(sql_error)?
        .map_or(Ok(None), |row| row.map(Some))
}

pub(crate) fn list_records_in(
    transaction: &Transaction<'_>,
    scope: &Scope,
    category: Option<&str>,
    status: Option<RecordStatus>,
    limit: usize,
) -> MemoryResult<(Cursor, Vec<MemoryRecord>)> {
    if limit == 0 || limit > 1_000 {
        return Err(domain_error(
            "INVALID_ARGUMENT",
            "record list limit must be within 1..1000",
        ));
    }
    let cursor = cursor_in(transaction, scope)?;
    let records = list_records_on(transaction, scope, category, status, limit)?;
    Ok((cursor, records))
}

fn cursor_in(connection: &rusqlite::Connection, scope: &Scope) -> MemoryResult<Cursor> {
    let pair: Option<(u64, u64)> = connection
        .query_row(
            "SELECT epoch, sequence FROM memory_scopes WHERE scope_digest=?1",
            [scope_digest(scope).to_string()],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .optional()
        .map_err(sql_error)?;
    let (epoch, sequence) = pair
        .ok_or_else(|| domain_error("SCOPE_NOT_FOUND", "memory scope has not been initialized"))?;
    Cursor::new(epoch, sequence)
        .map_err(|_| domain_error("STORE_CORRUPT", "scope cursor is outside valid bounds"))
}

fn list_records_on(
    connection: &rusqlite::Connection,
    scope: &Scope,
    category: Option<&str>,
    status: Option<RecordStatus>,
    limit: usize,
) -> MemoryResult<Vec<MemoryRecord>> {
    let mut statement = connection
        .prepare(&format!(
            "{} AND r.owner_scope_digest=?1
             AND (?2 IS NULL OR r.category=?2)
             AND (?3 IS NULL OR r.status=?3)
             ORDER BY r.updated_at_ms DESC, r.record_id ASC LIMIT ?4",
            RECORD_SELECT.replacen("WHERE r.record_id=?1", "WHERE 1=1", 1)
        ))
        .map_err(sql_error)?;
    let rows = statement
        .query_map(
            params![
                scope_digest(scope).to_string(),
                category,
                status.map(RecordStatus::as_str),
                limit
            ],
            map_record,
        )
        .map_err(sql_error)?
        .collect::<Result<Vec<_>, _>>()
        .map_err(sql_error)?;
    let mut records = Vec::with_capacity(rows.len());
    for row in rows {
        records.push(row?);
    }
    Ok(records)
}

fn map_record(row: &Row<'_>) -> rusqlite::Result<MemoryResult<MemoryRecord>> {
    let raw = RawRecord {
        id: row.get(0)?,
        owner_scope_digest: row.get(1)?,
        kind: row.get(2)?,
        category: row.get(3)?,
        status: row.get(4)?,
        revision: row.get(5)?,
        revision_digest: row.get(6)?,
        normalized_content_digest: row.get(7)?,
        importance: row.get(8)?,
        confidence: row.get(9)?,
        verification: row.get(10)?,
        expires_at_ms: row.get(11)?,
        retention_until_ms: row.get(12)?,
        created_at_ms: row.get(13)?,
        updated_at_ms: row.get(14)?,
        deleted_at_ms: row.get(15)?,
        parent_revision_digest: row.get(16)?,
        content: row.get(17)?,
        content_digest: row.get(18)?,
        metadata_json: row.get(19)?,
        author_scope_digest: row.get(20)?,
        authored_at_ms: row.get(21)?,
    };
    Ok(raw.try_into_record())
}

struct RawRecord {
    id: String,
    owner_scope_digest: String,
    kind: String,
    category: String,
    status: String,
    revision: u64,
    revision_digest: String,
    normalized_content_digest: String,
    importance: f64,
    confidence: f64,
    verification: String,
    expires_at_ms: Option<u64>,
    retention_until_ms: Option<u64>,
    created_at_ms: u64,
    updated_at_ms: u64,
    deleted_at_ms: Option<u64>,
    parent_revision_digest: Option<String>,
    content: String,
    content_digest: String,
    metadata_json: String,
    author_scope_digest: String,
    authored_at_ms: u64,
}

impl RawRecord {
    fn try_into_record(self) -> MemoryResult<MemoryRecord> {
        use std::str::FromStr;
        Ok(MemoryRecord {
            id: Id::new(self.id).map_err(|_| corrupt("record id is invalid"))?,
            owner_scope_digest: Digest::from_str(&self.owner_scope_digest)
                .map_err(|_| corrupt("owner scope digest is invalid"))?,
            kind: RecordKind::parse(&self.kind)?,
            category: self.category,
            status: RecordStatus::parse(&self.status)?,
            current: RecordRevision {
                number: self.revision,
                digest: Digest::from_str(&self.revision_digest)
                    .map_err(|_| corrupt("revision digest is invalid"))?,
                parent_digest: self
                    .parent_revision_digest
                    .map(|value| Digest::from_str(&value))
                    .transpose()
                    .map_err(|_| corrupt("parent revision digest is invalid"))?,
                content: self.content,
                content_digest: Digest::from_str(&self.content_digest)
                    .map_err(|_| corrupt("content digest is invalid"))?,
                metadata: serde_json::from_str(&self.metadata_json)
                    .map_err(|_| corrupt("record metadata is invalid"))?,
                author_scope_digest: Digest::from_str(&self.author_scope_digest)
                    .map_err(|_| corrupt("author scope digest is invalid"))?,
                authored_at_ms: self.authored_at_ms,
            },
            normalized_content_digest: Digest::from_str(&self.normalized_content_digest)
                .map_err(|_| corrupt("normalized content digest is invalid"))?,
            importance: self.importance,
            confidence: self.confidence,
            verification: VerificationState::parse(&self.verification)?,
            expires_at_ms: self.expires_at_ms,
            retention_until_ms: self.retention_until_ms,
            created_at_ms: self.created_at_ms,
            updated_at_ms: self.updated_at_ms,
            deleted_at_ms: self.deleted_at_ms,
        })
    }
}

fn validate_draft(draft: &RecordDraft) -> MemoryResult<()> {
    if draft.category.is_empty()
        || draft.category.len() > 128
        || draft.content.is_empty()
        || draft.content.len() > MAX_CONTENT_BYTES
        || !draft.importance.is_finite()
        || !(0.0..=1.0).contains(&draft.importance)
        || !draft.confidence.is_finite()
        || !(0.0..=1.0).contains(&draft.confidence)
        || (draft.kind == RecordKind::Anchor && draft.expires_at_ms.is_some())
    {
        return Err(domain_error(
            "INVALID_RECORD",
            "memory draft violates the record contract",
        ));
    }
    match (&draft.kind, &draft.smart_predicate) {
        (RecordKind::SmartNote, Some(predicate)) => predicate
            .validate()
            .map_err(|_| domain_error("INVALID_SMART_NOTE", "smart-note predicate is invalid"))?,
        (RecordKind::SmartNote, None) => {
            return Err(domain_error(
                "INVALID_SMART_NOTE",
                "smart-note records require a predicate",
            ))
        }
        (_, Some(_)) => {
            return Err(domain_error(
                "INVALID_SMART_NOTE",
                "only smart-note records may carry a predicate",
            ))
        }
        (_, None) => {}
    }
    if instruction_shaped(&draft.content)
        && draft
            .metadata
            .get("promoted_control")
            .and_then(Value::as_bool)
            == Some(true)
    {
        return Err(domain_error(
            "CONTENT_AUTHORITY_DENIED",
            "memory content cannot promote itself into control authority",
        ));
    }
    Ok(())
}

pub fn instruction_shaped(content: &str) -> bool {
    let normalized = content
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .to_lowercase();
    [
        "system:",
        "assistant:",
        "developer:",
        "ignore previous",
        "ignore all previous",
        "you must",
        "do not ask",
    ]
    .iter()
    .any(|prefix| normalized.starts_with(prefix))
}

fn require_request(
    request: &MutationRequest,
    operation: Operation,
    draft: &RecordDraft,
) -> MemoryResult<()> {
    if request.operation != operation
        || request.target_scope != draft.scope
        || request.record_id.as_ref() != Some(&draft.id)
        || request.category.as_ref() != Some(&draft.category)
    {
        return Err(domain_error(
            "MUTATION_REQUEST_MISMATCH",
            "mutation request does not describe the record draft",
        ));
    }
    Ok(())
}

fn require_record_request(request: &MutationRequest, operation: Operation) -> MemoryResult<()> {
    if request.operation != operation
        || request.record_id.is_none()
        || !matches!(request.revision, RevisionPrecondition::Match(_))
    {
        return Err(domain_error(
            "MUTATION_REQUEST_MISMATCH",
            "record mutation requires an exact record and revision",
        ));
    }
    Ok(())
}

fn require_source_scope(sources: &[SourceSnapshot], owner: Digest) -> MemoryResult<()> {
    if sources
        .iter()
        .any(|source| source.owner_scope_digest != owner)
    {
        return Err(denied("source snapshots must belong to the target scope"));
    }
    Ok(())
}

fn refuse_duplicate(
    transaction: &Transaction<'_>,
    draft: &RecordDraft,
    normalized: Digest,
    exclude_id: Option<&Id>,
) -> MemoryResult<()> {
    let duplicate: Option<String> = transaction
        .query_row(
            "SELECT record_id FROM memory_records
             WHERE owner_scope_digest=?1 AND kind=?2 AND category=?3
               AND normalized_content_digest=?4 AND status IN ('active','stale')
               AND (?5 IS NULL OR record_id<>?5) LIMIT 1",
            params![
                scope_digest(&draft.scope).to_string(),
                draft.kind.as_str(),
                draft.category,
                normalized.to_string(),
                exclude_id.map(Id::as_str)
            ],
            |row| row.get(0),
        )
        .optional()
        .map_err(sql_error)?;
    if duplicate.is_some() {
        return Err(domain_error(
            "DUPLICATE_MEMORY",
            "an active record already has this exact normalized content",
        ));
    }
    Ok(())
}

fn normalized_digest(content: &str) -> Digest {
    Digest::sha256(
        content
            .split_whitespace()
            .collect::<Vec<_>>()
            .join(" ")
            .to_lowercase()
            .as_bytes(),
    )
}

fn revision_digest(
    record_id: &Id,
    revision: u64,
    parent: Option<Digest>,
    content: Digest,
    metadata: &[u8],
    smart_predicate: Option<&[u8]>,
    author: Digest,
    authored_at_ms: u64,
) -> Digest {
    let mut hasher = Sha256::new();
    hasher.update(b"hypermid.memory.revision.v1\0");
    hash_part(&mut hasher, record_id.as_str().as_bytes());
    hasher.update(revision.to_be_bytes());
    match parent {
        Some(value) => {
            hasher.update([1]);
            hasher.update(value.as_bytes());
        }
        None => hasher.update([0]),
    }
    hasher.update(content.as_bytes());
    hash_part(&mut hasher, metadata);
    match smart_predicate {
        Some(value) => {
            hasher.update([1]);
            hash_part(&mut hasher, value);
        }
        None => hasher.update([0]),
    }
    hasher.update(author.as_bytes());
    hasher.update(authored_at_ms.to_be_bytes());
    Digest::from_bytes(hasher.finalize().into())
}

fn smart_predicate_json(draft: &RecordDraft) -> MemoryResult<Option<String>> {
    draft
        .smart_predicate
        .as_ref()
        .map(serde_json::to_string)
        .transpose()
        .map_err(|_| corrupt("smart-note predicate could not be encoded"))
}

fn hash_part(hasher: &mut Sha256, value: &[u8]) {
    hasher.update((value.len() as u64).to_be_bytes());
    hasher.update(value);
}

fn provenance_source_ids(
    transaction: &Transaction<'_>,
    record_id: &Id,
) -> MemoryResult<Vec<String>> {
    let mut statement = transaction
        .prepare("SELECT DISTINCT source_id FROM memory_provenance WHERE record_id=?1")
        .map_err(sql_error)?;
    let result = statement
        .query_map([record_id.as_str()], |row| row.get(0))
        .map_err(sql_error)?
        .collect::<Result<Vec<_>, _>>()
        .map_err(sql_error)?;
    Ok(result)
}

fn require_auth_time(context: &AuthContext, now_ms: u64) -> MemoryResult<()> {
    if context.request.now_ms != now_ms {
        return Err(denied(
            "authenticated capability time must match the mutation timestamp",
        ));
    }
    Ok(())
}

fn expected_digest(request: &MutationRequest) -> Option<Digest> {
    match request.revision {
        RevisionPrecondition::MustNotExist => None,
        RevisionPrecondition::Match(digest) => Some(digest),
    }
}

const fn verification_name(state: VerificationState) -> &'static str {
    match state {
        VerificationState::Unverified => "unverified",
        VerificationState::Supported => "supported",
        VerificationState::Disputed => "disputed",
        VerificationState::Refuted => "refuted",
        VerificationState::Unknown => "unknown",
    }
}

fn as_i64(value: u64) -> MemoryResult<i64> {
    i64::try_from(value)
        .map_err(|_| domain_error("INVALID_ARGUMENT", "timestamp exceeds SQLite range"))
}

fn denied(message: &str) -> hypermid_contracts::Error {
    error("AUTHORIZATION_DENIED", message, EffectState::NotStarted)
}

fn conflict(message: &str) -> hypermid_contracts::Error {
    error("REVISION_CONFLICT", message, EffectState::NotStarted)
}

fn not_found() -> hypermid_contracts::Error {
    error(
        "RECORD_NOT_FOUND",
        "memory record does not exist",
        EffectState::NotStarted,
    )
}

fn corrupt(message: &str) -> hypermid_contracts::Error {
    error("STORE_CORRUPT", message, EffectState::Unknown)
}

#[cfg(test)]
mod lineage_record_tests {
    use std::collections::BTreeSet;

    use hypermid_core::capability::{
        AuthenticatedPrincipal, AuthorizationRequest, CapabilityGrant, CapabilityOperation,
        PrincipalKind,
    };
    use hypermid_store::authorization::put_grant;

    use super::*;
    use crate::provenance::{recover_source_bytes, SourceKind};
    use crate::sharing::{
        publish_sharing_judgment_in, KnowledgeSharingJudgment, SharingClassification, TrustDecision,
    };
    use crate::summary::{SummaryDetails, SummaryLevel};

    fn id(value: &str) -> Id {
        Id::new(value).unwrap()
    }

    fn scope() -> Scope {
        Scope::new(id("owner-1"), id("project-1"), Some(id("workspace-1")))
    }

    fn principal() -> AuthenticatedPrincipal {
        AuthenticatedPrincipal {
            principal_id: id("principal-1"),
            owner_id: id("owner-1"),
            kind: PrincipalKind::Foreground,
        }
    }

    fn context(
        target: &Scope,
        operation: CapabilityOperation,
        resource: &str,
        now_ms: u64,
    ) -> AuthContext {
        AuthContext {
            principal: principal(),
            request: AuthorizationRequest {
                claimed_scope: target.clone(),
                target_scope: target.clone(),
                operation,
                resource_id: id(resource),
                now_ms,
            },
            capability_id: id("cap-memory-records"),
        }
    }

    fn request(
        target: &Scope,
        operation: Operation,
        resource: &str,
        category: &str,
        revision: RevisionPrecondition,
        suffix: &str,
    ) -> MutationRequest {
        MutationRequest {
            operation,
            actor_scope: target.clone(),
            target_scope: target.clone(),
            record_id: Some(id(resource)),
            category: Some(category.to_owned()),
            revision,
            trace: hypermid_contracts::Trace::new(
                id(&format!("trace-{suffix}")),
                id(&format!("request-{suffix}")),
            ),
        }
    }

    fn draft(
        target: &Scope,
        record_id: &str,
        kind: RecordKind,
        category: &str,
        content: &str,
    ) -> RecordDraft {
        RecordDraft {
            id: id(record_id),
            scope: target.clone(),
            kind,
            category: category.to_owned(),
            content: content.to_owned(),
            metadata: Map::new(),
            importance: 0.7,
            confidence: 0.8,
            expires_at_ms: None,
            retention_until_ms: Some(5_000),
            provenance: Vec::new(),
            lineage: Vec::new(),
            smart_predicate: (kind == RecordKind::SmartNote).then(|| {
                serde_json::from_value(serde_json::json!({
                    "operator": "all",
                    "clauses": [{
                        "field": "record.category",
                        "comparison": "eq",
                        "value": category
                    }]
                }))
                .unwrap()
            }),
            summary: None,
        }
    }

    fn prepare_store(path: &std::path::Path, target: &Scope) -> MemoryStore {
        let mut store = MemoryStore::open(path).unwrap();
        store.ensure_scope(target, 1).unwrap();
        let grant = CapabilityGrant {
            capability_id: id("cap-memory-records"),
            issuer_owner_id: id("owner-1"),
            principal_id: id("principal-1"),
            claimed_scope: target.clone(),
            target_scope: target.clone(),
            operations: BTreeSet::from([
                CapabilityOperation::Append,
                CapabilityOperation::Read,
                CapabilityOperation::Revise,
                CapabilityOperation::Archive,
                CapabilityOperation::Restore,
                CapabilityOperation::Delete,
            ]),
            resources: BTreeSet::from([id("source-record"), id("summary-record")]),
            expires_at_ms: 10_000,
        };
        store
            .immediate(|transaction| {
                put_grant(transaction.raw(), &principal(), &grant).unwrap();
                Ok(())
            })
            .unwrap();
        store
    }

    #[test]
    fn lineage_revisions_are_conflict_safe_invalidate_descendants_and_keep_raw_provenance() {
        let directory = tempfile::tempdir().unwrap();
        let target = scope();
        let path = directory.path().join("memory.sqlite3");
        let mut store = prepare_store(&path, &target);
        let raw = "chronological source bytes remain exact";
        let source = SourceSnapshot {
            source_id: id("raw-source"),
            owner_scope_digest: scope_digest(&target),
            kind: SourceKind::Message,
            source_digest: Digest::sha256(raw.as_bytes()),
            locator: Some("session-1:message-1".to_owned()),
            captured_content: Some(raw.to_owned()),
            capture_method: "journal-copy".to_owned(),
            observed_at_ms: 10,
        };
        let mut source_draft = draft(
            &target,
            "source-record",
            RecordKind::Note,
            "evidence",
            "first interpretation",
        );
        source_draft.provenance.push(ProvenanceSpan {
            source_id: source.source_id.clone(),
            span_start: None,
            span_end: None,
            quoted_digest: Some(source.source_digest),
        });
        let created_source = store
            .create_record(
                &context(&target, CapabilityOperation::Append, "source-record", 100),
                &request(
                    &target,
                    Operation::Create,
                    "source-record",
                    "evidence",
                    RevisionPrecondition::MustNotExist,
                    "source-create",
                ),
                &source_draft,
                std::slice::from_ref(&source),
                100,
            )
            .unwrap();

        let mut summary_draft = draft(
            &target,
            "summary-record",
            RecordKind::Summary,
            "evidence",
            "revisable connective summary",
        );
        summary_draft.lineage.push(LineageEdge {
            parent_id: created_source.record.id.clone(),
            relation: crate::lineage::LineageRelation::DerivedFrom,
            parent_revision_digest: created_source.record.current.digest,
        });
        summary_draft.summary = Some(SummaryDetails {
            input_set_digest: Digest::sha256(created_source.record.current.digest.as_bytes()),
            level: SummaryLevel::Standard,
            decay_half_life_ms: Some(60_000),
        });
        let created_summary = store
            .create_record(
                &context(&target, CapabilityOperation::Append, "summary-record", 200),
                &request(
                    &target,
                    Operation::Create,
                    "summary-record",
                    "evidence",
                    RevisionPrecondition::MustNotExist,
                    "summary-create",
                ),
                &summary_draft,
                &[],
                200,
            )
            .unwrap();

        store
            .immediate(|transaction| {
                transaction
                    .raw()
                    .execute(
                        "INSERT INTO embedding_registrations(
                            registration_id, owner_scope_digest, mode, provider_identity,
                            model_id, dimensions, metric, normalized, fingerprint,
                            state, created_at_ms
                         ) VALUES ('registration-1', ?1, 'local', 'local-provider',
                                   'model-1', 1, 'cosine', 1, ?2, 'active', 200)",
                        params![scope_digest(&target).to_string(), "b".repeat(64)],
                    )
                    .map_err(sql_error)?;
                for record in [&created_source.record, &created_summary.record] {
                    transaction
                        .raw()
                        .execute(
                            "INSERT INTO memory_embeddings(
                                record_id, revision_digest, registration_id,
                                vector_f32, dimensions, norm, created_at_ms
                             ) VALUES (?1, ?2, 'registration-1', ?3, 1, 1.0, 200)",
                            params![
                                record.id.as_str(),
                                record.current.digest.to_string(),
                                1.0_f32.to_le_bytes().to_vec()
                            ],
                        )
                        .map_err(sql_error)?;
                }
                crate::fts::index_memory_record(
                    transaction.raw(),
                    created_summary.record.id.as_str(),
                    &created_summary.record.owner_scope_digest.to_string(),
                    &created_summary.record.category,
                    &created_summary.record.current.content,
                    &created_summary.record.current.digest.to_string(),
                )
            })
            .unwrap();

        let policy_digest = Digest::sha256(b"owner-sharing-policy-v1");
        store
            .immediate(|transaction| {
                for (suffix, record) in [
                    ("source", &created_source.record),
                    ("summary", &created_summary.record),
                ] {
                    let verify_request = request(
                        &target,
                        Operation::Verify,
                        record.id.as_str(),
                        &record.category,
                        RevisionPrecondition::Match(record.current.digest),
                        &format!("sharing-{suffix}"),
                    );
                    let verify_context = context(
                        &target,
                        CapabilityOperation::Revise,
                        record.id.as_str(),
                        250,
                    );
                    let authorization = transaction.authorize(&verify_context, &verify_request)?;
                    publish_sharing_judgment_in(
                        transaction,
                        &authorization,
                        &verify_request,
                        &KnowledgeSharingJudgment {
                            record_id: record.id.clone(),
                            expected_revision_digest: record.current.digest,
                            classification: SharingClassification::Shared,
                            trust_decision: TrustDecision::Allow,
                            policy_id: "owner-sharing-policy".to_owned(),
                            policy_version: 1,
                            policy_digest,
                            provider_id: Some("policy-provider".to_owned()),
                            model_id: Some("policy-model".to_owned()),
                            evidence_digest: source.source_digest,
                        },
                        250,
                    )?;
                }
                Ok(())
            })
            .unwrap();

        let shared_access = crate::AccessRequest {
            operation: crate::GrantOperation::Read,
            actor_scope: target.clone(),
            target_scope: target.clone(),
            resource_id: created_source.record.id.clone(),
            category: Some(created_source.record.category.clone()),
            trace: hypermid_contracts::Trace::new(
                id("trace-sharing-read"),
                id("request-sharing-read"),
            ),
        };
        let mut api = crate::MemoryApi::new(store);
        let visible = api
            .shared_record(
                &context(&target, CapabilityOperation::Read, "source-record", 275),
                &shared_access,
                policy_digest,
            )
            .unwrap();
        assert_eq!(visible.record.unwrap().id, created_source.record.id);

        let stale_expected = created_source.record.current.digest;
        let mut revised = source_draft.clone();
        revised.content = "corrected interpretation".to_owned();
        let updated = api
            .update_record(
                &context(&target, CapabilityOperation::Revise, "source-record", 300),
                &request(
                    &target,
                    Operation::Update,
                    "source-record",
                    "evidence",
                    RevisionPrecondition::Match(stale_expected),
                    "source-update",
                ),
                &revised,
                std::slice::from_ref(&source),
                300,
            )
            .unwrap();
        assert_eq!(updated.record.current.number, 2);
        assert!(updated.invalidated_ids.contains(&id("summary-record")));
        let refused = api
            .shared_record(
                &context(&target, CapabilityOperation::Read, "source-record", 325),
                &shared_access,
                policy_digest,
            )
            .unwrap_err();
        assert_eq!(refused.code, "AUTHORIZATION_DENIED");
        let stale_summary = api.store.record(&id("summary-record")).unwrap().unwrap();
        assert_eq!(stale_summary.status, RecordStatus::Stale);
        api.store
            .read(|connection| {
                let embeddings: i64 = connection
                    .query_row(
                        "SELECT count(*) FROM memory_embeddings
                         WHERE record_id IN ('source-record','summary-record')",
                        [],
                        |row| row.get(0),
                    )
                    .map_err(sql_error)?;
                let fts_rows: i64 = connection
                    .query_row(
                        "SELECT count(*) FROM memory_fts_rows WHERE record_id='summary-record'",
                        [],
                        |row| row.get(0),
                    )
                    .map_err(sql_error)?;
                let invalidated_judgments: i64 = connection
                    .query_row(
                        "SELECT count(*) FROM memory_sharing_judgments
                         WHERE record_id IN ('source-record','summary-record')
                           AND invalidated_at_ms=300
                           AND invalidation_reason='content_edited'",
                        [],
                        |row| row.get(0),
                    )
                    .map_err(sql_error)?;
                let current_source_fts: i64 = connection
                    .query_row(
                        "SELECT count(*) FROM memory_fts_rows
                         WHERE record_id='source-record' AND revision_digest=?1",
                        [updated.record.current.digest.to_hex()],
                        |row| row.get(0),
                    )
                    .map_err(sql_error)?;
                assert_eq!((embeddings, fts_rows), (0, 0));
                assert_eq!(invalidated_judgments, 2);
                assert_eq!(current_source_fts, 1);
                Ok(())
            })
            .unwrap();

        let stale_attempt = api
            .update_record(
                &context(&target, CapabilityOperation::Revise, "source-record", 350),
                &request(
                    &target,
                    Operation::Update,
                    "source-record",
                    "evidence",
                    RevisionPrecondition::Match(stale_expected),
                    "source-stale-update",
                ),
                &revised,
                std::slice::from_ref(&source),
                350,
            )
            .unwrap_err();
        assert_eq!(stale_attempt.code, "REVISION_CONFLICT");
        assert_eq!(
            api.store
                .record(&id("source-record"))
                .unwrap()
                .unwrap()
                .current
                .number,
            2
        );

        let mut cyclic = revised.clone();
        cyclic.content = "cycle attempt".to_owned();
        cyclic.lineage.push(LineageEdge {
            parent_id: id("summary-record"),
            relation: crate::lineage::LineageRelation::DerivedFrom,
            parent_revision_digest: created_summary.record.current.digest,
        });
        let cycle = api
            .update_record(
                &context(&target, CapabilityOperation::Revise, "source-record", 400),
                &request(
                    &target,
                    Operation::Update,
                    "source-record",
                    "evidence",
                    RevisionPrecondition::Match(updated.record.current.digest),
                    "source-cycle",
                ),
                &cyclic,
                std::slice::from_ref(&source),
                400,
            )
            .unwrap_err();
        assert_eq!(cycle.code, "LINEAGE_CYCLE");

        let tombstoned = api
            .delete_record(
                &context(&target, CapabilityOperation::Delete, "source-record", 500),
                &request(
                    &target,
                    Operation::Delete,
                    "source-record",
                    "evidence",
                    RevisionPrecondition::Match(updated.record.current.digest),
                    "source-delete",
                ),
                500,
            )
            .unwrap();
        assert_eq!(tombstoned.record.status, RecordStatus::Tombstoned);
        let recovered = api
            .store
            .immediate(|transaction| {
                recover_source_bytes(transaction.raw(), &id("raw-source"), None, None)
            })
            .unwrap();
        assert_eq!(recovered, raw);

        let purge = api
            .purge_record(
                &context(&target, CapabilityOperation::Delete, "source-record", 1_000),
                &request(
                    &target,
                    Operation::Purge,
                    "source-record",
                    "evidence",
                    RevisionPrecondition::Match(updated.record.current.digest),
                    "source-purge",
                ),
                1_000,
            )
            .unwrap_err();
        assert_eq!(purge.code, "RETENTION_NOT_ELAPSED");
    }
}
