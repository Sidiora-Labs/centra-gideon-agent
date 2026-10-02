use crate::mode::ContextMode;
use crate::provider::ModelBudget;
use hypermid_contracts::{Cursor, Digest, Id, Scope};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

pub const MAX_SNAPSHOT_ITEMS: usize = 100_000;
pub const MAX_CONTRIBUTION_BYTES: usize = 1_048_576;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SubagentSnapshot {
    pub child_session_id: Id,
    pub parent_session_id: Id,
    pub scope: Scope,
    pub spawn_cursor: Cursor,
    pub source_digest: Digest,
    pub mode_ceiling: ContextMode,
    pub provider_profile_digest: Digest,
    pub model_budget: ModelBudget,
    pub item_ids: Vec<Id>,
}

impl SubagentSnapshot {
    #[allow(clippy::too_many_arguments)]
    pub fn create(
        child_session_id: Id,
        parent_session_id: Id,
        scope: Scope,
        spawn_cursor: Cursor,
        parent_mode: ContextMode,
        mode_ceiling: ContextMode,
        provider_profile_digest: Digest,
        model_budget: ModelBudget,
        item_ids: Vec<Id>,
    ) -> Result<Self, SubagentError> {
        if child_session_id == parent_session_id {
            return Err(SubagentError::ChildIdentityRequired);
        }
        if mode_rank(mode_ceiling) > mode_rank(parent_mode) {
            return Err(SubagentError::ModeEscalation);
        }
        if item_ids.len() > MAX_SNAPSHOT_ITEMS
            || item_ids.iter().collect::<BTreeSet<_>>().len() != item_ids.len()
        {
            return Err(SubagentError::InvalidSnapshotItems);
        }
        validate_budget(&model_budget)?;
        let mut snapshot = Self {
            child_session_id,
            parent_session_id,
            scope,
            spawn_cursor,
            source_digest: Digest::sha256([]),
            mode_ceiling,
            provider_profile_digest,
            model_budget,
            item_ids,
        };
        snapshot.source_digest = snapshot.computed_digest()?;
        Ok(snapshot)
    }

    pub fn validate(&self) -> Result<(), SubagentError> {
        if self.child_session_id == self.parent_session_id {
            return Err(SubagentError::ChildIdentityRequired);
        }
        if self.item_ids.len() > MAX_SNAPSHOT_ITEMS
            || self.item_ids.iter().collect::<BTreeSet<_>>().len() != self.item_ids.len()
        {
            return Err(SubagentError::InvalidSnapshotItems);
        }
        validate_budget(&self.model_budget)?;
        if self.computed_digest()? != self.source_digest {
            return Err(SubagentError::SnapshotDigestMismatch);
        }
        Ok(())
    }

    pub fn allows_item(&self, item_id: &Id) -> bool {
        self.item_ids.contains(item_id)
    }

    fn computed_digest(&self) -> Result<Digest, SubagentError> {
        #[derive(Serialize)]
        struct DigestInput<'a> {
            child_session_id: &'a Id,
            parent_session_id: &'a Id,
            scope: &'a Scope,
            spawn_cursor: Cursor,
            mode_ceiling: ContextMode,
            provider_profile_digest: Digest,
            model_budget: &'a ModelBudget,
            item_ids: &'a [Id],
        }
        let bytes = serde_json::to_vec(&DigestInput {
            child_session_id: &self.child_session_id,
            parent_session_id: &self.parent_session_id,
            scope: &self.scope,
            spawn_cursor: self.spawn_cursor,
            mode_ceiling: self.mode_ceiling,
            provider_profile_digest: self.provider_profile_digest,
            model_budget: &self.model_budget,
            item_ids: &self.item_ids,
        })
        .map_err(|_| SubagentError::Serialization)?;
        Ok(Digest::sha256(bytes))
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ChildUsage {
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub cache_read_tokens: u64,
    pub cache_write_tokens: u64,
}

impl ChildUsage {
    pub fn checked_add(&mut self, value: &Self) -> Result<(), SubagentError> {
        self.input_tokens = self
            .input_tokens
            .checked_add(value.input_tokens)
            .ok_or(SubagentError::UsageOverflow)?;
        self.output_tokens = self
            .output_tokens
            .checked_add(value.output_tokens)
            .ok_or(SubagentError::UsageOverflow)?;
        self.cache_read_tokens = self
            .cache_read_tokens
            .checked_add(value.cache_read_tokens)
            .ok_or(SubagentError::UsageOverflow)?;
        self.cache_write_tokens = self
            .cache_write_tokens
            .checked_add(value.cache_write_tokens)
            .ok_or(SubagentError::UsageOverflow)?;
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SubagentContribution {
    pub child_session_id: Id,
    pub child_cursor: Cursor,
    pub content: String,
    pub content_digest: Digest,
    pub authorized_by: Id,
}

impl SubagentContribution {
    pub fn validate(&self) -> Result<(), SubagentError> {
        if self.content.is_empty() || self.content.len() > MAX_CONTRIBUTION_BYTES {
            return Err(SubagentError::InvalidContribution);
        }
        if Digest::sha256(self.content.as_bytes()) != self.content_digest {
            return Err(SubagentError::ContributionDigestMismatch);
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct GideonContributionAuthorization {
    pub authorization_id: Id,
    pub authorized_by: Id,
    pub parent_session_id: Id,
    pub child_session_id: Id,
    pub scope: Scope,
    pub expires_at_ms: u64,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ParentContribution {
    pub parent_item_id: Id,
    pub parent_session_id: Id,
    pub child_session_id: Id,
    pub child_cursor: Cursor,
    pub child_snapshot_digest: Digest,
    pub content: String,
    pub content_digest: Digest,
    pub authorized_by: Id,
}

fn mode_rank(mode: ContextMode) -> u8 {
    match mode {
        ContextMode::Off => 0,
        ContextMode::PassThrough => 1,
        ContextMode::Shadow => 2,
        ContextMode::Primary => 3,
    }
}

fn validate_budget(budget: &ModelBudget) -> Result<(), SubagentError> {
    let regions = budget
        .baseline_tokens
        .checked_add(budget.delta_tokens)
        .and_then(|value| value.checked_add(budget.tail_tokens))
        .ok_or(SubagentError::InvalidBudget)?;
    if budget.context_window_tokens == 0
        || budget.reserved_output_tokens > budget.context_window_tokens
        || budget.max_input_tokens > budget.context_window_tokens - budget.reserved_output_tokens
        || regions > budget.max_input_tokens
        || budget.max_items == 0
    {
        return Err(SubagentError::InvalidBudget);
    }
    Ok(())
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, thiserror::Error)]
pub enum SubagentError {
    #[error("child session identity must differ from the parent")]
    ChildIdentityRequired,
    #[error("child mode ceiling exceeds the parent mode")]
    ModeEscalation,
    #[error("snapshot item identities are duplicated or unbounded")]
    InvalidSnapshotItems,
    #[error("snapshot model budget is invalid")]
    InvalidBudget,
    #[error("snapshot digest does not match its immutable fields")]
    SnapshotDigestMismatch,
    #[error("child usage counter overflowed")]
    UsageOverflow,
    #[error("contribution content is empty or oversized")]
    InvalidContribution,
    #[error("contribution content digest does not match")]
    ContributionDigestMismatch,
    #[error("subagent contract serialization failed")]
    Serialization,
}
