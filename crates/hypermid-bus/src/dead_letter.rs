use hypermid_contracts::{Digest, Id, Scope};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DeadLetterRecord {
    pub original_event_id: Id,
    pub topic: String,
    pub scope: Scope,
    pub payload_digest: Digest,
    pub delivery_count: u32,
    pub reason: String,
    pub recorded_ms: u64,
}
