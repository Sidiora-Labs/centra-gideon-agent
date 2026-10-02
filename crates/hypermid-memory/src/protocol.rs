use crate::embedding::ValidatedEmbedding;
use crate::model::scope_digest;
use crate::{error, MemoryResult};
use base64::{engine::general_purpose::STANDARD as BASE64, Engine as _};
use hypermid_contracts::{Cursor, Digest, EffectState, Id, Scope, Trace};
use hypermid_core::capability::{
    AuthContext, AuthenticatedPrincipal, AuthorizationRequest, CapabilityOperation,
    PrincipalKind as CorePrincipalKind,
};
use hypermid_protocol::{Envelope, MessageKind, Principal, PrincipalKind};
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::str::FromStr;

pub const MEMORY_PROTOCOL: &str = "memory.v1";
pub const MAX_MEMORY_PAYLOAD_BYTES: usize = 4 * 1024 * 1024;
pub const MAX_VECTOR_DIMENSIONS: usize = 65_536;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum MemoryOperation {
    RecordRead,
    Search,
    EmbeddingQuery,
    Create,
    Update,
    Archive,
    Restore,
    Merge,
    Split,
    Relocate,
    Delete,
    Purge,
    Verify,
    Embed,
    Index,
    Summarize,
    Import,
    Export,
    Maintenance,
    Diagnostics,
}

impl MemoryOperation {
    pub const fn wire_name(self) -> &'static str {
        match self {
            Self::RecordRead => "memory.record.read",
            Self::Search => "memory.search",
            Self::EmbeddingQuery => "memory.embedding.query",
            Self::Create => "memory.record.create",
            Self::Update => "memory.record.update",
            Self::Archive => "memory.record.archive",
            Self::Restore => "memory.record.restore",
            Self::Merge => "memory.record.merge",
            Self::Split => "memory.record.split",
            Self::Relocate => "memory.record.relocate",
            Self::Delete => "memory.record.delete",
            Self::Purge => "memory.record.purge",
            Self::Verify => "memory.record.verify",
            Self::Embed => "memory.embedding.publish",
            Self::Index => "memory.index.enqueue",
            Self::Summarize => "memory.summary.enqueue",
            Self::Import => "memory.import.prepare",
            Self::Export => "memory.export.prepare",
            Self::Maintenance => "memory.maintenance",
            Self::Diagnostics => "memory.diagnostics",
        }
    }

    pub const fn mutates(self) -> bool {
        matches!(
            self,
            Self::Create
                | Self::Update
                | Self::Archive
                | Self::Restore
                | Self::Merge
                | Self::Split
                | Self::Relocate
                | Self::Delete
                | Self::Purge
                | Self::Verify
                | Self::Embed
                | Self::Index
                | Self::Summarize
                | Self::Import
                | Self::Export
                | Self::Maintenance
        )
    }

    pub const fn capability(self) -> CapabilityOperation {
        match self {
            Self::RecordRead | Self::Search | Self::EmbeddingQuery | Self::Diagnostics => {
                CapabilityOperation::Read
            }
            Self::Create | Self::Import => CapabilityOperation::Append,
            Self::Update
            | Self::Merge
            | Self::Split
            | Self::Relocate
            | Self::Verify
            | Self::Embed
            | Self::Index
            | Self::Summarize => CapabilityOperation::Revise,
            Self::Archive => CapabilityOperation::Archive,
            Self::Restore => CapabilityOperation::Restore,
            Self::Delete | Self::Purge => CapabilityOperation::Delete,
            Self::Export => CapabilityOperation::Export,
            Self::Maintenance => CapabilityOperation::Administer,
        }
    }

    pub const fn mutation_event_name(self) -> Option<&'static str> {
        match self {
            Self::Create => Some("create"),
            Self::Update => Some("update"),
            Self::Archive => Some("archive"),
            Self::Restore => Some("restore"),
            Self::Merge => Some("merge"),
            Self::Split => Some("split"),
            Self::Relocate => Some("relocate"),
            Self::Delete => Some("delete"),
            Self::Purge => Some("purge"),
            Self::Verify => Some("verify"),
            Self::Embed => Some("embed"),
            Self::Index => Some("index"),
            Self::Summarize => Some("summarize"),
            Self::Import => Some("import"),
            Self::Export => Some("export"),
            Self::RecordRead
            | Self::Search
            | Self::EmbeddingQuery
            | Self::Maintenance
            | Self::Diagnostics => None,
        }
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct MemoryRequest {
    pub version: String,
    pub operation: MemoryOperation,
    pub actor_scope: Scope,
    pub target_scope: Scope,
    pub resource_id: Id,
    pub capability_id: Id,
    pub trace: Trace,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub expected_cursor: Option<Cursor>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub idempotency_key: Option<Id>,
    #[serde(default)]
    pub payload: Value,
}

