use std::{
    collections::HashMap,
    str::FromStr,
    sync::{Arc, Mutex},
};

use hypermid_contracts::{Cursor, Digest, EffectState, Error, Id, Scope};
use hypermid_control::{
    ControlOperation, ControlReply, ControlRequest, GenerationStamp, MutationOutcome,
};
use hypermid_protocol::Principal;
use serde_json::{json, Value};

use crate::{
    diagnostics::{DiagnosticSnapshot, DiagnosticsStore, RouteDiagnostic},
    health::{DurableStore, Evidence, HealthTracker},
    registry::Registry,
    router::Router,
};

#[derive(Clone)]
struct CachedMutation {
    digest: Digest,
    reply: ControlReply,
}

#[derive(Clone)]
pub struct ControlPlane {
    daemon_instance_id: Id,
    started_ms: u64,
    registry: Registry,
    router: Router,
    health: HealthTracker,
    durable_store: DurableStore,
    diagnostics: DiagnosticsStore,
    mutations: Arc<Mutex<HashMap<(Id, Id), CachedMutation>>>,
    event_epoch: u64,
    oldest_event_sequence: u64,
}

impl ControlPlane {
    pub fn new(
        daemon_instance_id: Id,
        started_ms: u64,
        registry: Registry,
        router: Router,
        health: HealthTracker,
        durable_store: DurableStore,
        diagnostics: DiagnosticsStore,
    ) -> Self {
        Self {
            daemon_instance_id,
            started_ms,
            registry,
            router,
            health,
            durable_store,
            diagnostics,
            mutations: Arc::new(Mutex::new(HashMap::new())),
            event_epoch: 1,
            oldest_event_sequence: 0,
        }
    }

    pub fn health(&self) -> &HealthTracker {
        &self.health
    }

    pub fn diagnostics(&self) -> &DiagnosticsStore {
        &self.diagnostics
    }

