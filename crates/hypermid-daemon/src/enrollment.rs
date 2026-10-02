use std::collections::BTreeSet;
use std::path::Path;

use hypermid_contracts::{Id, Scope};
use hypermid_core::capability::{
    AuthenticatedPrincipal, CapabilityGrant, CapabilityOperation, PrincipalKind,
};
use hypermid_store::authorization::{put_grant, revoke, AuthorizationStoreError};
use rusqlite::{Connection, OpenFlags};
use thiserror::Error;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LocalOperatorEnrollment {
    pub credential_id: Id,
    pub scope: Scope,
    pub capability_id: Id,
    pub operations: BTreeSet<CapabilityOperation>,
    pub resources: BTreeSet<Id>,
    pub expires_at_ms: u64,
}

impl LocalOperatorEnrollment {
    pub fn grant(&self) -> Result<CapabilityGrant, EnrollmentError> {
        if self.operations.is_empty() || self.resources.is_empty() || self.expires_at_ms == 0 {
            return Err(EnrollmentError::Invalid);
        }
        Ok(CapabilityGrant {
            capability_id: self.capability_id.clone(),
            issuer_owner_id: self.scope.owner_id.clone(),
            principal_id: self.credential_id.clone(),
            claimed_scope: self.scope.clone(),
            target_scope: self.scope.clone(),
            operations: self.operations.clone(),
            resources: self.resources.clone(),
            expires_at_ms: self.expires_at_ms,
        })
    }

