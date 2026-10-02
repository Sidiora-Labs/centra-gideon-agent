use serde::{de, Deserialize, Deserializer, Serialize, Serializer};
use sha2::{Digest as _, Sha256};
use std::{fmt, str::FromStr};

pub mod storage;

pub const MAX_SAFE_INTEGER: u64 = 9_007_199_254_740_991;

#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct Id(String);

impl Id {
    pub fn new(value: impl Into<String>) -> Result<Self, ContractViolation> {
        let value = value.into();
        let bytes = value.as_bytes();
        if bytes.is_empty()
            || bytes.len() > 160
            || !bytes[0].is_ascii_alphanumeric()
            || !bytes[1..]
                .iter()
                .all(|byte| byte.is_ascii_alphanumeric() || b"._:-".contains(byte))
        {
            return Err(ContractViolation::InvalidId);
        }
        Ok(Self(value))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }

    pub fn into_string(self) -> String {
        self.0
    }
}

impl fmt::Display for Id {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl FromStr for Id {
    type Err = ContractViolation;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        Self::new(value)
    }
}

impl Serialize for Id {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        serializer.serialize_str(&self.0)
    }
}

impl<'de> Deserialize<'de> for Id {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        Self::new(String::deserialize(deserializer)?).map_err(de::Error::custom)
    }
}

#[derive(Clone, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Scope {
    pub owner_id: Id,
    pub project_id: Id,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub workspace_id: Option<Id>,
}

impl Scope {
    pub fn new(owner_id: Id, project_id: Id, workspace_id: Option<Id>) -> Self {
        Self {
            owner_id,
            project_id,
            workspace_id,
        }
    }
}

#[derive(Clone, Copy, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct Digest([u8; 32]);

impl Digest {
    pub fn from_bytes(bytes: [u8; 32]) -> Self {
        Self(bytes)
    }

    pub fn sha256(input: impl AsRef<[u8]>) -> Self {
        Self(Sha256::digest(input.as_ref()).into())
    }

    pub fn as_bytes(&self) -> &[u8; 32] {
        &self.0
    }

    pub fn to_hex(self) -> String {
        const HEX: &[u8; 16] = b"0123456789abcdef";
        let mut value = String::with_capacity(64);
        for byte in self.0 {
            value.push(HEX[(byte >> 4) as usize] as char);
            value.push(HEX[(byte & 0x0f) as usize] as char);
        }
        value
    }
}

impl fmt::Debug for Digest {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_tuple("Digest")
            .field(&self.to_hex())
            .finish()
    }
}

impl fmt::Display for Digest {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.to_hex())
    }
}

impl FromStr for Digest {
    type Err = ContractViolation;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        if value.len() != 64
            || !value
                .bytes()
                .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
        {
            return Err(ContractViolation::InvalidDigest);
        }
        let mut bytes = [0_u8; 32];
        for (index, pair) in value.as_bytes().chunks_exact(2).enumerate() {
            bytes[index] = (hex_nibble(pair[0])? << 4) | hex_nibble(pair[1])?;
        }
        Ok(Self(bytes))
    }
}

impl Serialize for Digest {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        serializer.serialize_str(&self.to_hex())
    }
}

impl<'de> Deserialize<'de> for Digest {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        Self::from_str(&String::deserialize(deserializer)?).map_err(de::Error::custom)
    }
}

