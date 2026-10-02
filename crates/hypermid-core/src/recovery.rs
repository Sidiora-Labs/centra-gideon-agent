use crate::history::{range_digest, RecoveredItem};
use crate::projection::{projection_bytes, NormalizedBlock, Projection, ProjectionRegion};
use crate::provider::ModelBudget;
use hypermid_contracts::{Cursor, Digest, Error, Id, Scope};
use serde::{Deserialize, Serialize};
use sha2::{Digest as _, Sha256};

pub const MAX_NORMALIZED_PROJECTION_BYTES: usize = 8 * 1024 * 1024;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RecoveryBinding {
    pub scope: Scope,
    pub session_id: Id,
    pub cursor: Cursor,
    pub source_digest: Digest,
    pub generation: u64,
    pub provider_profile_digest: Digest,
    pub policy_revision: u64,
    pub baseline_digest: Digest,
    pub delta_digest: Digest,
    pub tail_digest: Digest,
    pub model_budget: ModelBudget,
}

impl RecoveryBinding {
    pub fn from_projection(projection: &Projection, model_budget: ModelBudget) -> Self {
        Self {
            scope: projection.scope.clone(),
            session_id: projection.session_id.clone(),
            cursor: projection.source_cursor,
            source_digest: projection.source_digest,
            generation: projection.generation,
            provider_profile_digest: projection.provider_profile_digest,
            policy_revision: projection.policy_revision,
            baseline_digest: projection.baseline.digest,
            delta_digest: projection.delta.digest,
            tail_digest: projection.tail.digest,
            model_budget,
        }
    }

