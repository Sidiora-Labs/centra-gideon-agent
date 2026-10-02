use crate::export::{
    canonical_json, canonical_scope_entries, reject_forbidden, stream_digest, ExportEntryKind,
    MemoryExportBundle, MemoryExportEntry,
};
use crate::model::{scope_digest, MutationRequest, Operation};
use crate::{
    error, AuthContext, Digest, EffectState, Id, MemoryResult, MemoryStore, MemoryTransaction,
    Scope, Trace,
};
use rusqlite::{params, OptionalExtension};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct MemoryImportManifest {
    pub schema_version: u64,
    pub source_digest: Digest,
    pub source_scope: Scope,
    pub target_scope: Scope,
    pub item_count: u64,
    pub scope_mapping: BTreeMap<String, Scope>,
    pub created_at_ms: u64,
    pub trace: Trace,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ImportState {
    Staged,
    Validated,
    Applied,
    Rejected,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct MemoryImportBatch {
    pub batch_id: Id,
    pub manifest: MemoryImportManifest,
    pub state: ImportState,
    pub rejected: Vec<ImportRejection>,
    pub replayed: bool,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ImportRejection {
    pub item_key: String,
    pub error_code: String,
}

pub fn stage_import(
    store: &mut MemoryStore,
    context: &AuthContext,
    request: &MutationRequest,
    batch_id: Id,
    bundle: &MemoryExportBundle,
    target_scope: Scope,
    scope_mapping: BTreeMap<String, Scope>,
    created_at_ms: u64,
) -> MemoryResult<MemoryImportBatch> {
    if request.operation != Operation::Import
        || request.target_scope != target_scope
        || context.request.now_ms != created_at_ms
    {
        return Err(invalid(
            "import authorization does not match the destination",
        ));
    }
    if bundle.manifest.schema_version > crate::MEMORY_SCHEMA_VERSION {
        return Err(invalid("import schema is newer than this runtime"));
    }
    let source_scope_digest = scope_digest(&bundle.manifest.scope).to_hex();
    if bundle.manifest.scope != target_scope
        && scope_mapping.get(&source_scope_digest) != Some(&target_scope)
    {
        return Err(denied(
            "scope relocation requires an explicit destination mapping",
        ));
    }
    let source_bytes = bundle.to_jsonl_unchecked()?;
    let source_digest = Digest::sha256(&source_bytes);
    let manifest = MemoryImportManifest {
        schema_version: bundle.manifest.schema_version,
        source_digest,
        source_scope: bundle.manifest.scope.clone(),
        target_scope: target_scope.clone(),
        item_count: bundle.entries.len() as u64,
        scope_mapping,
        created_at_ms,
        trace: request.trace.clone(),
    };
    store.immediate_authorized(context, request, |tx, authorization| {
        tx.reauthorize(context, request, authorization)?;
        tx.ensure_scope(&target_scope, as_i64(created_at_ms)?)?;
        let target_digest = scope_digest(&target_scope).to_hex();
        if let Some((existing_id, state, stored_manifest)) = tx
            .raw()
            .query_row(
                "SELECT batch_id,state,manifest_json FROM import_batches
                 WHERE target_scope_digest=?1 AND source_digest=?2",
                params![target_digest, source_digest.to_hex()],
                |row| {
                    Ok((
                        row.get::<_, String>(0)?,
                        row.get::<_, String>(1)?,
                        row.get::<_, String>(2)?,
                    ))
                },
            )
            .optional()
            .map_err(sql_error)?
        {
            let existing_id = Id::new(existing_id)
                .map_err(|_| corrupt("import batch id is invalid"))?;
            let stored_manifest = serde_json::from_str(&stored_manifest)
                .map_err(|_| corrupt("import manifest is invalid"))?;
            return Ok(MemoryImportBatch {
                batch_id: existing_id.clone(),
                manifest: stored_manifest,
                state: parse_state(&state)?,
                rejected: load_rejections(tx, &existing_id)?,
                replayed: true,
            });
        }
        let mut keys = BTreeSet::new();
        let mut rejected = Vec::new();
        if bundle.entries.len() as u64 != bundle.manifest.item_count
            || stream_digest(&bundle.entries)? != bundle.manifest.stream_digest
        {
            rejected.push(ImportRejection {
                item_key: "__manifest__".to_owned(),
                error_code: "MANIFEST_DIGEST_MISMATCH".to_owned(),
            });
        }
        for entry in &bundle.entries {
            let error_code = if !keys.insert(entry.item_key.clone()) {
                Some("DUPLICATE_ITEM")
            } else if reject_forbidden(&entry.payload).is_err() {
                Some("FORBIDDEN_STATE")
            } else if Digest::sha256(canonical_json(&entry.payload)?) != entry.item_digest {
                Some("ITEM_DIGEST_MISMATCH")
            } else {
                None
            };
            if let Some(code) = error_code {
                rejected.push(ImportRejection {
                    item_key: entry.item_key.clone(),
                    error_code: code.to_owned(),
                });
            }
        }
        let state = if rejected.is_empty() { "validated" } else { "rejected" };
        let manifest_json = serde_json::to_string(&manifest)
            .map_err(|_| invalid("import manifest could not be encoded"))?;
        tx.raw().execute(
            "INSERT INTO import_batches(
                batch_id,source_digest,target_scope_digest,actor_scope_digest,
                schema_version,manifest_json,state,item_count,created_at_ms)
             VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9)",
            params![batch_id.as_str(),source_digest.to_hex(),target_digest,
                authorization.actor_scope_digest.to_hex(),manifest.schema_version,
                manifest_json,state,manifest.item_count,created_at_ms],
        ).map_err(sql_error)?;
        for entry in &bundle.entries {
            let rejection = rejected.iter().find(|item| item.item_key == entry.item_key);
            tx.raw().execute(
                "INSERT INTO import_items(batch_id,item_key,item_digest,payload_json,state,error_code)
                 VALUES (?1,?2,?3,?4,?5,?6)",
                params![batch_id.as_str(),entry.item_key,entry.item_digest.to_hex(),
                    serde_json::to_string(&entry.payload).unwrap(),
                    if rejection.is_some() { "rejected" } else { "valid" },
                    rejection.map(|item| item.error_code.as_str())],
            ).map_err(sql_error)?;
        }
        if rejected.iter().any(|item| item.item_key == "__manifest__") {
            let payload = serde_json::to_value(&bundle.manifest)
                .map_err(|_| invalid("import manifest could not be encoded"))?;
            tx.raw().execute(
                "INSERT INTO import_items(batch_id,item_key,item_digest,payload_json,state,error_code)
                 VALUES (?1,'__manifest__',?2,?3,'rejected','MANIFEST_DIGEST_MISMATCH')",
                params![
                    batch_id.as_str(),
                    Digest::sha256(canonical_json(&payload)?).to_hex(),
                    serde_json::to_string(&payload).unwrap(),
                ],
            ).map_err(sql_error)?;
        }
        Ok(MemoryImportBatch {
            batch_id,
            manifest,
            state: if rejected.is_empty() { ImportState::Validated } else { ImportState::Rejected },
            rejected,
            replayed: false,
        })
    })
}

pub fn apply_import(
    store: &mut MemoryStore,
    context: &AuthContext,
    request: &MutationRequest,
    batch: &MemoryImportBatch,
    bundle: &MemoryExportBundle,
    applied_at_ms: u64,
) -> MemoryResult<MemoryImportBatch> {
    let (batch, ()) = apply_import_with_finalizer(
        store,
        context,
        request,
        batch,
        bundle,
        applied_at_ms,
        |_, _| Ok(()),
    )?;
    Ok(batch)
}

pub(crate) fn apply_import_with_finalizer<T>(
    store: &mut MemoryStore,
    context: &AuthContext,
    request: &MutationRequest,
    batch: &MemoryImportBatch,
    bundle: &MemoryExportBundle,
    applied_at_ms: u64,
    finalize: impl FnOnce(&MemoryTransaction<'_>, bool) -> MemoryResult<T>,
) -> MemoryResult<(MemoryImportBatch, T)> {
    if batch.state == ImportState::Rejected || request.operation != Operation::Import {
        return Err(invalid("only a validated import batch can be applied"));
    }
    if context.request.now_ms != applied_at_ms
        || request.target_scope != batch.manifest.target_scope
        || batch.manifest.source_digest != Digest::sha256(bundle.to_jsonl_unchecked()?)
    {
        return Err(invalid("import source or authorization time changed"));
    }
    store.immediate_authorized(context, request, |tx, authorization| {
        tx.reauthorize(context, request, authorization)?;
        let state: String = tx
            .raw()
            .query_row(
                "SELECT state FROM import_batches WHERE batch_id=?1 AND source_digest=?2",
                params![
                    batch.batch_id.as_str(),
                    batch.manifest.source_digest.to_hex()
                ],
                |row| row.get(0),
            )
            .map_err(sql_error)?;
        if state == "applied" {
            let mut replay = batch.clone();
            replay.state = ImportState::Applied;
            replay.replayed = true;
            let finalized = finalize(tx, true)?;
            return Ok((replay, finalized));
        }
        if state != "validated" {
            return Err(invalid("import batch is not validated"));
        }
        let source_digest = scope_digest(&batch.manifest.source_scope).to_hex();
        let target_digest = scope_digest(&batch.manifest.target_scope).to_hex();
        tx.ensure_scope(&batch.manifest.target_scope, as_i64(applied_at_ms)?)?;
        let missing = preflight_entries(tx, bundle, &source_digest, &target_digest)?;
        for entry in &missing {
            apply_entry(
                tx,
                &entry.item_key,
                &entry.payload,
                &source_digest,
                &target_digest,
            )?;
        }
        let current_cursor = tx.cursor(&batch.manifest.target_scope)?;
        if missing.is_empty() {
            if current_cursor != bundle.manifest.cursor {
                return Err(conflict(
                    "identical import rows have a different destination cursor",
                ));
            }
        } else {
            if current_cursor > bundle.manifest.cursor {
                return Err(conflict(
                    "import would regress the destination scope cursor",
                ));
            }
            tx.raw()
                .execute(
                    "UPDATE memory_scopes
                     SET epoch=?2, sequence=?3, updated_at_ms=MAX(updated_at_ms,?4)
                     WHERE scope_digest=?1",
                    params![
                        target_digest,
                        bundle.manifest.cursor.epoch,
                        bundle.manifest.cursor.sequence,
                        applied_at_ms,
                    ],
                )
                .map_err(sql_error)?;
        }
        tx.raw()
            .execute(
                "UPDATE import_items SET state='applied' WHERE batch_id=?1 AND state='valid'",
                [batch.batch_id.as_str()],
            )
            .map_err(sql_error)?;
        let finalized = finalize(tx, false)?;
        tx.raw()
            .execute(
                "UPDATE import_batches SET state='applied',applied_at_ms=?2
             WHERE batch_id=?1 AND state='validated'",
                params![batch.batch_id.as_str(), applied_at_ms],
            )
            .map_err(sql_error)?;
        let mut applied = batch.clone();
        applied.state = ImportState::Applied;
        applied.replayed = false;
        Ok((applied, finalized))
    })
}

fn preflight_entries<'a>(
    transaction: &MemoryTransaction<'_>,
    bundle: &'a MemoryExportBundle,
    source_scope: &str,
    target_scope: &str,
) -> MemoryResult<Vec<&'a MemoryExportEntry>> {
    let destination = canonical_scope_entries(transaction.raw(), target_scope, true)?
        .into_iter()
        .filter(|entry| entry.kind != ExportEntryKind::Scope)
        .map(|entry| (entry.item_key.clone(), entry))
        .collect::<BTreeMap<_, _>>();
    let expected_keys = bundle
        .entries
        .iter()
        .filter(|entry| entry.kind != ExportEntryKind::Scope)
        .map(|entry| entry.item_key.as_str())
        .collect::<BTreeSet<_>>();
    if destination.iter().any(|(item_key, entry)| {
        entry.kind != ExportEntryKind::Grant && !expected_keys.contains(item_key.as_str())
    }) {
        return Err(conflict(
            "destination contains authoritative rows outside the import bundle",
        ));
    }

    let mut missing = Vec::new();
    for entry in bundle
        .entries
        .iter()
        .filter(|entry| entry.kind != ExportEntryKind::Scope)
    {
        let expected_payload = mapped_payload(entry, source_scope, target_scope)?;
        let expected_digest = Digest::sha256(canonical_json(&expected_payload)?);
        match destination.get(&entry.item_key) {
            Some(current)
                if current.kind == entry.kind
                    && current.item_digest == expected_digest
                    && current.payload == expected_payload => {}
            Some(_) => {
                return Err(conflict(
                    "destination row conflicts with the canonical import entry",
                ))
            }
            None => missing.push(entry),
        }
    }
    Ok(missing)
}

