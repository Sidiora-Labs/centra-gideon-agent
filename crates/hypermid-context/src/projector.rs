pub use hypermid_core::projection::{
    project, projection_bytes, Projection, ProjectionError, ProjectionRequest,
};

use crate::journal::Journal;
use hypermid_contracts::{Cursor, Digest, Id, Scope};
use hypermid_core::history::{
    ContextItem as JournalItem, JournalRange, PartKind as JournalPartKind, Role as JournalRole,
};
use hypermid_core::projection::{
    ContextMode, ContextPart, ContextRole, PartKind, ProjectionBudgetInputs, ProjectionItem,
    ProjectionSummary, RegionKind,
};
use hypermid_core::summary::SummaryRecord;
use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone, Copy, Debug, Default)]
pub struct DeterministicProjector;

impl DeterministicProjector {
    pub fn project(&self, request: &ProjectionRequest) -> Result<Projection, ProjectionError> {
        project(request)
    }

    pub fn project_journal(
        &self,
        journal: &Journal,
        request: JournalProjectionRequest,
    ) -> Result<Projection, ProjectorError> {
        if journal.scope() != &request.scope || journal.session_id() != &request.session_id {
            return Err(ProjectorError::ScopeMismatch);
        }
        let committed = journal
            .items_through(request.source_cursor)
            .map_err(|_| ProjectorError::History)?;
        if committed.is_empty() {
            return Err(ProjectorError::History);
        }
        let range = JournalRange::new(committed[0].cursor, request.source_cursor)
            .map_err(|_| ProjectorError::History)?;
        let source_digest = journal
            .source_digest(range)
            .map_err(|_| ProjectorError::History)?;
        let mut selections = request
            .items
            .into_iter()
            .map(|selection| (selection.item_id, (selection.region, selection.token_mass)))
            .collect::<BTreeMap<_, _>>();
        let items = committed
            .iter()
            .filter_map(|item| {
                selections
                    .remove(&item.item_id)
                    .map(|(region, token_mass)| normalize_item(item, region, token_mass))
            })
            .collect::<Vec<_>>();
        if !selections.is_empty() {
            return Err(ProjectorError::IncompleteCoverage);
        }
        let records = request
            .summaries
            .iter()
            .map(|selection| selection.record.clone())
            .collect::<Vec<_>>();
        verify_summary_sources(journal, &records, request.source_cursor)?;
        let mut covered = items
            .iter()
            .map(|item| item.cursor)
            .collect::<BTreeSet<_>>();
        for selection in &request.summaries {
            for sequence in
                selection.record.source_start.sequence..=selection.record.source_end.sequence
            {
                let cursor = Cursor::new(selection.record.source_start.epoch, sequence)
                    .map_err(|_| ProjectorError::IncompleteCoverage)?;
                if !covered.insert(cursor) {
                    return Err(ProjectorError::OverlappingSummary);
                }
            }
        }
        if committed.iter().any(|item| !covered.contains(&item.cursor))
            || covered.len() != committed.len()
        {
            return Err(ProjectorError::IncompleteCoverage);
        }
        let summaries = request
            .summaries
            .iter()
            .map(|selection| {
                let tier = selection
                    .record
                    .tiers
                    .get(selection.level as usize)
                    .cloned()
                    .ok_or(ProjectorError::InvalidSummaryTier)?;
                Ok(ProjectionSummary {
                    summary_id: selection.record.summary_id.clone(),
                    source_start: selection.record.source_start,
                    source_end: selection.record.source_end,
                    source_digest: selection.record.source_digest,
                    tier,
                    region: selection.region,
                })
            })
            .collect::<Result<Vec<_>, ProjectorError>>()?;
        let projection = project(&ProjectionRequest {
            scope: request.scope,
            session_id: request.session_id,
            source_cursor: request.source_cursor,
            source_digest,
            generation: request.generation,
            policy_revision: request.policy_revision,
            mode: request.mode,
            provider_profile_digest: request.provider_profile_digest,
            budget_inputs: request.budget_inputs,
            created_at: request.created_at,
            items,
            summaries,
        })
        .map_err(ProjectorError::Projection)?;
        Ok(projection)
    }
}

