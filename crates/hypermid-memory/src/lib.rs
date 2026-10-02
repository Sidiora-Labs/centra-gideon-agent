pub mod auth;
pub mod migrations;
pub mod model;
pub mod store;
pub use auth::{authorize, authorize_access, capability_operation, reauthorize};
pub use hypermid_contracts::{Cursor, Digest, EffectState, Error, Id, Scope, Trace};
pub use hypermid_core::capability::{AuthContext, AuthenticatedPrincipal, AuthorizationRequest, CapabilityGrant, CapabilityOperation, PrincipalKind};
pub use migrations::{memory_migrations, MEMORY_SCHEMA_VERSION};
pub use model::{scope_digest, AccessRequest, Authorization, AuthorizationBasis, GrantOperation, GrantReference, MutationRequest, Operation, RevisionPrecondition, ShareGrant};
pub use store::{MemorySchemaEvidence, MemoryStore, MemoryTransaction};
pub type MemoryResult<T> = Result<T, Error>;
pub(crate) fn error(code: &'static str, message: impl Into<String>, effect_state: EffectState) -> Error {
    Error::new(code, message, false, None, Some(effect_state)).expect("memory error constants satisfy the shared Error contract")
}
