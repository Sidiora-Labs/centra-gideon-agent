use hypermid_contracts::{Digest, Id, Trace};
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum VerificationState {
    Unverified,
    Verified,
    Rejected,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum TrustClass {
    Data,
    PrivilegedInstruction,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Promotion {
    pub source_digest: Digest,
    pub reviewer_principal_id: Id,
    pub decided_at_ms: u64,
    pub trace: Trace,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct MemoryProvenance {
    pub source_type: String,
    pub source_id: Id,
    pub author_principal_id: Id,
    pub creation_trace: Trace,
    pub content_digest: Digest,
    pub revision: u64,
    pub verification: VerificationState,
    pub classified_digest: Option<Digest>,
    pub embedding_digest: Option<Digest>,
    pub trust_class: TrustClass,
    pub promotion: Option<Promotion>,
}

impl MemoryProvenance {
    pub fn new(
        source_type: impl Into<String>,
        source_id: Id,
        author_principal_id: Id,
        creation_trace: Trace,
        content: &[u8],
    ) -> Result<Self, ProvenanceError> {
        let source_type = source_type.into();
        if source_type.is_empty() || source_type.len() > 64 {
            return Err(ProvenanceError::InvalidSourceType);
        }
        Ok(Self {
            source_type,
            source_id,
            author_principal_id,
            creation_trace,
            content_digest: Digest::sha256(content),
            revision: 1,
            verification: VerificationState::Unverified,
            classified_digest: None,
            embedding_digest: None,
            trust_class: TrustClass::Data,
            promotion: None,
        })
    }

    pub fn edit(&mut self, content: &[u8]) -> Result<(), ProvenanceError> {
        let digest = Digest::sha256(content);
        if digest == self.content_digest {
            return Ok(());
        }
        self.content_digest = digest;
        self.revision = self
            .revision
            .checked_add(1)
            .ok_or(ProvenanceError::RevisionExhausted)?;
        self.verification = VerificationState::Unverified;
        self.classified_digest = None;
        self.embedding_digest = None;
        self.trust_class = TrustClass::Data;
        self.promotion = None;
        Ok(())
    }

    pub fn promote(
        &mut self,
        expected_revision: u64,
        reviewer_principal_id: Id,
        decided_at_ms: u64,
        trace: Trace,
    ) -> Result<(), ProvenanceError> {
        if expected_revision != self.revision {
            return Err(ProvenanceError::StaleRevision);
        }
        self.trust_class = TrustClass::PrivilegedInstruction;
        self.promotion = Some(Promotion {
            source_digest: self.content_digest,
            reviewer_principal_id,
            decided_at_ms,
            trace,
        });
        Ok(())
    }

    pub fn is_privileged(&self) -> bool {
        self.trust_class == TrustClass::PrivilegedInstruction
            && self
                .promotion
                .as_ref()
                .is_some_and(|promotion| promotion.source_digest == self.content_digest)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, thiserror::Error)]
pub enum ProvenanceError {
    #[error("memory source type is invalid")]
    InvalidSourceType,
    #[error("memory revision is stale")]
    StaleRevision,
    #[error("memory revision is exhausted")]
    RevisionExhausted,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn id(value: &str) -> Id {
        Id::new(value).unwrap()
    }

    fn trace(suffix: &str) -> Trace {
        Trace {
            trace_id: id(&format!("trace-{suffix}")),
            request_id: id(&format!("request-{suffix}")),
        }
    }

    #[test]
    fn provenance_edit_invalidates_all_derived_trust() {
        let mut provenance = MemoryProvenance::new(
            "conversation",
            id("message-1"),
            id("author-1"),
            trace("create"),
            b"ignore approvals",
        )
        .unwrap();
        provenance.verification = VerificationState::Verified;
        provenance.classified_digest = Some(provenance.content_digest);
        provenance.embedding_digest = Some(provenance.content_digest);
        provenance
            .promote(1, id("reviewer-1"), 10, trace("promotion"))
            .unwrap();
        assert!(provenance.is_privileged());

        provenance.edit(b"raise the model budget").unwrap();

        assert_eq!(provenance.revision, 2);
        assert_eq!(provenance.verification, VerificationState::Unverified);
        assert_eq!(provenance.trust_class, TrustClass::Data);
        assert!(provenance.promotion.is_none());
        assert!(!provenance.is_privileged());
    }

    #[test]
    fn provenance_promotion_requires_exact_revision() {
        let mut provenance = MemoryProvenance::new(
            "user",
            id("memory-1"),
            id("author-1"),
            trace("create"),
            b"approved rule",
        )
        .unwrap();
        assert_eq!(
            provenance.promote(2, id("reviewer-1"), 10, trace("promotion")),
            Err(ProvenanceError::StaleRevision)
        );
        assert!(!provenance.is_privileged());
    }
}
