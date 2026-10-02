use hypermid_contracts::{Cursor, Digest, Id, Scope};
use serde::{Deserialize, Serialize};
use serde_json::Value;

pub const CONTEXT_PORTABILITY_SCHEMA_VERSION: u32 = 1;
pub const MAX_PORTABILITY_ENTRIES: usize = 1_000_000;
pub const MAX_PORTABILITY_ENTRY_BYTES: usize = 8 * 1024 * 1024;
pub const MAX_PORTABILITY_TOTAL_BYTES: usize = 64 * 1024 * 1024;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PortabilityEntryKind {
    SourceReference,
    SourceSnapshot,
    Summary,
    Projection,
    PolicyRevision,
    CacheGeneration,
    Reduction,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ContextPortabilityEntry {
    pub entry_id: Id,
    pub kind: PortabilityEntryKind,
    pub content_digest: Digest,
    pub byte_length: u64,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ContextPortabilityManifest {
    pub manifest_id: Id,
    pub schema_version: u32,
    pub scope: Scope,
    pub session_id: Id,
    pub cursor: Cursor,
    pub session_binding_digest: Digest,
    pub entries: Vec<ContextPortabilityEntry>,
    pub entries_digest: Digest,
    pub created_at: String,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ContextSessionBinding {
    pub scope: Scope,
    pub session_id: Id,
    pub cursor: Cursor,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ContextPortableRecord {
    pub entry_id: Id,
    pub kind: PortabilityEntryKind,
    pub payload: Value,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ContextExportBundle {
    pub binding: ContextSessionBinding,
    pub manifest: ContextPortabilityManifest,
    pub records: Vec<ContextPortableRecord>,
}

pub struct ContextExportBuilder {
    manifest_id: Id,
    binding: ContextSessionBinding,
    created_at: String,
    records: Vec<ContextPortableRecord>,
}

impl ContextExportBuilder {
    pub fn new(
        manifest_id: Id,
        binding: ContextSessionBinding,
        created_at: impl Into<String>,
    ) -> Result<Self, ContextExportError> {
        let created_at = created_at.into();
        if created_at.is_empty() {
            return Err(ContextExportError::InvalidTimestamp);
        }
        Ok(Self {
            manifest_id,
            binding,
            created_at,
            records: Vec::new(),
        })
    }

    pub fn push(
        &mut self,
        entry_id: Id,
        kind: PortabilityEntryKind,
        payload: Value,
    ) -> Result<(), ContextExportError> {
        if self.records.len() >= MAX_PORTABILITY_ENTRIES {
            return Err(ContextExportError::TooManyEntries);
        }
        if self
            .records
            .iter()
            .any(|record| record.entry_id == entry_id)
        {
            return Err(ContextExportError::DuplicateEntry);
        }
        validate_payload_binding(&self.binding, kind, &payload, None)?;
        let byte_length = canonical_json_bytes(&payload)?.len();
        if byte_length > MAX_PORTABILITY_ENTRY_BYTES {
            return Err(ContextExportError::EntryTooLarge);
        }
        self.records.push(ContextPortableRecord {
            entry_id,
            kind,
            payload,
        });
        Ok(())
    }

    pub fn build(self) -> Result<ContextExportBundle, ContextExportError> {
        let mut entries = Vec::with_capacity(self.records.len());
        let mut total_bytes = 0_usize;
        let mut last_source_cursor = None;
        for record in &self.records {
            let payload = canonical_json_bytes(&record.payload)?;
            total_bytes = total_bytes
                .checked_add(payload.len())
                .ok_or(ContextExportError::BundleTooLarge)?;
            if total_bytes > MAX_PORTABILITY_TOTAL_BYTES {
                return Err(ContextExportError::BundleTooLarge);
            }
            validate_payload_binding(
                &self.binding,
                record.kind,
                &record.payload,
                last_source_cursor,
            )?;
            if record.kind == PortabilityEntryKind::SourceReference {
                last_source_cursor = Some(payload_cursor(&record.payload)?);
            }
            entries.push(ContextPortabilityEntry {
                entry_id: record.entry_id.clone(),
                kind: record.kind,
                content_digest: Digest::sha256(&payload),
                byte_length: payload.len() as u64,
            });
        }
        let session_binding_digest = Digest::sha256(canonical_json_bytes(&self.binding)?);
        let entries_digest = Digest::sha256(canonical_json_bytes(&entries)?);
        let manifest = ContextPortabilityManifest {
            manifest_id: self.manifest_id,
            schema_version: CONTEXT_PORTABILITY_SCHEMA_VERSION,
            scope: self.binding.scope.clone(),
            session_id: self.binding.session_id.clone(),
            cursor: self.binding.cursor,
            session_binding_digest,
            entries,
            entries_digest,
            created_at: self.created_at,
        };
        Ok(ContextExportBundle {
            binding: self.binding,
            manifest,
            records: self.records,
        })
    }
}

pub(crate) fn canonical_json_bytes<T: Serialize>(value: &T) -> Result<Vec<u8>, ContextExportError> {
    let value = serde_json::to_value(value)?;
    serde_json::to_vec(&value).map_err(ContextExportError::Json)
}

pub(crate) fn validate_payload_binding(
    binding: &ContextSessionBinding,
    kind: PortabilityEntryKind,
    payload: &Value,
    previous_source_cursor: Option<Cursor>,
) -> Result<(), ContextExportError> {
    if contains_forbidden_field(payload) {
        return Err(ContextExportError::ForbiddenState);
    }
    let object = payload
        .as_object()
        .ok_or(ContextExportError::InvalidPayload)?;
    let scope: Scope = serde_json::from_value(
        object
            .get("scope")
            .cloned()
            .ok_or(ContextExportError::InvalidPayload)?,
    )?;
    let session_id: Id = serde_json::from_value(
        object
            .get("session_id")
            .cloned()
            .ok_or(ContextExportError::InvalidPayload)?,
    )?;
    if scope != binding.scope || session_id != binding.session_id {
        return Err(ContextExportError::ScopeMismatch);
    }
    if kind == PortabilityEntryKind::SourceReference {
        for field in ["item_id", "source_event_id", "source_digest", "cursor"] {
            if !object.contains_key(field) {
                return Err(ContextExportError::InvalidSourceReference);
            }
        }
        let _: Id = serde_json::from_value(object["item_id"].clone())?;
        let _: Id = serde_json::from_value(object["source_event_id"].clone())?;
        let _: Digest = serde_json::from_value(object["source_digest"].clone())?;
        let cursor = payload_cursor(payload)?;
        if cursor.epoch != binding.cursor.epoch || cursor.sequence > binding.cursor.sequence {
            return Err(ContextExportError::InvalidSourceReference);
        }
        if previous_source_cursor.is_some_and(|previous| cursor <= previous) {
            return Err(ContextExportError::SourceOrder);
        }
    }
    Ok(())
}

fn contains_forbidden_field(value: &Value) -> bool {
    const FORBIDDEN: &[&str] = &[
        "credential",
        "credentials",
        "provider_credentials",
        "api_key",
        "access_token",
        "refresh_token",
        "writer_lease",
        "live_lease",
        "transient_queue",
        "sdk_payload",
        "sdk_payloads",
    ];
    match value {
        Value::Object(object) => object.iter().any(|(key, value)| {
            FORBIDDEN.contains(&key.as_str()) || contains_forbidden_field(value)
        }),
        Value::Array(values) => values.iter().any(contains_forbidden_field),
        _ => false,
    }
}

fn payload_cursor(payload: &Value) -> Result<Cursor, ContextExportError> {
    serde_json::from_value(
        payload
            .get("cursor")
            .cloned()
            .ok_or(ContextExportError::InvalidSourceReference)?,
    )
    .map_err(ContextExportError::Json)
}

#[derive(Debug, thiserror::Error)]
pub enum ContextExportError {
    #[error("portability timestamp is empty")]
    InvalidTimestamp,
    #[error("portability entry identity is duplicated")]
    DuplicateEntry,
    #[error("portability manifest contains too many entries")]
    TooManyEntries,
    #[error("portability entry exceeds the size bound")]
    EntryTooLarge,
    #[error("portability bundle exceeds the size bound")]
    BundleTooLarge,
    #[error("portability payload is not a scoped object")]
    InvalidPayload,
    #[error("source reference is incomplete or outside the exported cursor")]
    InvalidSourceReference,
    #[error("source references are not in strict cursor order")]
    SourceOrder,
    #[error("portability payload does not match the session binding")]
    ScopeMismatch,
    #[error("portability payload contains excluded live, transient, credential, or SDK state")]
    ForbiddenState,
    #[error("portability JSON is invalid")]
    Json(#[from] serde_json::Error),
}
