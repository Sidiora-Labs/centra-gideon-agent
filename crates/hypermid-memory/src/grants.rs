use std::collections::BTreeSet;

use hypermid_core::capability::{AuthContext, CapabilityOperation, PrincipalKind};
use hypermid_store::authorization::{
    authorize as authorize_capability, commit_authorized, AuthorizationStoreError,
};
use rusqlite::{params, OptionalExtension};
use serde::{Deserialize, Serialize};

use crate::model::{scope_digest, GrantOperation, ShareGrant};
use crate::{error, EffectState, Id, MemoryApi, MemoryResult, Scope};

pub const SHARE_GRANTS_RESOURCE: &str = "memory-share-grants";

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ShareGrantDraft {
    pub id: Id,
    pub owner_scope: Scope,
    pub grantee_scope: Scope,
    pub operations: BTreeSet<GrantOperation>,
    pub categories: Option<BTreeSet<String>>,
    pub expires_at_ms: i64,
    pub expected_revision: u64,
}

impl MemoryApi {
    pub fn create_share_grant(
        &mut self,
        context: &AuthContext,
        draft: &ShareGrantDraft,
    ) -> MemoryResult<ShareGrant> {
        validate_owner_context(
            context,
            &draft.owner_scope,
            &draft.id,
            CapabilityOperation::Administer,
        )?;
        validate_draft(draft, context.request.now_ms)?;
        let granted_at_ms = timestamp(context.request.now_ms)?;
        let revision = draft
            .expected_revision
            .checked_add(1)
            .ok_or_else(|| invalid("memory share grant revision cannot advance"))?;
        let grant = ShareGrant {
            id: draft.id.clone(),
            owner_scope: draft.owner_scope.clone(),
            grantee_scope: draft.grantee_scope.clone(),
            operations: draft.operations.clone(),
            categories: draft.categories.clone(),
            granted_at_ms,
            expires_at_ms: Some(draft.expires_at_ms),
            revoked_at_ms: None,
            revision,
        };
        let operations = serde_json::to_string(&grant.operations)
            .map_err(|_| corrupt("memory share grant operations could not be encoded"))?;
        let categories = grant
            .categories
            .as_ref()
            .map(serde_json::to_string)
            .transpose()
            .map_err(|_| corrupt("memory share grant categories could not be encoded"))?;

        self.store.immediate(|transaction| {
            let ticket = authorize_capability(transaction.raw(), context)
                .map_err(map_authorization_error)?;
            transaction.ensure_scope(&grant.owner_scope, granted_at_ms)?;
            transaction.ensure_scope(&grant.grantee_scope, granted_at_ms)?;
            let current: Option<(u64, String, String)> = transaction
                .raw()
                .query_row(
                    "SELECT revision, owner_scope_digest, grantee_scope_digest \
                     FROM memory_share_grants WHERE grant_id = ?1",
                    [grant.id.as_str()],
                    |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
                )
                .optional()
                .map_err(read_error)?;
            let owner_digest = scope_digest(&grant.owner_scope).to_hex();
            let grantee_digest = scope_digest(&grant.grantee_scope).to_hex();
            if current.as_ref().is_some_and(|(_, owner, grantee)| {
                owner != &owner_digest || grantee != &grantee_digest
            }) || current.map(|(revision, _, _)| revision).unwrap_or(0)
                != draft.expected_revision
            {
                return Err(revision_conflict());
            }
            commit_authorized(transaction.raw(), &ticket, context, |raw| {
                raw.execute(
                    "INSERT INTO memory_share_grants(\
                         grant_id, owner_scope_digest, grantee_scope_digest, operations_json,\
                         categories_json, granted_at_ms, expires_at_ms, revoked_at_ms, revision\
                     ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, NULL, ?8) \
                     ON CONFLICT(grant_id) DO UPDATE SET \
                         owner_scope_digest=excluded.owner_scope_digest,\
                         grantee_scope_digest=excluded.grantee_scope_digest,\
                         operations_json=excluded.operations_json,\
                         categories_json=excluded.categories_json,\
                         granted_at_ms=excluded.granted_at_ms,\
                         expires_at_ms=excluded.expires_at_ms,\
                         revoked_at_ms=NULL, revision=excluded.revision",
                    params![
                        grant.id.as_str(),
                        scope_digest(&grant.owner_scope).to_hex(),
                        scope_digest(&grant.grantee_scope).to_hex(),
                        operations,
                        categories,
                        grant.granted_at_ms,
                        grant.expires_at_ms,
                        grant.revision,
                    ],
                )?;
                Ok(())
            })
            .map_err(map_authorization_error)?;
            Ok(grant.clone())
        })
    }

