use hypermid_contracts::{Id, Scope};
use hypermid_core::capability::{
    authorize_exact, AuthContext, AuthenticatedPrincipal, AuthorizationDenial, CapabilityGrant,
    CapabilityOperation, PrincipalKind,
};
use rusqlite::{params, Connection, OptionalExtension, Transaction};
use std::collections::BTreeSet;

pub const AUTHORIZATION_SCHEMA_SQL: &str = r#"
CREATE TABLE IF NOT EXISTS hypermid_capabilities (
    capability_id TEXT PRIMARY KEY,
    issuer_owner_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    claimed_owner_id TEXT NOT NULL,
    claimed_project_id TEXT NOT NULL,
    claimed_workspace_id TEXT,
    target_owner_id TEXT NOT NULL,
    target_project_id TEXT NOT NULL,
    target_workspace_id TEXT,
    expires_at_ms INTEGER NOT NULL CHECK (expires_at_ms >= 0),
    revision INTEGER NOT NULL CHECK (revision > 0),
    revoked INTEGER NOT NULL DEFAULT 0 CHECK (revoked IN (0, 1))
);
CREATE TABLE IF NOT EXISTS hypermid_capability_operations (
    capability_id TEXT NOT NULL REFERENCES hypermid_capabilities(capability_id) ON DELETE CASCADE,
    operation TEXT NOT NULL,
    PRIMARY KEY (capability_id, operation)
);
CREATE TABLE IF NOT EXISTS hypermid_capability_resources (
    capability_id TEXT NOT NULL REFERENCES hypermid_capabilities(capability_id) ON DELETE CASCADE,
    resource_id TEXT NOT NULL,
    PRIMARY KEY (capability_id, resource_id)
);
"#;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CommitAuthorization {
    pub revision: u64,
    pub context: AuthContext,
}

