use crate::{Error, MemoryResult, Trace};
use serde::{de::DeserializeOwned, Deserialize, Serialize};

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct WireSuccess<T> {
    pub trace: Trace,
    pub result: T,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct WireFailure {
    pub trace: Trace,
    pub error: Error,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(untagged)]
pub enum WireOutcome<T> {
    Success(WireSuccess<T>),
    Failure(WireFailure),
}

pub fn decode<T: DeserializeOwned>(input: &[u8]) -> MemoryResult<T> {
    if input.len() > hypermid_protocol::MAX_FRAME_BYTES {
        return Err(crate::error(
            "REQUEST_TOO_LARGE",
            "memory request exceeds the authenticated protocol frame limit",
            crate::EffectState::NotStarted,
        ));
    }
    serde_json::from_slice(input).map_err(|_| {
        crate::error(
            "INVALID_REQUEST",
            "memory request is not valid canonical JSON for the selected operation",
            crate::EffectState::NotStarted,
        )
    })
}

pub fn encode<T: Serialize>(trace: Trace, result: MemoryResult<T>) -> Vec<u8> {
    let outcome = match result {
        Ok(result) => WireOutcome::Success(WireSuccess { trace, result }),
        Err(error) => WireOutcome::Failure(WireFailure { trace, error }),
    };
    serde_json::to_vec(&outcome).expect("wire outcome contains only validated serializable values")
}
