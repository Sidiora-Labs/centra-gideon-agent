use base64::{engine::general_purpose::STANDARD as BASE64, Engine as _};
pub use hypermid_contracts::{Cursor, Digest, EffectState, Error, Id, Scope, Trace};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;

pub const PROTOCOL_VERSION: &str = "1.0";
pub const MAX_ENVELOPE_BYTES: usize = 8 * 1024 * 1024;
pub const MAX_CHUNK_BYTES: usize = 64 * 1024;
pub const MAX_CHUNKS: u32 = 131_072;

pub type ProtocolError = Error;

#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Operation {
    Bind,
    Rebind,
    Ingest,
    Project,
    Reduce,
    Expand,
    SummaryClaim,
    SummaryPublish,
    Recover,
    Configure,
    SubagentSpawn,
    SubagentContribute,
    Export,
    Import,
    Diagnose,
}

impl Operation {
    pub fn is_mutation(self) -> bool {
        matches!(
            self,
            Self::Rebind
                | Self::Ingest
                | Self::Reduce
                | Self::SummaryPublish
                | Self::Configure
                | Self::SubagentSpawn
                | Self::SubagentContribute
                | Self::Import
        )
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RenderMode {
    HostSerialized,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct WriterLease {
    pub lease_id: Id,
    pub session_id: Id,
    pub scope: Scope,
    pub fence_token: Id,
    pub acquired_at: String,
    pub expires_at: String,
    pub cursor: Cursor,
}

impl WriterLease {
    pub fn validate(&self) -> Result<(), ProtocolViolation> {
        if self.acquired_at.is_empty() || self.expires_at.is_empty() {
            return Err(ProtocolViolation::InvalidLease);
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ProtocolRequest {
    pub protocol_version: String,
    pub operation: Operation,
    pub scope: Scope,
    pub trace: Trace,
    pub session_id: Id,
    pub render_mode: RenderMode,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub expected_cursor: Option<Cursor>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub writer_lease: Option<WriterLease>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub idempotency_key: Option<Id>,
    pub payload: Value,
}

impl ProtocolRequest {
    pub fn validate(&self) -> Result<(), ProtocolViolation> {
        if self.protocol_version != PROTOCOL_VERSION {
            return Err(ProtocolViolation::UnsupportedVersion);
        }
        if !self.payload.is_object() {
            return Err(ProtocolViolation::InvalidPayload);
        }
        if self.operation.is_mutation()
            && (self.expected_cursor.is_none()
                || self.writer_lease.is_none()
                || self.idempotency_key.is_none())
        {
            return Err(ProtocolViolation::MutationFieldsRequired);
        }
        if self.operation == Operation::Project {
            if self.expected_cursor.is_none() || self.idempotency_key.is_none() {
                return Err(ProtocolViolation::MutationFieldsRequired);
            }
            if self.payload.get("mode").and_then(Value::as_str) == Some("primary")
                && self.writer_lease.is_none()
            {
                return Err(ProtocolViolation::WriterLeaseRequired);
            }
        }
        if let Some(lease) = &self.writer_lease {
            lease.validate()?;
            if lease.session_id != self.session_id || lease.scope != self.scope {
                return Err(ProtocolViolation::LeaseBindingMismatch);
            }
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ProtocolResponse {
    pub protocol_version: String,
    pub operation: Operation,
    pub scope: Scope,
    pub trace: Trace,
    pub session_id: Id,
    pub cursor: Cursor,
    pub ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<ProtocolError>,
}

impl ProtocolResponse {
    pub fn validate(&self) -> Result<(), ProtocolViolation> {
        if self.protocol_version != PROTOCOL_VERSION {
            return Err(ProtocolViolation::UnsupportedVersion);
        }
        match (self.ok, self.result.is_some(), self.error.is_some()) {
            (true, true, false) | (false, false, true) => Ok(()),
            _ => Err(ProtocolViolation::InvalidResponse),
        }
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct TransferChunk {
    pub protocol_version: String,
    pub transfer_id: Id,
    pub ordinal: u32,
    pub total: u32,
    pub chunk_digest: Digest,
    pub full_digest: Digest,
    pub payload_base64: String,
    pub trace: Trace,
}

impl TransferChunk {
    pub fn decode_payload(&self) -> Result<Vec<u8>, ProtocolViolation> {
        if self.protocol_version != PROTOCOL_VERSION {
            return Err(ProtocolViolation::UnsupportedVersion);
        }
        if self.total == 0 || self.total > MAX_CHUNKS || self.ordinal >= self.total {
            return Err(ProtocolViolation::InvalidChunkOrder);
        }
        let payload = BASE64
            .decode(&self.payload_base64)
            .map_err(|_| ProtocolViolation::InvalidBase64)?;
        if payload.len() > MAX_CHUNK_BYTES {
            return Err(ProtocolViolation::ChunkTooLarge);
        }
        if Digest::sha256(&payload) != self.chunk_digest {
            return Err(ProtocolViolation::ChunkDigestMismatch);
        }
        Ok(payload)
    }
}

#[derive(Default)]
pub struct FrameDecoder {
    buffer: Vec<u8>,
}

impl FrameDecoder {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn feed(&mut self, input: &[u8]) -> Result<Vec<Value>, ProtocolViolation> {
        self.buffer.extend_from_slice(input);
        let mut frames = Vec::new();
        loop {
            if self.buffer.len() < 4 {
                break;
            }
            let length = u32::from_be_bytes(
                self.buffer[..4]
                    .try_into()
                    .expect("four-byte prefix was checked"),
            ) as usize;
            if length > MAX_ENVELOPE_BYTES {
                self.buffer.clear();
                return Err(ProtocolViolation::EnvelopeTooLarge);
            }
            if self.buffer.len() < length + 4 {
                break;
            }
            let body = self.buffer[4..length + 4].to_vec();
            self.buffer.drain(..length + 4);
            let value: Value =
                serde_json::from_slice(&body).map_err(|_| ProtocolViolation::InvalidJson)?;
            if !value.is_object() {
                return Err(ProtocolViolation::InvalidEnvelope);
            }
            frames.push(value);
        }
        Ok(frames)
    }

    pub fn finish(self) -> Result<(), ProtocolViolation> {
        if self.buffer.is_empty() {
            Ok(())
        } else {
            Err(ProtocolViolation::IncompleteFrame)
        }
    }
}

pub fn encode_frame<T: Serialize>(value: &T) -> Result<Vec<u8>, ProtocolViolation> {
    let value = serde_json::to_value(value).map_err(|_| ProtocolViolation::InvalidJson)?;
    if !value.is_object() {
        return Err(ProtocolViolation::InvalidEnvelope);
    }
    let body = serde_json::to_vec(&value).map_err(|_| ProtocolViolation::InvalidJson)?;
    if body.len() > MAX_ENVELOPE_BYTES {
        return Err(ProtocolViolation::EnvelopeTooLarge);
    }
    let length = u32::try_from(body.len()).map_err(|_| ProtocolViolation::EnvelopeTooLarge)?;
    let mut frame = Vec::with_capacity(body.len() + 4);
    frame.extend_from_slice(&length.to_be_bytes());
    frame.extend_from_slice(&body);
    Ok(frame)
}

struct TransferState {
    total: u32,
    full_digest: Digest,
    parts: Vec<Vec<u8>>,
}

#[derive(Default)]
pub struct ChunkAssembler {
    transfers: HashMap<Id, TransferState>,
}

impl ChunkAssembler {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn add(&mut self, chunk: TransferChunk) -> Result<Option<Vec<u8>>, ProtocolViolation> {
        let payload = chunk.decode_payload()?;
        let state = self
            .transfers
            .entry(chunk.transfer_id.clone())
            .or_insert_with(|| TransferState {
                total: chunk.total,
                full_digest: chunk.full_digest,
                parts: Vec::new(),
            });
        if state.total != chunk.total || state.full_digest != chunk.full_digest {
            self.transfers.remove(&chunk.transfer_id);
            return Err(ProtocolViolation::ChunkConflict);
        }
        if chunk.ordinal as usize != state.parts.len() {
            return Err(ProtocolViolation::InvalidChunkOrder);
        }
        state.parts.push(payload);
        if state.parts.len() != state.total as usize {
            return Ok(None);
        }
        let state = self
            .transfers
            .remove(&chunk.transfer_id)
            .expect("completed transfer exists");
        let assembled: Vec<u8> = state.parts.into_iter().flatten().collect();
        if Digest::sha256(&assembled) != state.full_digest {
            return Err(ProtocolViolation::FullDigestMismatch);
        }
        Ok(Some(assembled))
    }

    pub fn finish(&mut self, transfer_id: &Id) -> Result<(), ProtocolViolation> {
        if self.transfers.remove(transfer_id).is_some() {
            Err(ProtocolViolation::IncompleteTransfer)
        } else {
            Ok(())
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, thiserror::Error)]
pub enum ProtocolViolation {
    #[error("unsupported protocol version")]
    UnsupportedVersion,
    #[error("envelope exceeds eight MiB")]
    EnvelopeTooLarge,
    #[error("envelope is not valid UTF-8 JSON")]
    InvalidJson,
    #[error("envelope must be a JSON object")]
    InvalidEnvelope,
    #[error("stream ended within a frame")]
    IncompleteFrame,
    #[error("payload must be a JSON object")]
    InvalidPayload,
    #[error("mutation fields are incomplete")]
    MutationFieldsRequired,
    #[error("primary projection requires a writer lease")]
    WriterLeaseRequired,
    #[error("writer lease is malformed")]
    InvalidLease,
    #[error("writer lease does not match request scope and session")]
    LeaseBindingMismatch,
    #[error("response result and error fields do not match ok")]
    InvalidResponse,
    #[error("chunk ordinal or total is invalid")]
    InvalidChunkOrder,
    #[error("chunk payload is not valid base64")]
    InvalidBase64,
    #[error("chunk exceeds 64 KiB")]
    ChunkTooLarge,
    #[error("chunk digest does not match")]
    ChunkDigestMismatch,
    #[error("transfer metadata changed")]
    ChunkConflict,
    #[error("assembled transfer digest does not match")]
    FullDigestMismatch,
    #[error("transfer ended before all chunks arrived")]
    IncompleteTransfer,
}
