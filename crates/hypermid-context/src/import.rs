use crate::export::{
    canonical_json_bytes, validate_payload_binding, ContextExportBundle, ContextExportError,
    ContextPortableRecord, ContextSessionBinding, PortabilityEntryKind,
    CONTEXT_PORTABILITY_SCHEMA_VERSION, MAX_PORTABILITY_ENTRIES, MAX_PORTABILITY_ENTRY_BYTES,
    MAX_PORTABILITY_TOTAL_BYTES,
};
use hypermid_contracts::{Cursor, Digest, Id, Scope};
use hypermid_core::history::SourceAdapter;
use serde::{Deserialize, Serialize};
use std::collections::{btree_map::Entry, BTreeMap, BTreeSet};

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct StagedContextImport {
    pub staging_id: Id,
    pub bundle: ContextExportBundle,
    pub bundle_digest: Digest,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ContextImportReceipt {
    pub staging_id: Id,
    pub manifest_id: Id,
    pub scope: Scope,
    pub session_id: Id,
    pub cursor: Cursor,
    pub entries_digest: Digest,
    pub bundle_digest: Digest,
    pub committed: bool,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RestoredContext {
    pub receipt: ContextImportReceipt,
    pub binding: ContextSessionBinding,
    pub source_references: Vec<ContextPortableRecord>,
    pub derived_records: Vec<ContextPortableRecord>,
}

#[derive(Default)]
pub struct ContextImportStore {
    staged: BTreeMap<Id, StagedContextImport>,
    visible: BTreeMap<(Scope, Id), (ContextImportReceipt, ContextExportBundle)>,
}

impl ContextImportStore {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn stage(
        &mut self,
        staging_id: Id,
        target_scope: &Scope,
        bundle: ContextExportBundle,
    ) -> Result<&StagedContextImport, ContextImportError> {
        validate_context_export(&bundle, target_scope)?;
        let bundle_digest = Digest::sha256(canonical_json_bytes(&bundle)?);
        match self.staged.entry(staging_id.clone()) {
            Entry::Occupied(existing) => {
                if existing.get().bundle_digest != bundle_digest {
                    return Err(ContextImportError::StagingConflict);
                }
                Ok(existing.into_mut())
            }
            Entry::Vacant(empty) => Ok(empty.insert(StagedContextImport {
                staging_id,
                bundle,
                bundle_digest,
            })),
        }
    }

    pub fn commit(&mut self, staging_id: &Id) -> Result<ContextImportReceipt, ContextImportError> {
        let staged = self
            .staged
            .get(staging_id)
            .cloned()
            .ok_or(ContextImportError::StageNotFound)?;
        let key = (
            staged.bundle.manifest.scope.clone(),
            staged.bundle.manifest.session_id.clone(),
        );
        if let Some((receipt, _)) = self.visible.get(&key) {
            if receipt.bundle_digest == staged.bundle_digest {
                let receipt = receipt.clone();
                self.staged.remove(staging_id);
                return Ok(receipt);
            }
            return Err(ContextImportError::VisibleConflict);
        }
        let receipt = ContextImportReceipt {
            staging_id: staging_id.clone(),
            manifest_id: staged.bundle.manifest.manifest_id.clone(),
            scope: staged.bundle.manifest.scope.clone(),
            session_id: staged.bundle.manifest.session_id.clone(),
            cursor: staged.bundle.manifest.cursor,
            entries_digest: staged.bundle.manifest.entries_digest,
            bundle_digest: staged.bundle_digest,
            committed: true,
        };
        let bundle = staged.bundle.clone();
        self.visible.insert(key, (receipt.clone(), bundle));
        self.staged.remove(staging_id);
        Ok(receipt)
    }

    pub fn visible(&self, scope: &Scope, session_id: &Id) -> Option<&ContextImportReceipt> {
        self.visible
            .get(&(scope.clone(), session_id.clone()))
            .map(|(receipt, _)| receipt)
    }

    pub fn restore<A: SourceAdapter>(
        &self,
        scope: &Scope,
        session_id: &Id,
        source_adapter: &A,
    ) -> Result<RestoredContext, ContextImportError> {
        let (receipt, bundle) = self
            .visible
            .get(&(scope.clone(), session_id.clone()))
            .ok_or(ContextImportError::VisibleNotFound)?;
        validate_context_export(bundle, scope)?;
        for record in bundle
            .records
            .iter()
            .filter(|record| record.kind == PortabilityEntryKind::SourceReference)
        {
            let object = record
                .payload
                .as_object()
                .ok_or(ContextImportError::ManifestMismatch)?;
            let source_event_id: Id = serde_json::from_value(object["source_event_id"].clone())?;
            let source_digest: Digest = serde_json::from_value(object["source_digest"].clone())?;
            let source_bytes = source_adapter
                .resolve(scope, session_id, &source_event_id)
                .map_err(|_| ContextImportError::SourceUnavailable)?;
            if Digest::sha256(source_bytes) != source_digest {
                return Err(ContextImportError::SourceDigestMismatch);
            }
        }
        let (source_references, derived_records): (Vec<_>, Vec<_>) = bundle
            .records
            .iter()
            .cloned()
            .partition(|record| record.kind == PortabilityEntryKind::SourceReference);
        Ok(RestoredContext {
            receipt: receipt.clone(),
            binding: bundle.binding.clone(),
            source_references,
            derived_records,
        })
    }
}

pub fn validate_context_export(
    bundle: &ContextExportBundle,
    target_scope: &Scope,
) -> Result<(), ContextImportError> {
    let manifest = &bundle.manifest;
    if manifest.schema_version != CONTEXT_PORTABILITY_SCHEMA_VERSION {
        return Err(ContextImportError::UnsupportedVersion);
    }
    if &manifest.scope != target_scope
        || bundle.binding.scope != manifest.scope
        || bundle.binding.session_id != manifest.session_id
        || bundle.binding.cursor != manifest.cursor
    {
        return Err(ContextImportError::ScopeMismatch);
    }
    if manifest.created_at.is_empty()
        || manifest.entries.len() != bundle.records.len()
        || manifest.entries.len() > MAX_PORTABILITY_ENTRIES
    {
        return Err(ContextImportError::ManifestMismatch);
    }
    if Digest::sha256(canonical_json_bytes(&bundle.binding)?) != manifest.session_binding_digest
        || Digest::sha256(canonical_json_bytes(&manifest.entries)?) != manifest.entries_digest
    {
        return Err(ContextImportError::DigestMismatch);
    }
    let mut entry_ids = BTreeSet::new();
    let mut total_bytes = 0_usize;
    let mut last_source_cursor = None;
    for (entry, record) in manifest.entries.iter().zip(&bundle.records) {
        if !entry_ids.insert(entry.entry_id.clone())
            || entry.entry_id != record.entry_id
            || entry.kind != record.kind
        {
            return Err(ContextImportError::ManifestMismatch);
        }
        validate_payload_binding(
            &bundle.binding,
            record.kind,
            &record.payload,
            last_source_cursor,
        )?;
        if record.kind == PortabilityEntryKind::SourceReference {
            last_source_cursor = Some(serde_json::from_value(record.payload["cursor"].clone())?);
        }
        let payload = canonical_json_bytes(&record.payload)?;
        if payload.len() > MAX_PORTABILITY_ENTRY_BYTES
            || entry.byte_length != payload.len() as u64
            || entry.content_digest != Digest::sha256(&payload)
        {
            return Err(ContextImportError::DigestMismatch);
        }
        total_bytes = total_bytes
            .checked_add(payload.len())
            .ok_or(ContextImportError::BundleTooLarge)?;
        if total_bytes > MAX_PORTABILITY_TOTAL_BYTES {
            return Err(ContextImportError::BundleTooLarge);
        }
    }
    Ok(())
}

impl From<ContextExportError> for ContextImportError {
    fn from(error: ContextExportError) -> Self {
        match error {
            ContextExportError::ScopeMismatch => Self::ScopeMismatch,
            ContextExportError::EntryTooLarge | ContextExportError::BundleTooLarge => {
                Self::BundleTooLarge
            }
            ContextExportError::Json(error) => Self::Json(error),
            ContextExportError::ForbiddenState => Self::ForbiddenState,
            _ => Self::ManifestMismatch,
        }
    }
}

#[derive(Debug, thiserror::Error)]
pub enum ContextImportError {
    #[error("context portability schema version is unsupported")]
    UnsupportedVersion,
    #[error("context portability scope or session binding mismatches")]
    ScopeMismatch,
    #[error("context portability manifest does not match its records")]
    ManifestMismatch,
    #[error("context portability digest validation failed")]
    DigestMismatch,
    #[error("context portability bundle exceeds the size bound")]
    BundleTooLarge,
    #[error("context portability payload contains excluded state")]
    ForbiddenState,
    #[error("staging identity was reused for another import")]
    StagingConflict,
    #[error("staged context import was not found")]
    StageNotFound,
    #[error("another context import is already visible for this session")]
    VisibleConflict,
    #[error("committed context import was not found")]
    VisibleNotFound,
    #[error("authoritative context source could not be resolved")]
    SourceUnavailable,
    #[error("authoritative context source digest does not match the export")]
    SourceDigestMismatch,
    #[error("context portability JSON is invalid")]
    Json(#[from] serde_json::Error),
}