fn mapped_payload(
    entry: &MemoryExportEntry,
    source_scope: &str,
    target_scope: &str,
) -> MemoryResult<Value> {
    let mut payload = entry.payload.clone();
    if source_scope == target_scope {
        return Ok(payload);
    }
    let object = payload
        .as_object_mut()
        .ok_or_else(|| invalid("import payload must be an object"))?;
    for key in [
        "owner_scope_digest",
        "author_scope_digest",
        "actor_scope_digest",
        "grantee_scope_digest",
    ] {
        if object.get(key).and_then(Value::as_str) == Some(source_scope) {
            object.insert(key.to_owned(), Value::String(target_scope.to_owned()));
        }
    }
    Ok(payload)
}

fn apply_entry(
    tx: &MemoryTransaction<'_>,
    item_key: &str,
    payload: &Value,
    source_scope: &str,
    target_scope: &str,
) -> MemoryResult<()> {
    let p = payload
        .as_object()
        .ok_or_else(|| invalid("import payload must be an object"))?;
    let kind = item_key.split(':').next().unwrap_or_default();
    match kind {
        "scope" => return Ok(()),
        "record" => tx.raw().execute(
            "INSERT INTO memory_records VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,?15,?16,?17,?18,?19,?20)",
            params![s(p,"record_id")?,map_scope(p,"owner_scope_digest",source_scope,target_scope)?,s(p,"kind")?,s(p,"category")?,s(p,"status")?,u(p,"current_revision")?,s(p,"current_revision_digest")?,s(p,"normalized_content_digest")?,f(p,"importance")?,f(p,"confidence")?,s(p,"verification_state")?,oi(p,"valid_from_ms")?,oi(p,"valid_to_ms")?,oi(p,"observed_from_ms")?,oi(p,"observed_to_ms")?,oi(p,"expires_at_ms")?,oi(p,"retention_until_ms")?,u(p,"created_at_ms")?,u(p,"updated_at_ms")?,oi(p,"deleted_at_ms")?],
        ).map(|_|()).map_err(sql_error),
        "revision" => tx.raw().execute(
            "INSERT INTO memory_revisions VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11)",
            params![s(p,"record_id")?,u(p,"revision")?,s(p,"revision_digest")?,os(p,"parent_revision_digest")?,s(p,"content")?,s(p,"content_digest")?,json(p,"metadata")?,json_opt(p,"smart_predicate")?,map_scope(p,"author_scope_digest",source_scope,target_scope)?,u(p,"authored_at_ms")?,b(p,"immutable_anchor")?],
        ).map(|_|()).map_err(sql_error),
        "episode" => tx.raw().execute("INSERT INTO episode_details VALUES (?1,?2,?3,?4,?5)",params![s(p,"record_id")?,u(p,"observed_from_ms")?,u(p,"observed_to_ms")?,json(p,"participants")?,s(p,"episode_type")?]).map(|_|()).map_err(sql_error),
        "smart_note" => tx.raw().execute("INSERT INTO smart_note_details VALUES (?1,?2,?3,?4,?5,?6)",params![s(p,"record_id")?,json(p,"predicate")?,s(p,"predicate_digest")?,json_opt(p,"last_evaluated_cursor")?,oi(p,"last_result")?,oi(p,"next_evaluation_at_ms")?]).map(|_|()).map_err(sql_error),
        "summary" => tx.raw().execute("INSERT INTO summary_details VALUES (?1,?2,?3,?4,?5,?6)",params![s(p,"record_id")?,s(p,"input_set_digest")?,s(p,"summary_level")?,oi(p,"decay_half_life_ms")?,u(p,"refreshed_at_ms")?,oi(p,"stale_at_ms")?]).map(|_|()).map_err(sql_error),
        "source" => tx.raw().execute("INSERT INTO memory_sources VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9)",params![s(p,"source_id")?,map_scope(p,"owner_scope_digest",source_scope,target_scope)?,s(p,"source_kind")?,s(p,"source_digest")?,os(p,"locator")?,os(p,"captured_content")?,s(p,"capture_method")?,u(p,"observed_at_ms")?,u(p,"created_at_ms")?]).map(|_|()).map_err(sql_error),
        "provenance" => tx.raw().execute("INSERT INTO memory_provenance VALUES (?1,?2,?3,?4,?5,?6)",params![s(p,"record_id")?,u(p,"revision")?,s(p,"source_id")?,oi(p,"span_start")?,oi(p,"span_end")?,os(p,"quoted_digest")?]).map(|_|()).map_err(sql_error),
        "lineage" => tx.raw().execute("INSERT INTO memory_lineage VALUES (?1,?2,?3,?4,?5,?6)",params![s(p,"child_record_id")?,u(p,"child_revision")?,s(p,"parent_record_id")?,s(p,"parent_revision_digest")?,s(p,"relation")?,u(p,"created_at_ms")?]).map(|_|()).map_err(sql_error),
        "verification" => tx.raw().execute("INSERT INTO memory_verification_events VALUES (?1,?2,?3,?4,?5,?6,?7,?8)",params![s(p,"event_id")?,s(p,"record_id")?,s(p,"revision_digest")?,map_scope(p,"actor_scope_digest",source_scope,target_scope)?,s(p,"state")?,os(p,"evidence_source_id")?,f(p,"confidence")?,u(p,"created_at_ms")?]).map(|_|()).map_err(sql_error),
        "mutation" => tx.raw().execute("INSERT INTO memory_mutation_events VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13)",params![s(p,"event_id")?,map_scope(p,"owner_scope_digest",source_scope,target_scope)?,u(p,"epoch")?,u(p,"sequence")?,s(p,"operation")?,os(p,"record_id")?,os(p,"previous_revision_digest")?,os(p,"result_revision_digest")?,map_scope(p,"actor_scope_digest",source_scope,target_scope)?,os(p,"grant_id")?,s(p,"authorization_basis")?,json(p,"trace")?,u(p,"created_at_ms")?]).map(|_|()).map_err(sql_error),
        "grant" => tx.raw().execute("INSERT INTO memory_share_grants VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9)",params![s(p,"grant_id")?,map_scope(p,"owner_scope_digest",source_scope,target_scope)?,map_scope(p,"grantee_scope_digest",source_scope,target_scope)?,json(p,"operations")?,json_opt(p,"categories")?,u(p,"granted_at_ms")?,oi(p,"expires_at_ms")?,oi(p,"revoked_at_ms")?,u(p,"revision")?]).map(|_|()).map_err(sql_error),
        _ => Err(invalid("import entry kind is unsupported")),
    }
}