    pub fn operations() -> Vec<&'static str> {
        ControlOperation::ALL
            .into_iter()
            .map(ControlOperation::as_str)
            .collect()
    }

    pub fn execute(
        &self,
        principal: &Principal,
        bound_scope: &Scope,
        request: ControlRequest,
        now_ms: u64,
    ) -> ControlReply {
        if &request.scope != bound_scope {
            return self.error(
                Some(MutationOutcome::Refused),
                "SCOPE_DENIED",
                "request scope does not match the authenticated session scope",
                false,
                None,
            );
        }
        if !principal
            .scopes
            .iter()
            .any(|scope| scope == request.operation.required_scope())
        {
            return self.error(
                Some(MutationOutcome::Refused),
                "SCOPE_DENIED",
                "the authenticated principal lacks the operation scope",
                false,
                None,
            );
        }

        let is_mutation = request.operation.is_mutation()
            && !(request.operation == ControlOperation::RegistryRescan
                && request.payload.get("mode").and_then(Value::as_str) == Some("preview"));
        let fingerprint = Digest::sha256(
            serde_json::to_vec(&request)
                .expect("validated control requests always serialize deterministically"),
        );
        let mutation_key = (principal.id.clone(), request.message_id.clone());
        if is_mutation {
            match self.mutations.lock() {
                Ok(cache) => {
                    if let Some(cached) = cache.get(&mutation_key) {
                        if cached.digest != fingerprint {
                            return self.error(
                                Some(MutationOutcome::Refused),
                                "IDEMPOTENCY_CONFLICT",
                                "message id was already used for another mutation",
                                false,
                                None,
                            );
                        }
                        let mut reply = cached.reply.clone();
                        if reply.outcome == Some(MutationOutcome::Applied) {
                            reply.outcome = Some(MutationOutcome::AlreadyApplied);
                        }
                        return reply;
                    }
                }
                Err(_) => {
                    return self.error(
                        Some(MutationOutcome::Refused),
                        "CONTROL_STATE_UNAVAILABLE",
                        "mutation receipt state is unavailable",
                        true,
                        None,
                    );
                }
            }
        }

        let reply = self.execute_once(principal, &request, now_ms);
        if is_mutation {
            if let Ok(mut cache) = self.mutations.lock() {
                cache.insert(
                    mutation_key,
                    CachedMutation {
                        digest: fingerprint,
                        reply: reply.clone(),
                    },
                );
            }
        }
        reply
    }

    fn execute_once(
        &self,
        principal: &Principal,
        request: &ControlRequest,
        now_ms: u64,
    ) -> ControlReply {
        match request.operation {
            ControlOperation::ServerDescribe => self.describe(now_ms),
            ControlOperation::RegistryList => match self.registry.snapshot() {
                Ok(snapshot) => self.success(json!({
                    "generation": snapshot.generation,
                    "entries": snapshot.entries.into_iter().map(|(module_id, entry)| json!({
                        "module_id": module_id,
                        "state": format!("{:?}", entry.state).to_lowercase(),
                        "spawn_generation": entry.spawn_generation,
                        "artifact_digest": entry.manifest.manifest.artifact_digest,
                        "version": entry.manifest.manifest.version,
                    })).collect::<Vec<_>>(),
                })),
                Err(error) => {
                    self.error(None, "REGISTRY_UNAVAILABLE", &error.to_string(), true, None)
                }
            },
            ControlOperation::RegistryRescan => self.registry_rescan(&request.payload),
            ControlOperation::RouteOpen => self.route_open(principal, request),
            ControlOperation::RoutePoll => match self.router.snapshot() {
                Ok(snapshot) => self.success(json!({"routes": route_values(snapshot.routes)})),
                Err(error) => self.error(None, "ROUTE_UNAVAILABLE", &error.to_string(), true, None),
            },
            ControlOperation::RouteClose => self.route_close(&request.payload),
            ControlOperation::SupervisorList => {
                self.success(json!({"modules": self.health_snapshot(now_ms).modules}))
            }
            ControlOperation::SupervisorHealth | ControlOperation::OperatorStatus => self.success(
                serde_json::to_value(self.health_snapshot(now_ms))
                    .expect("health snapshot serializes"),
            ),
            ControlOperation::SupervisorRoutes => match self.router.snapshot() {
                Ok(snapshot) => self.success(json!({"routes": route_values(snapshot.routes)})),
                Err(error) => self.error(None, "ROUTE_UNAVAILABLE", &error.to_string(), true, None),
            },
            ControlOperation::SupervisorStderr
            | ControlOperation::SupervisorTerminals
            | ControlOperation::SupervisorSpawnSnapshot
            | ControlOperation::LogsRead
            | ControlOperation::DiagnosticsGet => {
                self.diagnostic_reply(now_ms, Some(&request.scope))
            }
            ControlOperation::SupervisorProbe | ControlOperation::DiagnosticsRerun => {
                let mut reply = self.diagnostic_reply(now_ms, Some(&request.scope));
                reply.outcome = Some(MutationOutcome::Applied);
                reply
            }
            ControlOperation::EventsSubscribe | ControlOperation::SupervisorSpawnSubscribe => {
                self.subscribe(&request.payload)
            }
            ControlOperation::EventsAck => self.ack(&request.payload),
            ControlOperation::SupervisorRestart
            | ControlOperation::SupervisorSwap
            | ControlOperation::SupervisorSetEnabled => self.error(
                Some(MutationOutcome::Refused),
                "SUPERVISOR_UNAVAILABLE",
                "no module supervisor is attached",
                true,
                None,
            ),
            ControlOperation::MaintenancePlan
            | ControlOperation::MaintenanceApply
            | ControlOperation::MaintenanceStatus
            | ControlOperation::MaintenanceCancel => self.error(
                if request.operation.is_mutation() {
                    Some(MutationOutcome::Refused)
                } else {
                    None
                },
                "MAINTENANCE_UNAVAILABLE",
                "no lifecycle maintenance backend is attached",
                true,
                None,
            ),
        }
    }

    fn registry_rescan(&self, payload: &Value) -> ControlReply {
        match payload
            .get("mode")
            .and_then(Value::as_str)
            .unwrap_or("preview")
        {
            "preview" => match self.registry.preview_rescan() {
                Ok(preview) => self.success(json!({
                    "mode": "preview",
                    "current_generation": preview.current_generation,
                    "candidate_generation": preview.candidate_generation,
                    "added": preview.added,
                    "removed": preview.removed,
                    "changed": preview.changed,
                })),
                Err(error) => self.error(
                    None,
                    "REGISTRY_RESCAN_FAILED",
                    &error.to_string(),
                    false,
                    None,
                ),
            },
            "apply" => match self.registry.rescan() {
                Ok(generation) => {
                    self.mutation_success(json!({"mode": "apply", "generation": generation}))
                }
                Err(error) => self.error(
                    Some(MutationOutcome::Refused),
                    "REGISTRY_RESCAN_FAILED",
                    &error.to_string(),
                    false,
                    None,
                ),
            },
            _ => self.error(
                Some(MutationOutcome::Refused),
                "INVALID_REQUEST",
                "rescan mode must be preview or apply",
                false,
                None,
            ),
        }
    }

    fn describe(&self, now_ms: u64) -> ControlReply {
        self.refresh_durable_health(now_ms);
        let health = self.health_snapshot(now_ms);
        let durable = self.durable_store.probe();
        let storage_version = durable
            .as_ref()
            .ok()
            .map(|health| health.storage_version.to_string());
        let digest_health = durable
            .as_ref()
            .map(|health| {
                if health.digest_intact {
                    "healthy"
                } else {
                    "broken"
                }
            })
            .unwrap_or("unknown");
        self.success(json!({
            "daemon_instance_id": self.daemon_instance_id,
            "registry_generation": self.registry_generation(),
            "protocol": hypermid_protocol::PROTOCOL,
            "protocol_version": hypermid_protocol::PROTOCOL,
            "protocol_min": hypermid_protocol::PROTOCOL,
            "protocol_max": hypermid_protocol::PROTOCOL,
            "build_version": env!("CARGO_PKG_VERSION"),
            "storage_version": storage_version,
            "storage_writable": durable.as_ref().is_ok_and(|health| health.writable),
            "digest_health": digest_health,
            "started_ms": self.started_ms,
            "server_time_ms": now_ms,
            "operations": Self::operations(),
            "health": {
                "status": health.status,
                "observed_at_ms": health.observed_at_ms,
                "checks": health.daemon,
                "modules": health.modules,
            },
        }))
    }

    fn refresh_durable_health(&self, now_ms: u64) {
        match self.durable_store.probe() {
            Ok(health) => {
                self.health.set_daemon_evidence(
                    "storage",
                    Evidence::Available {
                        observed_at_ms: now_ms,
                        value: json!({
                            "version": health.storage_version,
                            "writable": health.writable,
                        }),
                    },
                );
                self.health.set_daemon_evidence(
                    "digest_chain",
                    Evidence::Available {
                        observed_at_ms: now_ms,
                        value: json!(if health.digest_intact {
                            "intact"
                        } else {
                            "broken"
                        }),
                    },
                );
            }
            Err(reason) => {
                self.health.set_daemon_evidence(
                    "storage",
                    Evidence::Unavailable {
                        observed_at_ms: now_ms,
                        reason: reason.clone(),
                        last_good_ms: None,
                    },
                );
                self.health.set_daemon_evidence(
                    "digest_chain",
                    Evidence::Unavailable {
                        observed_at_ms: now_ms,
                        reason,
                        last_good_ms: None,
                    },
                );
            }
        }
    }

    fn route_open(&self, principal: &Principal, request: &ControlRequest) -> ControlReply {
        let Some(operation) = request.payload.get("operation").and_then(Value::as_str) else {
            return self.error(
                Some(MutationOutcome::Refused),
                "INVALID_REQUEST",
                "route operation is required",
                false,
                None,
            );
        };
        match self
            .router
            .reserve_route(principal, operation, Some(request.scope.clone()))
        {
            Ok(reservation) => {
                let _ = self.router.fail_bind(&reservation);
                self.error(
                    Some(MutationOutcome::Refused),
                    "ROUTE_BIND_UNAVAILABLE",
                    "module bind acknowledgement is unavailable",
                    true,
                    None,
                )
            }
            Err(error) => self.error(
                Some(MutationOutcome::Refused),
                "ROUTE_OPEN_FAILED",
                &error.to_string(),
                false,
                None,
            ),
        }
    }

    fn route_close(&self, payload: &Value) -> ControlReply {
        let Some(route_id) = payload.get("route_id").and_then(Value::as_str) else {
            return self.error(
                Some(MutationOutcome::Refused),
                "INVALID_REQUEST",
                "route_id is required",
                false,
                None,
            );
        };
        let Some(route_epoch) = payload.get("route_epoch").and_then(Value::as_u64) else {
            return self.error(
                Some(MutationOutcome::Refused),
                "INVALID_REQUEST",
                "route_epoch is required",
                false,
                None,
            );
        };
        match self.router.close_route(route_id, route_epoch) {
            Ok(()) => {
                self.mutation_success(json!({"route_id": route_id, "route_epoch": route_epoch}))
            }
            Err(error) => self.error(
                Some(MutationOutcome::Refused),
                "ROUTE_CLOSE_FAILED",
                &error.to_string(),
                false,
                None,
            ),
        }
    }

    fn subscribe(&self, payload: &Value) -> ControlReply {
        let current = Cursor::new(self.event_epoch, self.oldest_event_sequence)
            .expect("static control cursor is valid");
        if let Some(cursor_value) = payload.get("cursor") {
            let supplied = serde_json::from_value::<Cursor>(cursor_value.clone());
            match supplied {
                Ok(cursor)
                    if cursor.epoch != self.event_epoch
                        || cursor.sequence != self.oldest_event_sequence =>
                {
                    return self.reply(
                        None,
                        Some(json!({
                            "subscription_id": "events",
                            "cursor": current,
                            "recovery_cursor": current,
                            "reason": "cursor is foreign or older than retained history",
                        })),
                        Some(control_error(
                            "CURSOR_GAP",
                            "subscription cursor cannot be resumed",
                            false,
                            None,
                        )),
                    );
                }
                Err(_) => {
                    return self.error(None, "INVALID_REQUEST", "cursor is invalid", false, None);
                }
                _ => {}
            }
        }
        self.success(json!({
            "subscription_id": "events",
            "cursor": current,
            "snapshot": [],
            "daemon_instance_id": self.daemon_instance_id,
        }))
    }

    fn ack(&self, payload: &Value) -> ControlReply {
        let Some(cursor_value) = payload.get("cursor") else {
            return self.error(
                Some(MutationOutcome::Refused),
                "INVALID_REQUEST",
                "cursor is required",
                false,
                None,
            );
        };
        match serde_json::from_value::<Cursor>(cursor_value.clone()) {
            Ok(cursor) if cursor.epoch == self.event_epoch => {
                self.mutation_success(json!({"cursor": cursor}))
            }
            Ok(_) => self.error(
                Some(MutationOutcome::Refused),
                "CURSOR_GAP",
                "cursor belongs to another epoch",
                false,
                None,
            ),
            Err(_) => self.error(
                Some(MutationOutcome::Refused),
                "INVALID_REQUEST",
                "cursor is invalid",
                false,
                None,
            ),
        }
    }

    fn diagnostic_reply(&self, now_ms: u64, scope: Option<&Scope>) -> ControlReply {
        if let Ok(routes) = self.router.snapshot() {
            self.diagnostics.set_routes(
                routes
                    .routes
                    .into_iter()
                    .map(|route| RouteDiagnostic {
                        route_id: route.route_id,
                        route_epoch: route.route_epoch,
                        module_id: route.module_id,
                        spawn_generation: route.spawn_generation,
                        operation: route.operation,
                        scope: route.scope,
                    })
                    .collect(),
            );
        }
        let snapshot: DiagnosticSnapshot = self.diagnostics.snapshot(now_ms, scope);
        self.success(serde_json::to_value(snapshot).expect("diagnostic snapshot serializes"))
    }

    fn health_snapshot(&self, now_ms: u64) -> crate::health::HealthSnapshot {
        self.health.snapshot(
            self.daemon_instance_id.clone(),
            self.registry_generation(),
            now_ms,
        )
    }

    pub fn registry_generation(&self) -> u64 {
        self.registry
            .snapshot()
            .map(|snapshot| snapshot.generation)
            .unwrap_or(0)
    }

    fn generation(&self) -> GenerationStamp {
        GenerationStamp {
            daemon_instance_id: self.daemon_instance_id.clone(),
            registry_generation: self.registry_generation(),
        }
    }

    fn success(&self, payload: Value) -> ControlReply {
        self.reply(None, Some(payload), None)
    }

    fn mutation_success(&self, payload: Value) -> ControlReply {
        self.reply(Some(MutationOutcome::Applied), Some(payload), None)
    }

    fn error(
        &self,
        outcome: Option<MutationOutcome>,
        code: &str,
        message: &str,
        retryable: bool,
        effect_state: Option<EffectState>,
    ) -> ControlReply {
        self.reply(
            outcome,
            None,
            Some(control_error(code, message, retryable, effect_state)),
        )
    }

    fn reply(
        &self,
        outcome: Option<MutationOutcome>,
        payload: Option<Value>,
        error: Option<Error>,
    ) -> ControlReply {
        ControlReply {
            generation: self.generation(),
            outcome,
            payload,
            error,
        }
    }
}

