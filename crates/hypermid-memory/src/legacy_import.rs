use crate::export::{
    stream_digest, ExportEntryKind, MemoryExportBundle, MemoryExportEntry, MemoryExportManifest,
};
use crate::import::MemoryImportBatch;
use crate::model::{scope_digest, MutationRequest};
use crate::{error, Cursor, Digest, EffectState, Id, MemoryResult, Scope, MEMORY_SCHEMA_VERSION};
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use sha2::{Digest as _, Sha256};
use std::collections::{BTreeMap, BTreeSet, HashMap};

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum LegacyKnowledgeCategory {
    Page,
    Semantic,
    Episodic,
    Lesson,
    Slot,
    GraphLink,
    AuditEvent,
    Summary,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct LegacySourceItem {
    pub item_key: Id,
    pub category: LegacyKnowledgeCategory,
    pub source_identity: String,
    pub source_digest: Digest,
    pub payload: Map<String, Value>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct LegacyConversationLogEvidence {
    pub relative_path: String,
    pub byte_length: u64,
    pub source_digest: Digest,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct GideonLegacySnapshot {
    pub scope: Scope,
    pub items: Vec<LegacySourceItem>,
    #[serde(default)]
    pub conversation_logs: Vec<LegacyConversationLogEvidence>,
    pub source_digest: Digest,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct LegacyImportPlan {
    pub source_digest: Digest,
    pub destination_digest: Digest,
    pub record_count: u64,
    pub item_count: u64,
    pub bundle: MemoryExportBundle,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct LegacyImportReceipt {
    pub source_digest: Digest,
    pub destination_digest: Digest,
    pub batch: MemoryImportBatch,
    pub record_count: u64,
    pub item_count: u64,
}

pub fn plan_legacy_import(
    snapshot: &GideonLegacySnapshot,
    request: &MutationRequest,
    _created_at_ms: u64,
) -> MemoryResult<LegacyImportPlan> {
    if snapshot.scope != request.target_scope {
        return Err(invalid(
            "legacy snapshot scope does not match the import target",
        ));
    }
    validate_snapshot(snapshot)?;
    let created_at_ms = 0;
    let owner = scope_digest(&snapshot.scope);
    let mut records = Vec::new();
    let mut revisions = Vec::new();
    let mut details = Vec::new();
    let mut sources = BTreeMap::<String, MemoryExportEntry>::new();
    let mut provenance = Vec::new();
    let mut lineage = Vec::new();
    let mut mutations = Vec::new();
    let mut revisions_by_record = HashMap::<String, Digest>::new();
    let mut ordered = snapshot.items.iter().collect::<Vec<_>>();
    ordered.sort_by_key(|item| (category_name(item.category), item.item_key.clone()));

    for item in &ordered {
        let content = item_content(item)?;
        let kind = record_kind(item.category, item)?;
        let record_id = item.item_key.clone();
        let content_digest = Digest::sha256(content.as_bytes());
        let metadata = legacy_metadata(item);
        let metadata_bytes = canonical_json(&Value::Object(metadata.clone()))?;
        let revision_digest = revision_digest(
            &record_id,
            content_digest,
            &metadata_bytes,
            owner,
            created_at_ms,
        );
        revisions_by_record.insert(record_id.to_string(), revision_digest);
        records.push(entry(
            format!("record:{record_id}"),
            ExportEntryKind::Record,
            json!({
                "record_id": record_id,
                "owner_scope_digest": owner,
                "kind": kind,
                "category": category_name(item.category),
                "status": "active",
                "current_revision": 1,
                "current_revision_digest": revision_digest,
                "normalized_content_digest": normalized_digest(&content),
                "importance": item_importance(item),
                "confidence": item_confidence(item),
                "verification_state": "unverified",
                "valid_from_ms": null,
                "valid_to_ms": null,
                "observed_from_ms": null,
                "observed_to_ms": null,
                "expires_at_ms": null,
                "retention_until_ms": null,
                "created_at_ms": created_at_ms,
                "updated_at_ms": created_at_ms,
                "deleted_at_ms": null
            }),
        )?);
        revisions.push(entry(
            format!("revision:{record_id}:{:020}", 1),
            ExportEntryKind::Revision,
            json!({
                "record_id": record_id,
                "revision": 1,
                "revision_digest": revision_digest,
                "parent_revision_digest": null,
                "content": content,
                "content_digest": content_digest,
                "metadata": metadata,
                "smart_predicate": null,
                "author_scope_digest": owner,
                "authored_at_ms": created_at_ms,
                "immutable_anchor": item.category == LegacyKnowledgeCategory::AuditEvent
            }),
        )?);
        add_details(item, &record_id, created_at_ms, &mut details)?;
        let source_id = legacy_source_id(item);
        let source_payload = Value::Object(item.payload.clone());
        sources.entry(source_id.to_string()).or_insert(entry(
            format!("source:{source_id}"),
            ExportEntryKind::Source,
            json!({
                "source_id": source_id,
                "owner_scope_digest": owner,
                "source_kind": "memory",
                "source_digest": item.source_digest,
                "locator": item.source_identity,
                "captured_content": String::from_utf8(canonical_json(&source_payload)?).map_err(|_| invalid("legacy payload is not UTF-8"))?,
                "capture_method": "gideon_legacy_import",
                "observed_at_ms": created_at_ms,
                "created_at_ms": created_at_ms
            }),
        )?);
        provenance.push(provenance_entry(
            &record_id,
            &source_id,
            item.source_digest,
        )?);
        if item.category == LegacyKnowledgeCategory::Summary {
            add_summary_sources(
                item,
                snapshot,
                &record_id,
                owner,
                created_at_ms,
                &mut sources,
                &mut provenance,
            )?;
        }
        let sequence = records.len() as u64;
        let event_id = legacy_event_id(snapshot.source_digest, &record_id);
        mutations.push(entry(
            format!("mutation:{event_id}"),
            ExportEntryKind::Mutation,
            json!({
                "event_id": event_id,
                "owner_scope_digest": owner,
                "epoch": 1,
                "sequence": sequence,
                "operation": "import",
                "record_id": record_id,
                "previous_revision_digest": null,
                "result_revision_digest": revision_digest,
                "actor_scope_digest": owner,
                "grant_id": null,
                "authorization_basis": "owner",
                "trace": request.trace,
                "created_at_ms": created_at_ms
            }),
        )?);
    }

    add_graph_lineage(&ordered, &revisions_by_record, created_at_ms, &mut lineage)?;
    let scope_entry = entry(
        format!("scope:{}", owner.to_hex()),
        ExportEntryKind::Scope,
        json!({
            "scope_digest": owner,
            "scope": snapshot.scope,
            "epoch": 1,
            "sequence": records.len(),
            "created_at_ms": created_at_ms,
            "updated_at_ms": created_at_ms
        }),
    )?;
    let mut entries = vec![scope_entry];
    entries.extend(records);
    entries.extend(revisions);
    entries.extend(details);
    entries.extend(sources.into_values());
    entries.extend(provenance);
    entries.extend(lineage);
    entries.extend(mutations);
    let record_count = ordered.len() as u64;
    let destination_digest = stream_digest(&entries)?;
    let bundle = MemoryExportBundle {
        manifest: MemoryExportManifest {
            export_id: stable_id("legacy-plan", snapshot.source_digest.as_bytes()),
            schema_version: MEMORY_SCHEMA_VERSION,
            scope: snapshot.scope.clone(),
            record_count,
            item_count: entries.len() as u64,
            stream_digest: destination_digest,
            created_at_ms,
            cursor: Cursor::new(1, record_count)
                .map_err(|_| invalid("legacy record count exceeds cursor bounds"))?,
            trace: request.trace.clone(),
            include_grants: false,
        },
        entries,
    };
    bundle.verify()?;
    Ok(LegacyImportPlan {
        source_digest: snapshot.source_digest,
        destination_digest,
        record_count,
        item_count: bundle.manifest.item_count,
        bundle,
    })
}

fn validate_snapshot(snapshot: &GideonLegacySnapshot) -> MemoryResult<()> {
    let mut items = snapshot.items.iter().collect::<Vec<_>>();
    items.sort_by_key(|item| (category_name(item.category), item.item_key.clone()));
    let mut keys = BTreeSet::new();
    for item in &items {
        if item.source_identity.is_empty() || item.source_identity.len() > 4_096 {
            return Err(invalid("legacy source identity is invalid"));
        }
        let payload = Value::Object(item.payload.clone());
        if Digest::sha256(canonical_json(&payload)?) != item.source_digest {
            return Err(invalid(
                "legacy source item digest does not match its payload",
            ));
        }
        if !keys.insert((item.source_digest, item.item_key.clone())) {
            return Err(invalid("legacy source item idempotency key is duplicated"));
        }
    }
    let material = json!({
        "scope": snapshot.scope,
        "items": sorted_item_material(&snapshot.items),
        "conversation_logs": sorted_log_material(&snapshot.conversation_logs),
    });
    if Digest::sha256(canonical_json(&material)?) != snapshot.source_digest {
        return Err(invalid(
            "legacy snapshot digest does not match its ordered evidence",
        ));
    }
    Ok(())
}

fn sorted_item_material(items: &[LegacySourceItem]) -> Vec<Value> {
    let mut items = items.iter().collect::<Vec<_>>();
    items.sort_by_key(|item| (category_name(item.category), item.item_key.clone()));
    items
        .into_iter()
        .map(|item| {
            json!({
                "item_key": item.item_key,
                "category": item.category,
                "source_identity": item.source_identity,
                "source_digest": item.source_digest,
                "payload": item.payload,
            })
        })
        .collect()
}

fn sorted_log_material(logs: &[LegacyConversationLogEvidence]) -> Vec<Value> {
    let mut logs = logs.iter().collect::<Vec<_>>();
    logs.sort_by_key(|log| &log.relative_path);
    logs.into_iter()
        .map(|log| {
            json!({
                "relative_path": log.relative_path,
                "byte_length": log.byte_length,
                "source_digest": log.source_digest,
                "disposition": "retained_in_conversation_log"
            })
        })
        .collect()
}

fn item_content(item: &LegacySourceItem) -> MemoryResult<String> {
    let value = match item.category {
        LegacyKnowledgeCategory::Page
        | LegacyKnowledgeCategory::Semantic
        | LegacyKnowledgeCategory::Episodic
        | LegacyKnowledgeCategory::Lesson
        | LegacyKnowledgeCategory::Summary => item.payload.get("content").and_then(Value::as_str),
        LegacyKnowledgeCategory::Slot => item
            .payload
            .get("materialized")
            .and_then(Value::as_str)
            .or_else(|| item.payload.get("description").and_then(Value::as_str)),
        LegacyKnowledgeCategory::GraphLink | LegacyKnowledgeCategory::AuditEvent => None,
    };
    if let Some(value) = value.filter(|value| !value.is_empty()) {
        return Ok(value.to_owned());
    }
    String::from_utf8(canonical_json(&Value::Object(item.payload.clone()))?)
        .map_err(|_| invalid("legacy content is not UTF-8"))
}

fn record_kind(
    category: LegacyKnowledgeCategory,
    item: &LegacySourceItem,
) -> MemoryResult<&'static str> {
    Ok(match category {
        LegacyKnowledgeCategory::Page | LegacyKnowledgeCategory::GraphLink => "note",
        LegacyKnowledgeCategory::Semantic
        | LegacyKnowledgeCategory::Lesson
        | LegacyKnowledgeCategory::Slot => "fact",
        LegacyKnowledgeCategory::Episodic => "episode",
        LegacyKnowledgeCategory::AuditEvent => "anchor",
        LegacyKnowledgeCategory::Summary => {
            require_summary_references(item)?;
            "summary"
        }
    })
}

fn category_name(category: LegacyKnowledgeCategory) -> &'static str {
    match category {
        LegacyKnowledgeCategory::Page => "page",
        LegacyKnowledgeCategory::Semantic => "semantic",
        LegacyKnowledgeCategory::Episodic => "episodic",
        LegacyKnowledgeCategory::Lesson => "lesson",
        LegacyKnowledgeCategory::Slot => "slot",
        LegacyKnowledgeCategory::GraphLink => "graph_link",
        LegacyKnowledgeCategory::AuditEvent => "audit_event",
        LegacyKnowledgeCategory::Summary => "summary",
    }
}

fn legacy_metadata(item: &LegacySourceItem) -> Map<String, Value> {
    let mut metadata = item.payload.clone();
    metadata.insert("legacy_category".to_owned(), json!(item.category));
    metadata.insert(
        "legacy_source_identity".to_owned(),
        json!(item.source_identity),
    );
    metadata.insert("legacy_source_digest".to_owned(), json!(item.source_digest));
    metadata
}

fn add_details(
    item: &LegacySourceItem,
    record_id: &Id,
    created_at_ms: u64,
    details: &mut Vec<MemoryExportEntry>,
) -> MemoryResult<()> {
    match item.category {
        LegacyKnowledgeCategory::Episodic => details.push(entry(
            format!("episode:{record_id}"),
            ExportEntryKind::EpisodeDetail,
            json!({
                "record_id": record_id,
                "observed_from_ms": created_at_ms,
                "observed_to_ms": created_at_ms,
                "participants": item.payload.get("tags").cloned().unwrap_or_else(|| json!([])),
                "episode_type": "gideon_legacy"
            }),
        )?),
        LegacyKnowledgeCategory::Summary => {
            let references = require_summary_references(item)?;
            let digest = Digest::sha256(canonical_json(references)?);
            details.push(entry(
                format!("summary:{record_id}"),
                ExportEntryKind::SummaryDetail,
                json!({
                    "record_id": record_id,
                    "input_set_digest": digest,
                    "summary_level": item.payload.get("summary_level").and_then(Value::as_str).unwrap_or("standard"),
                    "decay_half_life_ms": null,
                    "refreshed_at_ms": created_at_ms,
                    "stale_at_ms": null
                }),
            )?);
        }
        _ => {}
    }
    Ok(())
}

fn add_summary_sources(
    item: &LegacySourceItem,
    snapshot: &GideonLegacySnapshot,
    record_id: &Id,
    owner: Digest,
    created_at_ms: u64,
    sources: &mut BTreeMap<String, MemoryExportEntry>,
    provenance: &mut Vec<MemoryExportEntry>,
) -> MemoryResult<()> {
    let references = require_summary_references(item)?
        .as_array()
        .ok_or_else(|| invalid("summary source references must be an array"))?;
    let known = snapshot
        .conversation_logs
        .iter()
        .map(|evidence| evidence.source_digest)
        .collect::<BTreeSet<_>>();
    for reference in references {
        let object = reference
            .as_object()
            .ok_or_else(|| invalid("summary source reference is invalid"))?;
        let digest: Digest = object
            .get("source_digest")
            .and_then(Value::as_str)
            .ok_or_else(|| invalid("summary source digest is missing"))?
            .parse()
            .map_err(|_| invalid("summary source digest is invalid"))?;
        if !known.contains(&digest) {
            return Err(invalid(
                "summary source digest is not present in ConversationLog evidence",
            ));
        }
        let locator = format!(
            "{}:{}",
            object
                .get("session_id")
                .and_then(Value::as_str)
                .unwrap_or("session"),
            object
                .get("source_event_id")
                .and_then(Value::as_str)
                .unwrap_or("event")
        );
        let source_id = stable_id("legacy-message", format!("{locator}:{digest}").as_bytes());
        sources.entry(source_id.to_string()).or_insert(entry(
            format!("source:{source_id}"),
            ExportEntryKind::Source,
            json!({
                "source_id": source_id,
                "owner_scope_digest": owner,
                "source_kind": "message",
                "source_digest": digest,
                "locator": locator,
                "captured_content": null,
                "capture_method": "gideon_legacy_import",
                "observed_at_ms": created_at_ms,
                "created_at_ms": created_at_ms
            }),
        )?);
        provenance.push(provenance_entry(record_id, &source_id, digest)?);
    }
    Ok(())
}

fn require_summary_references(item: &LegacySourceItem) -> MemoryResult<&Value> {
    item.payload
        .get("source_references")
        .filter(|value| value.as_array().is_some_and(|items| !items.is_empty()))
        .ok_or_else(|| invalid("legacy summary lacks validated Gideon source references"))
}

fn provenance_entry(
    record_id: &Id,
    source_id: &Id,
    quoted_digest: Digest,
) -> MemoryResult<MemoryExportEntry> {
    entry(
        format!("provenance:{record_id}:{:020}:{source_id}:{:020}", 1, 0),
        ExportEntryKind::Provenance,
        json!({
            "record_id": record_id,
            "revision": 1,
            "source_id": source_id,
            "span_start": 0,
            "span_end": 0,
            "quoted_digest": quoted_digest
        }),
    )
}

fn add_graph_lineage(
    items: &[&LegacySourceItem],
    revisions: &HashMap<String, Digest>,
    created_at_ms: u64,
    output: &mut Vec<MemoryExportEntry>,
) -> MemoryResult<()> {
    for item in items
        .iter()
        .filter(|item| item.category == LegacyKnowledgeCategory::GraphLink)
    {
        if item.payload.get("graph_kind").and_then(Value::as_str) != Some("edge") {
            continue;
        }
        let Some(value) = item.payload.get("value").and_then(Value::as_object) else {
            continue;
        };
        let Some(child) = graph_record_id(value.get("from_ref").and_then(Value::as_str)) else {
            continue;
        };
        let Some(parent) = graph_record_id(value.get("to_ref").and_then(Value::as_str)) else {
            continue;
        };
        let (Some(_), Some(parent_revision)) = (revisions.get(&child), revisions.get(&parent))
        else {
            continue;
        };
        let relation = match value.get("link_type").and_then(Value::as_str) {
            Some("derived_from") => "derived_from",
            Some("supersedes") => "supersedes",
            Some("contradicts") => "contradicts",
            Some("verifies") => "verifies",
            _ => "cites",
        };
        output.push(entry(
            format!("lineage:{child}:{:020}:{parent}:{relation}", 1),
            ExportEntryKind::Lineage,
            json!({
                "child_record_id": child,
                "child_revision": 1,
                "parent_record_id": parent,
                "parent_revision_digest": parent_revision,
                "relation": relation,
                "created_at_ms": created_at_ms
            }),
        )?);
    }
    Ok(())
}

fn graph_record_id(reference: Option<&str>) -> Option<String> {
    let reference = reference?;
    reference
        .strip_prefix("sem:")
        .map(|value| format!("semantic:{value}"))
        .or_else(|| {
            reference
                .strip_prefix("epi:")
                .map(|value| format!("episodic:{value}"))
        })
}

fn item_importance(item: &LegacySourceItem) -> f64 {
    item.payload
        .get("importance")
        .and_then(Value::as_f64)
        .filter(|value| value.is_finite())
        .unwrap_or(0.5)
        .clamp(0.0, 1.0)
}

fn item_confidence(item: &LegacySourceItem) -> f64 {
    item.payload
        .get("confidence")
        .and_then(Value::as_f64)
        .filter(|value| value.is_finite())
        .unwrap_or(0.5)
        .clamp(0.0, 1.0)
}

fn entry(
    item_key: String,
    kind: ExportEntryKind,
    payload: Value,
) -> MemoryResult<MemoryExportEntry> {
    Ok(MemoryExportEntry {
        item_key,
        kind,
        item_digest: Digest::sha256(canonical_json(&payload)?),
        payload,
    })
}

fn legacy_source_id(item: &LegacySourceItem) -> Id {
    stable_id(
        "legacy-source",
        format!("{}:{}", item.source_identity, item.source_digest).as_bytes(),
    )
}

fn legacy_event_id(source: Digest, record_id: &Id) -> Id {
    stable_id("legacy-event", format!("{source}:{record_id}").as_bytes())
}

fn stable_id(prefix: &str, material: &[u8]) -> Id {
    let digest = Digest::sha256(material).to_hex();
    Id::new(format!("{prefix}:{}", &digest[..32])).expect("stable legacy ids are valid")
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
    content: Digest,
    metadata: &[u8],
    author: Digest,
    authored_at_ms: u64,
) -> Digest {
    let mut hasher = Sha256::new();
    hasher.update(b"hypermid.memory.revision.v1\0");
    hash_part(&mut hasher, record_id.as_str().as_bytes());
    hasher.update(1_u64.to_be_bytes());
    hasher.update([0]);
    hasher.update(content.as_bytes());
    hash_part(&mut hasher, metadata);
    hasher.update(author.as_bytes());
    hasher.update(authored_at_ms.to_be_bytes());
    Digest::from_bytes(hasher.finalize().into())
}

fn hash_part(hasher: &mut Sha256, value: &[u8]) {
    hasher.update((value.len() as u64).to_be_bytes());
    hasher.update(value);
}

fn canonical_json(value: &Value) -> MemoryResult<Vec<u8>> {
    let mut output = Vec::new();
    write_canonical(value, &mut output)?;
    Ok(output)
}

fn write_canonical(value: &Value, output: &mut Vec<u8>) -> MemoryResult<()> {
    match value {
        Value::Null => output.extend_from_slice(b"null"),
        Value::Bool(value) => output.extend_from_slice(if *value { b"true" } else { b"false" }),
        Value::Number(value) => output.extend_from_slice(value.to_string().as_bytes()),
        Value::String(value) => output.extend_from_slice(
            serde_json::to_string(value)
                .map_err(|_| invalid("legacy JSON string is invalid"))?
                .as_bytes(),
        ),
        Value::Array(values) => {
            output.push(b'[');
            for (index, value) in values.iter().enumerate() {
                if index > 0 {
                    output.push(b',');
                }
                write_canonical(value, output)?;
            }
            output.push(b']');
        }
        Value::Object(values) => {
            output.push(b'{');
            let mut values = values.iter().collect::<Vec<_>>();
            values.sort_by_key(|(key, _)| *key);
            for (index, (key, value)) in values.into_iter().enumerate() {
                if index > 0 {
                    output.push(b',');
                }
                write_canonical(&Value::String(key.clone()), output)?;
                output.push(b':');
                write_canonical(value, output)?;
            }
            output.push(b'}');
        }
    }
    Ok(())
}

fn invalid(message: &'static str) -> hypermid_contracts::Error {
    error("INVALID_LEGACY_IMPORT", message, EffectState::NotStarted)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        capability_operation, AuthContext, AuthenticatedPrincipal, AuthorizationRequest,
        CapabilityGrant, CapabilityOperation, MemoryApi, MemoryStore, MutationRequest, Operation,
        PrincipalKind, RevisionPrecondition, Trace,
    };
    use hypermid_store::authorization::put_grant;

    fn id(value: &str) -> Id {
        Id::new(value).unwrap()
    }

    fn scope() -> Scope {
        Scope::new(id("legacy-owner"), id("legacy-project"), None)
    }

    fn source_item(
        item_key: &str,
        category: LegacyKnowledgeCategory,
        payload: Value,
    ) -> LegacySourceItem {
        let payload = payload.as_object().unwrap().clone();
        LegacySourceItem {
            item_key: id(item_key),
            category,
            source_identity: format!("GideonLegacy:{item_key}"),
            source_digest: Digest::sha256(canonical_json(&Value::Object(payload.clone())).unwrap()),
            payload,
        }
    }

    fn snapshot(scope: Scope) -> GideonLegacySnapshot {
        let log_digest = Digest::sha256(b"conversation-jsonl-bytes");
        let items = vec![
            source_item(
                "page:preferences",
                LegacyKnowledgeCategory::Page,
                json!({"name":"preferences","content":"Prefer concise answers"}),
            ),
            source_item(
                "semantic:alpha",
                LegacyKnowledgeCategory::Semantic,
                json!({"legacy_id":"alpha","value":"Rust","content":"Project uses Rust","confidence":0.9,"source":"memory","scope":"project","scope_ref":null,"category":"stack","created_at":"2026-01-01","updated_at":"2026-01-02"}),
            ),
            source_item(
                "episodic:episode-1",
                LegacyKnowledgeCategory::Episodic,
                json!({"legacy_id":"episode-1","conversation_id":"session-1","content":"Released version one","tags":["release"],"importance":0.8,"contributor":"user","created_at":"2026-01-03","last_accessed_at":null}),
            ),
            source_item(
                "lesson:retry",
                LegacyKnowledgeCategory::Lesson,
                json!({"legacy_id":"retry","value":"retry once","content":"Retry transient failures once","confidence":0.7,"source":"lesson","scope":"project","scope_ref":null,"category":"operations","created_at":"2026-01-04","updated_at":"2026-01-04"}),
            ),
            source_item(
                "slot:focus",
                LegacyKnowledgeCategory::Slot,
                json!({"name":"focus","title":"Focus","description":"Current focus","scope":"project","builtin":false,"materialized":"Ship Hypermid","lines":[{"text":"Ship Hypermid","added_at":"2026-01-05","reinforcements":2,"tombstoned":false,"tombstoned_by":null}]}),
            ),
            source_item(
                "graph:edge:one",
                LegacyKnowledgeCategory::GraphLink,
                json!({"graph_kind":"edge","value":{"id":"one","from_kind":"record","from_ref":"sem:alpha","link_type":"cites","to_ref":"epi:episode-1","provenance":"legacy","confidence":0.8,"source":"graph"}}),
            ),
            source_item(
                "audit:event-1",
                LegacyKnowledgeCategory::AuditEvent,
                json!({"legacy_id":"event-1","event_type":"update","memory_type":"semantic","memory_key":"alpha","old_value":null,"new_value":"Rust","source":"audit","created_at":"2026-01-06","undone_at":null}),
            ),
            source_item(
                "summary:one",
                LegacyKnowledgeCategory::Summary,
                json!({"content":"The project uses Rust and released version one","summary_level":"standard","source_references":[{"session_id":"session-1","source_event_id":"event-9","source_digest":log_digest,"cursor":{"epoch":1,"sequence":9}}]}),
            ),
        ];
        let conversation_logs = vec![LegacyConversationLogEvidence {
            relative_path: "session-1.jsonl".to_owned(),
            byte_length: 24,
            source_digest: log_digest,
        }];
        let material = json!({
            "scope": scope,
            "items": sorted_item_material(&items),
            "conversation_logs": sorted_log_material(&conversation_logs),
        });
        GideonLegacySnapshot {
            scope,
            items,
            conversation_logs,
            source_digest: Digest::sha256(canonical_json(&material).unwrap()),
        }
    }

    fn request(
        scope: &Scope,
        operation: Operation,
        resource: &str,
        suffix: &str,
    ) -> MutationRequest {
        MutationRequest {
            operation,
            actor_scope: scope.clone(),
            target_scope: scope.clone(),
            record_id: Some(id(resource)),
            category: None,
            revision: RevisionPrecondition::MustNotExist,
            trace: Trace::new(
                id(&format!("trace-{suffix}")),
                id(&format!("request-{suffix}")),
            ),
        }
    }

    fn context(
        scope: &Scope,
        operation: Operation,
        resource: &str,
        capability_id: &Id,
        now_ms: u64,
    ) -> AuthContext {
        AuthContext {
            principal: AuthenticatedPrincipal {
                principal_id: id("legacy-principal"),
                owner_id: scope.owner_id.clone(),
                kind: PrincipalKind::Foreground,
            },
            request: AuthorizationRequest {
                claimed_scope: scope.clone(),
                target_scope: scope.clone(),
                operation: capability_operation(operation),
                resource_id: id(resource),
                now_ms,
            },
            capability_id: capability_id.clone(),
        }
    }

    #[test]
    fn legacy_fixture_imports_through_memory_api_and_exports_canonical_records() {
        let directory = tempfile::tempdir().unwrap();
        let scope = scope();
        let capability_id = id("legacy-capability");
        let mut store = MemoryStore::open(directory.path().join("memory.sqlite3")).unwrap();
        store.ensure_scope(&scope, 1).unwrap();
        let principal = AuthenticatedPrincipal {
            principal_id: id("legacy-principal"),
            owner_id: scope.owner_id.clone(),
            kind: PrincipalKind::Foreground,
        };
        let grant = CapabilityGrant {
            capability_id: capability_id.clone(),
            issuer_owner_id: scope.owner_id.clone(),
            principal_id: principal.principal_id.clone(),
            claimed_scope: scope.clone(),
            target_scope: scope.clone(),
            operations: BTreeSet::from([CapabilityOperation::Append, CapabilityOperation::Export]),
            resources: BTreeSet::from([id("legacy-batch"), id("legacy-export")]),
            expires_at_ms: 10_000,
        };
        store
            .immediate(|transaction| {
                put_grant(transaction.raw(), &principal, &grant).unwrap();
                Ok(())
            })
            .unwrap();
        let snapshot = snapshot(scope.clone());
        let import_request = request(&scope, Operation::Import, "legacy-batch", "import");
        let import_context = context(
            &scope,
            Operation::Import,
            "legacy-batch",
            &capability_id,
            1_000,
        );
        let mut api = MemoryApi::new(store);
        let receipt = api
            .import_legacy(
                &import_context,
                &import_request,
                id("legacy-batch"),
                &snapshot,
                1_000,
            )
            .unwrap();
        assert_eq!(receipt.batch.state, crate::ImportState::Applied);
        assert_eq!(receipt.record_count, 8);

        let replay = api
            .import_legacy(
                &context(
                    &scope,
                    Operation::Import,
                    "legacy-batch",
                    &capability_id,
                    1_050,
                ),
                &import_request,
                id("legacy-batch"),
                &snapshot,
                1_050,
            )
            .unwrap();
        assert_eq!(replay.batch.batch_id, receipt.batch.batch_id);
        assert_eq!(replay.destination_digest, receipt.destination_digest);
        assert!(replay.batch.replayed);

        let export_request = request(&scope, Operation::Export, "legacy-export", "export");
        let exported = api
            .export_scope(
                &context(
                    &scope,
                    Operation::Export,
                    "legacy-export",
                    &capability_id,
                    1_100,
                ),
                &export_request,
                id("legacy-export"),
                false,
                1_100,
            )
            .unwrap();
        assert_eq!(exported.manifest.record_count, 8);
        assert!(exported
            .entries
            .iter()
            .any(|entry| entry.kind == ExportEntryKind::Lineage));
        assert!(exported.entries.iter().any(|entry| {
            entry.kind == ExportEntryKind::Record
                && entry.payload.get("kind") == Some(&Value::String("anchor".to_owned()))
        }));
        assert!(!exported.entries.iter().any(|entry| {
            entry
                .payload
                .get("captured_content")
                .and_then(Value::as_str)
                .is_some_and(|content| content.contains("conversation-jsonl-bytes"))
        }));
    }
}