fn hex_nibble(byte: u8) -> Result<u8, ContractViolation> {
    match byte {
        b'0'..=b'9' => Ok(byte - b'0'),
        b'a'..=b'f' => Ok(byte - b'a' + 10),
        _ => Err(ContractViolation::InvalidDigest),
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(try_from = "CursorWire", into = "CursorWire")]
pub struct Cursor {
    pub epoch: u64,
    pub sequence: u64,
}

impl Cursor {
    pub fn new(epoch: u64, sequence: u64) -> Result<Self, ContractViolation> {
        if epoch == 0 || epoch > MAX_SAFE_INTEGER || sequence > MAX_SAFE_INTEGER {
            return Err(ContractViolation::InvalidCursor);
        }
        Ok(Self { epoch, sequence })
    }

    pub fn next(self) -> Result<Self, ContractViolation> {
        Self::new(
            self.epoch,
            self.sequence
                .checked_add(1)
                .ok_or(ContractViolation::InvalidCursor)?,
        )
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct CursorWire {
    epoch: u64,
    sequence: u64,
}

impl TryFrom<CursorWire> for Cursor {
    type Error = ContractViolation;

    fn try_from(value: CursorWire) -> Result<Self, Self::Error> {
        Self::new(value.epoch, value.sequence)
    }
}

impl From<Cursor> for CursorWire {
    fn from(value: Cursor) -> Self {
        Self {
            epoch: value.epoch,
            sequence: value.sequence,
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum EffectState {
    NotStarted,
    Committed,
    Unknown,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(try_from = "ErrorWire", into = "ErrorWire")]
pub struct Error {
    pub code: String,
    pub message: String,
    pub retryable: bool,
    pub retry_after_ms: Option<u64>,
    pub effect_state: Option<EffectState>,
}

impl Error {
    pub fn new(
        code: impl Into<String>,
        message: impl Into<String>,
        retryable: bool,
        retry_after_ms: Option<u64>,
        effect_state: Option<EffectState>,
    ) -> Result<Self, ContractViolation> {
        let code = code.into();
        let message = message.into();
        let code_bytes = code.as_bytes();
        if code_bytes.len() < 2
            || code_bytes.len() > 64
            || !code_bytes[0].is_ascii_uppercase()
            || !code_bytes[1..]
                .iter()
                .all(|byte| byte.is_ascii_uppercase() || byte.is_ascii_digit() || *byte == b'_')
        {
            return Err(ContractViolation::InvalidErrorCode);
        }
        if message.chars().count() > 2_048 {
            return Err(ContractViolation::ErrorMessageTooLong);
        }
        if retry_after_ms.is_some_and(|delay| delay > 86_400_000) {
            return Err(ContractViolation::InvalidRetryDelay);
        }
        Ok(Self {
            code,
            message,
            retryable,
            retry_after_ms,
            effect_state,
        })
    }
}

impl fmt::Display for Error {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "{}: {}", self.code, self.message)
    }
}

impl std::error::Error for Error {}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct ErrorWire {
    code: String,
    message: String,
    retryable: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    retry_after_ms: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    effect_state: Option<EffectState>,
}

impl TryFrom<ErrorWire> for Error {
    type Error = ContractViolation;

    fn try_from(value: ErrorWire) -> Result<Self, Self::Error> {
        Self::new(
            value.code,
            value.message,
            value.retryable,
            value.retry_after_ms,
            value.effect_state,
        )
    }
}

impl From<Error> for ErrorWire {
    fn from(value: Error) -> Self {
        Self {
            code: value.code,
            message: value.message,
            retryable: value.retryable,
            retry_after_ms: value.retry_after_ms,
            effect_state: value.effect_state,
        }
    }
}

#[derive(Clone, Debug, Deserialize, Eq, Hash, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Trace {
    pub trace_id: Id,
    pub request_id: Id,
}

impl Trace {
    pub fn new(trace_id: Id, request_id: Id) -> Self {
        Self {
            trace_id,
            request_id,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, thiserror::Error)]
pub enum ContractViolation {
    #[error("Id does not match the Hypermid Id contract")]
    InvalidId,
    #[error("digest must be exactly 64 lowercase hexadecimal characters")]
    InvalidDigest,
    #[error("cursor values are outside the Hypermid wire range")]
    InvalidCursor,
    #[error("error code does not match the Hypermid Error contract")]
    InvalidErrorCode,
    #[error("error message exceeds 2048 characters")]
    ErrorMessageTooLong,
    #[error("retry delay exceeds 86400000 milliseconds")]
    InvalidRetryDelay,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn common_types_round_trip_through_the_wire_shape() {
        let scope = Scope::new(
            Id::new("owner-1").unwrap(),
            Id::new("project:opaque").unwrap(),
            Some(Id::new("workspace.1").unwrap()),
        );
        let encoded = serde_json::to_string(&scope).unwrap();
        assert_eq!(serde_json::from_str::<Scope>(&encoded).unwrap(), scope);

        let digest = Digest::sha256(b"hypermid");
        assert_eq!(Digest::from_str(&digest.to_string()).unwrap(), digest);
        assert!(Digest::from_str(&digest.to_string().to_uppercase()).is_err());

        assert!(Cursor::new(0, 0).is_err());
        assert!(serde_json::from_str::<Cursor>(r#"{"epoch":1,"sequence":0,"extra":1}"#).is_err());
    }
}