fn load_rejections(
    tx: &MemoryTransaction<'_>,
    batch_id: &Id,
) -> MemoryResult<Vec<ImportRejection>> {
    let mut statement = tx.raw().prepare("SELECT item_key,error_code FROM import_items WHERE batch_id=?1 AND state='rejected' ORDER BY item_key").map_err(sql_error)?;
    let rows = statement
        .query_map([batch_id.as_str()], |row| {
            Ok(ImportRejection {
                item_key: row.get(0)?,
                error_code: row.get(1)?,
            })
        })
        .map_err(sql_error)?;
    rows.collect::<Result<Vec<_>, _>>().map_err(sql_error)
}

fn s<'a>(p: &'a Map<String, Value>, key: &str) -> MemoryResult<&'a str> {
    p.get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| invalid("import string field is missing"))
}
fn os<'a>(p: &'a Map<String, Value>, key: &str) -> MemoryResult<Option<&'a str>> {
    Ok(match p.get(key) {
        Some(Value::Null) | None => None,
        Some(v) => Some(
            v.as_str()
                .ok_or_else(|| invalid("import optional string is invalid"))?,
        ),
    })
}
fn u(p: &Map<String, Value>, key: &str) -> MemoryResult<u64> {
    p.get(key)
        .and_then(Value::as_u64)
        .ok_or_else(|| invalid("import integer field is missing"))
}
fn oi(p: &Map<String, Value>, key: &str) -> MemoryResult<Option<i64>> {
    Ok(match p.get(key) {
        Some(Value::Null) | None => None,
        Some(v) => Some(
            v.as_i64()
                .ok_or_else(|| invalid("import optional integer is invalid"))?,
        ),
    })
}
fn f(p: &Map<String, Value>, key: &str) -> MemoryResult<f64> {
    p.get(key)
        .and_then(Value::as_f64)
        .filter(|v| v.is_finite())
        .ok_or_else(|| invalid("import number field is missing"))
}
fn b(p: &Map<String, Value>, key: &str) -> MemoryResult<bool> {
    p.get(key)
        .and_then(|v| match v {
            Value::Bool(v) => Some(*v),
            Value::Number(v) => v.as_u64().map(|v| v != 0),
            _ => None,
        })
        .ok_or_else(|| invalid("import boolean field is missing"))
}
fn json(p: &Map<String, Value>, key: &str) -> MemoryResult<String> {
    serde_json::to_string(
        p.get(key)
            .ok_or_else(|| invalid("import JSON field is missing"))?,
    )
    .map_err(|_| invalid("import JSON field is invalid"))
}
fn json_opt(p: &Map<String, Value>, key: &str) -> MemoryResult<Option<String>> {
    match p.get(key) {
        Some(Value::Null) | None => Ok(None),
        Some(v) => serde_json::to_string(v)
            .map(Some)
            .map_err(|_| invalid("import JSON field is invalid")),
    }
}
fn map_scope(
    p: &Map<String, Value>,
    key: &str,
    source: &str,
    target: &str,
) -> MemoryResult<String> {
    let value = s(p, key)?;
    Ok(if value == source {
        target.to_owned()
    } else {
        value.to_owned()
    })
}
fn as_i64(value: u64) -> MemoryResult<i64> {
    i64::try_from(value).map_err(|_| invalid("timestamp is too large"))
}
fn parse_state(value: &str) -> MemoryResult<ImportState> {
    match value {
        "staged" => Ok(ImportState::Staged),
        "validated" => Ok(ImportState::Validated),
        "applied" => Ok(ImportState::Applied),
        "rejected" => Ok(ImportState::Rejected),
        _ => Err(corrupt("import state is invalid")),
    }
}
fn invalid(message: &'static str) -> hypermid_contracts::Error {
    error("INVALID_IMPORT", message, EffectState::NotStarted)
}
fn denied(message: &'static str) -> hypermid_contracts::Error {
    error("AUTHORIZATION_DENIED", message, EffectState::NotStarted)
}
fn conflict(message: &'static str) -> hypermid_contracts::Error {
    error("IMPORT_CONFLICT", message, EffectState::NotStarted)
}
fn corrupt(message: &'static str) -> hypermid_contracts::Error {
    error("STORE_CORRUPT", message, EffectState::Unknown)
}
fn sql_error(source: rusqlite::Error) -> hypermid_contracts::Error {
    error(
        "IMPORT_FAILED",
        format!("memory import failed: {source}"),
        EffectState::Unknown,
    )
}

