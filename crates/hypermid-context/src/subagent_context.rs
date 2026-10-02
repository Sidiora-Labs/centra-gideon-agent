use hypermid_contracts::{Cursor, Id, Scope};
use hypermid_core::history::{ContextPart, IngestRequest, PartKind, PendingContextItem, Role};
use hypermid_core::subagent::{
    ChildUsage, GideonContributionAuthorization, ParentContribution, SubagentContribution,
    SubagentError, SubagentSnapshot,
};
use std::collections::HashMap;

use crate::journal::{Journal, JournalError};

#[derive(Clone, Debug)]
struct ChildState {
    snapshot: SubagentSnapshot,
    cursor: Cursor,
    usage: ChildUsage,
}

#[derive(Default)]
pub struct SubagentContext {
    children: HashMap<Id, ChildState>,
    contributions: HashMap<Id, ParentContribution>,
}

impl SubagentContext {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn spawn(&mut self, snapshot: SubagentSnapshot) -> Result<SubagentSnapshot, ContextError> {
        snapshot.validate()?;
        if let Some(existing) = self.children.get(&snapshot.child_session_id) {
            return if existing.snapshot == snapshot {
                Ok(existing.snapshot.clone())
            } else {
                Err(ContextError::ChildIdentityConflict)
            };
        }
        self.children.insert(
            snapshot.child_session_id.clone(),
            ChildState {
                cursor: Cursor::new(1, 0).map_err(|_| ContextError::ChildCursorMismatch)?,
                usage: ChildUsage {
                    input_tokens: 0,
                    output_tokens: 0,
                    cache_read_tokens: 0,
                    cache_write_tokens: 0,
                },
                snapshot: snapshot.clone(),
            },
        );
        Ok(snapshot)
    }

    pub fn snapshot(
        &self,
        child_session_id: &Id,
        scope: &Scope,
    ) -> Result<SubagentSnapshot, ContextError> {
        let state = self
            .children
            .get(child_session_id)
            .ok_or(ContextError::ChildNotFound)?;
        if &state.snapshot.scope != scope {
            return Err(ContextError::ScopeMismatch);
        }
        Ok(state.snapshot.clone())
    }

    pub fn record_child_outcome(
        &mut self,
        child_session_id: &Id,
        scope: &Scope,
        cursor: Cursor,
        usage: ChildUsage,
    ) -> Result<(), ContextError> {
        let state = self
            .children
            .get_mut(child_session_id)
            .ok_or(ContextError::ChildNotFound)?;
        if &state.snapshot.scope != scope {
            return Err(ContextError::ScopeMismatch);
        }
        if cursor.epoch != state.cursor.epoch || cursor.sequence < state.cursor.sequence {
            return Err(ContextError::ChildCursorMismatch);
        }
        state.cursor = cursor;
        state.usage.checked_add(&usage)?;
        Ok(())
    }

    pub fn child_usage(
        &self,
        child_session_id: &Id,
        scope: &Scope,
    ) -> Result<ChildUsage, ContextError> {
        let state = self
            .children
            .get(child_session_id)
            .ok_or(ContextError::ChildNotFound)?;
        if &state.snapshot.scope != scope {
            return Err(ContextError::ScopeMismatch);
        }
        Ok(state.usage.clone())
    }

    #[allow(clippy::too_many_arguments)]
    pub fn publish_contribution(
        &mut self,
        parent_journal: &Journal,
        scope: &Scope,
        parent_item_id: Id,
        part_id: Id,
        idempotency_key: Id,
        created_at: String,
        contribution: SubagentContribution,
        authorization: &GideonContributionAuthorization,
        now_ms: u64,
    ) -> Result<ParentContribution, ContextError> {
        contribution.validate()?;
        let child = self
            .children
            .get(&contribution.child_session_id)
            .ok_or(ContextError::ChildNotFound)?;
        if &child.snapshot.scope != scope || parent_journal.scope() != scope {
            return Err(ContextError::ScopeMismatch);
        }
        if parent_journal.session_id() != &child.snapshot.parent_session_id {
            return Err(ContextError::ParentMismatch);
        }
        if contribution.child_cursor.epoch != child.cursor.epoch
            || contribution.child_cursor.sequence > child.cursor.sequence
        {
            return Err(ContextError::ChildCursorMismatch);
        }
        if authorization.expires_at_ms <= now_ms
            || authorization.authorized_by != contribution.authorized_by
            || authorization.parent_session_id != child.snapshot.parent_session_id
            || authorization.child_session_id != child.snapshot.child_session_id
            || &authorization.scope != scope
        {
            return Err(ContextError::AuthorizationDenied);
        }
        if let Some(previous) = self.contributions.get(&idempotency_key) {
            return Ok(previous.clone());
        }

        let pending = PendingContextItem {
            item_id: parent_item_id.clone(),
            source_event_id: idempotency_key.clone(),
            source_digest: contribution.content_digest,
            scope: scope.clone(),
            session_id: child.snapshot.parent_session_id.clone(),
            role: Role::Assistant,
            parts: vec![ContextPart {
                part_id,
                kind: PartKind::Text,
                content_digest: contribution.content_digest,
                text: Some(contribution.content.clone()),
                call_id: None,
                tool_name: None,
                arguments_json: None,
                result_json: None,
                media_type: None,
                source_uri: None,
                width: None,
                height: None,
                metadata: Some(serde_json::json!({
                    "child_session_id": child.snapshot.child_session_id,
                    "child_cursor": contribution.child_cursor,
                    "child_snapshot_digest": child.snapshot.source_digest,
                    "authorized_by": contribution.authorized_by,
                })),
            }],
            relations: Vec::new(),
            created_at,
            recoverable: true,
            tombstone: false,
        };
        let item = parent_journal.append(IngestRequest {
            expected_cursor: parent_journal.current_cursor(),
            idempotency_key: idempotency_key.clone(),
            item: pending,
            source_snapshot: Some(contribution.content.as_bytes().to_vec()),
        })?;
        let published = ParentContribution {
            parent_item_id: item.item_id,
            parent_session_id: item.session_id,
            child_session_id: contribution.child_session_id,
            child_cursor: contribution.child_cursor,
            child_snapshot_digest: child.snapshot.source_digest,
            content: contribution.content,
            content_digest: contribution.content_digest,
            authorized_by: contribution.authorized_by,
        };
        self.contributions
            .insert(idempotency_key, published.clone());
        Ok(published)
    }
}

#[derive(Debug, thiserror::Error)]
pub enum ContextError {
    #[error(transparent)]
    Contract(#[from] SubagentError),
    #[error(transparent)]
    Journal(#[from] JournalError),
    #[error("child session identity already names another snapshot")]
    ChildIdentityConflict,
    #[error("child session was not spawned")]
    ChildNotFound,
    #[error("scope does not own this child context")]
    ScopeMismatch,
    #[error("child outcome cursor is outside its recorded stream")]
    ChildCursorMismatch,
    #[error("child snapshot belongs to another parent")]
    ParentMismatch,
    #[error("Gideon did not authorize this exact contribution")]
    AuthorizationDenied,
}
