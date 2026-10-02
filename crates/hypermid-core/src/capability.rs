use hypermid_contracts::{Id, Scope};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
use std::error::Error;
use std::fmt;

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum CapabilityOperation {
    Read,
    Append,
    Revise,
    Archive,
    Delete,
    Export,
    Restore,
    Administer,
    ModelUse,
    NetworkUse,
    ArtifactInstall,
    ArtifactUpdate,
}

impl CapabilityOperation {
    pub fn mutates(self) -> bool {
        self != Self::Read
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PrincipalKind {
    Foreground,
    Background,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct AuthenticatedPrincipal {
    pub principal_id: Id,
    pub owner_id: Id,
    pub kind: PrincipalKind,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct CapabilityGrant {
    pub capability_id: Id,
    pub issuer_owner_id: Id,
    pub principal_id: Id,
    pub claimed_scope: Scope,
    pub target_scope: Scope,
    pub operations: BTreeSet<CapabilityOperation>,
    pub resources: BTreeSet<Id>,
    pub expires_at_ms: u64,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct AuthorizationRequest {
    pub claimed_scope: Scope,
    pub target_scope: Scope,
    pub operation: CapabilityOperation,
    pub resource_id: Id,
    pub now_ms: u64,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct AuthContext {
    pub principal: AuthenticatedPrincipal,
    pub request: AuthorizationRequest,
    pub capability_id: Id,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct AuthorizationDenial;

impl AuthorizationDenial {
    pub const CODE: &'static str = "AUTHORIZATION_DENIED";
}

impl fmt::Display for AuthorizationDenial {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(Self::CODE)
    }
}

impl Error for AuthorizationDenial {}

pub fn authorize_exact(
    principal: &AuthenticatedPrincipal,
    request: &AuthorizationRequest,
    grant: &CapabilityGrant,
) -> Result<(), AuthorizationDenial> {
    if principal.owner_id != request.claimed_scope.owner_id
        || principal.principal_id != grant.principal_id
        || grant.issuer_owner_id != request.target_scope.owner_id
        || request.claimed_scope != grant.claimed_scope
        || request.target_scope != grant.target_scope
        || !grant.operations.contains(&request.operation)
        || !grant.resources.contains(&request.resource_id)
        || request.now_ms >= grant.expires_at_ms
    {
        return Err(AuthorizationDenial);
    }

    let same_project = request.target_scope.owner_id == request.claimed_scope.owner_id
        && request.target_scope.project_id == request.claimed_scope.project_id;
    if !same_project && request.operation == CapabilityOperation::Read {
        let shared_workspace = request.claimed_scope.workspace_id.is_some()
            && request.claimed_scope.workspace_id == request.target_scope.workspace_id;
        if !shared_workspace {
            return Err(AuthorizationDenial);
        }
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn id(value: &str) -> Id {
        Id::new(value).unwrap()
    }

    fn scope(owner: &str, project: &str, workspace: Option<&str>) -> Scope {
        Scope::new(id(owner), id(project), workspace.map(id))
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

    fn principal(kind: PrincipalKind) -> AuthenticatedPrincipal {
        AuthenticatedPrincipal {
            principal_id: id("principal-1"),
            owner_id: id("alice"),
            kind,
        }
    }

    #[test]
    fn capability_rejects_claimed_owner_that_differs_from_authenticated_owner() {
        let claimed = scope("mallory", "project-a", None);
        let grant = grant(claimed.clone(), claimed.clone(), CapabilityOperation::Read);
        let request = AuthorizationRequest {
            claimed_scope: claimed.clone(),
            target_scope: claimed,
            operation: CapabilityOperation::Read,
            resource_id: id("record-1"),
            now_ms: 100,
        };

        assert_eq!(
            authorize_exact(&principal(PrincipalKind::Foreground), &request, &grant),
            Err(AuthorizationDenial)
        );
    }

    #[test]
    fn capability_allows_exact_shared_workspace_read() {
        let claimed = scope("alice", "project-a", Some("workspace-1"));
        let target = scope("bob", "project-b", Some("workspace-1"));
        let grant = grant(claimed.clone(), target.clone(), CapabilityOperation::Read);
        let request = AuthorizationRequest {
            claimed_scope: claimed,
            target_scope: target,
            operation: CapabilityOperation::Read,
            resource_id: id("record-1"),
            now_ms: 100,
        };

        assert_eq!(
            authorize_exact(&principal(PrincipalKind::Foreground), &request, &grant),
            Ok(())
        );
    }

    #[test]
    fn capability_allows_exact_target_owner_grant_for_cross_project_write() {
        let claimed = scope("alice", "project-a", Some("workspace-1"));
        let target = scope("bob", "project-b", Some("workspace-2"));
        let grant = grant(claimed.clone(), target.clone(), CapabilityOperation::Revise);
        let request = AuthorizationRequest {
            claimed_scope: claimed,
            target_scope: target,
            operation: CapabilityOperation::Revise,
            resource_id: id("record-1"),
            now_ms: 100,
        };

        assert_eq!(
            authorize_exact(&principal(PrincipalKind::Background), &request, &grant),
            Ok(())
        );
    }

    #[test]
    fn capability_denies_cross_project_write_not_issued_by_target_owner() {
        let claimed = scope("alice", "project-a", Some("workspace-1"));
        let target = scope("bob", "project-b", Some("workspace-1"));
        let mut grant = grant(claimed.clone(), target.clone(), CapabilityOperation::Revise);
        grant.issuer_owner_id = id("alice");
        let request = AuthorizationRequest {
            claimed_scope: claimed,
            target_scope: target,
            operation: CapabilityOperation::Revise,
            resource_id: id("record-1"),
            now_ms: 100,
        };

        assert_eq!(
            authorize_exact(&principal(PrincipalKind::Background), &request, &grant),
            Err(AuthorizationDenial)
        );
    }

    #[test]
    fn capability_requires_exact_operation_resource_and_live_expiry() {
        let claimed = scope("alice", "project-a", None);
        let grant = grant(
            claimed.clone(),
            claimed.clone(),
            CapabilityOperation::Append,
        );
        for request in [
            AuthorizationRequest {
                claimed_scope: claimed.clone(),
                target_scope: claimed.clone(),
                operation: CapabilityOperation::Delete,
                resource_id: id("record-1"),
                now_ms: 100,
            },
            AuthorizationRequest {
                claimed_scope: claimed.clone(),
                target_scope: claimed.clone(),
                operation: CapabilityOperation::Append,
                resource_id: id("record-2"),
                now_ms: 100,
            },
            AuthorizationRequest {
                claimed_scope: claimed.clone(),
                target_scope: claimed,
                operation: CapabilityOperation::Append,
                resource_id: id("record-1"),
                now_ms: 200,
            },
        ] {
            assert_eq!(
                authorize_exact(&principal(PrincipalKind::Foreground), &request, &grant),
                Err(AuthorizationDenial)
            );
        }
    }
}
