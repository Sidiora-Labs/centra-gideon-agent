use std::{
    collections::{BTreeMap, HashMap},
    sync::{Arc, Mutex},
};

use hypermid_contracts::Scope;
use hypermid_protocol::{Envelope, MessageKind, Principal};
use thiserror::Error;

use crate::registry::{OperationTarget, Registry, RegistryError};

#[derive(Clone, Debug)]
pub struct RouteReservation {
    pub route_id: String,
    pub route_epoch: u64,
    pub module_id: String,
    pub spawn_generation: u64,
    pub operation: String,
    pub scope: Option<Scope>,
    required_scopes: Vec<String>,
}

#[derive(Clone, Debug)]
pub struct RouteBinding {
    pub route_id: String,
    pub route_epoch: u64,
    pub module_id: String,
    pub spawn_generation: u64,
    pub operation: String,
    pub scope: Option<Scope>,
}

#[derive(Clone, Debug)]
pub struct RoutedFrame {
    pub binding: RouteBinding,
    pub envelope: Envelope,
}

#[derive(Clone, Debug)]
pub struct RouteSnapshot {
    pub routes: Vec<RouteBinding>,
}

#[derive(Debug, Default)]
struct RouteState {
    next_slot: u64,
    epochs: HashMap<String, u64>,
    available: Vec<String>,
    reservations: HashMap<String, RouteReservation>,
    routes: BTreeMap<String, RouteBinding>,
}

#[derive(Clone, Debug)]
pub struct Router {
    registry: Registry,
    state: Arc<Mutex<RouteState>>,
}

#[derive(Debug, Error)]
pub enum RouteError {
    #[error(transparent)]
    Registry(#[from] RegistryError),
    #[error("route state lock is poisoned")]
    Poisoned,
    #[error("principal lacks operation scope {0}")]
    ScopeDenied(String),
    #[error("unknown route")]
    UnknownRoute,
    #[error("route epoch is stale")]
    StaleEpoch,
    #[error("route bind acknowledgement does not match its reservation")]
    BindMismatch,
    #[error("route operation or scope changed")]
    RouteMismatch,
    #[error("routed envelope has an invalid kind")]
    InvalidKind,
    #[error("request deadline has expired")]
    DeadlineExpired,
}

impl Router {
    pub fn new(registry: Registry) -> Self {
        Self {
            registry,
            state: Arc::new(Mutex::new(RouteState::default())),
        }
    }

    pub fn registry(&self) -> &Registry {
        &self.registry
    }

    pub fn reserve_route(
        &self,
        principal: &Principal,
        operation: &str,
        scope: Option<Scope>,
    ) -> Result<RouteReservation, RouteError> {
        let target = self.registry.resolve_operation(operation)?;
        authorize(principal, &target)?;
        let mut state = self.state.lock().map_err(|_| RouteError::Poisoned)?;
        let route_id = state.available.pop().unwrap_or_else(|| {
            state.next_slot = state.next_slot.saturating_add(1);
            format!("route-{}", state.next_slot)
        });
        let epoch = state.epochs.entry(route_id.clone()).or_default();
        *epoch = epoch.saturating_add(1).max(1);
        let reservation = RouteReservation {
            route_id: route_id.clone(),
            route_epoch: *epoch,
            module_id: target.module_id,
            spawn_generation: target.spawn_generation,
            operation: operation.into(),
            scope,
            required_scopes: target.operation.required_scopes,
        };
        state.reservations.insert(route_id, reservation.clone());
        Ok(reservation)
    }

    pub fn acknowledge_bind(
        &self,
        reservation: &RouteReservation,
        module_id: &str,
        spawn_generation: u64,
    ) -> Result<RouteBinding, RouteError> {
        let target = self.registry.resolve_operation(&reservation.operation)?;
        let mut state = self.state.lock().map_err(|_| RouteError::Poisoned)?;
        let current = state
            .reservations
            .get(&reservation.route_id)
            .ok_or(RouteError::UnknownRoute)?;
        if current.route_epoch != reservation.route_epoch
            || current.module_id != module_id
            || current.spawn_generation != spawn_generation
            || target.module_id != module_id
            || target.spawn_generation != spawn_generation
        {
            return Err(RouteError::BindMismatch);
        }
        let binding = RouteBinding {
            route_id: reservation.route_id.clone(),
            route_epoch: reservation.route_epoch,
            module_id: module_id.into(),
            spawn_generation,
            operation: reservation.operation.clone(),
            scope: reservation.scope.clone(),
        };
        state.reservations.remove(&reservation.route_id);
        state
            .routes
            .insert(reservation.route_id.clone(), binding.clone());
        Ok(binding)
    }

