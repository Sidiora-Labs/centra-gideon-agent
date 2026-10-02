use crate::projection::{ContextRole, NormalizedBlock, PartKind, Projection};
use hypermid_contracts::{Digest, Id};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeSet, VecDeque};
use std::error::Error;
use std::fmt;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ImageAccounting {
    Tokens,
    Tiles,
    HostReported,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ProviderCapabilities {
    pub profile_id: Id,
    pub context_window_tokens: u64,
    pub reserved_output_tokens: u64,
    pub roles: BTreeSet<ContextRole>,
    pub part_kinds: BTreeSet<PartKind>,
    pub requires_tool_adjacency: bool,
    pub supports_reasoning: bool,
    pub supports_cache_boundaries: bool,
    pub max_cache_boundaries: u64,
    pub max_images: u64,
    pub image_accounting: ImageAccounting,
}

impl ProviderCapabilities {
    pub fn digest(&self) -> Result<Digest, ProviderSerializationError> {
        let value =
            serde_json::to_value(self).map_err(|_| ProviderSerializationError::Serialization)?;
        let bytes =
            serde_json::to_vec(&value).map_err(|_| ProviderSerializationError::Serialization)?;
        Ok(Digest::sha256(bytes))
    }

    pub fn into_profile(self) -> Result<ProviderProfile, ProviderSerializationError> {
        let profile_digest = self.digest()?;
        Ok(ProviderProfile {
            capabilities: self,
            profile_digest,
        })
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ProviderProfile {
    #[serde(flatten)]
    pub capabilities: ProviderCapabilities,
    pub profile_digest: Digest,
}

impl ProviderProfile {
    pub fn validate(&self) -> Result<(), ProviderSerializationError> {
        if self.capabilities.context_window_tokens == 0
            || self.capabilities.reserved_output_tokens > self.capabilities.context_window_tokens
            || self.capabilities.roles.is_empty()
            || self.capabilities.part_kinds.is_empty()
            || (!self.capabilities.supports_cache_boundaries
                && self.capabilities.max_cache_boundaries != 0)
            || (self.capabilities.supports_cache_boundaries
                && self.capabilities.max_cache_boundaries == 0)
        {
            return Err(ProviderSerializationError::InvalidProfile);
        }
        if self.capabilities.digest()? != self.profile_digest {
            return Err(ProviderSerializationError::ProfileDigestMismatch);
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum BudgetConfidence {
    Measured,
    Calibrated,
    Conservative,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ModelBudget {
    pub context_window_tokens: u64,
    pub reserved_output_tokens: u64,
    pub max_input_tokens: u64,
    pub max_items: u64,
    pub max_images: u64,
    pub baseline_tokens: u64,
    pub delta_tokens: u64,
    pub tail_tokens: u64,
    pub confidence: BudgetConfidence,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ProviderGeneration {
    pub generation: u64,
    pub profile_digest: Digest,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct HostSerializedProjection {
    pub render_mode: String,
    pub generation: u64,
    pub provider_profile_digest: Digest,
    pub blocks: Vec<NormalizedBlock>,
    pub model_budget: ModelBudget,
    pub serialized_digest: Digest,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ProviderSerializationError {
    InvalidProfile,
    ProfileDigestMismatch,
    ProjectionProfileMismatch,
    GenerationNotAdvanced,
    UnsupportedRole,
    UnsupportedPart,
    UnsupportedReasoning,
    UnsupportedCacheBoundary,
    TooManyCacheBoundaries,
    TooManyImages,
    BudgetExceeded,
    OrphanToolResult,
    UnresolvedToolCall,
    ToolPairOrder,
    ToolPairNotAdjacent,
    Serialization,
}

impl fmt::Display for ProviderSerializationError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "{self:?}")
    }
}

impl Error for ProviderSerializationError {}

pub fn serialize_for_host(
    projection: &Projection,
    profile: &ProviderProfile,
    previous: Option<&ProviderGeneration>,
) -> Result<HostSerializedProjection, ProviderSerializationError> {
    profile.validate()?;
    if projection.provider_profile_digest != profile.profile_digest {
        return Err(ProviderSerializationError::ProjectionProfileMismatch);
    }
    if let Some(previous) = previous {
        if previous.profile_digest != profile.profile_digest
            && projection.generation <= previous.generation
        {
            return Err(ProviderSerializationError::GenerationNotAdvanced);
        }
    }

    let mut pending_tools = VecDeque::new();
    let mut cache_boundaries = 0_u64;
    let mut images = 0_u64;
    for block in &projection.blocks {
        if !profile.capabilities.roles.contains(&block.role) {
            return Err(ProviderSerializationError::UnsupportedRole);
        }
        if block.cache_boundary == crate::projection::CacheBoundary::After {
            cache_boundaries += 1;
            if !profile.capabilities.supports_cache_boundaries {
                return Err(ProviderSerializationError::UnsupportedCacheBoundary);
            }
        }
        for part in &block.parts {
            if !profile.capabilities.part_kinds.contains(&part.kind) {
                return Err(ProviderSerializationError::UnsupportedPart);
            }
            if part.kind == PartKind::Reasoning && !profile.capabilities.supports_reasoning {
                return Err(ProviderSerializationError::UnsupportedReasoning);
            }
            if matches!(part.kind, PartKind::Image | PartKind::File) {
                images += 1;
            }
            match part.kind {
                PartKind::ToolCall => {
                    if profile.capabilities.requires_tool_adjacency && !pending_tools.is_empty() {
                        return Err(ProviderSerializationError::ToolPairNotAdjacent);
                    }
                    pending_tools.push_back(
                        part.call_id
                            .clone()
                            .ok_or(ProviderSerializationError::UnresolvedToolCall)?,
                    );
                }
                PartKind::ToolResult => {
                    let result_id = part
                        .call_id
                        .as_ref()
                        .ok_or(ProviderSerializationError::OrphanToolResult)?;
                    let expected = pending_tools
                        .pop_front()
                        .ok_or(ProviderSerializationError::OrphanToolResult)?;
                    if &expected != result_id {
                        return Err(ProviderSerializationError::ToolPairOrder);
                    }
                }
                _ if profile.capabilities.requires_tool_adjacency && !pending_tools.is_empty() => {
                    return Err(ProviderSerializationError::ToolPairNotAdjacent);
                }
                _ => {}
            }
        }
    }
    if !pending_tools.is_empty() {
        return Err(ProviderSerializationError::UnresolvedToolCall);
    }
    if cache_boundaries > profile.capabilities.max_cache_boundaries {
        return Err(ProviderSerializationError::TooManyCacheBoundaries);
    }
    if images > profile.capabilities.max_images {
        return Err(ProviderSerializationError::TooManyImages);
    }

    let safe_input = profile
        .capabilities
        .context_window_tokens
        .saturating_sub(profile.capabilities.reserved_output_tokens);
    let used =
        projection.baseline.token_mass + projection.delta.token_mass + projection.tail.token_mass;
    if used > safe_input || used > projection.budget_inputs.max_input_tokens {
        return Err(ProviderSerializationError::BudgetExceeded);
    }
    let model_budget = ModelBudget {
        context_window_tokens: profile.capabilities.context_window_tokens,
        reserved_output_tokens: profile.capabilities.reserved_output_tokens,
        max_input_tokens: safe_input.min(projection.budget_inputs.max_input_tokens),
        max_items: projection.budget_inputs.max_items,
        max_images: profile
            .capabilities
            .max_images
            .min(projection.budget_inputs.max_images),
        baseline_tokens: projection.baseline.token_mass,
        delta_tokens: projection.delta.token_mass,
        tail_tokens: projection.tail.token_mass,
        confidence: BudgetConfidence::Conservative,
    };
    let digest_input = serde_json::json!({
        "blocks": projection.blocks,
        "model_budget": model_budget,
        "provider_profile_digest": profile.profile_digest,
        "render_mode": "host_serialized",
    });
    let digest_bytes =
        serde_json::to_vec(&digest_input).map_err(|_| ProviderSerializationError::Serialization)?;

    Ok(HostSerializedProjection {
        render_mode: "host_serialized".to_owned(),
        generation: projection.generation,
        provider_profile_digest: profile.profile_digest,
        blocks: projection.blocks.clone(),
        model_budget,
        serialized_digest: Digest::sha256(digest_bytes),
    })
}
