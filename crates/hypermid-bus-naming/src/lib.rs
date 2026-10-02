pub mod grants;
pub mod limits;
pub mod names;

pub use grants::{Grant, Role};
pub use limits::{Limits, RetentionPolicy};
pub use names::{DurableNames, StreamFamily, Topology};

#[derive(Clone, Debug, Eq, PartialEq, thiserror::Error)]
pub enum NamingError {
    #[error("invalid message-plane token: {0}")]
    InvalidToken(String),
    #[error("subject filter escapes its assigned prefix")]
    FilterEscape,
    #[error("stream subjects overlap")]
    StreamOverlap,
    #[error("stream limits are invalid")]
    InvalidLimits,
    #[error("backing-store retention is shorter than stream retention")]
    RetentionMismatch,
    #[error("grant is broader than the selected role")]
    GrantTooBroad,
}

pub(crate) fn validate_token(value: &str) -> Result<(), NamingError> {
    let bytes = value.as_bytes();
    if bytes.is_empty()
        || bytes.len() > 96
        || !(bytes[0].is_ascii_alphanumeric())
        || !bytes
            .iter()
            .all(|byte| byte.is_ascii_alphanumeric() || *byte == b'_' || *byte == b'-')
    {
        return Err(NamingError::InvalidToken(value.into()));
    }
    Ok(())
}

pub fn filter_is_contained(prefix: &str, filter: &str) -> bool {
    filter == prefix
        || filter
            .strip_prefix(prefix)
            .is_some_and(|tail| tail.starts_with('.'))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    #[test]
    fn topology_rejects_escapes_overlaps_and_short_retention() {
        let topology = Topology::new("owner", "project", None).unwrap();
        topology.validate_non_overlapping().unwrap();
        assert_eq!(
            topology
                .subject(StreamFamily::ModuleEvents, "memory", "changed")
                .unwrap(),
            "hm.owner.project.module-events.memory.changed"
        );
        assert!(Topology::new("bad.owner", "project", None).is_err());

        let participant = Grant {
            publish: vec!["hm.owner.project.module-events.memory.changed".into()],
            subscribe: vec![],
            create_consumers: false,
        };
        participant
            .validate(Role::Participant, "hm.owner.project")
            .unwrap();
        let escaped = Grant {
            publish: vec!["hm.other.project.>".into()],
            ..participant.clone()
        };
        assert_eq!(
            escaped.validate(Role::Participant, "hm.owner.project"),
            Err(NamingError::FilterEscape)
        );

        let limits = Limits {
            max_messages: 10,
            max_bytes: 1024,
            max_age: Duration::from_secs(60),
            max_message_bytes: 512,
            retention: RetentionPolicy::Limits,
        };
        assert_eq!(
            limits.validate(Duration::from_secs(59)),
            Err(NamingError::RetentionMismatch)
        );
    }
}
