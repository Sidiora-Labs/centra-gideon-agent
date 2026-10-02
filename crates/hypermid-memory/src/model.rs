use hypermid_contracts::{Digest, Id, Scope, Trace};
use hypermid_store::authorization::CommitAuthorization;
use serde::{Deserialize, Serialize};
use sha2::{Digest as _, Sha256};
use std::collections::BTreeSet;

#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Operation {
    Create,
    Update,
    Archive,
    Restore,
    Merge,
    Split,
    Relocate,
    Delete,
    Purge,
    Verify,
    Embed,
    Index,
    Summarize,
    Import,
    Export,
}

impl Operation {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Create => "create",
            Self::Update => "update",
            Self::Archive => "archive",
            Self::Restore => "restore",
            Self::Merge => "merge",
            Self::Split => "split",
            Self::Relocate => "relocate",
            Self::Delete => "delete",
            Self::Purge => "purge",
            Self::Verify => "verify",
            Self::Embed => "embed",
            Self::Index => "index",
            Self::Summarize => "summarize",
            Self::Import => "import",
            Self::Export => "export",
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum GrantOperation {
    Read,
    Search,
    Create,
    Update,
    Archive,
    Restore,
    Merge,
    Split,
    Relocate,
    Delete,
    Purge,
    Verify,
    Embed,
    Index,
    Summarize,
    Import,
    Export,
}

impl GrantOperation {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Read => "read",
            Self::Search => "search",
            Self::Create => "create",
            Self::Update => "update",
            Self::Archive => "archive",
            Self::Restore => "restore",
            Self::Merge => "merge",
            Self::Split => "split",
            Self::Relocate => "relocate",
            Self::Delete => "delete",
            Self::Purge => "purge",
            Self::Verify => "verify",
            Self::Embed => "embed",
            Self::Index => "index",
            Self::Summarize => "summarize",
            Self::Import => "import",
            Self::Export => "export",
        }
    }
}

impl From<Operation> for GrantOperation {
    fn from(operation: Operation) -> Self {
        match operation {
            Operation::Create => Self::Create,
            Operation::Update => Self::Update,
            Operation::Archive => Self::Archive,
            Operation::Restore => Self::Restore,
            Operation::Merge => Self::Merge,
            Operation::Split => Self::Split,
            Operation::Relocate => Self::Relocate,
            Operation::Delete => Self::Delete,
            Operation::Purge => Self::Purge,
            Operation::Verify => Self::Verify,
            Operation::Embed => Self::Embed,
            Operation::Index => Self::Index,
            Operation::Summarize => Self::Summarize,
            Operation::Import => Self::Import,
            Operation::Export => Self::Export,
        }
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(tag = "kind", content = "digest", rename_all = "snake_case")]
pub enum RevisionPrecondition {
    MustNotExist,
    Match(Digest),
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct MutationRequest {
    pub operation: Operation,
    pub actor_scope: Scope,
    pub target_scope: Scope,
    pub record_id: Option<Id>,
    pub category: Option<String>,
    pub revision: RevisionPrecondition,
    pub trace: Trace,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct AccessRequest {
    pub operation: GrantOperation,
    pub actor_scope: Scope,
    pub target_scope: Scope,
    pub resource_id: Id,
    pub category: Option<String>,
    pub trace: Trace,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ShareGrant {
    pub id: Id,
    pub owner_scope: Scope,
    pub grantee_scope: Scope,
    pub operations: BTreeSet<GrantOperation>,
    pub categories: Option<BTreeSet<String>>,
    pub granted_at_ms: i64,
    pub expires_at_ms: Option<i64>,
    pub revoked_at_ms: Option<i64>,
    pub revision: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AuthorizationBasis {
    Owner,
    Grant,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct GrantReference {
    pub id: Id,
    pub revision: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Authorization {
    pub basis: AuthorizationBasis,
    pub capability: CommitAuthorization,
    pub actor_scope_digest: Digest,
    pub target_scope_digest: Digest,
    pub operation: GrantOperation,
    pub category: Option<String>,
    pub grant: Option<GrantReference>,
}

pub fn scope_digest(scope: &Scope) -> Digest {
    let mut hasher = Sha256::new();
    hasher.update(b"hypermid.memory.scope.v1\0");
    hash_component(&mut hasher, scope.owner_id.as_str().as_bytes());
    hash_component(&mut hasher, scope.project_id.as_str().as_bytes());
    match &scope.workspace_id {
        Some(workspace_id) => {
            hasher.update([1]);
            hash_component(&mut hasher, workspace_id.as_str().as_bytes());
        }
        None => hasher.update([0]),
    }
    Digest::from_bytes(hasher.finalize().into())
}

fn hash_component(hasher: &mut Sha256, value: &[u8]) {
    hasher.update((value.len() as u64).to_be_bytes());
    hasher.update(value);
}
