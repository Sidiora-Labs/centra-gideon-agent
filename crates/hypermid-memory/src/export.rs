use crate::model::{scope_digest, MutationRequest, Operation};
use crate::{
    error, AuthContext, Cursor, Digest, EffectState, Id, MemoryResult, MemoryStore, Scope, Trace,
};
use rusqlite::{params, params_from_iter, types::Value as SqlValue, Transaction};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest as _, Sha256};
use std::collections::BTreeSet;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ExportEntryKind {
    Scope,
    Grant,
    Record,
    Revision,
    EpisodeDetail,
    SmartNoteDetail,
    SummaryDetail,
    Source,
    Provenance,
    Lineage,
    Verification,
    Mutation,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct MemoryExportEntry {
    pub item_key: String,
    pub kind: ExportEntryKind,
    pub payload: Value,
    pub item_digest: Digest,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct MemoryExportManifest {
    pub export_id: Id,
    pub schema_version: u64,
    pub scope: Scope,
    pub record_count: u64,
    pub item_count: u64,
    pub stream_digest: Digest,
    pub created_at_ms: u64,
    pub cursor: Cursor,
    pub trace: Trace,
    pub include_grants: bool,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct MemoryExportBundle {
    pub manifest: MemoryExportManifest,
    pub entries: Vec<MemoryExportEntry>,
}

impl MemoryExportBundle {
    pub fn to_jsonl(&self) -> MemoryResult<Vec<u8>> {
        self.verify()?;
        self.to_jsonl_unchecked()
    }

    pub(crate) fn to_jsonl_unchecked(&self) -> MemoryResult<Vec<u8>> {
        let mut bytes = canonical_line(&serde_json::json!({
            "kind": "manifest",
            "manifest": self.manifest,
        }))?;
        for entry in &self.entries {
            bytes.extend(canonical_line(&serde_json::json!({
                "kind": "entry",
                "entry": entry,
            }))?);
        }
        Ok(bytes)
    }

    pub fn verify(&self) -> MemoryResult<()> {
        if self.entries.len() as u64 != self.manifest.item_count {
            return Err(invalid("export item count does not match the manifest"));
        }
        let mut keys = BTreeSet::new();
        for entry in &self.entries {
            if !keys.insert(&entry.item_key) {
                return Err(invalid("export entry keys are not unique"));
            }
            if Digest::sha256(canonical_json(&entry.payload)?) != entry.item_digest {
                return Err(invalid("export entry digest mismatch"));
            }
            reject_forbidden(&entry.payload)?;
        }
        if stream_digest(&self.entries)? != self.manifest.stream_digest {
            return Err(invalid("export stream digest mismatch"));
        }
        Ok(())
    }
}

pub fn export_scope(
    store: &mut MemoryStore,
    context: &AuthContext,
    request: &MutationRequest,
    export_id: Id,
    include_grants: bool,
    created_at_ms: u64,
) -> MemoryResult<MemoryExportBundle> {
    if request.operation != Operation::Export || context.request.now_ms != created_at_ms {
        return Err(invalid("export authorization does not match the request"));
    }
    store.immediate_authorized(context, request, |memory_tx, authorization| {
        memory_tx.reauthorize(context, request, authorization)?;
        let tx = memory_tx.raw();
        let owner_digest = scope_digest(&request.target_scope).to_hex();
        let cursor = memory_tx.cursor(&request.target_scope)?;
        let entries = canonical_scope_entries(tx, &owner_digest, include_grants)?;
        let record_count = entries
            .iter()
            .filter(|entry| entry.kind == ExportEntryKind::Record)
            .count() as u64;
        let manifest = MemoryExportManifest {
            export_id: export_id.clone(),
            schema_version: crate::MEMORY_SCHEMA_VERSION,
            scope: request.target_scope.clone(),
            record_count,
            item_count: entries.len() as u64,
            stream_digest: stream_digest(&entries)?,
            created_at_ms,
            cursor,
            trace: request.trace.clone(),
            include_grants,
        };
        tx.execute(
            "INSERT INTO export_manifests(
                export_id, owner_scope_digest, actor_scope_digest, schema_version,
                cursor_json, stream_digest, record_count, include_grants, created_at_ms
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            params![
                export_id.as_str(),
                owner_digest,
                authorization.actor_scope_digest.to_hex(),
                crate::MEMORY_SCHEMA_VERSION,
                serde_json::to_string(&cursor).unwrap(),
                manifest.stream_digest.to_hex(),
                record_count,
                include_grants,
                created_at_ms,
            ],
        )
        .map_err(sql_error)?;
        let bundle = MemoryExportBundle { manifest, entries };
        bundle.verify()?;
        Ok(bundle)
    })
}

pub(crate) fn canonical_scope_entries(
    tx: &Transaction<'_>,
    owner_digest: &str,
    include_grants: bool,
) -> MemoryResult<Vec<MemoryExportEntry>> {
    let mut entries = Vec::new();
    collect(
        tx,
        &mut entries,
        ExportEntryKind::Scope,
        "scope",
        "SELECT scope_digest, json_object(
             'scope_digest', scope_digest, 'scope', json(scope_json),
             'epoch', epoch, 'sequence', sequence,
             'created_at_ms', created_at_ms, 'updated_at_ms', updated_at_ms)
         FROM memory_scopes WHERE scope_digest=?1 ORDER BY scope_digest",
        &[SqlValue::Text(owner_digest.to_owned())],
    )?;
    collect(
        tx,
        &mut entries,
        ExportEntryKind::Grant,
        "grant",
        "SELECT grant_id, json_object(
                 'grant_id', grant_id, 'owner_scope_digest', owner_scope_digest,
                 'grantee_scope_digest', grantee_scope_digest,
                 'operations', json(operations_json), 'categories', json(categories_json),
                 'granted_at_ms', granted_at_ms, 'expires_at_ms', expires_at_ms,
                 'revoked_at_ms', revoked_at_ms, 'revision', revision)
             FROM memory_share_grants
             WHERE owner_scope_digest=?1 AND (
                ?2=1 OR grant_id IN (
                    SELECT grant_id FROM memory_mutation_events
                    WHERE owner_scope_digest=?1 AND grant_id IS NOT NULL))
             ORDER BY grant_id",
        &[
            SqlValue::Text(owner_digest.to_owned()),
            SqlValue::Integer(if include_grants { 1 } else { 0 }),
        ],
    )?;
    collect_scope_rows(tx, &mut entries, owner_digest)?;
    Ok(entries)
}

fn collect_scope_rows(
    tx: &Transaction<'_>,
    entries: &mut Vec<MemoryExportEntry>,
    owner: &str,
) -> MemoryResult<()> {
    let owner_param = [SqlValue::Text(owner.to_owned())];
    collect(tx, entries, ExportEntryKind::Record, "record",
        "SELECT record_id, json_object(
            'record_id',record_id,'owner_scope_digest',owner_scope_digest,'kind',kind,
            'category',category,'status',status,'current_revision',current_revision,
            'current_revision_digest',current_revision_digest,
            'normalized_content_digest',normalized_content_digest,'importance',importance,
            'confidence',confidence,'verification_state',verification_state,
            'valid_from_ms',valid_from_ms,'valid_to_ms',valid_to_ms,
            'observed_from_ms',observed_from_ms,'observed_to_ms',observed_to_ms,
            'expires_at_ms',expires_at_ms,'retention_until_ms',retention_until_ms,
            'created_at_ms',created_at_ms,'updated_at_ms',updated_at_ms,'deleted_at_ms',deleted_at_ms)
         FROM memory_records WHERE owner_scope_digest=?1 ORDER BY record_id", &owner_param)?;
    collect(tx, entries, ExportEntryKind::Revision, "revision",
        "SELECT r.record_id || ':' || printf('%020d',v.revision), json_object(
            'record_id',v.record_id,'revision',v.revision,'revision_digest',v.revision_digest,
            'parent_revision_digest',v.parent_revision_digest,'content',v.content,
            'content_digest',v.content_digest,'metadata',json(v.metadata_json),
            'smart_predicate',json(v.smart_predicate_json),'author_scope_digest',v.author_scope_digest,
            'authored_at_ms',v.authored_at_ms,'immutable_anchor',v.immutable_anchor)
         FROM memory_revisions v JOIN memory_records r ON r.record_id=v.record_id
         WHERE r.owner_scope_digest=?1 ORDER BY v.record_id,v.revision", &owner_param)?;
    collect(tx, entries, ExportEntryKind::EpisodeDetail, "episode",
        "SELECT d.record_id, json_object('record_id',d.record_id,'observed_from_ms',d.observed_from_ms,
            'observed_to_ms',d.observed_to_ms,'participants',json(d.participants_json),'episode_type',d.episode_type)
         FROM episode_details d JOIN memory_records r ON r.record_id=d.record_id
         WHERE r.owner_scope_digest=?1 ORDER BY d.record_id", &owner_param)?;
    collect(tx, entries, ExportEntryKind::SmartNoteDetail, "smart_note",
        "SELECT d.record_id, json_object('record_id',d.record_id,'predicate',json(d.predicate_json),
            'predicate_digest',d.predicate_digest,'last_evaluated_cursor',json(d.last_evaluated_cursor_json),
            'last_result',d.last_result,'next_evaluation_at_ms',d.next_evaluation_at_ms)
         FROM smart_note_details d JOIN memory_records r ON r.record_id=d.record_id
         WHERE r.owner_scope_digest=?1 ORDER BY d.record_id", &owner_param)?;
    collect(tx, entries, ExportEntryKind::SummaryDetail, "summary",
        "SELECT d.record_id, json_object('record_id',d.record_id,'input_set_digest',d.input_set_digest,
            'summary_level',d.summary_level,'decay_half_life_ms',d.decay_half_life_ms,
            'refreshed_at_ms',d.refreshed_at_ms,'stale_at_ms',d.stale_at_ms)
         FROM summary_details d JOIN memory_records r ON r.record_id=d.record_id
         WHERE r.owner_scope_digest=?1 ORDER BY d.record_id", &owner_param)?;
    collect(tx, entries, ExportEntryKind::Source, "source",
        "SELECT source_id, json_object('source_id',source_id,'owner_scope_digest',owner_scope_digest,
            'source_kind',source_kind,'source_digest',source_digest,'locator',locator,
            'captured_content',captured_content,'capture_method',capture_method,
            'observed_at_ms',observed_at_ms,'created_at_ms',created_at_ms)
         FROM memory_sources WHERE owner_scope_digest=?1 ORDER BY source_id", &owner_param)?;
    collect(tx, entries, ExportEntryKind::Provenance, "provenance",
        "SELECT p.record_id || ':' || printf('%020d',p.revision) || ':' || p.source_id || ':' || printf('%020d',p.span_start),
            json_object('record_id',p.record_id,'revision',p.revision,'source_id',p.source_id,
            'span_start',p.span_start,'span_end',p.span_end,'quoted_digest',p.quoted_digest)
         FROM memory_provenance p JOIN memory_records r ON r.record_id=p.record_id
         WHERE r.owner_scope_digest=?1 ORDER BY p.record_id,p.revision,p.source_id,p.span_start", &owner_param)?;
    collect(tx, entries, ExportEntryKind::Lineage, "lineage",
        "SELECT l.child_record_id || ':' || printf('%020d',l.child_revision) || ':' || l.parent_record_id || ':' || l.relation,
            json_object('child_record_id',l.child_record_id,'child_revision',l.child_revision,
            'parent_record_id',l.parent_record_id,'parent_revision_digest',l.parent_revision_digest,
            'relation',l.relation,'created_at_ms',l.created_at_ms)
         FROM memory_lineage l JOIN memory_records r ON r.record_id=l.child_record_id
         WHERE r.owner_scope_digest=?1 AND EXISTS(
             SELECT 1 FROM memory_records p WHERE p.record_id=l.parent_record_id AND p.owner_scope_digest=?1)
         ORDER BY l.child_record_id,l.child_revision,l.parent_record_id,l.relation", &owner_param)?;
    collect(
        tx,
        entries,
        ExportEntryKind::Verification,
        "verification",
        "SELECT e.event_id, json_object('event_id',e.event_id,'record_id',e.record_id,
            'revision_digest',e.revision_digest,'actor_scope_digest',e.actor_scope_digest,
            'state',e.state,'evidence_source_id',e.evidence_source_id,'confidence',e.confidence,
            'created_at_ms',e.created_at_ms)
         FROM memory_verification_events e JOIN memory_records r ON r.record_id=e.record_id
         WHERE r.owner_scope_digest=?1 ORDER BY e.created_at_ms,e.event_id",
        &owner_param,
    )?;
    collect(tx, entries, ExportEntryKind::Mutation, "mutation",
        "SELECT event_id, json_object('event_id',event_id,'owner_scope_digest',owner_scope_digest,
            'epoch',epoch,'sequence',sequence,'operation',operation,'record_id',record_id,
            'previous_revision_digest',previous_revision_digest,'result_revision_digest',result_revision_digest,
            'actor_scope_digest',actor_scope_digest,'grant_id',grant_id,
            'authorization_basis',authorization_basis,'trace',json(trace_json),'created_at_ms',created_at_ms)
         FROM memory_mutation_events WHERE owner_scope_digest=?1 ORDER BY epoch,sequence,event_id", &owner_param)?;
    Ok(())
}

fn collect(
    tx: &Transaction<'_>,
    entries: &mut Vec<MemoryExportEntry>,
    kind: ExportEntryKind,
    prefix: &str,
    sql: &str,
    parameters: &[SqlValue],
) -> MemoryResult<()> {
    let mut statement = tx.prepare(sql).map_err(sql_error)?;
    let rows = statement
        .query_map(params_from_iter(parameters.iter()), |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
        })
        .map_err(sql_error)?;
    for row in rows {
        let (key, payload) = row.map_err(sql_error)?;
        let payload: Value = serde_json::from_str(&payload)
            .map_err(|_| corrupt("authoritative export row is not valid JSON"))?;
        reject_forbidden(&payload)?;
        entries.push(MemoryExportEntry {
            item_key: format!("{prefix}:{key}"),
            kind: kind.clone(),
            item_digest: Digest::sha256(canonical_json(&payload)?),
            payload,
        });
    }
    Ok(())
}

pub(crate) fn canonical_json(value: &Value) -> MemoryResult<Vec<u8>> {
    serde_json::to_vec(value).map_err(|_| invalid("value cannot be encoded as canonical JSON"))
}

fn canonical_line(value: &Value) -> MemoryResult<Vec<u8>> {
    let mut bytes = canonical_json(value)?;
    bytes.push(b'\n');
    Ok(bytes)
}

pub(crate) fn stream_digest(entries: &[MemoryExportEntry]) -> MemoryResult<Digest> {
    let mut hasher = Sha256::new();
    hasher.update(b"hypermid.memory.export.v1\0");
    for entry in entries {
        let encoded = canonical_json(&serde_json::json!({
            "item_key": entry.item_key,
            "kind": entry.kind,
            "payload": entry.payload,
            "item_digest": entry.item_digest,
        }))?;
        hasher.update((encoded.len() as u64).to_be_bytes());
        hasher.update(encoded);
    }
    Ok(Digest::from_bytes(hasher.finalize().into()))
}

pub(crate) fn reject_forbidden(value: &Value) -> MemoryResult<()> {
    const FORBIDDEN: &[&str] = &[
        "vector",
        "vector_f32",
        "embedding",
        "fts",
        "lease",
        "fencing_token",
        "credential",
        "secret",
        "api_key",
        "access_token",
        "refresh_token",
        "job_claim",
    ];
    match value {
        Value::Object(values) => {
            for (key, child) in values {
                let lowered = key.to_ascii_lowercase();
                if FORBIDDEN.iter().any(|term| lowered.contains(term)) {
                    return Err(invalid(
                        "export contains derivative or secret-bearing state",
                    ));
                }
                reject_forbidden(child)?;
            }
        }
        Value::Array(values) => {
            for child in values {
                reject_forbidden(child)?;
            }
        }
        _ => {}
    }
    Ok(())
}

fn invalid(message: &'static str) -> hypermid_contracts::Error {
    error("INVALID_EXPORT", message, EffectState::NotStarted)
}

fn corrupt(message: &'static str) -> hypermid_contracts::Error {
    error("STORE_CORRUPT", message, EffectState::Unknown)
}

fn sql_error(source: rusqlite::Error) -> hypermid_contracts::Error {
    error(
        "EXPORT_FAILED",
        format!("memory export failed: {source}"),
        EffectState::Unknown,
    )
}
