use std::collections::HashMap;

use hypermid_protocol::{Digest, Id};
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RelaySide {
    Initiator,
    Responder,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RelayGrant {
    pub kind: String,
    pub pipe_id: Id,
    pub side: RelaySide,
    pub token: String,
    pub expires_ms: u64,
}

#[derive(Clone, Debug)]
struct StoredGrant {
    token_digest: Digest,
    expires_ms: u64,
    consumed: bool,
}

#[derive(Default)]
pub struct RelayGrantRegistry {
    grants: HashMap<(Id, RelaySide), StoredGrant>,
}

impl RelayGrantRegistry {
    pub fn issue(&mut self, grant: RelayGrant, now_ms: u64) -> Result<(), RelayError> {
        validate_grant(&grant, now_ms)?;
        let key = (grant.pipe_id, grant.side);
        if self.grants.contains_key(&key) {
            return Err(RelayError::GrantAlreadyExists);
        }
        self.grants.insert(
            key,
            StoredGrant {
                token_digest: Digest::sha256(grant.token.as_bytes()),
                expires_ms: grant.expires_ms,
                consumed: false,
            },
        );
        Ok(())
    }

    pub fn consume(
        &mut self,
        pipe_id: &Id,
        side: RelaySide,
        token: &str,
        now_ms: u64,
    ) -> Result<(), RelayError> {
        let stored = self
            .grants
            .get_mut(&(pipe_id.clone(), side))
            .ok_or(RelayError::UnknownGrant)?;
        if now_ms >= stored.expires_ms {
            return Err(RelayError::ExpiredGrant);
        }
        if stored.consumed {
            return Err(RelayError::ConsumedGrant);
        }
        if stored.token_digest != Digest::sha256(token.as_bytes()) {
            return Err(RelayError::WrongGrant);
        }
        stored.consumed = true;
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, thiserror::Error)]
pub enum RelayError {
    #[error("relay grant is malformed")]
    InvalidGrant,
    #[error("relay grant already exists")]
    GrantAlreadyExists,
    #[error("relay grant is unknown")]
    UnknownGrant,
    #[error("relay grant expired")]
    ExpiredGrant,
    #[error("relay grant was already consumed")]
    ConsumedGrant,
    #[error("relay grant token or side does not match")]
    WrongGrant,
}

fn validate_grant(grant: &RelayGrant, now_ms: u64) -> Result<(), RelayError> {
    if grant.kind != "relay_grant"
        || !(32..=2048).contains(&grant.token.len())
        || now_ms >= grant.expires_ms
    {
        return Err(RelayError::InvalidGrant);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn grants_are_side_specific_single_use_and_expiring() {
        let pipe = Id::new("pipe-1").unwrap();
        let mut registry = RelayGrantRegistry::default();
        registry
            .issue(
                RelayGrant {
                    kind: "relay_grant".into(),
                    pipe_id: pipe.clone(),
                    side: RelaySide::Initiator,
                    token: "initiator-token-which-is-long-enough".into(),
                    expires_ms: 200,
                },
                100,
            )
            .unwrap();
        assert_eq!(
            registry.consume(
                &pipe,
                RelaySide::Responder,
                "initiator-token-which-is-long-enough",
                120
            ),
            Err(RelayError::UnknownGrant)
        );
        registry
            .consume(
                &pipe,
                RelaySide::Initiator,
                "initiator-token-which-is-long-enough",
                120,
            )
            .unwrap();
        assert_eq!(
            registry.consume(
                &pipe,
                RelaySide::Initiator,
                "initiator-token-which-is-long-enough",
                121
            ),
            Err(RelayError::ConsumedGrant)
        );
    }
}