#[derive(Clone, Debug)]
pub struct JournalItemSelection {
    pub item_id: Id,
    pub region: RegionKind,
    pub token_mass: u64,
}

#[derive(Clone, Debug)]
pub struct SummarySelection {
    pub record: SummaryRecord,
    pub level: u8,
    pub region: RegionKind,
}

#[derive(Clone, Debug)]
pub struct JournalProjectionRequest {
    pub scope: Scope,
    pub session_id: Id,
    pub source_cursor: Cursor,
    pub generation: u64,
    pub policy_revision: u64,
    pub mode: ContextMode,
    pub provider_profile_digest: Digest,
    pub budget_inputs: ProjectionBudgetInputs,
    pub created_at: String,
    pub items: Vec<JournalItemSelection>,
    pub summaries: Vec<SummarySelection>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ProjectorError {
    ScopeMismatch,
    History,
    IncompleteCoverage,
    SummarySourceMismatch,
    OverlappingSummary,
    InvalidSummaryTier,
    Projection(ProjectionError),
}

fn normalize_item(item: &JournalItem, region: RegionKind, token_mass: u64) -> ProjectionItem {
    let role = match item.role {
        JournalRole::System => ContextRole::System,
        JournalRole::User => ContextRole::User,
        JournalRole::Assistant => ContextRole::Assistant,
        JournalRole::Tool => ContextRole::Tool,
    };
    let parts = item
        .parts
        .iter()
        .map(|part| ContextPart {
            part_id: part.part_id.clone(),
            kind: match part.kind {
                JournalPartKind::Text => PartKind::Text,
                JournalPartKind::Reasoning => PartKind::Reasoning,
                JournalPartKind::ToolCall => PartKind::ToolCall,
                JournalPartKind::ToolResult => PartKind::ToolResult,
                JournalPartKind::Image => PartKind::Image,
                JournalPartKind::File => PartKind::File,
                JournalPartKind::ContextMarker => PartKind::ContextMarker,
            },
            content_digest: part.content_digest,
            text: part.text.clone(),
            call_id: part.call_id.clone(),
            tool_name: part.tool_name.clone(),
            arguments_json: part.arguments_json.clone(),
            result_json: part.result_json.clone(),
            media_type: part.media_type.clone(),
            source_uri: part.source_uri.clone(),
            width: part.width,
            height: part.height,
            metadata: part.metadata.clone(),
        })
        .collect();
    ProjectionItem {
        item_id: item.item_id.clone(),
        cursor: item.cursor,
        role,
        parts,
        region,
        token_mass,
    }
}

pub fn verify_summary_sources(
    journal: &Journal,
    summaries: &[SummaryRecord],
    source_cursor: Cursor,
) -> Result<(), ProjectorError> {
    let mut covered = BTreeSet::new();
    for summary in summaries {
        if &summary.scope != journal.scope()
            || &summary.session_id != journal.session_id()
            || summary.source_end > source_cursor
        {
            return Err(ProjectorError::SummarySourceMismatch);
        }
        let range = JournalRange::new(summary.source_start, summary.source_end)
            .map_err(|_| ProjectorError::SummarySourceMismatch)?;
        if journal
            .source_digest(range)
            .map_err(|_| ProjectorError::History)?
            != summary.source_digest
        {
            return Err(ProjectorError::SummarySourceMismatch);
        }
        for sequence in summary.source_start.sequence..=summary.source_end.sequence {
            if !covered.insert((summary.source_start.epoch, sequence)) {
                return Err(ProjectorError::OverlappingSummary);
            }
        }
    }
    Ok(())
}
