use crate::summary::SummaryTier;
use hypermid_contracts::{Cursor, Digest, Id, Scope};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest as _, Sha256};
use std::collections::BTreeSet;
use std::error::Error;
use std::fmt;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ContextMode {
    Off,
    PassThrough,
    Shadow,
    Primary,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ContextRole {
    System,
    User,
    Assistant,
    Tool,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PartKind {
    Text,
    Reasoning,
    ToolCall,
    ToolResult,
    Image,
    File,
    ContextMarker,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RegionKind {
    Baseline,
    Delta,
    Tail,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ContextPart {
    pub part_id: Id,
    pub kind: PartKind,
    pub content_digest: Digest,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub text: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub call_id: Option<Id>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub arguments_json: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result_json: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub media_type: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub source_uri: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub width: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub height: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub metadata: Option<Value>,
}

impl ContextPart {
    pub fn text(part_id: Id, text: impl Into<String>) -> Self {
        let text = text.into();
        Self {
            part_id,
            kind: PartKind::Text,
            content_digest: Digest::sha256(text.as_bytes()),
            text: Some(text),
            call_id: None,
            tool_name: None,
            arguments_json: None,
            result_json: None,
            media_type: None,
            source_uri: None,
            width: None,
            height: None,
            metadata: None,
        }
    }

    pub fn validate(&self) -> Result<(), ProjectionError> {
        let digest = match self.kind {
            PartKind::Text | PartKind::Reasoning | PartKind::ContextMarker => {
                let text = self
                    .text
                    .as_deref()
                    .ok_or(ProjectionError::InvalidPart("text is required"))?;
                Digest::sha256(text.as_bytes())
            }
            PartKind::ToolCall => {
                let call_id = self
                    .call_id
                    .as_ref()
                    .ok_or(ProjectionError::InvalidPart("tool call id is required"))?;
                let tool_name = self
                    .tool_name
                    .as_ref()
                    .ok_or(ProjectionError::InvalidPart("tool name is required"))?;
                let arguments = self
                    .arguments_json
                    .as_deref()
                    .ok_or(ProjectionError::InvalidPart("tool arguments are required"))?;
                structured_digest(&[
                    call_id.as_str().as_bytes(),
                    tool_name.as_bytes(),
                    arguments.as_bytes(),
                ])
            }
            PartKind::ToolResult => {
                let call_id = self.call_id.as_ref().ok_or(ProjectionError::InvalidPart(
                    "tool result call id is required",
                ))?;
                let result = self
                    .result_json
                    .as_deref()
                    .ok_or(ProjectionError::InvalidPart("tool result is required"))?;
                structured_digest(&[call_id.as_str().as_bytes(), result.as_bytes()])
            }
            PartKind::Image | PartKind::File => {
                let media_type = self
                    .media_type
                    .as_deref()
                    .filter(|value| !value.is_empty() && value.len() <= 128)
                    .ok_or(ProjectionError::InvalidPart("media type is required"))?;
                let source_uri = self
                    .source_uri
                    .as_deref()
                    .filter(|value| value.len() <= 4_096)
                    .ok_or(ProjectionError::InvalidPart("source uri is required"))?;
                if self
                    .width
                    .is_some_and(|value| value == 0 || value > 100_000)
                    || self
                        .height
                        .is_some_and(|value| value == 0 || value > 100_000)
                {
                    return Err(ProjectionError::InvalidPart("media dimensions are invalid"));
                }
                structured_digest(&[
                    media_type.as_bytes(),
                    source_uri.as_bytes(),
                    &self.width.unwrap_or(0).to_be_bytes(),
                    &self.height.unwrap_or(0).to_be_bytes(),
                ])
            }
        };
        if digest != self.content_digest {
            return Err(ProjectionError::DigestMismatch);
        }
        Ok(())
    }
}

fn structured_digest(fields: &[&[u8]]) -> Digest {
    let mut material = Vec::new();
    for field in fields {
        material.extend_from_slice(&u64::try_from(field.len()).unwrap_or(u64::MAX).to_be_bytes());
        material.extend_from_slice(field);
    }
    Digest::sha256(material)
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ProjectionItem {
    pub item_id: Id,
    pub cursor: Cursor,
    pub role: ContextRole,
    pub parts: Vec<ContextPart>,
    pub region: RegionKind,
    pub token_mass: u64,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ProjectionSummary {
    pub summary_id: Id,
    pub source_start: Cursor,
    pub source_end: Cursor,
    pub source_digest: Digest,
    pub tier: SummaryTier,
    pub region: RegionKind,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ProjectionBudgetInputs {
    pub context_window_tokens: u64,
    pub reserved_output_tokens: u64,
    pub max_input_tokens: u64,
    pub max_items: u64,
    pub max_images: u64,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct NormalizedBlock {
    pub block_id: Id,
    pub role: ContextRole,
    pub parts: Vec<ContextPart>,
    pub source_item_ids: Vec<Id>,
    pub content_digest: Digest,
    pub cache_boundary: CacheBoundary,
    pub synthetic: bool,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CacheBoundary {
    None,
    After,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ProjectionRegion {
    pub kind: RegionKind,
    pub digest: Digest,
    #[serde(skip)]
    pub bytes: Vec<u8>,
    pub item_ids: Vec<Id>,
    pub summary_ids: Vec<Id>,
    pub token_mass: u64,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ProjectionRequest {
    pub scope: Scope,
    pub session_id: Id,
    pub source_cursor: Cursor,
    pub source_digest: Digest,
    pub generation: u64,
    pub policy_revision: u64,
    pub mode: ContextMode,
    pub provider_profile_digest: Digest,
    pub budget_inputs: ProjectionBudgetInputs,
    pub created_at: String,
    pub items: Vec<ProjectionItem>,
    pub summaries: Vec<ProjectionSummary>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Projection {
    pub projection_id: Id,
    pub scope: Scope,
    pub session_id: Id,
    pub source_cursor: Cursor,
    pub source_digest: Digest,
    pub generation: u64,
    pub policy_revision: u64,
    pub mode: ContextMode,
    pub render_mode: String,
    pub provider_profile_digest: Digest,
    pub budget_inputs: ProjectionBudgetInputs,
    pub selected_item_ids: Vec<Id>,
    pub selected_summary_ids: Vec<Id>,
    pub baseline: ProjectionRegion,
    pub delta: ProjectionRegion,
    pub tail: ProjectionRegion,
    pub blocks: Vec<NormalizedBlock>,
    pub output_digest: Digest,
    pub reason_code: String,
    pub created_at: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ProjectionError {
    EmptyProjection,
    InvalidGeneration,
    InvalidPolicyRevision,
    InvalidCursorOrder,
    DuplicateItem,
    DuplicateSummary,
    InvalidSummary,
    EmptyParts,
    InvalidPart(&'static str),
    DigestMismatch,
    BudgetExceeded,
    Serialization,
}

impl fmt::Display for ProjectionError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidPart(message) => write!(formatter, "invalid part: {message}"),
            other => write!(formatter, "{other:?}"),
        }
    }
}

impl Error for ProjectionError {}

fn canonical<T: Serialize + ?Sized>(value: &T) -> Result<Vec<u8>, ProjectionError> {
    let value = serde_json::to_value(value).map_err(|_| ProjectionError::Serialization)?;
    serde_json::to_vec(&value).map_err(|_| ProjectionError::Serialization)
}

fn digest_sequence<'a>(values: impl IntoIterator<Item = &'a [u8]>) -> Digest {
    let mut hasher = Sha256::new();
    for value in values {
        hasher.update((value.len() as u64).to_be_bytes());
        hasher.update(value);
    }
    Digest::from_bytes(hasher.finalize().into())
}

fn build_region(
    kind: RegionKind,
    blocks: &[&NormalizedBlock],
    item_ids: Vec<Id>,
    summary_ids: Vec<Id>,
    token_mass: u64,
) -> Result<ProjectionRegion, ProjectionError> {
    let block_bytes = blocks
        .iter()
        .map(|block| canonical(*block))
        .collect::<Result<Vec<_>, _>>()?;
    let bytes = canonical(blocks)?;
    Ok(ProjectionRegion {
        kind,
        digest: digest_sequence(block_bytes.iter().map(Vec::as_slice)),
        bytes,
        item_ids,
        summary_ids,
        token_mass,
    })
}

pub fn project(request: &ProjectionRequest) -> Result<Projection, ProjectionError> {
    if request.items.is_empty() && request.summaries.is_empty() {
        return Err(ProjectionError::EmptyProjection);
    }
    if request.generation == 0 {
        return Err(ProjectionError::InvalidGeneration);
    }
    if request.policy_revision == 0 {
        return Err(ProjectionError::InvalidPolicyRevision);
    }
    if (request.items.len() + request.summaries.len()) as u64 > request.budget_inputs.max_items {
        return Err(ProjectionError::BudgetExceeded);
    }

    let mut seen = BTreeSet::new();
    let mut previous = None;
    let mut blocks = Vec::with_capacity(request.items.len());
    let mut metadata = Vec::with_capacity(request.items.len() + request.summaries.len());
    let mut total_mass = 0_u64;
    for item in &request.items {
        if item.parts.is_empty() {
            return Err(ProjectionError::EmptyParts);
        }
        if item.cursor.epoch != request.source_cursor.epoch
            || item.cursor > request.source_cursor
            || previous.is_some_and(|cursor| item.cursor <= cursor)
        {
            return Err(ProjectionError::InvalidCursorOrder);
        }
        if !seen.insert(item.item_id.clone()) {
            return Err(ProjectionError::DuplicateItem);
        }
        for part in &item.parts {
            part.validate()?;
        }
        total_mass = total_mass
            .checked_add(item.token_mass)
            .ok_or(ProjectionError::BudgetExceeded)?;
        previous = Some(item.cursor);
        let content_digest = digest_sequence(
            item.parts
                .iter()
                .map(|part| part.content_digest.as_bytes().as_slice()),
        );
        blocks.push(NormalizedBlock {
            block_id: Id::new(format!("block:{}", item.item_id))
                .map_err(|_| ProjectionError::Serialization)?,
            role: item.role,
            parts: item.parts.clone(),
            source_item_ids: vec![item.item_id.clone()],
            content_digest,
            cache_boundary: CacheBoundary::None,
            synthetic: false,
        });
        metadata.push((
            item.cursor,
            item.region,
            Some(item.item_id.clone()),
            None,
            item.token_mass,
        ));
    }
    let mut seen_summaries = BTreeSet::new();
    for summary in &request.summaries {
        if !seen_summaries.insert(summary.summary_id.clone()) {
            return Err(ProjectionError::DuplicateSummary);
        }
        if summary.source_start.epoch != request.source_cursor.epoch
            || summary.source_end > request.source_cursor
            || summary.source_start > summary.source_end
            || summary.tier.validate().is_err()
        {
            return Err(ProjectionError::InvalidSummary);
        }
        total_mass = total_mass
            .checked_add(summary.tier.token_mass)
            .ok_or(ProjectionError::BudgetExceeded)?;
        let part_id = Id::new(format!(
            "summary-tier:{}:{}",
            summary.summary_id, summary.tier.level
        ))
        .map_err(|_| ProjectionError::Serialization)?;
        let part = ContextPart {
            part_id,
            kind: PartKind::ContextMarker,
            content_digest: summary.tier.content_digest,
            text: Some(summary.tier.content.clone()),
            call_id: None,
            tool_name: None,
            arguments_json: None,
            result_json: None,
            media_type: None,
            source_uri: None,
            width: None,
            height: None,
            metadata: None,
        };
        blocks.push(NormalizedBlock {
            block_id: Id::new(format!(
                "summary:{}:{}",
                summary.summary_id, summary.tier.level
            ))
            .map_err(|_| ProjectionError::Serialization)?,
            role: ContextRole::System,
            parts: vec![part],
            source_item_ids: Vec::new(),
            content_digest: summary.tier.content_digest,
            cache_boundary: CacheBoundary::None,
            synthetic: true,
        });
        metadata.push((
            summary.source_start,
            summary.region,
            None,
            Some(summary.summary_id.clone()),
            summary.tier.token_mass,
        ));
    }
    if total_mass > request.budget_inputs.max_input_tokens {
        return Err(ProjectionError::BudgetExceeded);
    }

    let mut indexed = blocks.into_iter().zip(metadata).collect::<Vec<_>>();
    indexed.sort_by_key(|(_, metadata)| metadata.0);
    let blocks = indexed
        .iter()
        .map(|(block, _)| block.clone())
        .collect::<Vec<_>>();
    let make_region = |kind| {
        let selected = indexed
            .iter()
            .filter(|(_, metadata)| metadata.1 == kind)
            .collect::<Vec<_>>();
        build_region(
            kind,
            &selected.iter().map(|(block, _)| block).collect::<Vec<_>>(),
            selected
                .iter()
                .filter_map(|(_, metadata)| metadata.2.clone())
                .collect(),
            selected
                .iter()
                .filter_map(|(_, metadata)| metadata.3.clone())
                .collect(),
            selected.iter().map(|(_, metadata)| metadata.4).sum(),
        )
    };
    let baseline = make_region(RegionKind::Baseline)?;
    let delta = make_region(RegionKind::Delta)?;
    let tail = make_region(RegionKind::Tail)?;
    let output_bytes = canonical(&blocks)?;
    let output_digest = Digest::sha256(&output_bytes);
    let projection_id = Id::new(format!("projection:{output_digest}"))
        .map_err(|_| ProjectionError::Serialization)?;

    Ok(Projection {
        projection_id,
        scope: request.scope.clone(),
        session_id: request.session_id.clone(),
        source_cursor: request.source_cursor,
        source_digest: request.source_digest,
        generation: request.generation,
        policy_revision: request.policy_revision,
        mode: request.mode,
        render_mode: "host_serialized".to_owned(),
        provider_profile_digest: request.provider_profile_digest,
        budget_inputs: request.budget_inputs.clone(),
        selected_item_ids: request
            .items
            .iter()
            .map(|item| item.item_id.clone())
            .collect(),
        selected_summary_ids: request
            .summaries
            .iter()
            .map(|summary| summary.summary_id.clone())
            .collect(),
        baseline,
        delta,
        tail,
        blocks,
        output_digest,
        reason_code: "projection_created".to_owned(),
        created_at: request.created_at.clone(),
    })
}

pub fn projection_bytes(projection: &Projection) -> Result<Vec<u8>, ProjectionError> {
    canonical(projection)
}