    pub fn binding(&self) -> LocalCredentialBinding {
        LocalCredentialBinding {
            credential_id: self.credential_id.clone(),
            principal_id: self.credential_id.clone(),
            authorized_scope: self.scope.clone(),
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LocalCredentialBinding {
    pub credential_id: Id,
    pub principal_id: Id,
    pub authorized_scope: Scope,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExplicitOperatorGrant {
    pub operator_owner_id: Id,
    pub grantee_principal_id: Id,
    pub claimed_scope: Scope,
    pub target_scope: Scope,
    pub capability_id: Id,
    pub operations: BTreeSet<CapabilityOperation>,
    pub resources: BTreeSet<Id>,
    pub expires_at_ms: u64,
}

impl ExplicitOperatorGrant {
    fn grant(&self) -> Result<CapabilityGrant, EnrollmentError> {
        if self.operator_owner_id != self.target_scope.owner_id
            || self.operations.is_empty()
            || self.resources.is_empty()
            || self.expires_at_ms == 0
        {
            return Err(EnrollmentError::Invalid);
        }
        Ok(CapabilityGrant {
            capability_id: self.capability_id.clone(),
            issuer_owner_id: self.operator_owner_id.clone(),
            principal_id: self.grantee_principal_id.clone(),
            claimed_scope: self.claimed_scope.clone(),
            target_scope: self.target_scope.clone(),
            operations: self.operations.clone(),
            resources: self.resources.clone(),
            expires_at_ms: self.expires_at_ms,
        })
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EnrollmentReceipt {
    pub credential_id: Id,
    pub principal_id: Id,
    pub capability_id: Id,
    pub scope: Scope,
    pub revision: u64,
    pub expires_at_ms: u64,
}

pub fn principal_for_local_credential(credential_id: &Id, owner_id: &Id) -> AuthenticatedPrincipal {
    AuthenticatedPrincipal {
        principal_id: credential_id.clone(),
        owner_id: owner_id.clone(),
        kind: PrincipalKind::Foreground,
    }
}

pub fn install_local_operator_grant(
    database_path: &Path,
    enrollment: &LocalOperatorEnrollment,
) -> Result<EnrollmentReceipt, EnrollmentError> {
    ensure_owner_store(database_path)?;
    let _lease = acquire_store_lease(database_path)?;
    let grant = enrollment.grant()?;
    let mut connection = Connection::open_with_flags(
        database_path,
        OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )?;
    connection.pragma_update(None, "foreign_keys", "ON")?;
    let transaction = connection.transaction()?;
    let issuer = AuthenticatedPrincipal {
        principal_id: Id::new("local-operator-enrollment")
            .expect("static enrollment principal is valid"),
        owner_id: enrollment.scope.owner_id.clone(),
        kind: PrincipalKind::Foreground,
    };
    let revision = put_grant(&transaction, &issuer, &grant)?;
    transaction.commit()?;
    Ok(EnrollmentReceipt {
        credential_id: enrollment.credential_id.clone(),
        principal_id: enrollment.credential_id.clone(),
        capability_id: enrollment.capability_id.clone(),
        scope: enrollment.scope.clone(),
        revision,
        expires_at_ms: enrollment.expires_at_ms,
    })
}

pub fn install_explicit_operator_grant(
    database_path: &Path,
    enrollment: &ExplicitOperatorGrant,
) -> Result<EnrollmentReceipt, EnrollmentError> {
    ensure_owner_store(database_path)?;
    let _lease = acquire_store_lease(database_path)?;
    let grant = enrollment.grant()?;
    let mut connection = open_store(database_path)?;
    let transaction = connection.transaction()?;
    let issuer = operator_principal(&enrollment.operator_owner_id);
    let revision = put_grant(&transaction, &issuer, &grant)?;
    transaction.commit()?;
    Ok(EnrollmentReceipt {
        credential_id: enrollment.grantee_principal_id.clone(),
        principal_id: enrollment.grantee_principal_id.clone(),
        capability_id: enrollment.capability_id.clone(),
        scope: enrollment.claimed_scope.clone(),
        revision,
        expires_at_ms: enrollment.expires_at_ms,
    })
}

pub fn revoke_operator_grant(
    database_path: &Path,
    operator_owner_id: &Id,
    capability_id: &Id,
) -> Result<bool, EnrollmentError> {
    ensure_owner_store(database_path)?;
    let _lease = acquire_store_lease(database_path)?;
    let mut connection = open_store(database_path)?;
    let transaction = connection.transaction()?;
    let revoked = revoke(
        &transaction,
        &operator_principal(operator_owner_id),
        capability_id,
    )?;
    transaction.commit()?;
    Ok(revoked)
}

fn open_store(database_path: &Path) -> Result<Connection, rusqlite::Error> {
    let connection = Connection::open_with_flags(
        database_path,
        OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )?;
    connection.pragma_update(None, "foreign_keys", "ON")?;
    Ok(connection)
}

fn operator_principal(owner_id: &Id) -> AuthenticatedPrincipal {
    AuthenticatedPrincipal {
        principal_id: Id::new("local-operator-enrollment")
            .expect("static enrollment principal is valid"),
        owner_id: owner_id.clone(),
        kind: PrincipalKind::Foreground,
    }
}

fn acquire_store_lease(
    database_path: &Path,
) -> Result<hypermid_memory::MemoryStore, EnrollmentError> {
    hypermid_memory::MemoryStore::open(database_path).map_err(|_| EnrollmentError::StoreUnavailable)
}

#[derive(Debug, Error)]
pub enum EnrollmentError {
    #[error("local operator enrollment is invalid")]
    Invalid,
    #[error("local operator enrollment requires an owner-only durable store")]
    StoreOwnership,
    #[error("local operator enrollment requires exclusive access to the durable store")]
    StoreUnavailable,
    #[error("local operator enrollment storage failed")]
    Storage(#[from] rusqlite::Error),
    #[error("local operator capability was refused")]
    Authorization(#[from] AuthorizationStoreError),
}

#[cfg(unix)]
fn ensure_owner_store(path: &Path) -> Result<(), EnrollmentError> {
    use std::os::unix::fs::{MetadataExt, PermissionsExt};

    let metadata = std::fs::symlink_metadata(path).map_err(|_| EnrollmentError::StoreOwnership)?;
    let parent_owner = path
        .parent()
        .and_then(|parent| std::fs::symlink_metadata(parent).ok())
        .map(|parent| parent.uid())
        .ok_or(EnrollmentError::StoreOwnership)?;
    if !metadata.file_type().is_file()
        || metadata.permissions().mode() & 0o077 != 0
        || metadata.uid() != parent_owner
    {
        return Err(EnrollmentError::StoreOwnership);
    }
    Ok(())
}

#[cfg(not(unix))]
fn ensure_owner_store(path: &Path) -> Result<(), EnrollmentError> {
    let metadata = std::fs::symlink_metadata(path).map_err(|_| EnrollmentError::StoreOwnership)?;
    if !metadata.file_type().is_file() {
        return Err(EnrollmentError::StoreOwnership);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use hypermid_core::capability::{AuthContext, AuthorizationRequest};
    use hypermid_store::authorization::{authorize, AuthorizationStoreError};
    use tempfile::tempdir;

    fn id(value: &str) -> Id {
        Id::new(value).unwrap()
    }

    #[test]
    fn enrollment_installs_exact_credential_bound_grant_and_refuses_another_principal() {
        let directory = tempdir().unwrap();
        let database = directory.path().join("memory.sqlite3");
        drop(hypermid_memory::MemoryStore::open(&database).unwrap());
        let scope = Scope::new(id("alice"), id("project-a"), None);
        let enrollment = LocalOperatorEnrollment {
            credential_id: id("credential-a"),
            scope: scope.clone(),
            capability_id: id("capability-a"),
            operations: BTreeSet::from([CapabilityOperation::Append, CapabilityOperation::Read]),
            resources: BTreeSet::from([id("record-a")]),
            expires_at_ms: 2_000,
        };
        let receipt = install_local_operator_grant(&database, &enrollment).unwrap();
        assert_eq!(receipt.principal_id, id("credential-a"));
        assert_eq!(receipt.capability_id, id("capability-a"));

        let connection = Connection::open(&database).unwrap();
        let request = AuthorizationRequest {
            claimed_scope: scope.clone(),
            target_scope: scope.clone(),
            operation: CapabilityOperation::Append,
            resource_id: id("record-a"),
            now_ms: 1_000,
        };
        let context = AuthContext {
            principal: principal_for_local_credential(&id("credential-a"), &id("alice")),
            request: request.clone(),
            capability_id: id("capability-a"),
        };
        assert!(authorize(&connection, &context).is_ok());
        let refused = AuthContext {
            principal: principal_for_local_credential(&id("credential-b"), &id("alice")),
            request,
            capability_id: id("capability-a"),
        };
        assert!(matches!(
            authorize(&connection, &refused),
            Err(AuthorizationStoreError::Denied(_))
        ));
    }
}
