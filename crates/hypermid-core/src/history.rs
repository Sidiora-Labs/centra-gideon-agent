use crate::identity::IdentityRelation;
use hypermid_contracts::{Cursor, Digest, Id, Scope, MAX_SAFE_INTEGER};
use serde::{Deserialize, Serialize};
use serde_json::Value;

pub const MAX_PARTS: usize = 4_096;
pub const MAX_RELATIONS: usize = 64;

#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Role {
    System,
    User,
    Assistant,
    Tool,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PartKind {
    Text,
    Reasoning,
    ToolCall,
    ToolResult,
    Image,
    File,
    ContextMarker,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ContextPart {
    pub part_id: Id,
    pub kind: PartKind,
    pub content_digest: Digest,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub text: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub call_id: Option<Id>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub arguments_json: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result_json: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub media_type: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub source_uri: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub width: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub height: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub metadata: Option<Value>,
}

impl ContextPart {
    pub fn validate(&self) -> Result<(), HistoryViolation> {
        let material = match self.kind {
            PartKind::Text | PartKind::Reasoning | PartKind::ContextMarker => {
                self.text.as_deref().ok_or(HistoryViolation::InvalidPart)?
            }
            PartKind::ToolCall => {
                self.call_id.as_ref().ok_or(HistoryViolation::InvalidPart)?;
                let tool_name = self
                    .tool_name
                    .as_deref()
                    .filter(|value| !value.is_empty() && value.len() <= 256)
                    .ok_or(HistoryViolation::InvalidPart)?;
                let arguments = self
                    .arguments_json
                    .as_deref()
                    .ok_or(HistoryViolation::InvalidPart)?;
                validate_json(arguments)?;
                return self.validate_structured_digest(&[
                    self.call_id.as_ref().unwrap().as_str().as_bytes(),
                    tool_name.as_bytes(),
                    arguments.as_bytes(),
                ]);
            }
            PartKind::ToolResult => {
                self.call_id.as_ref().ok_or(HistoryViolation::InvalidPart)?;
                let result = self
                    .result_json
                    .as_deref()
                    .ok_or(HistoryViolation::InvalidPart)?;
                validate_json(result)?;
                return self.validate_structured_digest(&[
                    self.call_id.as_ref().unwrap().as_str().as_bytes(),
                    result.as_bytes(),
                ]);
            }
            PartKind::Image | PartKind::File => {
                let media_type = self
                    .media_type
                    .as_deref()
                    .filter(|value| !value.is_empty() && value.len() <= 128)
                    .ok_or(HistoryViolation::InvalidPart)?;
                let source_uri = self
                    .source_uri
                    .as_deref()
                    .filter(|value| value.len() <= 4_096)
                    .ok_or(HistoryViolation::InvalidPart)?;
                if self
                    .width
                    .is_some_and(|value| value == 0 || value > 100_000)
                    || self
                        .height
                        .is_some_and(|value| value == 0 || value > 100_000)
                {
                    return Err(HistoryViolation::InvalidPart);
                }
                return self.validate_structured_digest(&[
                    media_type.as_bytes(),
                    source_uri.as_bytes(),
                    &self.width.unwrap_or(0).to_be_bytes(),
                    &self.height.unwrap_or(0).to_be_bytes(),
                ]);
            }
        };
        if material.len() > 1_048_576 || Digest::sha256(material.as_bytes()) != self.content_digest
        {
            return Err(HistoryViolation::PartDigestMismatch);
        }
        Ok(())
    }

    fn validate_structured_digest(&self, fields: &[&[u8]]) -> Result<(), HistoryViolation> {
        let mut material = Vec::new();
        for field in fields {
            material
                .extend_from_slice(&u64::try_from(field.len()).unwrap_or(u64::MAX).to_be_bytes());
            material.extend_from_slice(field);
        }
        if Digest::sha256(material) != self.content_digest {
            return Err(HistoryViolation::PartDigestMismatch);
        }
        Ok(())
    }
}

fn validate_json(value: &str) -> Result<(), HistoryViolation> {
    if value.len() > 4_194_304 || serde_json::from_str::<Value>(value).is_err() {
        return Err(HistoryViolation::InvalidPart);
    }
    Ok(())
}

pub type ItemRelation = IdentityRelation;

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ContextItem {
    pub item_id: Id,
    pub source_event_id: Id,
    pub source_digest: Digest,
    pub scope: Scope,
    pub session_id: Id,
    pub cursor: Cursor,
    pub role: Role,
    pub parts: Vec<ContextPart>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub relations: Vec<ItemRelation>,
    pub created_at: String,
    pub recoverable: bool,
    #[serde(default, skip_serializing_if = "is_false")]
    pub tombstone: bool,
}

impl ContextItem {
    pub fn validate_shape(&self) -> Result<(), HistoryViolation> {
        if self.parts.is_empty()
            || self.parts.len() > MAX_PARTS
            || self.relations.len() > MAX_RELATIONS
            || self.created_at.is_empty()
        {
            return Err(HistoryViolation::InvalidItem);
        }
        for part in &self.parts {
            part.validate()?;
        }
        if self.tombstone && self.relations.is_empty() {
            return Err(HistoryViolation::UnrelatedTombstone);
        }
        Ok(())
    }

    pub fn reclaim_tag(&self) -> u64 {
        self.cursor.sequence
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PendingContextItem {
    pub item_id: Id,
    pub source_event_id: Id,
    pub source_digest: Digest,
    pub scope: Scope,
    pub session_id: Id,
    pub role: Role,
    pub parts: Vec<ContextPart>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub relations: Vec<ItemRelation>,
    pub created_at: String,
    pub recoverable: bool,
    #[serde(default, skip_serializing_if = "is_false")]
    pub tombstone: bool,
}

impl PendingContextItem {
    pub fn committed(self, cursor: Cursor) -> ContextItem {
        ContextItem {
            item_id: self.item_id,
            source_event_id: self.source_event_id,
            source_digest: self.source_digest,
            scope: self.scope,
            session_id: self.session_id,
            cursor,
            role: self.role,
            parts: self.parts,
            relations: self.relations,
            created_at: self.created_at,
            recoverable: self.recoverable,
            tombstone: self.tombstone,
        }
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct IngestRequest {
    pub expected_cursor: Cursor,
    pub idempotency_key: Id,
    pub item: PendingContextItem,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub source_snapshot: Option<Vec<u8>>,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct JournalRange {
    pub start: Cursor,
    pub end: Cursor,
}

impl JournalRange {
    pub fn new(start: Cursor, end: Cursor) -> Result<Self, HistoryViolation> {
        if start.epoch != end.epoch || start.sequence == 0 || start.sequence > end.sequence {
            return Err(HistoryViolation::InvalidRange);
        }
        Ok(Self { start, end })
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct RecoveredItem {
    pub item: ContextItem,
    pub source_bytes: Vec<u8>,
}

pub trait SourceAdapter {
    fn resolve(
        &self,
        scope: &Scope,
        session_id: &Id,
        source_event_id: &Id,
    ) -> Result<Vec<u8>, HistoryViolation>;
}

#[derive(Debug, thiserror::Error)]
pub enum HistoryViolation {
    #[error("context part is structurally invalid")]
    InvalidPart,
    #[error("context part digest does not match its canonical material")]
    PartDigestMismatch,
    #[error("context item is structurally invalid")]
    InvalidItem,
    #[error("tombstone must identify a prior item")]
    UnrelatedTombstone,
    #[error("journal range is invalid")]
    InvalidRange,
    #[error("scope or session does not match the bound journal")]
    ScopeMismatch,
    #[error("expected cursor does not match the committed cursor")]
    StaleCursor,
    #[error("source snapshot digest does not match the declared source digest")]
    SourceDigestMismatch,
    #[error("idempotency key was reused for different work")]
    IdempotencyConflict,
    #[error("source event identity was reused for different work")]
    SourceIdentityConflict,
    #[error("item identity is already committed")]
    ItemIdentityConflict,
    #[error("relation target does not exist or has a mismatched digest")]
    InvalidRelation,
    #[error("tool result has no prior unmatched tool call")]
    OrphanToolResult,
    #[error("tool call identity is already present")]
    DuplicateToolCall,
    #[error("tool result identity is already present")]
    DuplicateToolResult,
    #[error("requested history item was not found")]
    NotFound,
    #[error("source adapter refused or could not resolve the authoritative item")]
    SourceUnavailable,
    #[error("journal sequence exceeds the Hypermid wire range")]
    CursorExhausted,
}

pub fn range_digest<'a>(items: impl IntoIterator<Item = &'a ContextItem>) -> Digest {
    let mut material = Vec::new();
    for item in items {
        material.extend_from_slice(&item.cursor.epoch.to_be_bytes());
        material.extend_from_slice(&item.cursor.sequence.to_be_bytes());
        material.extend_from_slice(item.item_id.as_str().as_bytes());
        material.push(0);
        material.extend_from_slice(item.source_event_id.as_str().as_bytes());
        material.push(0);
        material.extend_from_slice(item.source_digest.as_bytes());
    }
    Digest::sha256(material)
}

pub fn next_cursor(cursor: Cursor) -> Result<Cursor, HistoryViolation> {
    if cursor.sequence >= MAX_SAFE_INTEGER {
        return Err(HistoryViolation::CursorExhausted);
    }
    Cursor::new(cursor.epoch, cursor.sequence + 1).map_err(|_| HistoryViolation::CursorExhausted)
}

fn is_false(value: &bool) -> bool {
    !*value
}