    pub fn revoke_share_grant(
        &mut self,
        context: &AuthContext,
        owner_scope: &Scope,
        grant_id: &Id,
        expected_revision: u64,
    ) -> MemoryResult<ShareGrant> {
        validate_owner_context(
            context,
            owner_scope,
            grant_id,
            CapabilityOperation::Administer,
        )?;
        let revoked_at_ms = timestamp(context.request.now_ms)?;
        self.store.immediate(|transaction| {
            let ticket = authorize_capability(transaction.raw(), context)
                .map_err(map_authorization_error)?;
            let current = load_grant(transaction.raw(), owner_scope, grant_id)?
                .ok_or_else(revision_conflict)?;
            if current.revision != expected_revision || current.revoked_at_ms.is_some() {
                return Err(revision_conflict());
            }
            commit_authorized(transaction.raw(), &ticket, context, |raw| {
                let changed = raw.execute(
                    "UPDATE memory_share_grants \
                     SET revoked_at_ms = ?1, revision = revision + 1 \
                     WHERE grant_id = ?2 AND owner_scope_digest = ?3 \
                       AND revision = ?4 AND revoked_at_ms IS NULL",
                    params![
                        revoked_at_ms,
                        grant_id.as_str(),
                        scope_digest(owner_scope).to_hex(),
                        expected_revision,
                    ],
                )?;
                if changed != 1 {
                    return Err(hypermid_core::capability::AuthorizationDenial.into());
                }
                Ok(())
            })
            .map_err(map_authorization_error)?;
            let revision = expected_revision
                .checked_add(1)
                .ok_or_else(revision_conflict)?;
            Ok(ShareGrant {
                revoked_at_ms: Some(revoked_at_ms),
                revision,
                ..current
            })
        })
    }

    pub fn list_share_grants(
        &mut self,
        context: &AuthContext,
        owner_scope: &Scope,
    ) -> MemoryResult<Vec<ShareGrant>> {
        let resource =
            Id::new(SHARE_GRANTS_RESOURCE).expect("static memory share grant resource is valid");
        validate_owner_context(
            context,
            owner_scope,
            &resource,
            CapabilityOperation::Administer,
        )?;
        self.store.immediate(|transaction| {
            authorize_capability(transaction.raw(), context).map_err(map_authorization_error)?;
            let owner_digest = scope_digest(owner_scope).to_hex();
            let mut statement = transaction
                .raw()
                .prepare(
                    "SELECT g.grant_id, owner.scope_json, grantee.scope_json,\
                            g.operations_json, g.categories_json, g.granted_at_ms,\
                            g.expires_at_ms, g.revoked_at_ms, g.revision \
                     FROM memory_share_grants g \
                     JOIN memory_scopes owner ON owner.scope_digest = g.owner_scope_digest \
                     JOIN memory_scopes grantee ON grantee.scope_digest = g.grantee_scope_digest \
                     WHERE g.owner_scope_digest = ?1 ORDER BY g.grant_id",
                )
                .map_err(read_error)?;
            let rows = statement
                .query_map([owner_digest], |row| {
                    Ok((
                        row.get::<_, String>(0)?,
                        row.get::<_, String>(1)?,
                        row.get::<_, String>(2)?,
                        row.get::<_, String>(3)?,
                        row.get::<_, Option<String>>(4)?,
                        row.get::<_, i64>(5)?,
                        row.get::<_, Option<i64>>(6)?,
                        row.get::<_, Option<i64>>(7)?,
                        row.get::<_, u64>(8)?,
                    ))
                })
                .map_err(read_error)?;
            rows.map(|row| decode_grant(row.map_err(read_error)?))
                .collect()
        })
    }
}

fn validate_owner_context(
    context: &AuthContext,
    owner_scope: &Scope,
    resource_id: &Id,
    operation: CapabilityOperation,
) -> MemoryResult<()> {
    if context.principal.kind != PrincipalKind::Foreground
        || context.principal.owner_id != owner_scope.owner_id
        || context.request.claimed_scope != *owner_scope
        || context.request.target_scope != *owner_scope
        || context.request.operation != operation
        || context.request.resource_id != *resource_id
    {
        return Err(denied());
    }
    Ok(())
}

fn validate_draft(draft: &ShareGrantDraft, now_ms: u64) -> MemoryResult<()> {
    if draft.owner_scope.owner_id == draft.grantee_scope.owner_id
        || draft.operations.is_empty()
        || draft.expires_at_ms <= timestamp(now_ms)?
        || draft.categories.as_ref().is_some_and(|categories| {
            categories.is_empty() || categories.iter().any(String::is_empty)
        })
    {
        return Err(invalid("memory share grant violates the grant contract"));
    }
    let shared_read = draft
        .operations
        .iter()
        .any(|operation| matches!(operation, GrantOperation::Read | GrantOperation::Search));
    if shared_read
        && (draft.owner_scope.workspace_id.is_none()
            || draft.owner_scope.workspace_id != draft.grantee_scope.workspace_id)
    {
        return Err(denied());
    }
    Ok(())
}