#[derive(Debug, thiserror::Error)]
pub enum AuthorizationStoreError {
    #[error("{0}")]
    Denied(#[from] AuthorizationDenial),
    #[error("authorization storage failed")]
    Storage(#[source] rusqlite::Error),
}

impl From<rusqlite::Error> for AuthorizationStoreError {
    fn from(error: rusqlite::Error) -> Self {
        Self::Storage(error)
    }
}

pub fn install_schema(transaction: &Transaction<'_>) -> Result<(), AuthorizationStoreError> {
    transaction.execute_batch(AUTHORIZATION_SCHEMA_SQL)?;
    Ok(())
}

pub fn put_grant(
    transaction: &Transaction<'_>,
    issuer: &AuthenticatedPrincipal,
    grant: &CapabilityGrant,
) -> Result<u64, AuthorizationStoreError> {
    if issuer.kind != PrincipalKind::Foreground
        || issuer.owner_id != grant.issuer_owner_id
        || issuer.owner_id != grant.target_scope.owner_id
        || grant.operations.is_empty()
        || grant.resources.is_empty()
    {
        return Err(AuthorizationDenial.into());
    }
    let prior: Option<(u64, String)> = transaction
        .query_row(
            "SELECT revision, issuer_owner_id FROM hypermid_capabilities WHERE capability_id = ?1",
            params![grant.capability_id.as_str()],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .optional()?;
    if prior
        .as_ref()
        .is_some_and(|(_, owner)| owner != issuer.owner_id.as_str())
    {
        return Err(AuthorizationDenial.into());
    }
    let revision = prior
        .map(|(revision, _)| revision)
        .unwrap_or(0)
        .checked_add(1)
        .ok_or(AuthorizationDenial)?;
    let expiry = i64::try_from(grant.expires_at_ms).map_err(|_| AuthorizationDenial)?;
    transaction.execute(
        "INSERT INTO hypermid_capabilities(
             capability_id, issuer_owner_id, principal_id,
             claimed_owner_id, claimed_project_id, claimed_workspace_id,
             target_owner_id, target_project_id, target_workspace_id,
             expires_at_ms, revision, revoked
         ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, 0)
         ON CONFLICT(capability_id) DO UPDATE SET
             issuer_owner_id=excluded.issuer_owner_id,
             principal_id=excluded.principal_id,
             claimed_owner_id=excluded.claimed_owner_id,
             claimed_project_id=excluded.claimed_project_id,
             claimed_workspace_id=excluded.claimed_workspace_id,
             target_owner_id=excluded.target_owner_id,
             target_project_id=excluded.target_project_id,
             target_workspace_id=excluded.target_workspace_id,
             expires_at_ms=excluded.expires_at_ms,
             revision=excluded.revision,
             revoked=0",
        params![
            grant.capability_id.as_str(),
            grant.issuer_owner_id.as_str(),
            grant.principal_id.as_str(),
            grant.claimed_scope.owner_id.as_str(),
            grant.claimed_scope.project_id.as_str(),
            grant.claimed_scope.workspace_id.as_ref().map(Id::as_str),
            grant.target_scope.owner_id.as_str(),
            grant.target_scope.project_id.as_str(),
            grant.target_scope.workspace_id.as_ref().map(Id::as_str),
            expiry,
            revision,
        ],
    )?;
    transaction.execute(
        "DELETE FROM hypermid_capability_operations WHERE capability_id = ?1",
        params![grant.capability_id.as_str()],
    )?;
    transaction.execute(
        "DELETE FROM hypermid_capability_resources WHERE capability_id = ?1",
        params![grant.capability_id.as_str()],
    )?;
    for operation in &grant.operations {
        transaction.execute(
            "INSERT INTO hypermid_capability_operations(capability_id, operation) VALUES (?1, ?2)",
            params![grant.capability_id.as_str(), operation_name(*operation)],
        )?;
    }
    for resource in &grant.resources {
        transaction.execute(
            "INSERT INTO hypermid_capability_resources(capability_id, resource_id) VALUES (?1, ?2)",
            params![grant.capability_id.as_str(), resource.as_str()],
        )?;
    }
    Ok(revision)
}

pub fn revoke(
    transaction: &Transaction<'_>,
    issuer: &AuthenticatedPrincipal,
    capability_id: &Id,
) -> Result<bool, AuthorizationStoreError> {
    if issuer.kind != PrincipalKind::Foreground {
        return Err(AuthorizationDenial.into());
    }
    let issuer_owner: Option<String> = transaction
        .query_row(
            "SELECT issuer_owner_id FROM hypermid_capabilities WHERE capability_id = ?1",
            params![capability_id.as_str()],
            |row| row.get(0),
        )
        .optional()?;
    if issuer_owner.as_deref() != Some(issuer.owner_id.as_str()) {
        return Err(AuthorizationDenial.into());
    }
    let changed = transaction.execute(
        "UPDATE hypermid_capabilities
         SET revoked = 1, revision = revision + 1
         WHERE capability_id = ?1 AND revoked = 0",
        params![capability_id.as_str()],
    )?;
    Ok(changed == 1)
}

pub fn authorize(
    connection: &Connection,
    context: &AuthContext,
) -> Result<CommitAuthorization, AuthorizationStoreError> {
    let (grant, revision) = load_live(connection, &context.capability_id)?;
    authorize_exact(&context.principal, &context.request, &grant)?;
    Ok(CommitAuthorization {
        revision,
        context: context.clone(),
    })
}

pub fn commit_authorized<T>(
    transaction: &Transaction<'_>,
    authorization: &CommitAuthorization,
    current_context: &AuthContext,
    mutation: impl FnOnce(&Transaction<'_>) -> Result<T, AuthorizationStoreError>,
) -> Result<T, AuthorizationStoreError> {
    if !current_context.request.operation.mutates()
        || authorization.context.capability_id != current_context.capability_id
        || authorization.context.principal != current_context.principal
        || authorization.context.request.claimed_scope != current_context.request.claimed_scope
        || authorization.context.request.target_scope != current_context.request.target_scope
        || authorization.context.request.operation != current_context.request.operation
        || authorization.context.request.resource_id != current_context.request.resource_id
        || current_context.request.now_ms < authorization.context.request.now_ms
    {
        return Err(AuthorizationDenial.into());
    }
    let (grant, revision) = load_live(transaction, &current_context.capability_id)?;
    if revision != authorization.revision {
        return Err(AuthorizationDenial.into());
    }
    authorize_exact(&current_context.principal, &current_context.request, &grant)?;
    mutation(transaction)
}

fn load_live(
    connection: &Connection,
    capability_id: &Id,
) -> Result<(CapabilityGrant, u64), AuthorizationStoreError> {
    let row: Option<(
        String,
        String,
        String,
        String,
        Option<String>,
        String,
        String,
        Option<String>,
        i64,
        u64,
        bool,
    )> = connection
        .query_row(
            "SELECT issuer_owner_id, principal_id,
                    claimed_owner_id, claimed_project_id, claimed_workspace_id,
                    target_owner_id, target_project_id, target_workspace_id,
                    expires_at_ms, revision, revoked
             FROM hypermid_capabilities WHERE capability_id = ?1",
            params![capability_id.as_str()],
            |row| {
                Ok((
                    row.get(0)?,
                    row.get(1)?,
                    row.get(2)?,
                    row.get(3)?,
                    row.get(4)?,
                    row.get(5)?,
                    row.get(6)?,
                    row.get(7)?,
                    row.get(8)?,
                    row.get(9)?,
                    row.get(10)?,
                ))
            },
        )
        .optional()?;
    let Some((
        issuer_owner_id,
        principal_id,
        claimed_owner,
        claimed_project,
        claimed_workspace,
        target_owner,
        target_project,
        target_workspace,
        expires_at_ms,
        revision,
        revoked,
    )) = row
    else {
        return Err(AuthorizationDenial.into());
    };
    if revoked || expires_at_ms < 0 {
        return Err(AuthorizationDenial.into());
    }

    let operations = load_operations(connection, capability_id)?;
    let resources = load_resources(connection, capability_id)?;
    let grant = CapabilityGrant {
        capability_id: capability_id.clone(),
        issuer_owner_id: parse_id(issuer_owner_id)?,
        principal_id: parse_id(principal_id)?,
        claimed_scope: parse_scope(claimed_owner, claimed_project, claimed_workspace)?,
        target_scope: parse_scope(target_owner, target_project, target_workspace)?,
        operations,
        resources,
        expires_at_ms: u64::try_from(expires_at_ms).map_err(|_| AuthorizationDenial)?,
    };
    Ok((grant, revision))
}

fn load_operations(
    connection: &Connection,
    capability_id: &Id,
) -> Result<BTreeSet<CapabilityOperation>, AuthorizationStoreError> {
    let mut statement = connection.prepare(
        "SELECT operation FROM hypermid_capability_operations
         WHERE capability_id = ?1 ORDER BY operation",
    )?;
    let names = statement
        .query_map(params![capability_id.as_str()], |row| {
            row.get::<_, String>(0)
        })?
        .collect::<Result<Vec<_>, _>>()?;
    names
        .into_iter()
        .map(|name| parse_operation(&name).ok_or_else(|| AuthorizationDenial.into()))
        .collect()
}

fn load_resources(
    connection: &Connection,
    capability_id: &Id,
) -> Result<BTreeSet<Id>, AuthorizationStoreError> {
    let mut statement = connection.prepare(
        "SELECT resource_id FROM hypermid_capability_resources
         WHERE capability_id = ?1 ORDER BY resource_id",
    )?;
    let values = statement
        .query_map(params![capability_id.as_str()], |row| {
            row.get::<_, String>(0)
        })?
        .collect::<Result<Vec<_>, _>>()?;
    values.into_iter().map(parse_id).collect()
}

fn parse_scope(
    owner_id: String,
    project_id: String,
    workspace_id: Option<String>,
) -> Result<Scope, AuthorizationStoreError> {
    Ok(Scope::new(
        parse_id(owner_id)?,
        parse_id(project_id)?,
        workspace_id.map(parse_id).transpose()?,
    ))
}

fn parse_id(value: String) -> Result<Id, AuthorizationStoreError> {
    Id::new(value).map_err(|_| AuthorizationDenial.into())
}

fn operation_name(operation: CapabilityOperation) -> &'static str {
    match operation {
        CapabilityOperation::Read => "read",
        CapabilityOperation::Append => "append",
        CapabilityOperation::Revise => "revise",
        CapabilityOperation::Archive => "archive",
        CapabilityOperation::Delete => "delete",
        CapabilityOperation::Export => "export",
        CapabilityOperation::Restore => "restore",
        CapabilityOperation::Administer => "administer",
        CapabilityOperation::ModelUse => "model-use",
        CapabilityOperation::NetworkUse => "network-use",
        CapabilityOperation::ArtifactInstall => "artifact-install",
        CapabilityOperation::ArtifactUpdate => "artifact-update",
    }
}

fn parse_operation(value: &str) -> Option<CapabilityOperation> {
    Some(match value {
        "read" => CapabilityOperation::Read,
        "append" => CapabilityOperation::Append,
        "revise" => CapabilityOperation::Revise,
        "archive" => CapabilityOperation::Archive,
        "delete" => CapabilityOperation::Delete,
        "export" => CapabilityOperation::Export,
        "restore" => CapabilityOperation::Restore,
        "administer" => CapabilityOperation::Administer,
        "model-use" => CapabilityOperation::ModelUse,
        "network-use" => CapabilityOperation::NetworkUse,
        "artifact-install" => CapabilityOperation::ArtifactInstall,
        "artifact-update" => CapabilityOperation::ArtifactUpdate,
        _ => return None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use hypermid_core::capability::PrincipalKind;

    fn id(value: &str) -> Id {
        Id::new(value).unwrap()
    }

    fn scope(owner: &str, project: &str, workspace: Option<&str>) -> Scope {
        Scope::new(id(owner), id(project), workspace.map(id))
    }

    fn principal(kind: PrincipalKind) -> AuthenticatedPrincipal {
        AuthenticatedPrincipal {
            principal_id: id("principal-1"),
            owner_id: id("alice"),
            kind,
        }
    }

    fn owner_principal(owner: &str) -> AuthenticatedPrincipal {
        AuthenticatedPrincipal {
            principal_id: id(&format!("issuer-{owner}")),
            owner_id: id(owner),
            kind: PrincipalKind::Foreground,
        }
    }

    fn grant(
        claimed_scope: Scope,
        target_scope: Scope,
        operation: CapabilityOperation,
    ) -> CapabilityGrant {
        CapabilityGrant {
            capability_id: id("cap-1"),
            issuer_owner_id: target_scope.owner_id.clone(),
            principal_id: id("principal-1"),
            claimed_scope,
            target_scope,
            operations: BTreeSet::from([operation]),
            resources: BTreeSet::from([id("record-1")]),
            expires_at_ms: 200,
        }
    }

    fn connection() -> Connection {
        let connection = Connection::open_in_memory().unwrap();
        connection
            .pragma_update(None, "foreign_keys", "ON")
            .unwrap();
        connection
            .execute_batch(
                "CREATE TABLE records(resource_id TEXT PRIMARY KEY, value TEXT NOT NULL);
                 CREATE TABLE cursor(singleton INTEGER PRIMARY KEY, sequence INTEGER NOT NULL);
                 INSERT INTO cursor VALUES (1, 0);",
            )
            .unwrap();
        let transaction = connection.unchecked_transaction().unwrap();
        install_schema(&transaction).unwrap();
        transaction.commit().unwrap();
        connection
    }

    #[test]
    fn authorization_revocation_before_commit_leaves_record_and_cursor_unchanged() {
        let mut connection = connection();
        let own = scope("alice", "project-a", None);
        let capability = grant(own.clone(), own.clone(), CapabilityOperation::Append);
        let transaction = connection.transaction().unwrap();
        put_grant(&transaction, &owner_principal("alice"), &capability).unwrap();
        transaction.commit().unwrap();

        let request = hypermid_core::capability::AuthorizationRequest {
            claimed_scope: own.clone(),
            target_scope: own,
            operation: CapabilityOperation::Append,
            resource_id: id("record-1"),
            now_ms: 100,
        };
        let ticket = authorize(
            &connection,
            &AuthContext {
                principal: principal(PrincipalKind::Background),
                request,
                capability_id: id("cap-1"),
            },
        )
        .unwrap();
        let transaction = connection.transaction().unwrap();
        assert!(revoke(&transaction, &owner_principal("alice"), &id("cap-1")).unwrap());
        transaction.commit().unwrap();

        let transaction = connection.transaction().unwrap();
        assert!(matches!(
            commit_authorized(&transaction, &ticket, &ticket.context, |tx| {
                tx.execute("INSERT INTO records VALUES ('record-1', 'forbidden')", [])?;
                tx.execute("UPDATE cursor SET sequence = sequence + 1", [])?;
                Ok(())
            }),
            Err(AuthorizationStoreError::Denied(_))
        ));
        transaction.rollback().unwrap();

        let count: u64 = connection
            .query_row("SELECT COUNT(*) FROM records", [], |row| row.get(0))
            .unwrap();
        let cursor: u64 = connection
            .query_row("SELECT sequence FROM cursor", [], |row| row.get(0))
            .unwrap();
        assert_eq!((count, cursor), (0, 0));
    }

    #[test]
    fn authorization_foreign_read_requires_exact_shared_scope_and_resource() {
        let mut connection = connection();
        let claimed = scope("alice", "project-a", Some("workspace-1"));
        let target = scope("bob", "project-b", Some("workspace-1"));
        let capability = grant(claimed.clone(), target.clone(), CapabilityOperation::Read);
        let transaction = connection.transaction().unwrap();
        put_grant(&transaction, &owner_principal("bob"), &capability).unwrap();
        transaction.commit().unwrap();

        let exact = hypermid_core::capability::AuthorizationRequest {
            claimed_scope: claimed.clone(),
            target_scope: target.clone(),
            operation: CapabilityOperation::Read,
            resource_id: id("record-1"),
            now_ms: 100,
        };
        assert!(authorize(
            &connection,
            &AuthContext {
                principal: principal(PrincipalKind::Foreground),
                request: exact.clone(),
                capability_id: id("cap-1"),
            }
        )
        .is_ok());

        for denied in [
            hypermid_core::capability::AuthorizationRequest {
                resource_id: id("record-2"),
                ..exact.clone()
            },
            hypermid_core::capability::AuthorizationRequest {
                operation: CapabilityOperation::Revise,
                ..exact.clone()
            },
            hypermid_core::capability::AuthorizationRequest {
                target_scope: scope("bob", "project-b", Some("workspace-2")),
                ..exact
            },
        ] {
            assert!(matches!(
                authorize(
                    &connection,
                    &AuthContext {
                        principal: principal(PrincipalKind::Foreground),
                        request: denied,
                        capability_id: id("cap-1"),
                    }
                ),
                Err(AuthorizationStoreError::Denied(_))
            ));
        }
    }

    #[test]
    fn authorization_background_principal_cannot_mint_capability() {
        let mut connection = connection();
        let own = scope("alice", "project-a", None);
        let capability = grant(own.clone(), own, CapabilityOperation::Append);
        let transaction = connection.transaction().unwrap();
        assert!(matches!(
            put_grant(
                &transaction,
                &principal(PrincipalKind::Background),
                &capability
            ),
            Err(AuthorizationStoreError::Denied(_))
        ));
        transaction.rollback().unwrap();
    }

    #[test]
    fn authorization_expiry_is_rechecked_at_commit_time() {
        let mut connection = connection();
        let own = scope("alice", "project-a", None);
        let capability = grant(own.clone(), own.clone(), CapabilityOperation::Append);
        let transaction = connection.transaction().unwrap();
        put_grant(&transaction, &owner_principal("alice"), &capability).unwrap();
        transaction.commit().unwrap();
        let context = AuthContext {
            principal: principal(PrincipalKind::Background),
            request: hypermid_core::capability::AuthorizationRequest {
                claimed_scope: own.clone(),
                target_scope: own.clone(),
                operation: CapabilityOperation::Append,
                resource_id: id("record-1"),
                now_ms: 100,
            },
            capability_id: id("cap-1"),
        };
        let ticket = authorize(&connection, &context).unwrap();
        let expired = AuthContext {
            request: hypermid_core::capability::AuthorizationRequest {
                now_ms: 200,
                ..context.request.clone()
            },
            ..context
        };
        let transaction = connection.transaction().unwrap();
        assert!(matches!(
            commit_authorized(&transaction, &ticket, &expired, |_| Ok(())),
            Err(AuthorizationStoreError::Denied(_))
        ));
        transaction.rollback().unwrap();
    }
}
