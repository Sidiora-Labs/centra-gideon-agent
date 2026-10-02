pub mod activation;
pub mod ownership;
pub mod release_index;
pub mod service;

pub use activation::{RegistryError, RegistryLifecycle};
pub use ownership::{OwnedPath, OwnershipError, OwnershipLedger, UninstallResult};
pub use release_index::{
    ReleaseEntry, ReleaseIndex, ReleaseIndexError, SignedReleaseIndex, VerifiedReleaseIndex,
};
pub use service::{
    ManagerError, ServiceAction, ServiceDefinition, ServiceManager, ServicePlatform,
};
