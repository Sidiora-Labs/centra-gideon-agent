use std::{
    collections::{BTreeMap, BTreeSet, HashMap},
    ffi::OsString,
    path::PathBuf,
    sync::Arc,
    time::Duration,
};

use hypermid_bus::{
    BeginEffect, BeginOutcome, BusError, DurableEffectLedger, EffectStatus, ReconciliationOutcome,
};
use hypermid_contracts::{Digest, Id, Scope};
use hypermid_mcp::{
    Cancellation, InvocationOutcome, McpError, McpStdioClient, StdioBudgets, ToolDefinition,
};
use hypermid_protocol::{Envelope, Principal};
use hypermid_sandbox::{FilesystemGrant, NetworkGrant, ResourceCeilings, SandboxLaunch};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use thiserror::Error;
use tokio::{sync::Mutex, time::Instant};

use crate::{
    cancellation::RouteKey,
    connection::{ConnectionFlow, ConnectionFlowError, TerminalDecision},
    flow::RouteFlowConfig,
    registry::{Registry, RegistryError},
    router::{RouteBinding, RouteError, Router},
    supervisor::{Supervisor, SupervisorError, SupervisorState},
};

#[derive(Clone, Debug)]
pub struct McpSandboxPolicy {
    pub executable: PathBuf,
    pub executable_sha256: String,
    pub arguments: Vec<OsString>,
    pub environment: BTreeMap<OsString, OsString>,
    pub working_directory: PathBuf,
    pub filesystem: Vec<FilesystemGrant>,
    pub network: NetworkGrant,
    pub ceilings: ResourceCeilings,
    pub maximum_lifetime: Duration,
}

