use hypermid_contracts::{Cursor, Digest, Id, Scope};
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum IdentityKind {
    Session,
    JournalItem,
    ToolCall,
    Summary,
    Projection,
    SubagentSnapshot,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RelationKind {
    Continues,
    Supersedes,
    Regenerates,
    ForksFrom,
    Imports,
    DerivedFrom,
    ContributedBy,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SourceIdentity {
    pub source_event_id: Id,
    pub source_digest: Digest,
}

impl SourceIdentity {
    pub fn new(source_event_id: Id, source_digest: Digest) -> Self {
        Self {
            source_event_id,
            source_digest,
        }
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct IdentityRelation {
    pub kind: RelationKind,
    pub item_id: Id,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub source_digest: Option<Digest>,
}

impl IdentityRelation {
    pub fn new(kind: RelationKind, item_id: Id, source_digest: Option<Digest>) -> Self {
        Self {
            kind,
            item_id,
            source_digest,
        }
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ContextIdentity {
    pub identity_id: Id,
    pub kind: IdentityKind,
    pub scope: Scope,
    pub session_id: Id,
    #[serde(flatten)]
    pub source: Option<SourceIdentity>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub relations: Vec<IdentityRelation>,
}

impl ContextIdentity {
    pub fn new(
        identity_id: Id,
        kind: IdentityKind,
        scope: Scope,
        session_id: Id,
        source: Option<SourceIdentity>,
        relations: Vec<IdentityRelation>,
    ) -> Result<Self, IdentityContractError> {
        if kind == IdentityKind::JournalItem && source.is_none() {
            return Err(IdentityContractError::SourceIdentityRequired);
        }
        Ok(Self {
            identity_id,
            kind,
            scope,
            session_id,
            source,
            relations,
        })
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RebindRecord {
    pub rebind_id: Id,
    pub session_id: Id,
    pub previous_scope: Scope,
    pub next_scope: Scope,
    pub cursor: Cursor,
    pub reason: String,
}

impl RebindRecord {
    pub fn new(
        rebind_id: Id,
        session_id: Id,
        previous_scope: Scope,
        next_scope: Scope,
        cursor: Cursor,
        reason: impl Into<String>,
    ) -> Result<Self, IdentityContractError> {
        let reason = reason.into();
        if reason.is_empty() || reason.chars().count() > 1_024 {
            return Err(IdentityContractError::InvalidRebindReason);
        }
        Ok(Self {
            rebind_id,
            session_id,
            previous_scope,
            next_scope,
            cursor,
            reason,
        })
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct IdentityBinding {
    pub session_id: Id,
    pub scope: Scope,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub rebinds: Vec<RebindRecord>,
}

impl IdentityBinding {
    pub fn new(session_id: Id, scope: Scope) -> Self {
        Self {
            session_id,
            scope,
            rebinds: Vec::new(),
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, thiserror::Error)]
pub enum IdentityContractError {
    #[error("journal items require a canonical source identity")]
    SourceIdentityRequired,
    #[error("rebind reason must contain 1 to 1024 characters")]
    InvalidRebindReason,
}