pub fn parse_request(
    message_id: Id,
    operation: &str,
    scope: Scope,
    payload: Option<Value>,
) -> Result<ControlRequest, Error> {
    Ok(ControlRequest {
        message_id,
        operation: ControlOperation::from_str(operation).map_err(|()| {
            control_error(
                "UNKNOWN_OPERATION",
                "operation is not available",
                false,
                None,
            )
        })?,
        scope,
        payload: payload.unwrap_or(Value::Null),
    })
}

fn route_values(routes: Vec<crate::router::RouteBinding>) -> Vec<Value> {
    routes
        .into_iter()
        .map(|route| {
            json!({
                "route_id": route.route_id,
                "route_epoch": route.route_epoch,
                "module_id": route.module_id,
                "spawn_generation": route.spawn_generation,
                "operation": route.operation,
                "scope": route.scope,
            })
        })
        .collect()
}

fn control_error(
    code: &str,
    message: &str,
    retryable: bool,
    effect_state: Option<EffectState>,
) -> Error {
    Error::new(code, message, retryable, None, effect_state)
        .expect("control errors satisfy the shared Error contract")
}

pub fn bootstrap_health(now_ms: u64) -> HealthTracker {
    let tracker = HealthTracker::default();
    tracker.set_daemon_evidence(
        "process",
        Evidence::Available {
            observed_at_ms: now_ms,
            value: json!("live"),
        },
    );
    tracker.set_daemon_evidence(
        "transport",
        Evidence::Available {
            observed_at_ms: now_ms,
            value: json!("tls13_mutual_auth"),
        },
    );
    tracker
}