#[cfg(test)]
mod portability_tests {
    use super::*;
    use crate::export::export_scope;
    use crate::records::{RecordDraft, RecordKind};
    use crate::{
        capability_operation, AuthenticatedPrincipal, AuthorizationRequest, CapabilityGrant,
        CapabilityOperation, PrincipalKind, RevisionPrecondition,
    };
    use hypermid_store::authorization::put_grant;
    use std::collections::BTreeSet;

    fn id(value: &str) -> Id {
        Id::new(value).unwrap()
    }

    fn scope() -> Scope {
        Scope::new(id("owner-portability"), id("project-portability"), None)
    }

    fn principal() -> AuthenticatedPrincipal {
        AuthenticatedPrincipal {
            principal_id: id("principal-portability"),
            owner_id: id("owner-portability"),
            kind: PrincipalKind::Foreground,
        }
    }

    fn request(
        target: &Scope,
        operation: Operation,
        resource: &str,
        suffix: &str,
    ) -> MutationRequest {
        MutationRequest {
            operation,
            actor_scope: target.clone(),
            target_scope: target.clone(),
            record_id: Some(id(resource)),
            category: if operation == Operation::Create {
                Some("project_fact".to_owned())
            } else {
                None
            },
            revision: RevisionPrecondition::MustNotExist,
            trace: Trace::new(
                id(&format!("trace-{suffix}")),
                id(&format!("request-{suffix}")),
            ),
        }
    }

