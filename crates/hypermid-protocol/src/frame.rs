use std::collections::HashSet;

use base64::{engine::general_purpose::STANDARD as BASE64, Engine as _};
use hypermid_contracts::{Digest, Error, Id, Scope, Trace, MAX_SAFE_INTEGER};
use serde::{de::DeserializeOwned, Deserialize, Serialize};
use serde_json::Value;
use thiserror::Error;

pub const PROTOCOL: &str = "hypermid.v1";
pub const MAX_FRAME_BYTES: usize = 8 * 1024 * 1024;
pub const MAX_CHUNK_BYTES: usize = 64 * 1024;

#[derive(Debug, Error)]
pub enum ProtocolError {
    #[error("frame body is empty")]
    EmptyFrame,
    #[error("frame body length {actual} exceeds {maximum} bytes")]
    FrameTooLarge { actual: usize, maximum: usize },
    #[error("frame is not valid UTF-8")]
    InvalidUtf8,
    #[error("frame contains duplicate object key {0:?}")]
    DuplicateKey(String),
    #[error("frame contains a non-canonical integer")]
    NonCanonicalInteger,
    #[error("invalid JSON: {0}")]
    InvalidJson(String),
    #[error("unsupported protocol {received:?}; supported protocol is {PROTOCOL}")]
    UnsupportedProtocol { received: String },
    #[error("invalid event chunk: {0}")]
    InvalidChunk(&'static str),
    #[error("event digest mismatch")]
    DigestMismatch,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MessageKind {
    Request,
    Response,
    Event,
    Credit,
    Cancel,
    Ping,
    Pong,
    Close,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Envelope {
    pub protocol: String,
    pub kind: MessageKind,
    pub message_id: Id,
    pub sequence: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reply_to: Option<Id>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub route_id: Option<Id>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub route_epoch: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub operation: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub scope: Option<Scope>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub trace: Option<Trace>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub deadline_ms: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub payload: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<Error>,
}

impl Envelope {
    pub fn validate(&self) -> Result<(), ProtocolError> {
        if self.protocol != PROTOCOL {
            return Err(ProtocolError::UnsupportedProtocol {
                received: self.protocol.clone(),
            });
        }
        match self.kind {
            MessageKind::Request => {
                if self.route_id.is_none()
                    || self.route_epoch.is_none()
                    || self.operation.is_none()
                    || self.deadline_ms.is_none()
                {
                    return Err(ProtocolError::InvalidJson(
                        "request is missing route_id, route_epoch, operation, or deadline_ms"
                            .into(),
                    ));
                }
            }
            MessageKind::Response | MessageKind::Cancel if self.reply_to.is_none() => {
                return Err(ProtocolError::InvalidJson(
                    "response or cancel is missing reply_to".into(),
                ));
            }
            MessageKind::Credit
                if self.route_id.is_none()
                    || self.route_epoch.is_none()
                    || self.payload.is_none() =>
            {
                return Err(ProtocolError::InvalidJson(
                    "credit is missing route_id, route_epoch, or payload".into(),
                ));
            }
            _ => {}
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EventChunk {
    pub event_id: Id,
    pub chunk_index: u32,
    pub chunk_count: u32,
    pub digest: Digest,
    pub data: String,
}

impl EventChunk {
    pub fn decoded(&self) -> Result<Vec<u8>, ProtocolError> {
        let data = BASE64
            .decode(&self.data)
            .map_err(|_| ProtocolError::InvalidChunk("data is not canonical base64"))?;
        if data.len() > MAX_CHUNK_BYTES {
            return Err(ProtocolError::InvalidChunk("chunk exceeds 64 KiB"));
        }
        Ok(data)
    }
}

pub fn chunk_event(event_id: Id, body: &[u8]) -> Result<Vec<EventChunk>, ProtocolError> {
    if body.is_empty() {
        return Err(ProtocolError::InvalidChunk("event body is empty"));
    }
    let count = body.len().div_ceil(MAX_CHUNK_BYTES);
    if count > 131_072 {
        return Err(ProtocolError::InvalidChunk("event has too many chunks"));
    }
    let digest = Digest::sha256(body);
    Ok(body
        .chunks(MAX_CHUNK_BYTES)
        .enumerate()
        .map(|(index, data)| EventChunk {
            event_id: event_id.clone(),
            chunk_index: index as u32,
            chunk_count: count as u32,
            digest: digest.clone(),
            data: BASE64.encode(data),
        })
        .collect())
}

#[derive(Default)]
pub struct EventReassembler {
    event_id: Option<Id>,
    digest: Option<Digest>,
    count: Option<u32>,
    next: u32,
    bytes: Vec<u8>,
}

impl EventReassembler {
    pub fn push(&mut self, chunk: EventChunk) -> Result<Option<Vec<u8>>, ProtocolError> {
        if chunk.chunk_count == 0 || chunk.chunk_index >= chunk.chunk_count {
            return Err(ProtocolError::InvalidChunk("invalid chunk count or index"));
        }
        if chunk.chunk_index != self.next {
            return Err(ProtocolError::InvalidChunk(
                "chunk is reordered or replayed",
            ));
        }
        if self.next == 0 {
            self.event_id = Some(chunk.event_id.clone());
            self.digest = Some(chunk.digest.clone());
            self.count = Some(chunk.chunk_count);
        } else if self.event_id.as_ref() != Some(&chunk.event_id)
            || self.digest.as_ref() != Some(&chunk.digest)
            || self.count != Some(chunk.chunk_count)
        {
            return Err(ProtocolError::InvalidChunk(
                "chunk transfer metadata changed",
            ));
        }
        self.bytes.extend_from_slice(&chunk.decoded()?);
        self.next += 1;
        if self.next != chunk.chunk_count {
            return Ok(None);
        }
        let actual = Digest::sha256(&self.bytes);
        if self.digest != Some(actual) {
            return Err(ProtocolError::DigestMismatch);
        }
        Ok(Some(std::mem::take(&mut self.bytes)))
    }
}

pub fn parse_json<T: DeserializeOwned>(body: &[u8]) -> Result<T, ProtocolError> {
    if body.is_empty() {
        return Err(ProtocolError::EmptyFrame);
    }
    if body.len() > MAX_FRAME_BYTES {
        return Err(ProtocolError::FrameTooLarge {
            actual: body.len(),
            maximum: MAX_FRAME_BYTES,
        });
    }
    std::str::from_utf8(body).map_err(|_| ProtocolError::InvalidUtf8)?;
    reject_duplicate_keys(body)?;
    let value: Value = serde_json::from_slice(body)
        .map_err(|error| ProtocolError::InvalidJson(error.to_string()))?;
    validate_wire_numbers(&value)?;
    serde_json::from_value(value).map_err(|error| ProtocolError::InvalidJson(error.to_string()))
}

fn validate_wire_numbers(value: &Value) -> Result<(), ProtocolError> {
    match value {
        Value::Number(number) => {
            if let Some(integer) = number.as_i64() {
                if integer < -(MAX_SAFE_INTEGER as i64) || integer > MAX_SAFE_INTEGER as i64 {
                    return Err(ProtocolError::NonCanonicalInteger);
                }
            } else if let Some(integer) = number.as_u64() {
                if integer > MAX_SAFE_INTEGER {
                    return Err(ProtocolError::NonCanonicalInteger);
                }
            } else if let Some(float) = number.as_f64() {
                if !float.is_finite()
                    || (float.fract() == 0.0 && float.abs() > MAX_SAFE_INTEGER as f64)
                {
                    return Err(ProtocolError::NonCanonicalInteger);
                }
            } else {
                return Err(ProtocolError::NonCanonicalInteger);
            }
            Ok(())
        }
        Value::Array(values) => values.iter().try_for_each(validate_wire_numbers),
        Value::Object(values) => values.values().try_for_each(validate_wire_numbers),
        _ => Ok(()),
    }
}

fn reject_duplicate_keys(body: &[u8]) -> Result<(), ProtocolError> {
    use serde::de::{MapAccess, SeqAccess, Visitor};

    struct UniqueVisitor;
    impl<'de> Visitor<'de> for UniqueVisitor {
        type Value = ();

        fn expecting(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
            formatter.write_str("a JSON value without duplicate keys")
        }
        fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<(), A::Error> {
            let mut keys = HashSet::new();
            while let Some(key) = map.next_key::<String>()? {
                if !keys.insert(key.clone()) {
                    return Err(serde::de::Error::custom(format!("duplicate key {key:?}")));
                }
                map.next_value_seed(UniqueSeed)?;
            }
            Ok(())
        }
        fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<(), A::Error> {
            while seq.next_element_seed(UniqueSeed)?.is_some() {}
            Ok(())
        }
        fn visit_bool<E: serde::de::Error>(self, _: bool) -> Result<(), E> {
            Ok(())
        }
        fn visit_i64<E: serde::de::Error>(self, _: i64) -> Result<(), E> {
            Ok(())
        }
        fn visit_u64<E: serde::de::Error>(self, _: u64) -> Result<(), E> {
            Ok(())
        }
        fn visit_f64<E: serde::de::Error>(self, _: f64) -> Result<(), E> {
            Ok(())
        }
        fn visit_str<E: serde::de::Error>(self, _: &str) -> Result<(), E> {
            Ok(())
        }
        fn visit_string<E: serde::de::Error>(self, _: String) -> Result<(), E> {
            Ok(())
        }
        fn visit_none<E: serde::de::Error>(self) -> Result<(), E> {
            Ok(())
        }
        fn visit_unit<E: serde::de::Error>(self) -> Result<(), E> {
            Ok(())
        }
        fn visit_some<D: serde::Deserializer<'de>>(self, deserializer: D) -> Result<(), D::Error> {
            deserializer.deserialize_any(UniqueVisitor)
        }
        fn visit_newtype_struct<D: serde::Deserializer<'de>>(
            self,
            deserializer: D,
        ) -> Result<(), D::Error> {
            deserializer.deserialize_any(UniqueVisitor)
        }
        fn visit_bytes<E: serde::de::Error>(self, _: &[u8]) -> Result<(), E> {
            Ok(())
        }
        fn visit_byte_buf<E: serde::de::Error>(self, _: Vec<u8>) -> Result<(), E> {
            Ok(())
        }
    }
    struct UniqueSeed;
    impl<'de> serde::de::DeserializeSeed<'de> for UniqueSeed {
        type Value = ();
        fn deserialize<D: serde::Deserializer<'de>>(self, deserializer: D) -> Result<(), D::Error> {
            deserializer.deserialize_any(UniqueVisitor)
        }
    }

    let mut deserializer = serde_json::Deserializer::from_slice(body);
    serde::de::DeserializeSeed::deserialize(UniqueSeed, &mut deserializer).map_err(|error| {
        let message = error.to_string();
        if let Some(start) = message.find("duplicate key ") {
            ProtocolError::DuplicateKey(
                message[start + 14..]
                    .split(" at line")
                    .next()
                    .unwrap_or("")
                    .trim_matches('"')
                    .to_owned(),
            )
        } else {
            ProtocolError::InvalidJson(message)
        }
    })?;
    deserializer
        .end()
        .map_err(|error| ProtocolError::InvalidJson(error.to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn duplicate_keys_and_unsafe_numbers_are_refused() {
        assert!(matches!(
            parse_json::<Value>(br#"{"a":1,"a":2}"#),
            Err(ProtocolError::DuplicateKey(_))
        ));
        assert_eq!(
            parse_json::<Value>(br#"{"importance":0.75,"confidence":1e-3}"#).unwrap(),
            serde_json::json!({"importance": 0.75, "confidence": 1e-3})
        );
        assert!(matches!(
            parse_json::<Value>(br#"{"a":9007199254740992}"#),
            Err(ProtocolError::NonCanonicalInteger)
        ));
        assert!(matches!(
            parse_json::<Value>(br#"{"a":-9007199254740992}"#),
            Err(ProtocolError::NonCanonicalInteger)
        ));
        assert!(parse_json::<Value>(br#"{"a":NaN}"#).is_err());
        assert!(parse_json::<Value>(br#"{"a":1e400}"#).is_err());
    }

    #[test]
    fn chunks_reassemble_and_replays_fail() {
        let body = vec![7; MAX_CHUNK_BYTES + 1];
        let chunks = chunk_event(Id::new("event-1").unwrap(), &body).unwrap();
        let mut reassembler = EventReassembler::default();
        assert!(reassembler.push(chunks[0].clone()).unwrap().is_none());
        assert_eq!(reassembler.push(chunks[1].clone()).unwrap().unwrap(), body);
        let mut replayed = EventReassembler::default();
        assert!(replayed.push(chunks[1].clone()).is_err());
    }
}
