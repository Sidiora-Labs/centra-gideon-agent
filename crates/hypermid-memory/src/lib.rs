pub mod api;
pub mod app_scopes;
pub use app_scopes::AppScopeReceipt;
pub mod auth;
pub mod budget;
pub mod bus;
pub mod embedding;
pub mod export;
pub mod ffi;
pub mod file_predicate;
pub mod fts;
pub mod git_index;
pub mod grants;
pub mod import;
pub mod invalidation;
pub mod jobs;
pub mod lease;
pub mod legacy_import;
pub mod lineage;
pub mod maintenance;
pub mod migrations;
pub mod model;
pub mod protocol;
pub mod private_scopes;
pub use private_scopes::PrivateScopeReceipt;
pub mod provenance;
pub mod rank;
pub mod records;
pub mod recovery;
pub mod search;
pub mod sharing;
pub mod smart_note;
pub mod snapshot;
pub mod source_index;
pub mod store;
pub mod summary;

pub use api::{
    EmbeddingView, MaintenanceClaimKey, MemoryApi, MemoryBudgetState, MemoryDiagnostics,
    MemoryHealth, MemoryRecoveryState, RecordList, RecordView, ServiceState,
};
pub use auth::{authorize, authorize_access, capability_operation, reauthorize};
pub use export::{ExportEntryKind, MemoryExportBundle, MemoryExportEntry, MemoryExportManifest};
pub use grants::{ShareGrantDraft, SHARE_GRANTS_RESOURCE};
pub use hypermid_contracts::{Cursor, Digest, EffectState, Error, Id, Scope, Trace};
pub use hypermid_core::capability::{
    AuthContext, AuthenticatedPrincipal, AuthorizationRequest, CapabilityGrant,
    CapabilityOperation, PrincipalKind,
};
pub use import::{ImportRejection, ImportState, MemoryImportBatch, MemoryImportManifest};
pub use legacy_import::{
    plan_legacy_import, GideonLegacySnapshot, LegacyConversationLogEvidence, LegacyImportPlan,
    LegacyImportReceipt, LegacyKnowledgeCategory, LegacySourceItem,
};
pub use lineage::{LineageEdge, LineageRelation};
pub use maintenance::{
    KnowledgePublication, KnowledgePublicationAuthorities, KnowledgePublicationAuthority,
    KnowledgePublicationReceipt, KnowledgeRecall, KnowledgeRecallReceipt, KnowledgeSharingReceipt,
    KnowledgeVerification, KnowledgeVerificationReceipt, MaintenanceClaimReceipt,
    MaintenanceJobSpec, MaintenanceJobStatus, MaintenanceKind, MaintenancePublication,
    MaintenancePublicationReceipt, SmartNoteDecision, SmartNoteEvaluation, TerminalState,
};
pub use migrations::{memory_migrations, MigrationFence, MEMORY_SCHEMA_VERSION};
pub use model::{
    scope_digest, AccessRequest, Authorization, AuthorizationBasis, GrantOperation, GrantReference,
    MutationRequest, Operation, RevisionPrecondition, ShareGrant,
};
pub use provenance::{ProvenanceSpan, SourceKind, SourceSnapshot};
pub use records::{
    MemoryRecord, RecordDraft, RecordKind, RecordMutation, RecordRevision, RecordStatus,
    RelocationMutation, SplitMutation, VerificationState,
};
pub use recovery::{DerivativeRebuildReceipt, MemoryRecoveryReport};
pub use search::{SearchHit, SearchMode, SearchResponse, StoredSearchRequest, SuppressionCounts};
pub use sharing::{
    KnowledgeSharingJudgment, SharingClassification, SharingJudgmentReceipt, SmartNoteCandidate,
    SmartNoteCandidatePage, TrustDecision,
};
pub use store::{MemorySchemaEvidence, MemoryStore, MemoryTransaction};
pub use summary::{SummaryDetails, SummaryLevel};

pub type MemoryResult<T> = Result<T, Error>;

pub(crate) fn error(
    code: &'static str,
    message: impl Into<String>,
    effect_state: EffectState,
) -> Error {
    Error::new(code, message, false, None, Some(effect_state))
        .expect("memory error constants satisfy the shared Error contract")
}