    pub fn fail_bind(&self, reservation: &RouteReservation) -> Result<(), RouteError> {
        let mut state = self.state.lock().map_err(|_| RouteError::Poisoned)?;
        let current = state
            .reservations
            .get(&reservation.route_id)
            .ok_or(RouteError::UnknownRoute)?;
        if current.route_epoch != reservation.route_epoch {
            return Err(RouteError::StaleEpoch);
        }
        state.reservations.remove(&reservation.route_id);
        state.available.push(reservation.route_id.clone());
        Ok(())
    }

    pub fn dispatch(
        &self,
        principal: &Principal,
        envelope: Envelope,
        now_ms: u64,
    ) -> Result<RoutedFrame, RouteError> {
        if !matches!(envelope.kind, MessageKind::Request | MessageKind::Cancel) {
            return Err(RouteError::InvalidKind);
        }
        let route_id = envelope
            .route_id
            .as_ref()
            .map(hypermid_contracts::Id::as_str)
            .ok_or(RouteError::UnknownRoute)?;
        let route_epoch = envelope.route_epoch.ok_or(RouteError::StaleEpoch)?;
        let binding = {
            let state = self.state.lock().map_err(|_| RouteError::Poisoned)?;
            let binding = state.routes.get(route_id).ok_or(RouteError::UnknownRoute)?;
            if binding.route_epoch != route_epoch {
                return Err(RouteError::StaleEpoch);
            }
            binding.clone()
        };
        let target = self.registry.resolve_operation(&binding.operation)?;
        authorize(principal, &target)?;
        if target.module_id != binding.module_id
            || target.spawn_generation != binding.spawn_generation
            || envelope
                .operation
                .as_deref()
                .is_some_and(|value| value != binding.operation)
            || envelope.scope != binding.scope
        {
            return Err(RouteError::RouteMismatch);
        }
        if envelope
            .deadline_ms
            .is_some_and(|deadline| deadline <= now_ms)
        {
            return Err(RouteError::DeadlineExpired);
        }
        Ok(RoutedFrame { binding, envelope })
    }

    pub fn close_route(&self, route_id: &str, route_epoch: u64) -> Result<(), RouteError> {
        let mut state = self.state.lock().map_err(|_| RouteError::Poisoned)?;
        let binding = state.routes.get(route_id).ok_or(RouteError::UnknownRoute)?;
        if binding.route_epoch != route_epoch {
            return Err(RouteError::StaleEpoch);
        }
        state.routes.remove(route_id);
        state.available.push(route_id.into());
        Ok(())
    }

    pub fn close_module_routes(
        &self,
        module_id: &str,
        spawn_generation: u64,
    ) -> Result<Vec<RouteBinding>, RouteError> {
        let mut state = self.state.lock().map_err(|_| RouteError::Poisoned)?;
        let ids: Vec<_> = state
            .routes
            .values()
            .filter(|route| {
                route.module_id == module_id && route.spawn_generation == spawn_generation
            })
            .map(|route| route.route_id.clone())
            .collect();
        let mut closed = Vec::with_capacity(ids.len());
        for id in ids {
            if let Some(route) = state.routes.remove(&id) {
                state.available.push(id);
                closed.push(route);
            }
        }
        Ok(closed)
    }

    pub fn snapshot(&self) -> Result<RouteSnapshot, RouteError> {
        let state = self.state.lock().map_err(|_| RouteError::Poisoned)?;
        Ok(RouteSnapshot {
            routes: state.routes.values().cloned().collect(),
        })
    }
}

fn authorize(principal: &Principal, target: &OperationTarget) -> Result<(), RouteError> {
    for required in &target.operation.required_scopes {
        if !principal.scopes.iter().any(|granted| granted == required) {
            return Err(RouteError::ScopeDenied(required.clone()));
        }
    }
    Ok(())
}
