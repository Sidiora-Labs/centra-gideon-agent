use std::collections::{BTreeMap, BTreeSet};

use hypermid_contracts::{Digest, Id, Scope};
use hypermid_protocol::Principal;
use hypermid_transport::federation::{EffectClass, FederatedCall, FederationError};
use serde::{Deserialize, Serialize};

use crate::manifest::EffectClass as ManifestEffectClass;
use crate::registry::{ModuleRegistrationState, Registry, RegistryError};
use crate::router::{RouteError, RouteReservation, Router};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FederatedOperation {
    pub module_id: Id,
    pub name: String,
    pub effect: EffectClass,
    pub remote: bool,
    pub required_scopes: BTreeSet<String>,
}

#[derive(Clone, Debug, Default)]
pub struct FederationCatalog {
    operations: BTreeMap<String, FederatedOperation>,
}

impl FederationCatalog {
    pub fn new(operations: impl IntoIterator<Item = FederatedOperation>) -> Self {
        Self {
            operations: operations
                .into_iter()
                .filter(|operation| operation.remote)
                .map(|operation| (operation.name.clone(), operation))
                .collect(),
        }
    }

    pub fn get(&self, operation: &str) -> Option<&FederatedOperation> {
        self.operations.get(operation)
    }

    pub fn advertised(&self) -> impl Iterator<Item = &FederatedOperation> {
        self.operations.values()
    }

    pub fn digest(&self) -> Digest {
        let bytes = serde_json::to_vec(&self.operations)
            .expect("federation catalog contains only serializable contract values");
        Digest::sha256(bytes)
    }

    pub fn from_registry(registry: &Registry) -> Result<Self, RegistryError> {
        let snapshot = registry.snapshot()?;
        let mut operations = Vec::new();
        for entry in snapshot.entries.into_values() {
            if entry.state != ModuleRegistrationState::Ready {
                continue;
            }
            let module_id = entry.manifest.manifest.module_id.clone();
            for operation in &entry.manifest.manifest.operations {
                operations.push(FederatedOperation {
                    module_id: module_id.clone(),
                    name: operation.name.clone(),
                    effect: match operation.effect {
                        ManifestEffectClass::Query => EffectClass::Query,
                        ManifestEffectClass::Idempotent => EffectClass::Idempotent,
                        ManifestEffectClass::Durable => EffectClass::Durable,
                    },
                    remote: operation.remote,
                    required_scopes: operation.required_scopes.iter().cloned().collect(),
                });
            }
        }
        Ok(Self::new(operations))
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RemoteGrant {
    pub peer_id: Id,
    pub principal_id: Id,
    pub scope: Scope,
    pub scopes: BTreeSet<String>,
    pub operations: BTreeSet<String>,
    pub expires_ms: u64,
}

#[derive(Clone, Debug, PartialEq)]
pub struct AdmittedFederatedCall {
    pub peer_id: Id,
    pub module_id: Id,
    pub principal: Principal,
    pub call: FederatedCall,
}

#[derive(Clone, Debug, Default)]
pub struct FederationGate {
    enabled: bool,
    local_scopes: BTreeSet<String>,
    grants: BTreeMap<Id, RemoteGrant>,
}

impl FederationGate {
    pub fn disabled() -> Self {
        Self::default()
    }

    pub fn enabled(local_scopes: impl IntoIterator<Item = String>) -> Self {
        Self {
            enabled: true,
            local_scopes: local_scopes.into_iter().collect(),
            grants: BTreeMap::new(),
        }
    }

    pub fn pair(&mut self, grant: RemoteGrant) {
        self.grants.insert(grant.peer_id.clone(), grant);
    }

    pub fn unpair(&mut self, peer_id: &Id) {
        self.grants.remove(peer_id);
    }

    pub fn admit(
        &self,
        session_peer_id: &Id,
        catalog: &FederationCatalog,
        call: FederatedCall,
        now_ms: u64,
    ) -> Result<AdmittedFederatedCall, FederationAdmissionError> {
        if !self.enabled {
            return Err(FederationAdmissionError::Disabled);
        }
        call.validate(now_ms)?;
        let grant = self
            .grants
            .get(session_peer_id)
            .ok_or(FederationAdmissionError::UnpairedPeer)?;
        if now_ms >= grant.expires_ms {
            return Err(FederationAdmissionError::ExpiredGrant);
        }
        if call.principal.id != grant.principal_id {
            return Err(FederationAdmissionError::PrincipalMismatch);
        }
        if call.scope != grant.scope {
            return Err(FederationAdmissionError::ScopeMismatch);
        }
        if !grant.operations.contains(&call.operation) {
            return Err(FederationAdmissionError::OperationDenied);
        }
        let operation = catalog
            .get(&call.operation)
            .ok_or(FederationAdmissionError::OperationNotRemote)?;
        if operation.effect != call.effect {
            return Err(FederationAdmissionError::EffectMismatch);
        }
        let principal_scopes = call
            .principal
            .scopes
            .iter()
            .map(String::as_str)
            .collect::<BTreeSet<_>>();
        if operation.required_scopes.iter().any(|required| {
            !grant.scopes.contains(required)
                || !self.local_scopes.contains(required)
                || !principal_scopes.contains(required.as_str())
        }) {
            return Err(FederationAdmissionError::ScopeDenied);
        }
        Ok(AdmittedFederatedCall {
            peer_id: session_peer_id.clone(),
            module_id: operation.module_id.clone(),
            principal: call.principal.clone(),
            call,
        })
    }

    pub fn admit_and_reserve(
        &self,
        session_peer_id: &Id,
        catalog: &FederationCatalog,
        router: &Router,
        call: FederatedCall,
        now_ms: u64,
    ) -> Result<(AdmittedFederatedCall, RouteReservation), FederationAdmissionError> {
        let admitted = self.admit(session_peer_id, catalog, call, now_ms)?;
        let reservation = router.reserve_route(
            &admitted.principal,
            &admitted.call.operation,
            Some(admitted.call.scope.clone()),
        )?;
        if reservation.module_id != admitted.module_id.as_str() {
            router.fail_bind(&reservation)?;
            return Err(FederationAdmissionError::CatalogDrift);
        }
        Ok((admitted, reservation))
    }
}

#[derive(Debug, thiserror::Error)]
pub enum FederationAdmissionError {
    #[error("federation is disabled")]
    Disabled,
    #[error("peer is not paired")]
    UnpairedPeer,
    #[error("peer grant expired")]
    ExpiredGrant,
    #[error("originating principal is not bound to the peer grant")]
    PrincipalMismatch,
    #[error("federated scope is not the exact granted scope")]
    ScopeMismatch,
    #[error("operation is absent from the peer grant")]
    OperationDenied,
    #[error("operation is not advertised for remote use")]
    OperationNotRemote,
    #[error("call effect classification differs from the local registry")]
    EffectMismatch,
    #[error("federated catalog changed before route reservation")]
    CatalogDrift,
    #[error("required scope is absent from the peer, principal, or local policy")]
    ScopeDenied,
    #[error(transparent)]
    Transport(#[from] FederationError),
    #[error(transparent)]
    Route(#[from] RouteError),
}