impl MemoryRequest {
    pub fn validate(&self) -> MemoryResult<()> {
        if self.version != MEMORY_PROTOCOL {
            return Err(protocol_error(
                "MEMORY_PROTOCOL_UNSUPPORTED",
                "the memory protocol version is unsupported",
            ));
        }
        if self.operation.mutates()
            && (self.expected_cursor.is_none() || self.idempotency_key.is_none())
        {
            return Err(protocol_error(
                "MEMORY_MUTATION_FIELDS_REQUIRED",
                "memory mutations require an expected cursor and idempotency key",
            ));
        }
        if self.operation.mutates()
            && self
                .idempotency_key
                .as_ref()
                .is_none_or(|key| key != &self.trace.request_id)
        {
            return Err(protocol_error(
                "MEMORY_IDEMPOTENCY_MISMATCH",
                "the mutation idempotency key must equal the trace request id",
            ));
        }
        let bytes = serde_json::to_vec(&self.payload).map_err(|_| {
            protocol_error(
                "MEMORY_PAYLOAD_INVALID",
                "the memory payload cannot be encoded",
            )
        })?;
        if bytes.len() > MAX_MEMORY_PAYLOAD_BYTES {
            return Err(protocol_error(
                "MEMORY_PAYLOAD_TOO_LARGE",
                "the memory payload exceeds four MiB",
            ));
        }
        reject_non_integer_numbers(&self.payload)?;
        reject_secret_fields(&self.payload)?;
        Ok(())
    }
}

pub fn request_envelope(request: MemoryRequest) -> MemoryResult<Envelope> {
    request.validate()?;
    Ok(Envelope {
        protocol: hypermid_protocol::PROTOCOL.into(),
        kind: MessageKind::Request,
        message_id: request.trace.request_id.clone(),
        sequence: 1,
        reply_to: None,
        route_id: Some(Id::new("memory").expect("memory is a valid route identifier")),
        route_epoch: Some(1),
        operation: Some(request.operation.wire_name().into()),
        scope: Some(request.actor_scope.clone()),
        trace: Some(request.trace.clone()),
        deadline_ms: Some(30_000),
        payload: Some(serde_json::to_value(request).map_err(|_| {
            protocol_error(
                "MEMORY_REQUEST_INVALID",
                "the memory request document cannot be encoded",
            )
        })?),
        error: None,
    })
}

#[derive(Clone, Debug)]
pub struct BoundMemoryRequest {
    pub context: AuthContext,
    pub request: MemoryRequest,
}

