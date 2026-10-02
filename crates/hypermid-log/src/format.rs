use crate::filter::{valid_logger, Level};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;
use thiserror::Error;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct BoundField {
    pub key: String,
    pub value: String,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct LogRecord {
    pub timestamp: String,
    pub level: Level,
    pub logger: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub bound: Vec<BoundField>,
    pub message: String,
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub fields: BTreeMap<String, Value>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Error)]
pub enum ParseError {
    #[error("log line is not valid UTF-8 JSON")]
    InvalidJson,
    #[error("log line does not match the canonical shape")]
    InvalidShape,
    #[error("log line timestamp is invalid")]
    InvalidTimestamp,
    #[error("log line logger is invalid")]
    InvalidLogger,
    #[error("log line contains a newline")]
    EmbeddedNewline,
}

pub fn format_line(record: &LogRecord) -> Result<Vec<u8>, ParseError> {
    validate(record)?;
    let mut result = serde_json::to_vec(record).map_err(|_| ParseError::InvalidShape)?;
    result.push(b'\n');
    Ok(result)
}

pub fn parse_line(line: &[u8]) -> Result<LogRecord, ParseError> {
    if line[..line.len().saturating_sub(1)].contains(&b'\n') || line.contains(&b'\r') {
        return Err(ParseError::EmbeddedNewline);
    }
    let body = line.strip_suffix(b"\n").unwrap_or(line);
    let record: LogRecord = serde_json::from_slice(body).map_err(|_| ParseError::InvalidJson)?;
    validate(&record)?;
    Ok(record)
}

fn validate(record: &LogRecord) -> Result<(), ParseError> {
    if !timestamp_shape(&record.timestamp) {
        return Err(ParseError::InvalidTimestamp);
    }
    if !valid_logger(&record.logger) {
        return Err(ParseError::InvalidLogger);
    }
    if record.message.contains(['\n', '\r']) {
        return Err(ParseError::EmbeddedNewline);
    }
    if record
        .bound
        .iter()
        .any(|item| item.key.is_empty() || item.key.len() > 160)
    {
        return Err(ParseError::InvalidShape);
    }
    Ok(())
}

fn timestamp_shape(value: &str) -> bool {
    value.len() >= 20
        && value.as_bytes().get(4) == Some(&b'-')
        && value.as_bytes().get(7) == Some(&b'-')
        && value.as_bytes().get(10) == Some(&b'T')
        && value.ends_with('Z')
}