    pub fn validate(&self) -> Result<(), RecoveryViolation> {
        if self.generation == 0
            || self.policy_revision == 0
            || self.model_budget.context_window_tokens == 0
            || self.model_budget.reserved_output_tokens > self.model_budget.context_window_tokens
            || self.model_budget.max_input_tokens
                > self
                    .model_budget
                    .context_window_tokens
                    .saturating_sub(self.model_budget.reserved_output_tokens)
            || self.model_budget.max_items == 0
        {
            return Err(RecoveryViolation::InvalidBinding);
        }
        let region_tokens = self
            .model_budget
            .baseline_tokens
            .checked_add(self.model_budget.delta_tokens)
            .and_then(|value| value.checked_add(self.model_budget.tail_tokens))
            .ok_or(RecoveryViolation::InvalidBudget)?;
        if region_tokens > self.model_budget.max_input_tokens {
            return Err(RecoveryViolation::InvalidBudget);
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct LastKnownGood {
    pub projection_id: Id,
    pub binding: RecoveryBinding,
    pub output_digest: Digest,
    pub normalized_digest: Digest,
    pub normalized_projection: Vec<u8>,
    pub stored_at: String,
}

impl LastKnownGood {
    pub fn capture(
        projection: &Projection,
        model_budget: ModelBudget,
        stored_at: impl Into<String>,
    ) -> Result<Self, RecoveryViolation> {
        let normalized_projection =
            projection_bytes(projection).map_err(|_| RecoveryViolation::InvalidProjection)?;
        let record = Self {
            projection_id: projection.projection_id.clone(),
            binding: RecoveryBinding::from_projection(projection, model_budget),
            output_digest: projection.output_digest,
            normalized_digest: Digest::sha256(&normalized_projection),
            normalized_projection,
            stored_at: stored_at.into(),
        };
        record.validate()?;
        Ok(record)
    }

    pub fn validate(&self) -> Result<Projection, RecoveryViolation> {
        self.binding.validate()?;
        if self.stored_at.is_empty()
            || self.normalized_projection.is_empty()
            || self.normalized_projection.len() > MAX_NORMALIZED_PROJECTION_BYTES
            || Digest::sha256(&self.normalized_projection) != self.normalized_digest
        {
            return Err(RecoveryViolation::CorruptRecord);
        }
        let mut projection: Projection = serde_json::from_slice(&self.normalized_projection)
            .map_err(|_| RecoveryViolation::CorruptRecord)?;
        hydrate_region_bytes(&mut projection)?;
        let block_value = serde_json::to_value(&projection.blocks)
            .map_err(|_| RecoveryViolation::CorruptRecord)?;
        let blocks =
            serde_json::to_vec(&block_value).map_err(|_| RecoveryViolation::CorruptRecord)?;
        if projection.projection_id != self.projection_id
            || projection.scope != self.binding.scope
            || projection.session_id != self.binding.session_id
            || projection.source_cursor != self.binding.cursor
            || projection.source_digest != self.binding.source_digest
            || projection.generation != self.binding.generation
            || projection.provider_profile_digest != self.binding.provider_profile_digest
            || projection.policy_revision != self.binding.policy_revision
            || projection.baseline.digest != self.binding.baseline_digest
            || projection.delta.digest != self.binding.delta_digest
            || projection.tail.digest != self.binding.tail_digest
            || projection.output_digest != self.output_digest
            || Digest::sha256(blocks) != self.output_digest
        {
            return Err(RecoveryViolation::CorruptRecord);
        }
        Ok(projection)
    }
}

fn hydrate_region_bytes(projection: &mut Projection) -> Result<(), RecoveryViolation> {
    projection.baseline.bytes = verified_region_bytes(&projection.blocks, &projection.baseline)?;
    projection.delta.bytes = verified_region_bytes(&projection.blocks, &projection.delta)?;
    projection.tail.bytes = verified_region_bytes(&projection.blocks, &projection.tail)?;
    Ok(())
}

fn verified_region_bytes(
    blocks: &[NormalizedBlock],
    region: &ProjectionRegion,
) -> Result<Vec<u8>, RecoveryViolation> {
    let selected = blocks
        .iter()
        .filter(|block| {
            block
                .source_item_ids
                .iter()
                .any(|item_id| region.item_ids.contains(item_id))
                || region.summary_ids.iter().any(|summary_id| {
                    block
                        .block_id
                        .as_str()
                        .starts_with(&format!("summary:{summary_id}:"))
                })
        })
        .collect::<Vec<_>>();
    let value = serde_json::to_value(&selected).map_err(|_| RecoveryViolation::CorruptRecord)?;
    let bytes = serde_json::to_vec(&value).map_err(|_| RecoveryViolation::CorruptRecord)?;
    let mut digest = Sha256::new();
    for block in &selected {
        let value = serde_json::to_value(block).map_err(|_| RecoveryViolation::CorruptRecord)?;
        let block_bytes =
            serde_json::to_vec(&value).map_err(|_| RecoveryViolation::CorruptRecord)?;
        digest.update((block_bytes.len() as u64).to_be_bytes());
        digest.update(block_bytes);
    }
    if Digest::from_bytes(digest.finalize().into()) != region.digest {
        return Err(RecoveryViolation::CorruptRecord);
    }
    Ok(bytes)
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ReplayRequest {
    pub binding: RecoveryBinding,
    pub input_tokens: u64,
    pub item_count: u64,
    pub image_count: u64,
}

impl ReplayRequest {
    pub fn validate_fit(&self) -> Result<(), RecoveryViolation> {
        self.binding.validate()?;
        if self.input_tokens > self.binding.model_budget.max_input_tokens
            || self.item_count > self.binding.model_budget.max_items
            || self.image_count > self.binding.model_budget.max_images
        {
            return Err(RecoveryViolation::BudgetMismatch);
        }
        Ok(())
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct RebuiltState {
    pub scope: Scope,
    pub session_id: Id,
    pub cursor: Cursor,
    pub source_digest: Digest,
    pub items: Vec<RecoveredItem>,
}

impl RebuiltState {
    pub fn from_recovered(
        scope: Scope,
        session_id: Id,
        cursor: Cursor,
        items: Vec<RecoveredItem>,
    ) -> Result<Self, RecoveryViolation> {
        if items.is_empty() {
            if cursor.sequence != 0 {
                return Err(RecoveryViolation::JournalGap);
            }
            return Ok(Self {
                scope,
                session_id,
                cursor,
                source_digest: Digest::sha256(b""),
                items,
            });
        }
        if items.len() as u64 != cursor.sequence {
            return Err(RecoveryViolation::JournalGap);
        }
        for (index, recovered) in items.iter().enumerate() {
            let expected = index as u64 + 1;
            if recovered.item.scope != scope
                || recovered.item.session_id != session_id
                || recovered.item.cursor.epoch != cursor.epoch
                || recovered.item.cursor.sequence != expected
                || Digest::sha256(&recovered.source_bytes) != recovered.item.source_digest
            {
                return Err(RecoveryViolation::JournalGap);
            }
        }
        let source_digest = range_digest(items.iter().map(|item| &item.item));
        Ok(Self {
            scope,
            session_id,
            cursor,
            source_digest,
            items,
        })
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, thiserror::Error)]
pub enum RecoveryViolation {
    #[error("last-known-good binding is invalid")]
    InvalidBinding,
    #[error("last-known-good budget is invalid")]
    InvalidBudget,
    #[error("normalized projection is invalid")]
    InvalidProjection,
    #[error("last-known-good record is corrupt or partial")]
    CorruptRecord,
    #[error("last-known-good binding does not match the replay request")]
    BindingMismatch,
    #[error("replay request no longer fits its declared budget")]
    BudgetMismatch,
    #[error("journal replay has a gap, mismatch, or unverified source")]
    JournalGap,
}

pub fn replay_refusal(violation: RecoveryViolation) -> Error {
    let (code, message) = match violation {
        RecoveryViolation::BindingMismatch => (
            "RECOVERY_BINDING_MISMATCH",
            "last-known-good projection does not match the active binding",
        ),
        RecoveryViolation::BudgetMismatch | RecoveryViolation::InvalidBudget => (
            "RECOVERY_BUDGET_MISMATCH",
            "last-known-good projection does not fit the active budget",
        ),
        RecoveryViolation::CorruptRecord => (
            "RECOVERY_CORRUPT",
            "last-known-good projection is corrupt and cannot be replayed",
        ),
        _ => (
            "RECOVERY_REFUSED",
            "last-known-good projection is not eligible for replay",
        ),
    };
    Error::new(code, message, true, None, None).expect("static recovery error is valid")
}
