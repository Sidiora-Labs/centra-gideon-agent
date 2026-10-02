use hypermid_contracts::Id;
use hypermid_core::capability::{
    AuthContext, AuthenticatedPrincipal, AuthorizationRequest, PrincipalKind,
};
use hypermid_protocol::PrincipalKind as ProtocolPrincipalKind;
use hypermid_store::authorization::{authorize, CommitAuthorization};
use hypermid_transport::AuthenticatedSession;
use rusqlite::Connection;

#[derive(Clone, Copy, Debug, Default)]
pub struct Dispatcher;

impl Dispatcher {
    pub fn authorize(
        &self,
        connection: &Connection,
        context: &AuthContext,
    ) -> Result<CommitAuthorization, DispatchError> {
        authorize(connection, context).map_err(|_| DispatchError::denied())
    }

    pub fn authorize_session(
        &self,
        connection: &Connection,
        session: &AuthenticatedSession,
        request: AuthorizationRequest,
        capability_id: Id,
    ) -> Result<CommitAuthorization, DispatchError> {
        let context = self.context_for_session(session, request, capability_id)?;
        self.authorize(connection, &context)
    }

    pub fn context_for_session(
        &self,
        session: &AuthenticatedSession,
        request: AuthorizationRequest,
        capability_id: Id,
    ) -> Result<AuthContext, DispatchError> {
        if request.claimed_scope != session.bound_scope {
            return Err(DispatchError::denied());
        }
        let kind = match session.accepted.principal.kind {
            ProtocolPrincipalKind::LocalUser | ProtocolPrincipalKind::Device => {
                PrincipalKind::Foreground
            }
            ProtocolPrincipalKind::SupervisedModule | ProtocolPrincipalKind::Service => {
                PrincipalKind::Background
            }
        };
        Ok(AuthContext {
            principal: AuthenticatedPrincipal {
                principal_id: session.accepted.principal.id.clone(),
                owner_id: session.bound_scope.owner_id.clone(),
                kind,
            },
            request,
            capability_id,
        })
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct DispatchError {
    pub code: &'static str,
    pub message: &'static str,
    pub retryable: bool,
}

impl DispatchError {
    fn denied() -> Self {
        Self {
            code: "AUTHORIZATION_DENIED",
            message: "request is not authorized",
            retryable: false,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use hypermid_contracts::{Id, Scope};
    use hypermid_core::capability::{CapabilityGrant, CapabilityOperation, PrincipalKind};
    use hypermid_store::authorization::{install_schema, put_grant};
    use std::collections::BTreeSet;

    fn id(value: &str) -> Id {
        Id::new(value).unwrap()
    }

    #[test]
    fn dispatch_maps_all_capability_rejections_to_one_public_error() {
        let mut connection = Connection::open_in_memory().unwrap();
        let transaction = connection.transaction().unwrap();
        install_schema(&transaction).unwrap();
        transaction.commit().unwrap();
        let scope = Scope::new(id("alice"), id("project-a"), None);
        let principal = AuthenticatedPrincipal {
            principal_id: id("principal-1"),
            owner_id: id("mallory"),
            kind: PrincipalKind::Foreground,
        };
        let request = AuthorizationRequest {
            claimed_scope: scope.clone(),
            target_scope: scope.clone(),
            operation: CapabilityOperation::Read,
            resource_id: id("record-1"),
            now_ms: 100,
        };
        let grant = CapabilityGrant {
            capability_id: id("cap-1"),
            issuer_owner_id: id("alice"),
            principal_id: id("principal-1"),
            claimed_scope: scope.clone(),
            target_scope: scope,
            operations: BTreeSet::from([CapabilityOperation::Read]),
            resources: BTreeSet::from([id("record-1")]),
            expires_at_ms: 200,
        };
        let transaction = connection.transaction().unwrap();
        put_grant(
            &transaction,
            &AuthenticatedPrincipal {
                principal_id: id("owner-principal"),
                owner_id: id("alice"),
                kind: PrincipalKind::Foreground,
            },
            &grant,
        )
        .unwrap();
        transaction.commit().unwrap();

        let error = Dispatcher
            .authorize(
                &connection,
                &AuthContext {
                    principal,
                    request,
                    capability_id: id("cap-1"),
                },
            )
            .unwrap_err();
        assert_eq!(
            error,
            DispatchError {
                code: "AUTHORIZATION_DENIED",
                message: "request is not authorized",
                retryable: false,
            }
        );
    }
}
