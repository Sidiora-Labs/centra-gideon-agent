use hypermid_contracts::{Cursor, Id, Scope};
use hypermid_core::identity::{ContextIdentity, IdentityBinding, IdentityKind, RebindRecord};
use serde::{Deserialize, Serialize};
use std::collections::{btree_map::Entry, BTreeMap};

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct IdentityState {
    pub schema_version: u32,
    pub bindings: Vec<IdentityBinding>,
    pub identities: Vec<ContextIdentity>,
}

#[derive(Clone, Debug, Default)]
pub struct IdentityStore {
    bindings: BTreeMap<Id, IdentityBinding>,
    identities: BTreeMap<Id, ContextIdentity>,
    source_items: BTreeMap<(Id, Id), Id>,
}

impl IdentityStore {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn bind_session(
        &mut self,
        session_id: Id,
        scope: Scope,
    ) -> Result<&IdentityBinding, IdentityStoreError> {
        match self.bindings.entry(session_id.clone()) {
            Entry::Occupied(existing) => {
                if existing.get().scope != scope {
                    return Err(IdentityStoreError::ScopeMismatch);
                }
                Ok(existing.into_mut())
            }
            Entry::Vacant(empty) => Ok(empty.insert(IdentityBinding::new(session_id, scope))),
        }
    }

    pub fn binding(
        &self,
        session_id: &Id,
        scope: &Scope,
    ) -> Result<&IdentityBinding, IdentityStoreError> {
        let binding = self
            .bindings
            .get(session_id)
            .ok_or(IdentityStoreError::SessionNotBound)?;
        if &binding.scope != scope {
            return Err(IdentityStoreError::ScopeMismatch);
        }
        Ok(binding)
    }

    pub fn identity(
        &self,
        identity_id: &Id,
        scope: &Scope,
    ) -> Result<&ContextIdentity, IdentityStoreError> {
        let identity = self
            .identities
            .get(identity_id)
            .ok_or(IdentityStoreError::IdentityNotFound)?;
        self.binding(&identity.session_id, scope)?;
        Ok(identity)
    }

    pub fn register(
        &mut self,
        identity: ContextIdentity,
    ) -> Result<&ContextIdentity, IdentityStoreError> {
        if identity.kind == IdentityKind::JournalItem && identity.source.is_none() {
            return Err(IdentityStoreError::SourceIdentityRequired);
        }
        self.binding(&identity.session_id, &identity.scope)?;
        match self.identities.entry(identity.identity_id.clone()) {
            Entry::Occupied(existing) => {
                if existing.get() != &identity {
                    return Err(IdentityStoreError::IdentityConflict);
                }
                Ok(existing.into_mut())
            }
            Entry::Vacant(empty) => Ok(empty.insert(identity)),
        }
    }

    pub fn register_source_item(
        &mut self,
        identity: ContextIdentity,
    ) -> Result<&ContextIdentity, IdentityStoreError> {
        if identity.kind != IdentityKind::JournalItem || identity.source.is_none() {
            return Err(IdentityStoreError::SourceIdentityRequired);
        }
        self.binding(&identity.session_id, &identity.scope)?;
        let source = identity.source.as_ref().expect("checked source");
        let key = (identity.session_id.clone(), source.source_event_id.clone());
        if let Some(existing_id) = self.source_items.get(&key) {
            let existing = self
                .identities
                .get(existing_id)
                .expect("source index references identity");
            if existing.source.as_ref() != identity.source.as_ref() {
                return Err(IdentityStoreError::SourceIdentityConflict);
            }
            return Ok(existing);
        }
        let identity_id = identity.identity_id.clone();
        self.register(identity)?;
        self.source_items.insert(key, identity_id.clone());
        Ok(self
            .identities
            .get(&identity_id)
            .expect("registered source identity"))
    }

    pub fn rebind(
        &mut self,
        rebind_id: Id,
        session_id: Id,
        previous_scope: Scope,
        next_scope: Scope,
        cursor: Cursor,
        reason: impl Into<String>,
    ) -> Result<RebindRecord, IdentityStoreError> {
        let reason = reason.into();
        if let Some(existing) = self.bindings.get(&session_id).and_then(|binding| {
            binding
                .rebinds
                .iter()
                .find(|item| item.rebind_id == rebind_id)
        }) {
            if existing.previous_scope != previous_scope
                || existing.next_scope != next_scope
                || existing.cursor != cursor
                || existing.reason != reason
            {
                return Err(IdentityStoreError::IdentityConflict);
            }
            return Ok(existing.clone());
        }
        self.binding(&session_id, &previous_scope)?;
        if previous_scope.owner_id != next_scope.owner_id
            || previous_scope.project_id != next_scope.project_id
        {
            return Err(IdentityStoreError::ScopeMismatch);
        }
        if previous_scope.workspace_id == next_scope.workspace_id {
            return Err(IdentityStoreError::InvalidRebind);
        }
        let record = RebindRecord::new(
            rebind_id,
            session_id.clone(),
            previous_scope,
            next_scope.clone(),
            cursor,
            reason,
        )?;
        let binding = self
            .bindings
            .get_mut(&session_id)
            .expect("validated binding");
        binding.scope = next_scope.clone();
        binding.rebinds.push(record.clone());
        for identity in self.identities.values_mut() {
            if identity.session_id == session_id {
                identity.scope = next_scope.clone();
            }
        }
        Ok(record)
    }

    pub fn state(&self) -> IdentityState {
        IdentityState {
            schema_version: 1,
            bindings: self.bindings.values().cloned().collect(),
            identities: self.identities.values().cloned().collect(),
        }
    }

    pub fn from_state(state: IdentityState) -> Result<Self, IdentityStoreError> {
        if state.schema_version != 1 {
            return Err(IdentityStoreError::UnsupportedVersion);
        }
        let mut store = Self::new();
        for binding in state.bindings {
            if binding
                .rebinds
                .iter()
                .any(|rebind| rebind.session_id != binding.session_id)
                || store.bindings.contains_key(&binding.session_id)
            {
                return Err(IdentityStoreError::IdentityConflict);
            }
            store.bindings.insert(binding.session_id.clone(), binding);
        }
        for identity in state.identities {
            let source_key = identity
                .source
                .as_ref()
                .map(|source| (identity.session_id.clone(), source.source_event_id.clone()));
            let identity_id = identity.identity_id.clone();
            store.register(identity)?;
            if let Some(key) = source_key {
                if store.source_items.insert(key, identity_id).is_some() {
                    return Err(IdentityStoreError::IdentityConflict);
                }
            }
        }
        Ok(store)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, thiserror::Error)]
pub enum IdentityStoreError {
    #[error("session has no identity binding")]
    SessionNotBound,
    #[error("scope does not match session binding")]
    ScopeMismatch,
    #[error("identity was not found")]
    IdentityNotFound,
    #[error("identity is already bound differently")]
    IdentityConflict,
    #[error("source event id was replayed with another digest")]
    SourceIdentityConflict,
    #[error("source registration requires a journal item and source identity")]
    SourceIdentityRequired,
    #[error("workspace binding did not change")]
    InvalidRebind,
    #[error("identity state version is unsupported")]
    UnsupportedVersion,
    #[error(transparent)]
    Contract(#[from] hypermid_core::identity::IdentityContractError),
}