fn load_grant(
    connection: &rusqlite::Connection,
    owner_scope: &Scope,
    grant_id: &Id,
) -> MemoryResult<Option<ShareGrant>> {
    let row = connection
        .query_row(
            "SELECT g.grant_id, owner.scope_json, grantee.scope_json,\
                    g.operations_json, g.categories_json, g.granted_at_ms,\
                    g.expires_at_ms, g.revoked_at_ms, g.revision \
             FROM memory_share_grants g \
             JOIN memory_scopes owner ON owner.scope_digest = g.owner_scope_digest \
             JOIN memory_scopes grantee ON grantee.scope_digest = g.grantee_scope_digest \
             WHERE g.grant_id = ?1 AND g.owner_scope_digest = ?2",
            params![grant_id.as_str(), scope_digest(owner_scope).to_hex()],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, Option<String>>(4)?,
                    row.get::<_, i64>(5)?,
                    row.get::<_, Option<i64>>(6)?,
                    row.get::<_, Option<i64>>(7)?,
                    row.get::<_, u64>(8)?,
                ))
            },
        )
        .optional()
        .map_err(read_error)?;
    row.map(decode_grant).transpose()
}

type GrantRow = (
    String,
    String,
    String,
    String,
    Option<String>,
    i64,
    Option<i64>,
    Option<i64>,
    u64,
);

fn decode_grant(row: GrantRow) -> MemoryResult<ShareGrant> {
    Ok(ShareGrant {
        id: Id::new(row.0).map_err(|_| corrupt("stored memory share grant id is invalid"))?,
        owner_scope: serde_json::from_str(&row.1)
            .map_err(|_| corrupt("stored memory share grant owner scope is invalid"))?,
        grantee_scope: serde_json::from_str(&row.2)
            .map_err(|_| corrupt("stored memory share grant recipient scope is invalid"))?,
        operations: serde_json::from_str(&row.3)
            .map_err(|_| corrupt("stored memory share grant operations are invalid"))?,
        categories: row
            .4
            .map(|value| serde_json::from_str(&value))
            .transpose()
            .map_err(|_| corrupt("stored memory share grant categories are invalid"))?,
        granted_at_ms: row.5,
        expires_at_ms: row.6,
        revoked_at_ms: row.7,
        revision: row.8,
    })
}

fn timestamp(value: u64) -> MemoryResult<i64> {
    i64::try_from(value).map_err(|_| invalid("memory share grant timestamp is out of range"))
}

fn map_authorization_error(source: AuthorizationStoreError) -> crate::Error {
    match source {
        AuthorizationStoreError::Denied(_) => denied(),
        AuthorizationStoreError::Storage(_) => error(
            "STORE_WRITE_FAILED",
            "memory share grant authorization storage failed",
            EffectState::Unknown,
        ),
    }
}

fn read_error(_: rusqlite::Error) -> crate::Error {
    error(
        "STORE_READ_FAILED",
        "memory share grant storage could not be read",
        EffectState::Unknown,
    )
}

fn denied() -> crate::Error {
    error(
        "AUTHORIZATION_DENIED",
        "memory share grant authority was refused",
        EffectState::NotStarted,
    )
}

fn invalid(message: &'static str) -> crate::Error {
    error("INVALID_ARGUMENT", message, EffectState::NotStarted)
}

fn revision_conflict() -> crate::Error {
    error(
        "REVISION_CONFLICT",
        "memory share grant revision precondition failed",
        EffectState::NotStarted,
    )
}

fn corrupt(message: &'static str) -> crate::Error {
    error("STORE_CORRUPT", message, EffectState::Unknown)
}

#[cfg(test)]
mod tests {
    use super::*;
    use hypermid_core::capability::{
        AuthenticatedPrincipal, AuthorizationRequest, CapabilityGrant,
    };
    use hypermid_store::authorization::put_grant;

    fn id(value: &str) -> Id {
        Id::new(value).unwrap()
    }

    fn scope(owner: &str, project: &str, workspace: Option<&str>) -> Scope {
        Scope::new(id(owner), id(project), workspace.map(id))
    }

    fn context(
        principal: &AuthenticatedPrincipal,
        capability_id: &str,
        owner_scope: &Scope,
        operation: CapabilityOperation,
        resource: &str,
        now_ms: u64,
    ) -> AuthContext {
        AuthContext {
            principal: principal.clone(),
            request: AuthorizationRequest {
                claimed_scope: owner_scope.clone(),
                target_scope: owner_scope.clone(),
                operation,
                resource_id: id(resource),
                now_ms,
            },
            capability_id: id(capability_id),
        }
    }