    fn context(
        target: &Scope,
        operation: Operation,
        resource: &str,
        capability_id: &Id,
        now_ms: u64,
    ) -> AuthContext {
        AuthContext {
            principal: principal(),
            request: AuthorizationRequest {
                claimed_scope: target.clone(),
                target_scope: target.clone(),
                operation: capability_operation(operation),
                resource_id: id(resource),
                now_ms,
            },
            capability_id: capability_id.clone(),
        }
    }

    fn install_capability(store: &mut MemoryStore, target: &Scope, capability_id: &Id) {
        let grant = CapabilityGrant {
            capability_id: capability_id.clone(),
            issuer_owner_id: target.owner_id.clone(),
            principal_id: principal().principal_id,
            claimed_scope: target.clone(),
            target_scope: target.clone(),
            operations: BTreeSet::from([CapabilityOperation::Append, CapabilityOperation::Export]),
            resources: BTreeSet::from([
                id("record-portability"),
                id("export-portability"),
                id("export-conflict-before"),
                id("export-conflict-after"),
                id("batch-portability"),
                id("batch-same-scope"),
                id("batch-conflict"),
                id("batch-rejected"),
            ]),
            expires_at_ms: 10_000,
        };
        store
            .immediate(|transaction| {
                put_grant(transaction.raw(), &principal(), &grant).unwrap();
                Ok(())
            })
            .unwrap();
    }