pub fn bind_authenticated_request(
    principal: &Principal,
    authenticated_scope: &Scope,
    envelope: &Envelope,
    now_ms: u64,
) -> MemoryResult<BoundMemoryRequest> {
    envelope.validate().map_err(|_| {
        protocol_error(
            "MEMORY_ENVELOPE_INVALID",
            "the memory request envelope is invalid",
        )
    })?;
    if envelope.kind != MessageKind::Request {
        return Err(protocol_error(
            "MEMORY_ENVELOPE_INVALID",
            "memory calls require a request envelope",
        ));
    }
    let payload = envelope.payload.clone().ok_or_else(|| {
        protocol_error(
            "MEMORY_ENVELOPE_INVALID",
            "the memory request payload is absent",
        )
    })?;
    let request: MemoryRequest = serde_json::from_value(payload).map_err(|_| {
        protocol_error(
            "MEMORY_REQUEST_INVALID",
            "the memory request document is invalid",
        )
    })?;
    request.validate()?;
    if &request.actor_scope != authenticated_scope
        || envelope.scope.as_ref() != Some(authenticated_scope)
        || envelope.trace.as_ref() != Some(&request.trace)
        || envelope.message_id != request.trace.request_id
        || envelope.operation.as_deref() != Some(request.operation.wire_name())
    {
        return Err(authorization_denied());
    }
    if !principal
        .scopes
        .iter()
        .any(|scope| scope == request.operation.wire_name())
    {
        return Err(authorization_denied());
    }
    let principal_kind = match principal.kind {
        PrincipalKind::LocalUser | PrincipalKind::Device => CorePrincipalKind::Foreground,
        PrincipalKind::SupervisedModule | PrincipalKind::Service => CorePrincipalKind::Background,
    };
    let context = AuthContext {
        principal: AuthenticatedPrincipal {
            principal_id: principal.id.clone(),
            owner_id: authenticated_scope.owner_id.clone(),
            kind: principal_kind,
        },
        request: AuthorizationRequest {
            claimed_scope: authenticated_scope.clone(),
            target_scope: request.target_scope.clone(),
            operation: request.operation.capability(),
            resource_id: request.resource_id.clone(),
            now_ms,
        },
        capability_id: request.capability_id.clone(),
    };
    Ok(BoundMemoryRequest { context, request })
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct MemoryResponse {
    pub version: String,
    pub trace: Trace,
    pub cursor: Cursor,
    pub replayed: bool,
    #[serde(default)]
    pub payload: Value,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct MemoryEvent {
    pub cursor: Cursor,
    pub trace: Trace,
    pub operation: MemoryOperation,
    pub resource_id: Id,
    #[serde(default)]
    pub payload: Value,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct MemoryEventBatch {
    pub version: String,
    pub resumed_after: Cursor,
    pub events: Vec<MemoryEvent>,
    pub next_cursor: Cursor,
    pub terminated: bool,
}

impl MemoryEventBatch {
    pub fn validate(&self, maximum_events: usize) -> MemoryResult<()> {
        if self.version != MEMORY_PROTOCOL
            || maximum_events == 0
            || self.events.len() > maximum_events
        {
            return Err(protocol_error(
                "MEMORY_STREAM_INVALID",
                "the memory stream version or event bound is invalid",
            ));
        }
        let mut previous = self.resumed_after;
        for event in &self.events {
            if event.cursor.epoch != previous.epoch || event.cursor.sequence <= previous.sequence {
                return Err(protocol_error(
                    "MEMORY_STREAM_INVALID",
                    "memory stream cursors are replayed, reordered, or cross epochs",
                ));
            }
            previous = event.cursor;
        }
        if self.next_cursor != previous {
            return Err(protocol_error(
                "MEMORY_STREAM_INVALID",
                "the memory stream continuation cursor is invalid",
            ));
        }
        let bytes = serde_json::to_vec(self).map_err(|_| {
            protocol_error(
                "MEMORY_STREAM_INVALID",
                "the memory stream cannot be encoded",
            )
        })?;
        if bytes.len() > MAX_MEMORY_PAYLOAD_BYTES {
            return Err(protocol_error(
                "MEMORY_BACKPRESSURE",
                "the memory stream exceeds its byte credit",
            ));
        }
        Ok(())
    }
}

impl MemoryResponse {
    pub fn new(trace: Trace, cursor: Cursor, replayed: bool, payload: Value) -> Self {
        Self {
            version: MEMORY_PROTOCOL.into(),
            trace,
            cursor,
            replayed,
            payload,
        }
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct EmbeddingVectorWire {
    pub encoding: String,
    pub registration_id: Id,
    pub fingerprint: Digest,
    pub input_digest: Digest,
    pub dimensions: usize,
    pub vector_base64: String,
}

impl EmbeddingVectorWire {
    pub fn from_validated(value: &ValidatedEmbedding) -> Self {
        let bytes = value
            .vector
            .iter()
            .flat_map(|component| component.to_le_bytes())
            .collect::<Vec<_>>();
        Self {
            encoding: "f32le-base64".into(),
            registration_id: value.registration_id.clone(),
            fingerprint: value.fingerprint,
            input_digest: value.input_digest,
            dimensions: value.dimensions,
            vector_base64: BASE64.encode(bytes),
        }
    }

    pub fn decode(&self) -> MemoryResult<Vec<f32>> {
        if self.encoding != "f32le-base64"
            || self.dimensions == 0
            || self.dimensions > MAX_VECTOR_DIMENSIONS
        {
            return Err(protocol_error(
                "MEMORY_VECTOR_INVALID",
                "the embedding vector encoding or dimensions are invalid",
            ));
        }
        let bytes = BASE64.decode(&self.vector_base64).map_err(|_| {
            protocol_error(
                "MEMORY_VECTOR_INVALID",
                "the embedding vector is not canonical base64",
            )
        })?;
        if bytes.len() != self.dimensions.saturating_mul(4) {
            return Err(protocol_error(
                "MEMORY_VECTOR_INVALID",
                "the embedding vector byte length does not match its dimensions",
            ));
        }
        let vector = bytes
            .chunks_exact(4)
            .map(|chunk| f32::from_le_bytes(chunk.try_into().expect("four byte chunk")))
            .collect::<Vec<_>>();
        if vector.iter().any(|value| !value.is_finite())
            || vector
                .iter()
                .map(|value| f64::from(*value).powi(2))
                .sum::<f64>()
                <= f64::EPSILON
        {
            return Err(protocol_error(
                "MEMORY_VECTOR_INVALID",
                "the embedding vector is non-finite or has zero norm",
            ));
        }
        Ok(vector)
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MutationReceipt {
    pub cursor: Cursor,
    pub operation: String,
    pub record_id: Option<Id>,
    pub result_digest: Option<Digest>,
    pub replayed: bool,
}

pub fn acknowledged_mutation(
    connection: &Connection,
    request: &MemoryRequest,
) -> MemoryResult<Option<MutationReceipt>> {
    if !request.operation.mutates() || request.operation.mutation_event_name().is_none() {
        return Ok(None);
    }
    request.validate()?;
    let row: Option<(u64, u64, String, Option<String>, Option<String>)> = connection
        .query_row(
            "SELECT epoch, sequence, operation, record_id, result_revision_digest
             FROM memory_mutation_events
             WHERE owner_scope_digest=?1
               AND json_extract(trace_json, '$.request_id')=?2
             ORDER BY sequence ASC LIMIT 1",
            params![
                scope_digest(&request.target_scope).to_hex(),
                request.trace.request_id.as_str()
            ],
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
        .map_err(|_| store_error())?;
    let Some((epoch, sequence, operation, record_id, result_digest)) = row else {
        return Ok(None);
    };
    if record_id.as_deref() != Some(request.resource_id.as_str())
        || request.operation.mutation_event_name() != Some(operation.as_str())
    {
        return Err(protocol_error(
            "MEMORY_IDEMPOTENCY_CONFLICT",
            "the idempotency key was used for another memory operation or resource",
        ));
    }
    Ok(Some(MutationReceipt {
        cursor: Cursor::new(epoch, sequence).map_err(|_| store_error())?,
        operation,
        record_id: record_id
            .map(Id::new)
            .transpose()
            .map_err(|_| store_error())?,
        result_digest: result_digest
            .map(|value| Digest::from_str(&value))
            .transpose()
            .map_err(|_| store_error())?,
        replayed: true,
    }))
}

fn reject_secret_fields(value: &Value) -> MemoryResult<()> {
    match value {
        Value::Array(values) => values.iter().try_for_each(reject_secret_fields),
        Value::Object(values) => {
            for (key, value) in values {
                let normalized = key.to_ascii_lowercase();
                if matches!(
                    normalized.as_str(),
                    "secret" | "password" | "api_key" | "credential" | "access_token"
                ) || normalized.ends_with("_secret")
                    || normalized.ends_with("_password")
                {
                    return Err(protocol_error(
                        "MEMORY_SECRET_FIELD_REFUSED",
                        "secret-bearing fields cannot cross the memory transport",
                    ));
                }
                reject_secret_fields(value)?;
            }
            Ok(())
        }
        _ => Ok(()),
    }
}

fn reject_non_integer_numbers(value: &Value) -> MemoryResult<()> {
    match value {
        Value::Number(number) if !number.is_i64() && !number.is_u64() => Err(protocol_error(
            "MEMORY_PAYLOAD_INVALID",
            "memory transport numbers must be canonical integers",
        )),
        Value::Array(values) => values.iter().try_for_each(reject_non_integer_numbers),
        Value::Object(values) => values.values().try_for_each(reject_non_integer_numbers),
        _ => Ok(()),
    }
}

fn protocol_error(code: &'static str, message: &'static str) -> hypermid_contracts::Error {
    error(code, message, EffectState::NotStarted)
}

fn authorization_denied() -> hypermid_contracts::Error {
    error(
        "AUTHORIZATION_DENIED",
        "the authenticated transport identity does not authorize this memory request",
        EffectState::NotStarted,
    )
}

fn store_error() -> hypermid_contracts::Error {
    error(
        "STORE_READ_FAILED",
        "the mutation receipt could not be read",
        EffectState::Unknown,
    )
}

#[cfg(test)]
mod protocol_tests {
    use super::*;
    use crate::MemoryStore;
    use rusqlite::params;
    use serde_json::json;

    fn id(value: &str) -> Id {
        Id::new(value).unwrap()
    }

    fn scope(owner: &str) -> Scope {
        Scope::new(id(owner), id("project-1"), Some(id("workspace-1")))
    }

    fn request(actor_scope: Scope, operation: MemoryOperation, request_id: &str) -> MemoryRequest {
        let trace = Trace::new(id("trace-1"), id(request_id));
        MemoryRequest {
            version: MEMORY_PROTOCOL.into(),
            operation,
            actor_scope: actor_scope.clone(),
            target_scope: actor_scope,
            resource_id: id("record-1"),
            capability_id: id("capability-1"),
            trace: trace.clone(),
            expected_cursor: operation.mutates().then(|| Cursor::new(1, 0).unwrap()),
            idempotency_key: operation.mutates().then_some(trace.request_id),
            payload: json!({"content": "protocol conformance"}),
        }
    }

    #[test]
    fn protocol_binds_authenticated_scope_and_refuses_secret_fields() {
        let actor = scope("owner-1");
        let request = request(actor.clone(), MemoryOperation::Create, "request-1");
        let envelope = request_envelope(request.clone()).unwrap();
        let principal = Principal {
            id: id("gideon-1"),
            kind: PrincipalKind::Service,
            scopes: vec![MemoryOperation::Create.wire_name().into()],
            module_id: None,
            spawn_generation: None,
        };
        let bound = bind_authenticated_request(&principal, &actor, &envelope, 10).unwrap();
        assert_eq!(bound.context.request.claimed_scope, actor);
        assert_eq!(bound.context.request.operation, CapabilityOperation::Append);

        let denied =
            bind_authenticated_request(&principal, &scope("owner-2"), &envelope, 10).unwrap_err();
        assert_eq!(denied.code, "AUTHORIZATION_DENIED");

        let mut secret = request;
        secret.payload = json!({"nested": {"api_key": "refused"}});
        assert_eq!(
            secret.validate().unwrap_err().code,
            "MEMORY_SECRET_FIELD_REFUSED"
        );
    }

    #[test]
    fn protocol_vector_and_stream_are_bounded_and_cursor_ordered() {
        let embedding = ValidatedEmbedding {
            vector: vec![0.25, -0.5, 0.75],
            dimensions: 3,
            norm: 0.935_414_346_7,
            registration_id: id("registration-1"),
            fingerprint: Digest::sha256(b"registration"),
            input_digest: Digest::sha256(b"input"),
            input_tokens: None,
            cost_units: None,
        };
        let wire = EmbeddingVectorWire::from_validated(&embedding);
        assert_eq!(wire.decode().unwrap(), embedding.vector);

        let cursor0 = Cursor::new(1, 0).unwrap();
        let cursor1 = Cursor::new(1, 1).unwrap();
        let event = MemoryEvent {
            cursor: cursor1,
            trace: Trace::new(id("trace-1"), id("request-1")),
            operation: MemoryOperation::Create,
            resource_id: id("record-1"),
            payload: json!({}),
        };
        MemoryEventBatch {
            version: MEMORY_PROTOCOL.into(),
            resumed_after: cursor0,
            events: vec![event.clone()],
            next_cursor: cursor1,
            terminated: true,
        }
        .validate(1)
        .unwrap();
        let replayed = MemoryEventBatch {
            version: MEMORY_PROTOCOL.into(),
            resumed_after: cursor1,
            events: vec![event],
            next_cursor: cursor1,
            terminated: true,
        };
        assert_eq!(
            replayed.validate(1).unwrap_err().code,
            "MEMORY_STREAM_INVALID"
        );
    }

    #[test]
    fn protocol_recovers_only_the_exact_acknowledged_mutation() {
        let directory = tempfile::tempdir().unwrap();
        let mut store = MemoryStore::open(directory.path().join("memory.sqlite3")).unwrap();
        let actor = scope("owner-1");
        let request = request(actor.clone(), MemoryOperation::Create, "mutation-1");
        let actor_digest = scope_digest(&actor).to_hex();
        let trace_json = serde_json::to_string(&request.trace).unwrap();
        store
            .immediate(|transaction| {
                transaction.ensure_scope(&actor, 10)?;
                let cursor = transaction.advance_cursor(&actor, 10)?;
                transaction
                    .raw()
                    .execute(
                        "INSERT INTO memory_mutation_events(
                            event_id, owner_scope_digest, epoch, sequence, operation,
                            record_id, previous_revision_digest, result_revision_digest,
                            actor_scope_digest, grant_id, authorization_basis,
                            trace_json, created_at_ms
                         ) VALUES (?1, ?2, ?3, ?4, 'create', ?5, NULL, ?6, ?2, NULL,
                             'owner', ?7, 10)",
                        params![
                            "event-1",
                            actor_digest,
                            cursor.epoch,
                            cursor.sequence,
                            request.resource_id.as_str(),
                            Digest::sha256(b"revision").to_hex(),
                            trace_json,
                        ],
                    )
                    .unwrap();
                Ok(())
            })
            .unwrap();
        let receipt = store
            .read(|connection| acknowledged_mutation(connection, &request))
            .unwrap()
            .unwrap();
        assert_eq!(receipt.cursor, Cursor::new(1, 1).unwrap());
        assert!(receipt.replayed);

        let conflicting = MemoryRequest {
            operation: MemoryOperation::Update,
            ..request
        };
        let error = store
            .read(|connection| acknowledged_mutation(connection, &conflicting))
            .unwrap_err();
        assert_eq!(error.code, "MEMORY_IDEMPOTENCY_CONFLICT");
    }
}