    fn install(
        api: &mut MemoryApi,
        issuer: &AuthenticatedPrincipal,
        principal_id: &Id,
        capability_id: &str,
        scope: &Scope,
        operation: CapabilityOperation,
        resource: &str,
    ) {
        let grant = CapabilityGrant {
            capability_id: id(capability_id),
            issuer_owner_id: scope.owner_id.clone(),
            principal_id: principal_id.clone(),
            claimed_scope: scope.clone(),
            target_scope: scope.clone(),
            operations: BTreeSet::from([operation]),
            resources: BTreeSet::from([id(resource)]),
            expires_at_ms: 10_000,
        };
        api.store
            .immediate(|transaction| {
                put_grant(transaction.raw(), issuer, &grant)
                    .map(|_| ())
                    .map_err(map_authorization_error)
            })
            .unwrap();
    }

    #[test]
    fn owner_share_grants_are_exact_revisioned_listed_and_revoked() {
        let directory = tempfile::tempdir().unwrap();
        let mut api = MemoryApi::open(directory.path().join("memory.sqlite3")).unwrap();
        let owner_scope = scope("bob", "project-b", Some("workspace-shared"));
        let grantee_scope = scope("alice", "project-a", Some("workspace-shared"));
        let owner = AuthenticatedPrincipal {
            principal_id: id("bob-credential"),
            owner_id: id("bob"),
            kind: PrincipalKind::Foreground,
        };
        install(
            &mut api,
            &owner,
            &owner.principal_id,
            "cap-put",
            &owner_scope,
            CapabilityOperation::Administer,
            "share-1",
        );
        install(
            &mut api,
            &owner,
            &owner.principal_id,
            "cap-list",
            &owner_scope,
            CapabilityOperation::Administer,
            SHARE_GRANTS_RESOURCE,
        );

        let draft = ShareGrantDraft {
            id: id("share-1"),
            owner_scope: owner_scope.clone(),
            grantee_scope: grantee_scope.clone(),
            operations: BTreeSet::from([GrantOperation::Read]),
            categories: Some(BTreeSet::from(["facts".into()])),
            expires_at_ms: 9_000,
            expected_revision: 0,
        };
        let created = api
            .create_share_grant(
                &context(
                    &owner,
                    "cap-put",
                    &owner_scope,
                    CapabilityOperation::Administer,
                    "share-1",
                    1_000,
                ),
                &draft,
            )
            .unwrap();
        assert_eq!(created.revision, 1);
        assert_eq!(
            api.list_share_grants(
                &context(
                    &owner,
                    "cap-list",
                    &owner_scope,
                    CapabilityOperation::Administer,
                    SHARE_GRANTS_RESOURCE,
                    1_100,
                ),
                &owner_scope,
            )
            .unwrap(),
            vec![created.clone()]
        );

        let wrong_resource = context(
            &owner,
            "cap-put",
            &owner_scope,
            CapabilityOperation::Administer,
            "share-guessed",
            1_200,
        );
        assert_eq!(
            api.create_share_grant(&wrong_resource, &draft)
                .unwrap_err()
                .code,
            "AUTHORIZATION_DENIED"
        );
        let recipient_drift = ShareGrantDraft {
            grantee_scope: scope("carol", "project-c", Some("workspace-shared")),
            expected_revision: 1,
            ..draft.clone()
        };
        assert_eq!(
            api.create_share_grant(
                &context(
                    &owner,
                    "cap-put",
                    &owner_scope,
                    CapabilityOperation::Administer,
                    "share-1",
                    1_250,
                ),
                &recipient_drift,
            )
            .unwrap_err()
            .code,
            "REVISION_CONFLICT"
        );
        let self_grant = ShareGrantDraft {
            id: id("share-1"),
            grantee_scope: owner_scope.clone(),
            ..draft.clone()
        };
        assert_eq!(
            api.create_share_grant(
                &context(
                    &owner,
                    "cap-put",
                    &owner_scope,
                    CapabilityOperation::Administer,
                    "share-1",
                    1_300,
                ),
                &self_grant,
            )
            .unwrap_err()
            .code,
            "INVALID_ARGUMENT"
        );

        let revoked = api
            .revoke_share_grant(
                &context(
                    &owner,
                    "cap-put",
                    &owner_scope,
                    CapabilityOperation::Administer,
                    "share-1",
                    1_400,
                ),
                &owner_scope,
                &id("share-1"),
                1,
            )
            .unwrap();
        assert_eq!(revoked.revision, 2);
        assert_eq!(revoked.revoked_at_ms, Some(1_400));
        assert_eq!(
            api.revoke_share_grant(
                &context(
                    &owner,
                    "cap-put",
                    &owner_scope,
                    CapabilityOperation::Administer,
                    "share-1",
                    1_500,
                ),
                &owner_scope,
                &id("share-1"),
                1,
            )
            .unwrap_err()
            .code,
            "REVISION_CONFLICT"
        );
    }
}
