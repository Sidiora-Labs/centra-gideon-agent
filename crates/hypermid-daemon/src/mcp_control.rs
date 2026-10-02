use std::{
    collections::{BTreeMap, HashMap},
    ffi::OsString,
    path::{Path, PathBuf},
    sync::Arc,
    time::Duration,
};

use hypermid_bus::{DurableEffectLedger, EffectStatus};
use hypermid_contracts::{Digest, EffectState, Error, Id, Scope};
use hypermid_mcp::StdioBudgets;
use hypermid_protocol::{Envelope, Principal};
use hypermid_sandbox::{FilesystemAccess, FilesystemGrant, NetworkGrant, ResourceCeilings};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tokio::sync::Mutex;

use crate::{
    child_journal::ChildJournal,
    connection::{ConnectionFlow, TerminalDecision},
    flow::RouteFlowConfig,
    manifest::{EffectClass, ModuleRole},
    mcp_routes::{
        McpCallPayload, McpModuleConfig, McpRouteBridge, McpRouteError, McpRouteOutcome,
        McpSandboxPolicy, McpToolDescriptor,
    },
    registry::Registry,
    router::{RouteBinding, Router},
    supervisor::{SupervisedModuleSpec, Supervisor, SupervisorConfig},
};

pub const MCP_CONTROL_OPERATIONS: [&str; 5] = [
    "mcp.catalog",
    "mcp.invoke",
    "mcp.health",
    "mcp.effect.status",
    "mcp.session.evict",
];

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct StartupConfig {
    manifest_roots: Vec<PathBuf>,
    modules: Vec<StartupModule>,
    #[serde(default)]
    flow: StartupFlow,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct StartupModule {
    module_id: String,
    server_name: String,
    route_operation: String,
    sandbox: StartupSandbox,
    #[serde(default)]
    budgets: StartupBudgets,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct StartupSandbox {
    executable: PathBuf,
    executable_sha256: String,
    #[serde(default)]
    arguments: Vec<String>,
    working_directory: PathBuf,
    filesystem: Vec<StartupFilesystemGrant>,
    #[serde(default)]
    ceilings: StartupCeilings,
    maximum_lifetime_ms: u64,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct StartupFilesystemGrant {
    host_path: PathBuf,
    access: StartupFilesystemAccess,
}

#[derive(Clone, Copy, Debug, Deserialize)]
#[serde(rename_all = "snake_case")]
enum StartupFilesystemAccess {
    ReadOnly,
    ReadWrite,
}

#[derive(Clone, Copy, Debug, Deserialize)]
#[serde(default, deny_unknown_fields)]
struct StartupCeilings {
    address_space_bytes: u64,
    cpu_seconds: u64,
    file_bytes: u64,
    open_files: u64,
    processes: u64,
}

impl Default for StartupCeilings {
    fn default() -> Self {
        let value = ResourceCeilings::default();
        Self {
            address_space_bytes: value.address_space_bytes,
            cpu_seconds: value.cpu_seconds,
            file_bytes: value.file_bytes,
            open_files: value.open_files,
            processes: value.processes,
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize)]
#[serde(default, deny_unknown_fields)]
struct StartupBudgets {
    initialization_ms: u64,
    request_ms: u64,
    frame_bytes: usize,
    idle_ms: u64,
    shutdown_ms: u64,
    stderr_bytes: usize,
}

impl Default for StartupBudgets {
    fn default() -> Self {
        let value = StdioBudgets::default();
        Self {
            initialization_ms: value.initialization.as_millis() as u64,
            request_ms: value.request.as_millis() as u64,
            frame_bytes: value.frame_bytes,
            idle_ms: value.idle.as_millis() as u64,
            shutdown_ms: value.shutdown.as_millis() as u64,
            stderr_bytes: value.stderr_bytes,
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize)]
#[serde(default, deny_unknown_fields)]
struct StartupFlow {
    request_credits: u32,
    byte_credits: u64,
    max_queued_requests: usize,
    max_queued_bytes: u64,
}

impl Default for StartupFlow {
    fn default() -> Self {
        Self {
            request_credits: 8,
            byte_credits: 8 * 1024 * 1024,
            max_queued_requests: 32,
            max_queued_bytes: 8 * 1024 * 1024,
        }
    }
}

#[derive(Clone, Debug)]
struct RuntimeModule {
    module_id: String,
    server_name: String,
    route_operation: String,
    spawn_generation: u64,
    mutation: bool,
}

#[derive(Clone, Debug, Eq, Hash, PartialEq)]
struct SessionModuleKey {
    principal_id: Id,
    scope: Scope,
    session_id: Id,
    module_id: String,
}

#[derive(Clone)]
struct ActiveRoute {
    session: SessionModuleKey,
    binding: RouteBinding,
    internal_request_id: Id,
}

#[derive(Clone, Debug, Eq, Hash, PartialEq, Serialize)]
struct ActiveRequestKey {
    principal_id: Id,
    scope: Scope,
    session_id: Id,
    message_id: Id,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct InvokePayload {
    tool: String,
    arguments: Value,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    effect_id: Option<Id>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    input_digest: Option<Digest>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct EffectStatusPayload {
    effect_id: Id,
}

pub struct McpControlReply {
    pub payload: Option<Value>,
    pub error: Option<Error>,
}

pub struct McpControl {
    bridge: Arc<McpRouteBridge>,
    router: Router,
    modules: BTreeMap<String, RuntimeModule>,
    bindings: Mutex<HashMap<SessionModuleKey, RouteBinding>>,
    active_routes: Mutex<HashMap<ActiveRequestKey, ActiveRoute>>,
    flow: RouteFlowConfig,
}

#[derive(Debug, thiserror::Error)]
pub enum McpControlError {
    #[error("MCP startup configuration is invalid: {0}")]
    Configuration(String),
    #[error("MCP startup configuration I/O failed: {0}")]
    Io(#[from] std::io::Error),
    #[error("MCP startup configuration JSON is invalid: {0}")]
    Json(#[from] serde_json::Error),
    #[error(transparent)]
    Bridge(#[from] McpRouteError),
    #[error("MCP fabric startup failed: {0}")]
    Fabric(String),
}

impl McpControl {
    pub fn open(
        configuration: &Path,
        state_root: &Path,
        now_ms: u64,
    ) -> Result<Self, McpControlError> {
        ensure_private_regular_file(configuration)?;
        let config: StartupConfig = serde_json::from_slice(&std::fs::read(configuration)?)?;
        if config.manifest_roots.is_empty() || config.modules.is_empty() {
            return Err(McpControlError::Configuration(
                "manifest roots and modules must be nonempty".into(),
            ));
        }
        let registry = Registry::new(config.manifest_roots)
            .map_err(|error| McpControlError::Fabric(error.to_string()))?;
        registry
            .rescan()
            .map_err(|error| McpControlError::Fabric(error.to_string()))?;
        let router = Router::new(registry.clone());
        let supervisor = Supervisor::with_fabric(
            ChildJournal::open(state_root.join("mcp-children.jsonl"))
                .map_err(|error| McpControlError::Fabric(error.to_string()))?,
            SupervisorConfig::default(),
            Some(registry.clone()),
            Some(router.clone()),
        );
        let snapshot = registry
            .snapshot()
            .map_err(|error| McpControlError::Fabric(error.to_string()))?;
        let mut modules = BTreeMap::new();
        let mut bridge_configs = Vec::with_capacity(config.modules.len());
        for configured in config.modules {
            let entry = snapshot.entries.get(&configured.module_id).ok_or_else(|| {
                McpControlError::Configuration(format!(
                    "module {} is absent from the manifest registry",
                    configured.module_id
                ))
            })?;
            let manifest = &entry.manifest.manifest;
            if !manifest.roles.contains(&ModuleRole::OperationProvider)
                || !manifest.environment.is_empty()
            {
                return Err(McpControlError::Configuration(format!(
                    "module {} must be an operation provider with no manifest environment",
                    configured.module_id
                )));
            }
            let operation = manifest
                .operations
                .iter()
                .find(|operation| operation.name == configured.route_operation)
                .ok_or_else(|| {
                    McpControlError::Configuration(format!(
                        "module {} does not declare route operation {}",
                        configured.module_id, configured.route_operation
                    ))
                })?;
            if operation.remote || !operation.required_scopes.iter().any(|s| s == "mcp.invoke") {
                return Err(McpControlError::Configuration(format!(
                    "module {} route must be local and require mcp.invoke",
                    configured.module_id
                )));
            }
            if modules.contains_key(&configured.server_name) {
                return Err(McpControlError::Configuration(
                    "MCP server name is duplicated".into(),
                ));
            }
            supervisor
                .configure(SupervisedModuleSpec {
                    module_id: configured.module_id.clone(),
                    executable: entry.manifest.executable_path.clone(),
                    arguments: manifest.arguments.clone(),
                    environment: BTreeMap::new(),
                    artifact_digest: manifest.artifact_digest,
                    restart: manifest.restart.clone(),
                    overlap: manifest.overlap,
                })
                .map_err(|error| McpControlError::Fabric(error.to_string()))?;
            let mut launch_secret = [0_u8; 32];
            getrandom::fill(&mut launch_secret)
                .map_err(|error| McpControlError::Fabric(error.to_string()))?;
            let registry_generation = registry
                .prepare_spawn(&configured.module_id, &launch_secret)
                .map_err(|error| McpControlError::Fabric(error.to_string()))?;
            let supervisor_generation = supervisor
                .spawn(&configured.module_id, now_ms)
                .map_err(|error| McpControlError::Fabric(error.to_string()))?;
            if registry_generation != supervisor_generation {
                return Err(McpControlError::Fabric(
                    "registry and supervisor generations diverged".into(),
                ));
            }
            registry
                .authenticate_module(
                    &configured.module_id,
                    registry_generation,
                    &launch_secret,
                    &manifest.operations,
                )
                .map_err(|error| McpControlError::Fabric(error.to_string()))?;
            registry
                .mark_ready(&configured.module_id, registry_generation)
                .map_err(|error| McpControlError::Fabric(error.to_string()))?;
            supervisor
                .mark_ready(&configured.module_id, supervisor_generation)
                .map_err(|error| McpControlError::Fabric(error.to_string()))?;
            let runtime = RuntimeModule {
                module_id: configured.module_id.clone(),
                server_name: configured.server_name.clone(),
                route_operation: configured.route_operation.clone(),
                spawn_generation: registry_generation,
                mutation: operation.effect != EffectClass::Query,
            };
            bridge_configs.push(configured.into_bridge_config());
            modules.insert(runtime.server_name.clone(), runtime);
        }
        let flow = ConnectionFlow::new(64, 512)
            .map_err(|error| McpControlError::Fabric(error.to_string()))?;
        let bridge = Arc::new(McpRouteBridge::new(
            registry,
            router.clone(),
            supervisor,
            flow,
            DurableEffectLedger::open(state_root.join("mcp-effects.jsonl"), now_ms)
                .map_err(|error| McpControlError::Fabric(error.to_string()))?,
            bridge_configs,
        )?);
        Ok(Self {
            bridge,
            router,
            modules,
            bindings: Mutex::new(HashMap::new()),
            active_routes: Mutex::new(HashMap::new()),
            flow: config.flow.into(),
        })
    }

    pub fn handles(operation: &str) -> bool {
        MCP_CONTROL_OPERATIONS.contains(&operation)
    }

    pub async fn catalog(
        &self,
        principal: &Principal,
        scope: &Scope,
        session_id: &Id,
    ) -> McpControlReply {
        let mut tools = Vec::<McpToolDescriptor>::new();
        for module in self.modules.values() {
            let result = async {
                let binding = self.binding(principal, scope, session_id, module).await?;
                self.bridge
                    .catalog(principal, &binding, session_id.clone())
                    .await
            }
            .await;
            match result {
                Ok(mut catalog) => tools.append(&mut catalog),
                Err(error) => return failure("MCP_CATALOG_FAILED", error, EffectState::NotStarted),
            }
        }
        tools.sort_by(|left, right| left.name.cmp(&right.name));
        McpControlReply {
            payload: Some(json!({"tools": tools})),
            error: None,
        }
    }

    pub async fn invoke(
        &self,
        principal: &Principal,
        scope: &Scope,
        session_id: &Id,
        request: &Envelope,
        now_ms: u64,
    ) -> McpControlReply {
        let payload: InvokePayload = match request
            .payload
            .clone()
            .map(serde_json::from_value::<InvokePayload>)
        {
            Some(Ok(payload)) if payload.arguments.is_object() => payload,
            _ => {
                return failure_message(
                    "INVALID_REQUEST",
                    "MCP invoke payload is invalid",
                    EffectState::NotStarted,
                )
            }
        };
        let Some(module) = self.module_for_tool(&payload.tool) else {
            return failure_message(
                "UNKNOWN_TOOL",
                "MCP tool namespace is not configured",
                EffectState::NotStarted,
            );
        };
        if module.mutation != payload.effect_id.is_some()
            || module.mutation != payload.input_digest.is_some()
        {
            return failure_message(
                "INVALID_EFFECT_IDENTITY",
                "MCP effect identity does not match the operation effect class",
                EffectState::NotStarted,
            );
        }
        let binding = match self.binding(principal, scope, session_id, module).await {
            Ok(binding) => binding,
            Err(error) => return failure("MCP_ROUTE_FAILED", error, EffectState::NotStarted),
        };
        let key = session_key(principal, scope, session_id, module);
        let active_key = active_request_key(principal, scope, session_id, &request.message_id);
        let internal_request_id = Id::new(format!(
            "mcp-{}",
            Digest::sha256(
                serde_json::to_vec(&active_key)
                    .expect("MCP active request identity is serializable")
            )
        ))
        .expect("MCP internal request ids satisfy the Id contract");
        self.active_routes.lock().await.insert(
            active_key.clone(),
            ActiveRoute {
                session: key,
                binding: binding.clone(),
                internal_request_id: internal_request_id.clone(),
            },
        );
        let mut routed = request.clone();
        routed.message_id = internal_request_id;
        routed.route_id = Id::new(binding.route_id.clone()).ok();
        routed.route_epoch = Some(binding.route_epoch);
        routed.operation = Some(binding.operation.clone());
        routed.scope = binding.scope.clone();
        routed.payload = Some(
            serde_json::to_value(McpCallPayload {
                session_id: session_id.clone(),
                tool: payload.tool,
                arguments: payload.arguments,
                effect_id: payload.effect_id.clone(),
                input_digest: payload.input_digest,
            })
            .expect("MCP call payload is serializable"),
        );
        let result = self.bridge.invoke(principal, routed, now_ms).await;
        self.active_routes.lock().await.remove(&active_key);
        match result {
            Ok(McpRouteOutcome::Committed(result)) => McpControlReply {
                payload: Some(json!({"classification": "committed", "result": result})),
                error: None,
            },
            Ok(McpRouteOutcome::Unknown) => failure_message(
                "MCP_OUTCOME_UNKNOWN",
                "MCP mutation ended without an authoritative terminal receipt",
                EffectState::Unknown,
            ),
            Err(error) => {
                let state = match payload.effect_id.as_ref() {
                    Some(effect_id) => match self.bridge.effect_status(effect_id).await {
                        Some(EffectStatus::Committed { .. }) => EffectState::Committed,
                        Some(EffectStatus::NotStarted { .. }) => EffectState::NotStarted,
                        Some(EffectStatus::Intent) => EffectState::NotStarted,
                        Some(EffectStatus::Dispatched | EffectStatus::Unknown { .. }) => {
                            EffectState::Unknown
                        }
                        None => EffectState::NotStarted,
                    },
                    None => EffectState::NotStarted,
                };
                failure("MCP_INVOKE_FAILED", error, state)
            }
        }
    }

    pub async fn cancel(
        &self,
        principal: &Principal,
        scope: &Scope,
        session_id: &Id,
        request: Envelope,
        now_ms: u64,
    ) -> McpControlReply {
        let Some(target) = request.reply_to.as_ref() else {
            return failure_message(
                "INVALID_REQUEST",
                "cancel correlation id is required",
                EffectState::NotStarted,
            );
        };
        let key = active_request_key(principal, scope, session_id, target);
        let active = self.active_routes.lock().await.get(&key).cloned();
        let Some(active) = active else {
            return McpControlReply {
                payload: Some(json!({"classification": "not_started", "cancelled": false})),
                error: None,
            };
        };
        let mut routed = request;
        routed.route_id = Id::new(active.binding.route_id.clone()).ok();
        routed.route_epoch = Some(active.binding.route_epoch);
        routed.scope = active.binding.scope.clone();
        routed.reply_to = Some(active.internal_request_id);
        match self
            .bridge
            .cancel_for_session(principal, session_id, routed, now_ms)
            .await
        {
            Ok(TerminalDecision::Won(_)) => McpControlReply {
                payload: Some(json!({"classification": "cancelled", "cancelled": true})),
                error: None,
            },
            Ok(TerminalDecision::AlreadySettled(_)) => McpControlReply {
                payload: Some(json!({"classification": "already_settled", "cancelled": false})),
                error: None,
            },
            Ok(TerminalDecision::Unknown) => McpControlReply {
                payload: Some(json!({"classification": "not_started", "cancelled": false})),
                error: None,
            },
            Err(error) => failure("MCP_CANCEL_FAILED", error, EffectState::NotStarted),
        }
    }

    pub async fn health(&self, principal: &Principal, session_id: &Id) -> McpControlReply {
        match self
            .bridge
            .health_for_session(&principal.id, session_id)
            .await
        {
            Ok(modules) => McpControlReply {
                payload: Some(json!({"modules": modules})),
                error: None,
            },
            Err(error) => failure("MCP_HEALTH_FAILED", error, EffectState::NotStarted),
        }
    }

    pub async fn effect_status(
        &self,
        principal: &Principal,
        scope: &Scope,
        request: &Envelope,
    ) -> McpControlReply {
        let payload: EffectStatusPayload = match request.payload.clone().map(serde_json::from_value)
        {
            Some(Ok(payload)) => payload,
            _ => {
                return failure_message(
                    "INVALID_REQUEST",
                    "MCP effect status payload is invalid",
                    EffectState::NotStarted,
                )
            }
        };
        let Some(status) = self
            .bridge
            .effect_status_for(principal, scope, &payload.effect_id)
            .await
        else {
            return failure_message(
                "UNKNOWN_EFFECT",
                "MCP effect is absent from the authenticated principal and scope",
                EffectState::NotStarted,
            );
        };
        let state = match status {
            EffectStatus::Intent => "intent",
            EffectStatus::Dispatched => "dispatched",
            EffectStatus::Committed { .. } => "committed",
            EffectStatus::NotStarted { .. } => "not_started",
            EffectStatus::Unknown { .. } => "unknown",
        };
        McpControlReply {
            payload: Some(json!({"effect_id": payload.effect_id, "state": state})),
            error: None,
        }
    }

    pub async fn evict(
        &self,
        principal: &Principal,
        scope: &Scope,
        session_id: &Id,
    ) -> McpControlReply {
        if let Err(error) = self.bridge.evict_session(&principal.id, session_id).await {
            return failure("MCP_EVICTION_FAILED", error, EffectState::NotStarted);
        }
        let removed = {
            let mut bindings = self.bindings.lock().await;
            let keys = bindings
                .keys()
                .filter(|key| {
                    key.principal_id == principal.id
                        && key.scope == *scope
                        && key.session_id == *session_id
                })
                .cloned()
                .collect::<Vec<_>>();
            keys.into_iter()
                .filter_map(|key| bindings.remove(&key))
                .collect::<Vec<_>>()
        };
        for binding in removed {
            let _ = self
                .router
                .close_route(&binding.route_id, binding.route_epoch);
        }
        self.active_routes.lock().await.retain(|_, active| {
            active.session.principal_id != principal.id
                || active.session.scope != *scope
                || active.session.session_id != *session_id
        });
        match self
            .bridge
            .health_for_session(&principal.id, session_id)
            .await
        {
            Ok(modules)
                if modules
                    .iter()
                    .all(|module| module.connected_sessions == 0 && module.active_calls == 0) =>
            {
                McpControlReply {
                    payload: Some(json!({
                        "classification": "evicted",
                        "connected_sessions": 0,
                        "active_calls": 0,
                    })),
                    error: None,
                }
            }
            Ok(_) => failure_message(
                "MCP_EVICTION_INCOMPLETE",
                "MCP session still owns a pooled connection or active call",
                EffectState::Unknown,
            ),
            Err(error) => failure("MCP_EVICTION_FAILED", error, EffectState::Unknown),
        }
    }

    pub async fn shutdown(&self, now_ms: u64) -> Result<(), McpControlError> {
        let bindings = std::mem::take(&mut *self.bindings.lock().await);
        for binding in bindings.into_values() {
            let _ = self
                .router
                .close_route(&binding.route_id, binding.route_epoch);
        }
        let result = self.bridge.shutdown(now_ms).await;
        self.active_routes.lock().await.clear();
        result.map_err(McpControlError::from)
    }

    async fn binding(
        &self,
        principal: &Principal,
        scope: &Scope,
        session_id: &Id,
        module: &RuntimeModule,
    ) -> Result<RouteBinding, McpRouteError> {
        let key = session_key(principal, scope, session_id, module);
        if let Some(binding) = self.bindings.lock().await.get(&key).cloned() {
            return Ok(binding);
        }
        let reservation =
            self.router
                .reserve_route(principal, &module.route_operation, Some(scope.clone()))?;
        let binding = self.router.acknowledge_bind(
            &reservation,
            &module.module_id,
            module.spawn_generation,
        )?;
        self.bridge.attach_route(&binding, self.flow)?;
        let mut bindings = self.bindings.lock().await;
        if let Some(existing) = bindings.get(&key).cloned() {
            drop(bindings);
            let _ = self
                .router
                .close_route(&binding.route_id, binding.route_epoch);
            return Ok(existing);
        }
        bindings.insert(key, binding.clone());
        Ok(binding)
    }

    fn module_for_tool(&self, tool: &str) -> Option<&RuntimeModule> {
        let rest = tool.strip_prefix("mcp/")?;
        let (server, name) = rest.split_once('/')?;
        if name.is_empty() || name.contains('/') {
            return None;
        }
        self.modules.get(server)
    }
}

impl StartupModule {
    fn into_bridge_config(self) -> McpModuleConfig {
        McpModuleConfig {
            module_id: self.module_id,
            server_name: self.server_name,
            route_operation: self.route_operation,
            sandbox: McpSandboxPolicy {
                executable: self.sandbox.executable,
                executable_sha256: self.sandbox.executable_sha256,
                arguments: self
                    .sandbox
                    .arguments
                    .into_iter()
                    .map(OsString::from)
                    .collect(),
                environment: BTreeMap::new(),
                working_directory: self.sandbox.working_directory,
                filesystem: self
                    .sandbox
                    .filesystem
                    .into_iter()
                    .map(|grant| FilesystemGrant {
                        host_path: grant.host_path,
                        access: match grant.access {
                            StartupFilesystemAccess::ReadOnly => FilesystemAccess::ReadOnly,
                            StartupFilesystemAccess::ReadWrite => FilesystemAccess::ReadWrite,
                        },
                    })
                    .collect(),
                network: NetworkGrant::Denied,
                ceilings: ResourceCeilings {
                    address_space_bytes: self.sandbox.ceilings.address_space_bytes,
                    cpu_seconds: self.sandbox.ceilings.cpu_seconds,
                    file_bytes: self.sandbox.ceilings.file_bytes,
                    open_files: self.sandbox.ceilings.open_files,
                    processes: self.sandbox.ceilings.processes,
                },
                maximum_lifetime: Duration::from_millis(self.sandbox.maximum_lifetime_ms),
            },
            budgets: StdioBudgets {
                initialization: Duration::from_millis(self.budgets.initialization_ms),
                request: Duration::from_millis(self.budgets.request_ms),
                frame_bytes: self.budgets.frame_bytes,
                idle: Duration::from_millis(self.budgets.idle_ms),
                shutdown: Duration::from_millis(self.budgets.shutdown_ms),
                stderr_bytes: self.budgets.stderr_bytes,
            },
        }
    }
}

impl From<StartupFlow> for RouteFlowConfig {
    fn from(value: StartupFlow) -> Self {
        Self {
            request_credits: value.request_credits,
            byte_credits: value.byte_credits,
            max_queued_requests: value.max_queued_requests,
            max_queued_bytes: value.max_queued_bytes,
        }
    }
}

fn session_key(
    principal: &Principal,
    scope: &Scope,
    session_id: &Id,
    module: &RuntimeModule,
) -> SessionModuleKey {
    SessionModuleKey {
        principal_id: principal.id.clone(),
        scope: scope.clone(),
        session_id: session_id.clone(),
        module_id: module.module_id.clone(),
    }
}

fn active_request_key(
    principal: &Principal,
    scope: &Scope,
    session_id: &Id,
    message_id: &Id,
) -> ActiveRequestKey {
    ActiveRequestKey {
        principal_id: principal.id.clone(),
        scope: scope.clone(),
        session_id: session_id.clone(),
        message_id: message_id.clone(),
    }
}

fn failure(code: &str, error: impl std::fmt::Display, state: EffectState) -> McpControlReply {
    failure_message(code, &error.to_string(), state)
}

fn failure_message(code: &str, message: &str, state: EffectState) -> McpControlReply {
    McpControlReply {
        payload: None,
        error: Some(
            Error::new(code, message, false, None, Some(state))
                .expect("MCP control errors satisfy the shared contract"),
        ),
    }
}

fn ensure_private_regular_file(path: &Path) -> Result<(), McpControlError> {
    if !path.is_absolute() {
        return Err(McpControlError::Configuration(
            "configuration path must be absolute".into(),
        ));
    }
    let metadata = std::fs::symlink_metadata(path)?;
    if !metadata.file_type().is_file() {
        return Err(McpControlError::Configuration(
            "configuration must be a regular file".into(),
        ));
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::{MetadataExt, PermissionsExt};
        if metadata.uid() != unsafe { libc::geteuid() }
            || metadata.permissions().mode() & 0o777 != 0o600
        {
            return Err(McpControlError::Configuration(
                "configuration must be owned by the daemon user with mode 0600".into(),
            ));
        }
    }
    Ok(())
}