    #[test]
    fn same_scope_import_is_atomic_idempotent_and_rejects_conflicts() {
        let directory = tempfile::tempdir().unwrap();
        let target = scope();
        let capability_id = id("cap-portability");
        let mut source = MemoryStore::open(directory.path().join("source.sqlite3")).unwrap();
        install_capability(&mut source, &target, &capability_id);

        let create_request = request(&target, Operation::Create, "record-portability", "create");
        let create_context = context(
            &target,
            Operation::Create,
            "record-portability",
            &capability_id,
            100,
        );
        source
            .create_record(
                &create_context,
                &create_request,
                &RecordDraft {
                    id: id("record-portability"),
                    scope: target.clone(),
                    kind: RecordKind::Fact,
                    category: "project_fact".to_owned(),
                    content: "authoritative portable memory".to_owned(),
                    metadata: Map::new(),
                    smart_predicate: None,
                    importance: 0.8,
                    confidence: 0.9,
                    expires_at_ms: None,
                    retention_until_ms: None,
                    provenance: Vec::new(),
                    lineage: Vec::new(),
                    summary: None,
                },
                &[],
                100,
            )
            .unwrap();

        let export_request = request(&target, Operation::Export, "export-portability", "export");
        let export_context = context(
            &target,
            Operation::Export,
            "export-portability",
            &capability_id,
            200,
        );
        let bundle = export_scope(
            &mut source,
            &export_context,
            &export_request,
            id("export-portability"),
            false,
            200,
        )
        .unwrap();
        assert_eq!(bundle.manifest.record_count, 1);
        assert!(bundle.entries.iter().all(|entry| {
            !matches!(
                entry.item_key.split(':').next().unwrap(),
                "embedding" | "fts" | "lease" | "job"
            )
        }));

        let same_scope_request =
            request(&target, Operation::Import, "batch-same-scope", "same-scope");
        let same_scope_context = context(
            &target,
            Operation::Import,
            "batch-same-scope",
            &capability_id,
            250,
        );
        let same_scope_cursor = source.cursor(&target).unwrap();
        let same_scope_batch = stage_import(
            &mut source,
            &same_scope_context,
            &same_scope_request,
            id("batch-same-scope"),
            &bundle,
            target.clone(),
            BTreeMap::new(),
            250,
        )
        .unwrap();
        let same_scope_applied = apply_import(
            &mut source,
            &same_scope_context,
            &same_scope_request,
            &same_scope_batch,
            &bundle,
            250,
        )
        .unwrap();
        assert_eq!(same_scope_applied.state, ImportState::Applied);
        assert_eq!(source.cursor(&target).unwrap(), same_scope_cursor);

        let mut conflicting =
            MemoryStore::open(directory.path().join("conflicting.sqlite3")).unwrap();
        install_capability(&mut conflicting, &target, &capability_id);
        conflicting
            .create_record(
                &create_context,
                &create_request,
                &RecordDraft {
                    id: id("record-portability"),
                    scope: target.clone(),
                    kind: RecordKind::Fact,
                    category: "project_fact".to_owned(),
                    content: "divergent local memory".to_owned(),
                    metadata: Map::new(),
                    smart_predicate: None,
                    importance: 0.8,
                    confidence: 0.9,
                    expires_at_ms: None,
                    retention_until_ms: None,
                    provenance: Vec::new(),
                    lineage: Vec::new(),
                    summary: None,
                },
                &[],
                100,
            )
            .unwrap();
        let conflict_before = export_scope(
            &mut conflicting,
            &context(
                &target,
                Operation::Export,
                "export-conflict-before",
                &capability_id,
                250,
            ),
            &request(
                &target,
                Operation::Export,
                "export-conflict-before",
                "conflict-before",
            ),
            id("export-conflict-before"),
            false,
            250,
        )
        .unwrap();
        let conflict_request = request(&target, Operation::Import, "batch-conflict", "conflict");
        let conflict_context = context(
            &target,
            Operation::Import,
            "batch-conflict",
            &capability_id,
            275,
        );
        let conflict_batch = stage_import(
            &mut conflicting,
            &conflict_context,
            &conflict_request,
            id("batch-conflict"),
            &bundle,
            target.clone(),
            BTreeMap::new(),
            275,
        )
        .unwrap();
        let conflict_cursor = conflicting.cursor(&target).unwrap();
        let conflict = apply_import(
            &mut conflicting,
            &conflict_context,
            &conflict_request,
            &conflict_batch,
            &bundle,
            275,
        )
        .unwrap_err();
        assert_eq!(conflict.code, "IMPORT_CONFLICT");
        assert_eq!(conflict.effect_state, Some(EffectState::NotStarted));
        assert_eq!(conflicting.cursor(&target).unwrap(), conflict_cursor);
        let conflict_after = export_scope(
            &mut conflicting,
            &context(
                &target,
                Operation::Export,
                "export-conflict-after",
                &capability_id,
                280,
            ),
            &request(
                &target,
                Operation::Export,
                "export-conflict-after",
                "conflict-after",
            ),
            id("export-conflict-after"),
            false,
            280,
        )
        .unwrap();
        assert_eq!(
            conflict_after.manifest.stream_digest,
            conflict_before.manifest.stream_digest
        );
        assert_eq!(
            conflict_after.manifest.cursor,
            conflict_before.manifest.cursor
        );
        assert_eq!(
            conflicting
                .read(|connection| connection
                    .query_row(
                        "SELECT state FROM import_batches WHERE batch_id='batch-conflict'",
                        [],
                        |row| row.get::<_, String>(0)
                    )
                    .map_err(sql_error))
                .unwrap(),
            "validated"
        );

        let mut destination =
            MemoryStore::open(directory.path().join("destination.sqlite3")).unwrap();
        install_capability(&mut destination, &target, &capability_id);
        let import_request = request(&target, Operation::Import, "batch-portability", "import");
        let import_context = context(
            &target,
            Operation::Import,
            "batch-portability",
            &capability_id,
            300,
        );
        let staged = stage_import(
            &mut destination,
            &import_context,
            &import_request,
            id("batch-portability"),
            &bundle,
            target.clone(),
            BTreeMap::new(),
            300,
        )
        .unwrap();
        assert_eq!(staged.state, ImportState::Validated);
        assert_eq!(
            destination
                .read(|connection| connection
                    .query_row("SELECT count(*) FROM memory_records", [], |row| row
                        .get::<_, u64>(0))
                    .map_err(sql_error))
                .unwrap(),
            0
        );

        let applied = apply_import(
            &mut destination,
            &import_context,
            &import_request,
            &staged,
            &bundle,
            300,
        )
        .unwrap();
        assert_eq!(applied.state, ImportState::Applied);
        let replayed_stage = stage_import(
            &mut destination,
            &import_context,
            &import_request,
            id("batch-portability"),
            &bundle,
            target.clone(),
            BTreeMap::new(),
            300,
        )
        .unwrap();
        assert!(replayed_stage.replayed);
        let replayed_apply = apply_import(
            &mut destination,
            &import_context,
            &import_request,
            &replayed_stage,
            &bundle,
            300,
        )
        .unwrap();
        assert!(replayed_apply.replayed);
        assert_eq!(
            destination
                .read(|connection| connection
                    .query_row("SELECT count(*) FROM memory_records", [], |row| row
                        .get::<_, u64>(0))
                    .map_err(sql_error))
                .unwrap(),
            1
        );

        let mut rejected_bundle = bundle.clone();
        rejected_bundle.entries[1]
            .payload
            .as_object_mut()
            .unwrap()
            .insert(
                "api_key".to_owned(),
                Value::String("must-not-import".to_owned()),
            );
        let mut rejected_store =
            MemoryStore::open(directory.path().join("rejected.sqlite3")).unwrap();
        install_capability(&mut rejected_store, &target, &capability_id);
        let rejected_request = request(&target, Operation::Import, "batch-rejected", "rejected");
        let rejected_context = context(
            &target,
            Operation::Import,
            "batch-rejected",
            &capability_id,
            400,
        );
        let rejected = stage_import(
            &mut rejected_store,
            &rejected_context,
            &rejected_request,
            id("batch-rejected"),
            &rejected_bundle,
            target,
            BTreeMap::new(),
            400,
        )
        .unwrap();
        assert_eq!(rejected.state, ImportState::Rejected);
        assert!(!rejected.rejected.is_empty());
        assert_eq!(
            rejected_store
                .read(|connection| connection
                    .query_row("SELECT count(*) FROM memory_records", [], |row| row
                        .get::<_, u64>(0))
                    .map_err(sql_error))
                .unwrap(),
            0
        );
    }
}