#[derive(Clone, Debug)]
pub struct McpModuleConfig {
    pub module_id: String,
    pub server_name: String,
    pub route_operation: String,
    pub sandbox: McpSandboxPolicy,
    pub budgets: StdioBudgets,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct McpCallPayload {
    pub session_id: Id,
    pub tool: String,
    pub arguments: Value,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub effect_id: Option<Id>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub input_digest: Option<Digest>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct McpToolDescriptor {
    pub name: String,
    pub upstream_name: String,
    pub display_name: String,
    pub description: String,
    pub input_schema: Value,
    pub module_id: String,
    pub route_operation: String,
    pub effect: crate::manifest::EffectClass,
    pub requires_approval: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum McpRouteOutcome {
    Committed(Value),
    Unknown,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct McpModuleHealth {
    pub module_id: String,
    pub connected_sessions: usize,
    pub catalog_generation: u64,
    pub active_calls: usize,
    pub process_ready: bool,
}

#[derive(Clone, Debug, Eq, Hash, PartialEq)]
struct SessionKey {
    module_id: String,
    principal_id: Id,
    scope: Scope,
    session_id: Id,
}

struct SessionEntry {
    client: Arc<McpStdioClient>,
    tools: BTreeMap<String, ToolDefinition>,
    module_generation: u64,
}

struct ActiveCall {
    session: SessionKey,
    cancellation: Cancellation,
    mutation: bool,
    effect_id: Option<Id>,
}

pub struct McpRouteBridge {
    registry: Registry,
    router: Router,
    supervisor: Supervisor,
    flow: ConnectionFlow,
    effects: Arc<Mutex<DurableEffectLedger>>,
    configs: BTreeMap<String, McpModuleConfig>,
    sessions: Mutex<HashMap<SessionKey, Arc<SessionEntry>>>,
    active: Mutex<BTreeMap<Id, ActiveCall>>,
}

#[derive(Debug, Error)]
pub enum McpRouteError {
    #[error(transparent)]
    Registry(#[from] RegistryError),
    #[error(transparent)]
    Route(#[from] RouteError),
    #[error(transparent)]
    Supervisor(#[from] SupervisorError),
    #[error(transparent)]
    Flow(#[from] ConnectionFlowError),
    #[error(transparent)]
    Mcp(#[from] McpError),
    #[error(transparent)]
    Effect(#[from] BusError),
    #[error("MCP module configuration is invalid: {0}")]
    InvalidConfiguration(&'static str),
    #[error("MCP route payload is invalid")]
    InvalidPayload,
    #[error("MCP module is not ready")]
    ModuleNotReady,
    #[error("MCP tool is absent from the connected catalog")]
    UnknownTool,
    #[error("MCP tool namespace does not match its configured server")]
    ToolNamespace,
    #[error("mutating MCP call requires effect_id and input_digest")]
    MissingEffectIdentity,
    #[error("durable effect already has terminal or uncertain state: {0:?}")]
    ExistingEffect(EffectStatus),
}

impl McpRouteBridge {
    pub fn new(
        registry: Registry,
        router: Router,
        supervisor: Supervisor,
        flow: ConnectionFlow,
        effects: DurableEffectLedger,
        configs: Vec<McpModuleConfig>,
    ) -> Result<Self, McpRouteError> {
        let mut indexed = BTreeMap::new();
        for config in configs {
            validate_config(&config)?;
            if indexed.insert(config.module_id.clone(), config).is_some() {
                return Err(McpRouteError::InvalidConfiguration(
                    "module configuration is duplicated",
                ));
            }
        }
        Ok(Self {
            registry,
            router,
            supervisor,
            flow,
            effects: Arc::new(Mutex::new(effects)),
            configs: indexed,
            sessions: Mutex::new(HashMap::new()),
            active: Mutex::new(BTreeMap::new()),
        })
    }

    pub fn attach_route(
        &self,
        binding: &RouteBinding,
        config: RouteFlowConfig,
    ) -> Result<(), McpRouteError> {
        let module = self
            .configs
            .get(&binding.module_id)
            .ok_or(McpRouteError::ModuleNotReady)?;
        if binding.operation != module.route_operation {
            return Err(McpRouteError::ModuleNotReady);
        }
        self.flow.register_route(route_key(binding), config)?;
        Ok(())
    }

    pub async fn catalog(
        &self,
        principal: &Principal,
        binding: &RouteBinding,
        session_id: Id,
    ) -> Result<Vec<McpToolDescriptor>, McpRouteError> {
        self.ensure_ready(binding)?;
        let key = self.session_key(principal, binding, session_id)?;
        let session = self.connect(key).await?;
        let config = &self.configs[&binding.module_id];
        let effect = self
            .registry
            .resolve_operation(&binding.operation)?
            .operation
            .effect;
        Ok(session
            .tools
            .values()
            .map(|tool| McpToolDescriptor {
                name: canonical_tool_name(&config.server_name, &tool.name),
                upstream_name: tool.name.clone(),
                display_name: tool.name.clone(),
                description: tool.description.clone(),
                input_schema: tool.input_schema.clone(),
                module_id: binding.module_id.clone(),
                route_operation: binding.operation.clone(),
                effect,
                requires_approval: true,
            })
            .collect())
    }

    pub async fn invoke(
        &self,
        principal: &Principal,
        envelope: Envelope,
        now_ms: u64,
    ) -> Result<McpRouteOutcome, McpRouteError> {
        let routed = self.router.dispatch(principal, envelope, now_ms)?;
        let config = self
            .configs
            .get(&routed.binding.module_id)
            .ok_or(McpRouteError::ModuleNotReady)?;
        if routed.binding.operation != config.route_operation {
            return Err(McpRouteError::ModuleNotReady);
        }
        self.ensure_ready(&routed.binding)?;
        let payload: McpCallPayload = serde_json::from_value(
            routed
                .envelope
                .payload
                .clone()
                .ok_or(McpRouteError::InvalidPayload)?,
        )
        .map_err(|_| McpRouteError::InvalidPayload)?;
        let prefix = format!("mcp/{}/", config.server_name);
        let upstream = payload
            .tool
            .strip_prefix(&prefix)
            .filter(|name| !name.is_empty() && !name.contains('/'))
            .ok_or(McpRouteError::ToolNamespace)?;
        let session_key =
            self.session_key(principal, &routed.binding, payload.session_id.clone())?;
        let session = self.connect(session_key.clone()).await?;
        if !session.tools.contains_key(upstream) {
            return Err(McpRouteError::UnknownTool);
        }
        let operation = self.registry.resolve_operation(&routed.binding.operation)?;
        let mutation = !matches!(
            operation.operation.effect,
            crate::manifest::EffectClass::Query
        );
        if mutation
            && payload
                .effect_id
                .clone()
                .zip(payload.input_digest)
                .is_none()
        {
            return Err(McpRouteError::MissingEffectIdentity);
        }
        let request_id = routed.envelope.message_id.clone();
        let frame_bytes = serde_json::to_vec(&routed.envelope)
            .map_err(|_| McpRouteError::InvalidPayload)?
            .len() as u64;
        self.flow.admit_request(
            &routed.envelope,
            frame_bytes,
            now_ms,
            std::time::Instant::now(),
        )?;
        let cancellation = Cancellation::default();
        self.active.lock().await.insert(
            request_id.clone(),
            ActiveCall {
                session: session_key.clone(),
                cancellation: cancellation.clone(),
                mutation,
                effect_id: payload.effect_id.clone(),
            },
        );
        if mutation {
            let (effect_id, input_digest) = payload
                .effect_id
                .clone()
                .zip(payload.input_digest)
                .ok_or(McpRouteError::MissingEffectIdentity)?;
            let intent = BeginEffect {
                effect_id: effect_id.clone(),
                module_id: Id::new(routed.binding.module_id.clone())
                    .map_err(|_| McpRouteError::InvalidConfiguration("module id is invalid"))?,
                operation: payload.tool.clone(),
                principal: principal.clone(),
                scope: routed
                    .binding
                    .scope
                    .clone()
                    .ok_or(McpRouteError::InvalidPayload)?,
                input_digest,
                created_ms: now_ms,
            };
            let mut effects = self.effects.lock().await;
            if let BeginOutcome::Existing(record) = effects.begin(intent)? {
                self.active.lock().await.remove(&request_id);
                return match record.status {
                    EffectStatus::Committed { result, .. } => {
                        let _ = self
                            .flow
                            .settle(&request_id, hypermid_transport::TerminalKind::Response);
                        let value = serde_json::from_slice(&result)
                            .map_err(|_| McpRouteError::InvalidPayload)?;
                        Ok(McpRouteOutcome::Committed(value))
                    }
                    status => {
                        let _ = self
                            .flow
                            .settle(&request_id, hypermid_transport::TerminalKind::Error);
                        Err(McpRouteError::ExistingEffect(status))
                    }
                };
            }
            if cancellation.is_cancelled() {
                effects.reconcile(
                    &effect_id,
                    ReconciliationOutcome::NotStarted {
                        reason: "cancelled before MCP dispatch".into(),
                        settled_ms: now_ms,
                    },
                )?;
                self.active.lock().await.remove(&request_id);
                let _ = self.flow.cancel(&request_id);
                return Err(McpRouteError::Mcp(McpError::Cancelled));
            }
            effects.mark_dispatched(&effect_id)?;
        }
        let result = session
            .client
            .call_tool(upstream, payload.arguments, mutation, cancellation)
            .await;
        self.active.lock().await.remove(&request_id);
        match result {
            Ok(InvocationOutcome::Committed(value)) => {
                if let Some(effect_id) = payload.effect_id {
                    self.effects.lock().await.reconcile(
                        &effect_id,
                        ReconciliationOutcome::Committed {
                            result: serde_json::to_vec(&value)
                                .map_err(|_| McpRouteError::InvalidPayload)?,
                            settled_ms: now_ms,
                        },
                    )?;
                }
                let _ = self
                    .flow
                    .settle(&request_id, hypermid_transport::TerminalKind::Response);
                Ok(McpRouteOutcome::Committed(value))
            }
            Ok(InvocationOutcome::Unknown) => {
                if let Some(effect_id) = payload.effect_id {
                    self.effects.lock().await.reconcile(
                        &effect_id,
                        ReconciliationOutcome::Unknown {
                            reason:
                                "MCP call ended after request dispatch without a terminal receipt"
                                    .into(),
                            settled_ms: now_ms,
                        },
                    )?;
                }
                self.sessions.lock().await.remove(&session_key);
                let _ = self
                    .flow
                    .settle(&request_id, hypermid_transport::TerminalKind::Error);
                Ok(McpRouteOutcome::Unknown)
            }
            Err(error) => {
                if mutation {
                    if let Some(effect_id) = payload.effect_id {
                        let outcome = if matches!(error, McpError::Remote { .. }) {
                            ReconciliationOutcome::Committed {
                                result: serde_json::to_vec(&serde_json::json!({
                                    "error": error.to_string(),
                                }))
                                .map_err(|_| McpRouteError::InvalidPayload)?,
                                settled_ms: now_ms,
                            }
                        } else {
                            ReconciliationOutcome::Unknown {
                                reason: format!("MCP terminal receipt unavailable: {error}"),
                                settled_ms: now_ms,
                            }
                        };
                        self.effects.lock().await.reconcile(&effect_id, outcome)?;
                    }
                }
                if !matches!(error, McpError::Remote { .. }) {
                    self.sessions.lock().await.remove(&session_key);
                }
                let terminal = if matches!(error, McpError::Cancelled) {
                    hypermid_transport::TerminalKind::Cancelled
                } else if matches!(error, McpError::RequestTimeout) {
                    hypermid_transport::TerminalKind::TimedOut
                } else {
                    hypermid_transport::TerminalKind::Error
                };
                let _ = self.flow.settle(&request_id, terminal);
                Err(error.into())
            }
        }
    }

    pub async fn cancel_for_session(
        &self,
        principal: &Principal,
        session_id: &Id,
        envelope: Envelope,
        now_ms: u64,
    ) -> Result<TerminalDecision, McpRouteError> {
        let routed = self.router.dispatch(principal, envelope, now_ms)?;
        let target = routed
            .envelope
            .reply_to
            .as_ref()
            .ok_or(McpRouteError::InvalidPayload)?;
        let permitted = self
            .active
            .lock()
            .await
            .get(target)
            .filter(|active| {
                active.session.principal_id == principal.id
                    && active.session.session_id == *session_id
            })
            .map(|active| active.cancellation.clone());
        let Some(cancellation) = permitted else {
            return Ok(TerminalDecision::Unknown);
        };
        cancellation.cancel();
        Ok(self.flow.cancel(target)?)
    }

    pub async fn evict_session(
        &self,
        principal_id: &Id,
        session_id: &Id,
    ) -> Result<(), McpRouteError> {
        let call_ids = self
            .active
            .lock()
            .await
            .iter()
            .filter(|(_, call)| {
                &call.session.principal_id == principal_id && &call.session.session_id == session_id
            })
            .map(|(id, call)| {
                call.cancellation.cancel();
                id.clone()
            })
            .collect::<Vec<_>>();
        for id in call_ids {
            let _ = self.flow.cancel(&id);
        }
        let clients = {
            let mut sessions = self.sessions.lock().await;
            let keys = sessions
                .keys()
                .filter(|key| &key.principal_id == principal_id && &key.session_id == session_id)
                .cloned()
                .collect::<Vec<_>>();
            keys.into_iter()
                .filter_map(|key| sessions.remove(&key))
                .map(|session| Arc::clone(&session.client))
                .collect::<Vec<_>>()
        };
        for client in clients {
            client.close().await?;
        }
        for _ in 0..100 {
            let remains = self.active.lock().await.values().any(|call| {
                &call.session.principal_id == principal_id && &call.session.session_id == session_id
            });
            if !remains {
                return Ok(());
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        if self.active.lock().await.values().any(|call| {
            &call.session.principal_id == principal_id && &call.session.session_id == session_id
        }) {
            return Err(McpRouteError::ModuleNotReady);
        }
        Ok(())
    }

    pub async fn shutdown(&self, now_ms: u64) -> Result<(), McpRouteError> {
        let active = {
            let mut calls = self.active.lock().await;
            std::mem::take(&mut *calls)
                .into_iter()
                .map(|(id, call)| {
                    call.cancellation.cancel();
                    id
                })
                .collect::<Vec<_>>()
        };
        for id in active {
            let _ = self.flow.cancel(&id);
        }
        let sessions = std::mem::take(&mut *self.sessions.lock().await)
            .into_iter()
            .map(|(_, session)| Arc::clone(&session.client))
            .collect::<Vec<_>>();
        for client in sessions {
            client.close().await?;
        }
        let modules = self.configs.keys().cloned().collect::<BTreeSet<_>>();
        for module_id in &modules {
            self.supervisor.set_enabled(module_id, false, now_ms)?;
        }
        for step in 0..=300_u64 {
            self.supervisor.tick(now_ms.saturating_add(step * 10))?;
            if self
                .supervisor
                .snapshot()?
                .iter()
                .filter(|module| modules.contains(&module.module_id))
                .all(|module| module.pid.is_none())
            {
                return Ok(());
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        Err(McpRouteError::ModuleNotReady)
    }

    pub async fn health(&self) -> Result<Vec<McpModuleHealth>, McpRouteError> {
        let snapshots = self.supervisor.snapshot()?;
        let sessions = self.sessions.lock().await;
        let active = self.active.lock().await;
        Ok(self
            .configs
            .keys()
            .map(|module_id| {
                let process_ready = snapshots.iter().any(|snapshot| {
                    &snapshot.module_id == module_id && snapshot.state == SupervisorState::Ready
                });
                let connected_sessions = sessions
                    .keys()
                    .filter(|key| &key.module_id == module_id)
                    .count();
                let catalog_generation = sessions
                    .iter()
                    .filter(|(key, _)| &key.module_id == module_id)
                    .map(|(_, session)| session.module_generation)
                    .max()
                    .unwrap_or(0);
                let active_calls = active
                    .values()
                    .filter(|call| &call.session.module_id == module_id)
                    .count();
                McpModuleHealth {
                    module_id: module_id.clone(),
                    connected_sessions,
                    catalog_generation,
                    active_calls,
                    process_ready,
                }
            })
            .collect())
    }

    pub async fn health_for_session(
        &self,
        principal_id: &Id,
        session_id: &Id,
    ) -> Result<Vec<McpModuleHealth>, McpRouteError> {
        let snapshots = self.supervisor.snapshot()?;
        let sessions = self.sessions.lock().await;
        let active = self.active.lock().await;
        Ok(self
            .configs
            .keys()
            .map(|module_id| {
                let process_ready = snapshots.iter().any(|snapshot| {
                    &snapshot.module_id == module_id && snapshot.state == SupervisorState::Ready
                });
                let matching = |key: &SessionKey| {
                    &key.module_id == module_id
                        && &key.principal_id == principal_id
                        && &key.session_id == session_id
                };
                let connected_sessions = sessions.keys().filter(|key| matching(key)).count();
                let catalog_generation = sessions
                    .iter()
                    .filter(|(key, _)| matching(key))
                    .map(|(_, session)| session.module_generation)
                    .max()
                    .unwrap_or(0);
                let active_calls = active
                    .values()
                    .filter(|call| matching(&call.session))
                    .count();
                McpModuleHealth {
                    module_id: module_id.clone(),
                    connected_sessions,
                    catalog_generation,
                    active_calls,
                    process_ready,
                }
            })
            .collect())
    }

    pub async fn effect_status(&self, effect_id: &Id) -> Option<EffectStatus> {
        self.effects
            .lock()
            .await
            .status(effect_id)
            .map(|record| record.status.clone())
    }

    pub async fn effect_status_for(
        &self,
        principal: &Principal,
        scope: &Scope,
        effect_id: &Id,
    ) -> Option<EffectStatus> {
        self.effects
            .lock()
            .await
            .status(effect_id)
            .filter(|record| {
                record.intent.principal.id == principal.id && record.intent.scope == *scope
            })
            .map(|record| record.status.clone())
    }

    fn session_key(
        &self,
        principal: &Principal,
        binding: &RouteBinding,
        session_id: Id,
    ) -> Result<SessionKey, McpRouteError> {
        Ok(SessionKey {
            module_id: binding.module_id.clone(),
            principal_id: principal.id.clone(),
            scope: binding.scope.clone().ok_or(McpRouteError::InvalidPayload)?,
            session_id,
        })
    }

    async fn connect(&self, key: SessionKey) -> Result<Arc<SessionEntry>, McpRouteError> {
        if let Some(existing) = self.sessions.lock().await.get(&key).cloned() {
            return Ok(existing);
        }
        let config = self
            .configs
            .get(&key.module_id)
            .ok_or(McpRouteError::ModuleNotReady)?;
        let generation = self.ensure_module_ready_by_id(&key.module_id)?;
        let launch = sandbox_launch(&config.sandbox)?;
        let client = Arc::new(
            McpStdioClient::launch(launch, config.budgets, "hypermid-daemon", "0.1.0").await?,
        );
        let tools = match client.list_tools().await {
            Ok(tools) => tools,
            Err(error) => {
                let _ = client.close().await;
                return Err(error.into());
            }
        };
        let entry = Arc::new(SessionEntry {
            client,
            tools: tools
                .into_iter()
                .map(|tool| (tool.name.clone(), tool))
                .collect(),
            module_generation: generation,
        });
        let mut sessions = self.sessions.lock().await;
        if let Some(existing) = sessions.get(&key).cloned() {
            drop(sessions);
            entry.client.close().await?;
            return Ok(existing);
        }
        sessions.insert(key, Arc::clone(&entry));
        Ok(entry)
    }

    fn ensure_ready(&self, binding: &RouteBinding) -> Result<u64, McpRouteError> {
        let generation = self.ensure_module_ready_by_id(&binding.module_id)?;
        if generation != binding.spawn_generation {
            return Err(McpRouteError::ModuleNotReady);
        }
        Ok(generation)
    }

    fn ensure_module_ready_by_id(&self, module_id: &str) -> Result<u64, McpRouteError> {
        self.supervisor
            .snapshot()?
            .into_iter()
            .find(|module| module.module_id == module_id && module.state == SupervisorState::Ready)
            .map(|module| module.spawn_generation)
            .ok_or(McpRouteError::ModuleNotReady)
    }
}

fn route_key(binding: &RouteBinding) -> RouteKey {
    RouteKey {
        route_id: binding.route_id.clone(),
        route_epoch: binding.route_epoch,
    }
}

fn validate_config(config: &McpModuleConfig) -> Result<(), McpRouteError> {
    if Id::new(config.module_id.clone()).is_err()
        || config.server_name.is_empty()
        || config.server_name.len() > 80
        || config.server_name.bytes().any(|byte| {
            !(byte.is_ascii_lowercase() || byte.is_ascii_digit() || matches!(byte, b'-' | b'_'))
        })
        || config.route_operation.is_empty()
        || config.sandbox.maximum_lifetime.is_zero()
    {
        return Err(McpRouteError::InvalidConfiguration(
            "module id, server name, operation, or lifetime is invalid",
        ));
    }
    Ok(())
}

fn canonical_tool_name(server: &str, tool: &str) -> String {
    format!("mcp/{server}/{tool}")
}

fn sandbox_launch(policy: &McpSandboxPolicy) -> Result<SandboxLaunch, McpRouteError> {
    if policy.maximum_lifetime.is_zero() {
        return Err(McpRouteError::InvalidConfiguration(
            "sandbox lifetime is zero",
        ));
    }
    Ok(SandboxLaunch {
        executable: policy.executable.clone(),
        executable_sha256: policy.executable_sha256.clone(),
        arguments: policy.arguments.clone(),
        environment: policy.environment.clone(),
        working_directory: policy.working_directory.clone(),
        filesystem: policy.filesystem.clone(),
        network: policy.network,
        ceilings: policy.ceilings,
        deadline: Instant::now() + policy.maximum_lifetime,
    })
}
