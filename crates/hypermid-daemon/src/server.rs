use std::{
    collections::HashMap,
    path::Path,
    sync::Arc,
    time::{SystemTime, UNIX_EPOCH},
};

use hypermid_context::durable_writer::DurableWriterAuthority;
use hypermid_contracts::{EffectState, Error, Id};
use hypermid_control::{ControlReply, MutationOutcome};
use hypermid_protocol::{
    AuthenticationMethod, ClientAuthentication, ClientHello, CompatibilityRefusal,
    ConnectionLimits, Envelope, MessageKind, Principal, ProtocolError, PROTOCOL,
};
use hypermid_transport::{
    read_json_frame, write_json_frame, FrameCodec, PeerEvidence, ServerHandshake,
    ServerHandshakeConfig, TransportError,
};
use serde_json::{json, Value};
use tokio::io::{AsyncRead, AsyncWrite};
use tokio::sync::Mutex as AsyncMutex;

use crate::{
    bus_routes::{BusRoutes, BUS_OPERATIONS},
    context_routes::{ContextRoutes, CONTEXT_OPERATIONS},
    control::{bootstrap_health, parse_request, ControlPlane},
    diagnostics::DiagnosticsStore,
    effect_routes::{EffectRoutes, EFFECT_OPERATIONS},
    enrollment::{install_local_operator_grant, LocalOperatorEnrollment},
    health::DurableStore,
    lifecycle_routes::{LifecycleRoutes, LIFECYCLE_OPERATIONS},
    mcp_control::{McpControl, MCP_CONTROL_OPERATIONS},
    memory_routes::{MemoryRoutes, MEMORY_OPERATIONS},
    operator_routes::{OperatorRoutes, OPERATOR_ROUTE_OPERATIONS},
    registry::Registry,
    router::Router,
    security_routes::{SecurityRoutes, SECURITY_OPERATIONS},
    startup::{RecoveryAuthority, StartupReconciler, StartupRecoveryReport, StartupRestoreReceipt},
    writer_routes::{WriterRoutes, WRITER_OPERATIONS},
};

const HANDSHAKE_TIMEOUT_MS: u64 = 5_000;
const CONTROL_ROUTE: &str = "control";
const RECOVERY_CONTROL_OPERATIONS: &[&str] =
    &["server.describe", "diagnostics.get", "operator.status"];
const RECOVERY_LIFECYCLE_OPERATIONS: &[&str] = &[
    "lifecycle.migrate.plan",
    "lifecycle.migrate.apply",
    "lifecycle.restore.plan",
    "lifecycle.restore.apply",
    "lifecycle.status",
    "lifecycle.recover",
    "lifecycle.resume",
];

#[derive(Clone, Debug)]
pub struct ConnectionAuthorization {
    pub principal: Principal,
    pub scope: hypermid_contracts::Scope,
    pub expires_at_ms: u64,
}

pub struct ServerState {
    daemon_instance_id: Id,
    started_ms: u64,
    control: ControlPlane,
    bus_routes: Arc<BusRoutes>,
    context_routes: Option<Arc<ContextRoutes>>,
    effect_routes: Arc<EffectRoutes>,
    lifecycle_routes: Arc<LifecycleRoutes>,
    memory_routes: Option<Arc<MemoryRoutes>>,
    operator_routes: Option<Arc<OperatorRoutes>>,
    recovery_authority: Arc<RecoveryAuthority>,
    security_routes: Option<Arc<SecurityRoutes>>,
    startup_recovery: StartupRecoveryReport,
    startup_restore: Option<StartupRestoreReceipt>,
    writer_authority: Option<Arc<DurableWriterAuthority>>,
    writer_routes: Option<Arc<WriterRoutes>>,
    mcp_control: Option<Arc<McpControl>>,
}

impl ServerState {
    pub fn new(
        daemon_instance_id: String,
        state_root: &Path,
        enrollment: &LocalOperatorEnrollment,
        mcp_configuration: Option<&Path>,
    ) -> Result<Self, DaemonError> {
        let daemon_instance_id = Id::new(daemon_instance_id)
            .map_err(|error| DaemonError::Configuration(error.to_string()))?;
        let started_ms = now_ms()?;
        let registry = Registry::empty();
        let router = Router::new(registry.clone());
        let memory_path = state_root.join("memory.sqlite3");
        let recovery_authority = Arc::new(RecoveryAuthority::new(state_root));
        let startup_restore_result = recovery_authority.recover_pending(started_ms);
        let startup_restore = startup_restore_result.as_ref().ok().cloned().flatten();

        let memory_existed = memory_path.exists();
        let mut startup_recovery = if memory_existed {
            StartupReconciler::run(&memory_path, started_ms)
                .map_err(|error| DaemonError::DurableStore(error.to_string()))?
        } else {
            let bootstrap =
                DurableStore::bootstrap(&memory_path).map_err(DaemonError::DurableStore)?;
            drop(bootstrap);
            StartupReconciler::run(&memory_path, started_ms)
                .map_err(|error| DaemonError::DurableStore(error.to_string()))?
        };
        if let Err(error) = &startup_restore_result {
            startup_recovery.require_read_only(
                "PENDING_RESTORE_FAILED",
                format!("pending restore requires operator review: {error}"),
            );
        }

        let durable_store = if startup_recovery.is_ready() {
            match DurableStore::bootstrap(&memory_path) {
                Ok(store) => Some(store),
                Err(error) => {
                    startup_recovery.require_read_only(
                        "MEMORY_BOOTSTRAP_FAILED",
                        format!("durable memory bootstrap requires recovery: {error}"),
                    );
                    None
                }
            }
        } else {
            None
        };
        let writable = durable_store.is_some() && startup_recovery.is_ready();
        if writable {
            install_local_operator_grant(&memory_path, enrollment)
                .map_err(|error| DaemonError::Enrollment(error.to_string()))?;
        }

        let lifecycle_routes = Arc::new(
            LifecycleRoutes::open(
                state_root.join("lifecycle"),
                startup_restore.clone(),
                Arc::clone(&recovery_authority),
            )
            .map_err(|error| {
                DaemonError::DurableStore(format!("{}: {}", error.code, error.message))
            })?,
        );
        let effect_routes = Arc::new(
            EffectRoutes::open(state_root.join("effects.journal"), started_ms)
                .map_err(|error| DaemonError::Bus(error.to_string()))?,
        );
        let (
            memory_routes,
            operator_routes,
            writer_authority,
            context_routes,
            writer_routes,
            security_routes,
        ) = if writable {
            let memory_routes = Arc::new(
                MemoryRoutes::open(&memory_path)
                    .map_err(|error| DaemonError::DurableStore(error.to_string()))?,
            );
            lifecycle_routes
                .attach_memory_api(memory_routes.api())
                .map_err(|error| {
                    DaemonError::DurableStore(format!("{}: {}", error.code, error.message))
                })?;
            let operator_routes = Arc::new(
                OperatorRoutes::open(state_root, &memory_path, memory_routes.api(), enrollment)
                    .map_err(DaemonError::DurableStore)?,
            );
            let writer_authority = Arc::new(
                DurableWriterAuthority::open(state_root.join("writer.sqlite3"))
                    .map_err(|error| DaemonError::DurableStore(error.to_string()))?,
            );
            let context_routes = Arc::new(ContextRoutes::open(
                state_root.join("context"),
                Arc::clone(&writer_authority),
                memory_routes.api(),
            )?);
            let writer_routes = Arc::new(WriterRoutes::new(Arc::clone(&writer_authority)));
            let security_routes = Arc::new(
                SecurityRoutes::open(state_root.join("security.sqlite3"), &memory_path)
                    .map_err(|error| DaemonError::DurableStore(error.to_string()))?,
            );
            (
                Some(memory_routes),
                Some(operator_routes),
                Some(writer_authority),
                Some(context_routes),
                Some(writer_routes),
                Some(security_routes),
            )
        } else {
            (None, None, None, None, None, None)
        };
        let bus_routes = Arc::new(
            BusRoutes::open(state_root.join("events.sqlite3"), 1, 10_000, 5, 1_024)
                .map_err(|error| DaemonError::Bus(error.to_string()))?,
        );
        let mcp_control = if writable {
            mcp_configuration
                .map(|configuration| McpControl::open(configuration, state_root, started_ms))
                .transpose()
                .map_err(|error| DaemonError::Mcp(error.to_string()))?
                .map(Arc::new)
        } else {
            None
        };
        let diagnostics = DiagnosticsStore::default();
        diagnostics.set_resources(json!({
            "startup_recovery": &startup_recovery,
            "startup_restore": &startup_restore,
        }));
        let control = match durable_store {
            Some(durable_store) if writable => ControlPlane::new(
                daemon_instance_id.clone(),
                started_ms,
                registry,
                router,
                bootstrap_health(started_ms),
                durable_store,
                diagnostics,
            ),
            _ => ControlPlane::new_recovery(
                daemon_instance_id.clone(),
                started_ms,
                registry,
                router,
                bootstrap_health(started_ms),
                diagnostics,
                serde_json::to_string(&startup_recovery.issues)
                    .unwrap_or_else(|_| "memory validation requires recovery".into()),
            ),
        };
        Ok(Self {
            daemon_instance_id,
            started_ms,
            control,
            bus_routes,
            context_routes,
            effect_routes,
            lifecycle_routes,
            memory_routes,
            operator_routes,
            recovery_authority,
            security_routes,
            startup_recovery,
            startup_restore,
            writer_authority,
            writer_routes,
            mcp_control,
        })
    }

    pub fn daemon_instance_id(&self) -> &Id {
        &self.daemon_instance_id
    }

    pub fn registry_generation(&self) -> u64 {
        self.control.registry_generation()
    }

    pub fn control(&self) -> &ControlPlane {
        &self.control
    }

    pub fn operations(&self) -> Vec<String> {
        if self.read_only_recovery() {
            let mut operations = RECOVERY_CONTROL_OPERATIONS
                .iter()
                .chain(RECOVERY_LIFECYCLE_OPERATIONS.iter())
                .map(|operation| (*operation).to_owned())
                .collect::<Vec<_>>();
            operations.sort();
            return operations;
        }
        let mut operations = ControlPlane::operations()
            .into_iter()
            .map(str::to_owned)
            .collect::<Vec<_>>();
        operations.extend(
            MEMORY_OPERATIONS
                .iter()
                .map(|operation| (*operation).to_owned()),
        );
        operations.extend(
            EFFECT_OPERATIONS
                .iter()
                .map(|operation| (*operation).to_owned()),
        );
        operations.extend(
            LIFECYCLE_OPERATIONS
                .iter()
                .map(|operation| (*operation).to_owned()),
        );
        operations.extend(
            OPERATOR_ROUTE_OPERATIONS
                .iter()
                .map(|operation| (*operation).to_owned()),
        );
        if self.mcp_control.is_some() {
            operations.extend(
                MCP_CONTROL_OPERATIONS
                    .iter()
                    .map(|operation| (*operation).to_owned()),
            );
        }
        operations.extend(
            CONTEXT_OPERATIONS
                .iter()
                .map(|operation| (*operation).to_owned()),
        );
        operations.extend(
            BUS_OPERATIONS
                .iter()
                .map(|operation| (*operation).to_owned()),
        );
        operations.extend(
            WRITER_OPERATIONS
                .iter()
                .map(|operation| (*operation).to_owned()),
        );
        operations.extend(
            SECURITY_OPERATIONS
                .iter()
                .map(|operation| (*operation).to_owned()),
        );
        operations.push("passthrough".into());
        operations.push("events.publish:*".into());
        operations.push("events.subscribe:*".into());
        operations.sort();
        operations.dedup();
        operations
    }

    pub fn writer_authority(&self) -> &Arc<DurableWriterAuthority> {
        self.writer_authority
            .as_ref()
            .expect("writer authority is available in writable mode")
    }

    pub fn security_routes(&self) -> &Arc<SecurityRoutes> {
        self.security_routes
            .as_ref()
            .expect("security routes are available in writable mode")
    }

    pub fn effect_routes(&self) -> &Arc<EffectRoutes> {
        &self.effect_routes
    }

    pub fn lifecycle_routes(&self) -> &Arc<LifecycleRoutes> {
        &self.lifecycle_routes
    }

    pub fn recovery_authority(&self) -> &Arc<RecoveryAuthority> {
        &self.recovery_authority
    }

    pub fn startup_recovery(&self) -> &StartupRecoveryReport {
        &self.startup_recovery
    }

    pub fn startup_restore(&self) -> Option<&StartupRestoreReceipt> {
        self.startup_restore.as_ref()
    }

    pub fn mcp_control(&self) -> Option<&Arc<McpControl>> {
        self.mcp_control.as_ref()
    }

    pub fn read_only_recovery(&self) -> bool {
        self.memory_routes.is_none()
    }
}

pub async fn serve_connection<S>(
    stream: S,
    state: Arc<ServerState>,
    secret: [u8; 32],
    peer: PeerEvidence,
    authorization: ConnectionAuthorization,
) -> Result<(), DaemonError>
where
    S: AsyncRead + AsyncWrite + Unpin + Send + 'static,
{
    serve_authorized(stream, state, secret, peer, authorization).await
}

async fn serve_authorized<S>(
    mut stream: S,
    state: Arc<ServerState>,
    secret: [u8; 32],
    peer: PeerEvidence,
    authorization: ConnectionAuthorization,
) -> Result<(), DaemonError>
where
    S: AsyncRead + AsyncWrite + Unpin + Send + 'static,
{
    let session = tokio::time::timeout(
        std::time::Duration::from_millis(HANDSHAKE_TIMEOUT_MS),
        authenticate(&mut stream, &state, &secret, peer, &authorization),
    )
    .await
    .map_err(|_| DaemonError::HandshakeTimeout)??;

    let (mut reader, writer) = tokio::io::split(stream);
    let mut read_codec = FrameCodec::<Envelope>::default();
    let writer = Arc::new(AsyncMutex::new((FrameCodec::<Envelope>::default(), writer)));
    let mut changes = state.bus_routes.subscribe_changes();
    let mut subscriptions = HashMap::<Id, Id>::new();
    let mut mcp_requests = tokio::task::JoinSet::new();
    let result = loop {
        let read = read_codec.read(&mut reader);
        tokio::pin!(read);
        let request = loop {
            let remaining = authorization.expires_at_ms.saturating_sub(now_ms()?);
            if remaining == 0 {
                break Err(DaemonError::AuthorizationExpired);
            }
            tokio::select! {
                request = &mut read => break request.map_err(DaemonError::from),
                _ = changes.recv() => {
                    push_bus_events(&state, &session, &writer).await?;
                }
                _ = tokio::time::sleep(std::time::Duration::from_millis(remaining)) => {
                    break Err(DaemonError::AuthorizationExpired);
                }
            }
        };
        let request = match request {
            Ok(request) => request,
            Err(DaemonError::Transport(TransportError::Io(error)))
                if matches!(
                    error.kind(),
                    std::io::ErrorKind::UnexpectedEof
                        | std::io::ErrorKind::ConnectionReset
                        | std::io::ErrorKind::BrokenPipe
                ) =>
            {
                break Ok(())
            }
            Err(error) => break Err(error),
        };
        if request.kind == MessageKind::Request
            && request.operation.as_deref() == Some("mcp.invoke")
            && state.mcp_control.is_some()
        {
            let state = Arc::clone(&state);
            let session = session.clone();
            let writer = Arc::clone(&writer);
            mcp_requests.spawn(async move {
                let response = handle_envelope(&state, &session, request).await;
                let _ = write_outbound(&writer, response).await;
            });
            continue;
        }
        let response = if request.kind == MessageKind::Cancel {
            cancel_request(&state, &session, &mut subscriptions, &request).await
        } else {
            handle_envelope(&state, &session, request.clone()).await
        };
        if request.operation.as_deref() == Some("events.subscribe") && response.error.is_none() {
            if let Some(consumer_id) = response
                .payload
                .as_ref()
                .and_then(|payload| payload.get("subscription_id"))
                .and_then(Value::as_str)
                .and_then(|value| Id::new(value).ok())
            {
                subscriptions.insert(request.message_id.clone(), consumer_id);
            }
        }
        if let Err(error) = write_outbound(&writer, response).await {
            break Err(error.into());
        }
        if let Err(error) = push_bus_events(&state, &session, &writer).await {
            break Err(error);
        }
    };
    let _ = state.bus_routes.close_session(&session.accepted.session_id);
    if let Some(mcp) = state.mcp_control() {
        let _ = mcp
            .evict(
                &session.accepted.principal,
                &session.bound_scope,
                &session.accepted.session_id,
            )
            .await;
    }
    while !mcp_requests.is_empty() {
        if tokio::time::timeout(std::time::Duration::from_secs(2), mcp_requests.join_next())
            .await
            .is_err()
        {
            mcp_requests.abort_all();
            break;
        }
    }
    result
}

async fn cancel_request(
    state: &ServerState,
    session: &hypermid_transport::AuthenticatedSession,
    subscriptions: &mut HashMap<Id, Id>,
    request: &Envelope,
) -> Envelope {
    let cancelled = request
        .reply_to
        .as_ref()
        .and_then(|message_id| subscriptions.remove(message_id))
        .and_then(|consumer_id| {
            state
                .bus_routes
                .cancel_subscription(&session.accepted.session_id, &consumer_id)
                .ok()
        })
        .unwrap_or(false);
    if cancelled {
        return response(
            request,
            Some(json!({"classification": "cancelled", "cancelled": true})),
            None,
        );
    }
    if let Some(mcp) = state.mcp_control() {
        let reply = mcp
            .cancel(
                &session.accepted.principal,
                &session.bound_scope,
                &session.accepted.session_id,
                request.clone(),
                now_ms().unwrap_or(0),
            )
            .await;
        return response(request, reply.payload, reply.error);
    }
    response(
        request,
        Some(json!({"classification": "not_started", "cancelled": false})),
        None,
    )
}

async fn push_bus_events<S>(
    state: &ServerState,
    session: &hypermid_transport::AuthenticatedSession,
    writer: &Arc<AsyncMutex<(FrameCodec<Envelope>, S)>>,
) -> Result<(), DaemonError>
where
    S: AsyncWrite + Unpin,
{
    for consumer_id in state
        .bus_routes
        .subscription_ids(&session.accepted.session_id)
        .map_err(|error| DaemonError::Bus(error.to_string()))?
    {
        if let Some(payload) = state
            .bus_routes
            .next_event(&session.accepted.session_id, &consumer_id, now_ms()?)
            .map_err(|error| DaemonError::Bus(error.to_string()))?
        {
            write_outbound(
                writer,
                Envelope {
                    protocol: PROTOCOL.into(),
                    kind: MessageKind::Event,
                    message_id: Id::new(random_id("event")?)
                        .expect("generated event ids satisfy the Id contract"),
                    sequence: 0,
                    reply_to: None,
                    route_id: Some(Id::new(CONTROL_ROUTE).expect("control route id is valid")),
                    route_epoch: Some(1),
                    operation: Some("events.deliver".into()),
                    scope: Some(session.bound_scope.clone()),
                    trace: None,
                    deadline_ms: None,
                    payload: Some(payload),
                    error: None,
                },
            )
            .await?;
        }
    }
    Ok(())
}

async fn write_outbound<S>(
    writer: &Arc<AsyncMutex<(FrameCodec<Envelope>, S)>>,
    envelope: Envelope,
) -> Result<(), TransportError>
where
    S: AsyncWrite + Unpin,
{
    let mut guard = writer.lock().await;
    let (codec, stream) = &mut *guard;
    codec.write(stream, envelope).await
}

async fn authenticate<S>(
    stream: &mut S,
    state: &ServerState,
    secret: &[u8],
    peer: PeerEvidence,
    authorization: &ConnectionAuthorization,
) -> Result<hypermid_transport::AuthenticatedSession, DaemonError>
where
    S: AsyncRead + AsyncWrite + Unpin,
{
    let hello: ClientHello = read_json_frame(stream).await?;
    if now_ms()? >= authorization.expires_at_ms {
        return Err(DaemonError::AuthorizationExpired);
    }
    let bound_scope = authorization.scope.clone();
    let principal = authorization.principal.clone();
    let config = ServerHandshakeConfig {
        daemon_instance_id: state.daemon_instance_id.clone(),
        authorized_scope: bound_scope,
        auth_method: AuthenticationMethod::HmacSha256,
        challenge_ttl_ms: HANDSHAKE_TIMEOUT_MS,
        limits: ConnectionLimits::default(),
    };
    let mut nonce = [0_u8; 32];
    getrandom::fill(&mut nonce).map_err(|error| DaemonError::Random(error.to_string()))?;
    let now = now_ms()?;
    let (handshake, challenge) = match ServerHandshake::challenge(&config, hello, nonce, now) {
        Ok(result) => result,
        Err(error @ TransportError::Protocol(ProtocolError::UnsupportedProtocol { .. })) => {
            write_json_frame(stream, &CompatibilityRefusal::unsupported_protocol()).await?;
            return Err(error.into());
        }
        Err(error) => return Err(error.into()),
    };
    write_json_frame(stream, &challenge).await?;
    let authentication: ClientAuthentication = read_json_frame(stream).await?;
    let session = handshake.authenticate(
        &config,
        authentication,
        secret,
        principal,
        Id::new(random_id("session")?)
            .map_err(|error| DaemonError::Configuration(error.to_string()))?,
        peer,
        now_ms()?,
    )?;
    write_json_frame(stream, &session.accepted).await?;
    Ok(session)
}

async fn handle_envelope(
    state: &ServerState,
    session: &hypermid_transport::AuthenticatedSession,
    request: Envelope,
) -> Envelope {
    match request.kind {
        MessageKind::Ping => response(
            &request,
            Some(json!({"server_time_ms": now_ms().ok()})),
            None,
        ),
        MessageKind::Cancel => response(
            &request,
            Some(json!({"classification": "not_started", "cancelled": false})),
            None,
        ),
        MessageKind::Request => handle_request(state, session, &request).await,
        _ => response(
            &request,
            None,
            Some(protocol_error(
                "INVALID_MESSAGE_KIND",
                "the daemon accepts request, cancel, and ping envelopes from clients",
                false,
                None,
            )),
        ),
    }
}

async fn handle_request(
    state: &ServerState,
    session: &hypermid_transport::AuthenticatedSession,
    request: &Envelope,
) -> Envelope {
    if request.route_id.as_ref().map(Id::as_str) != Some(CONTROL_ROUTE)
        || request.route_epoch != Some(1)
    {
        return response(
            request,
            None,
            Some(protocol_error(
                "STALE_ROUTE",
                "the bootstrap control route is control at epoch 1",
                true,
                None,
            )),
        );
    }
    let now = match now_ms() {
        Ok(now) => now,
        Err(error) => {
            return response(
                request,
                None,
                Some(protocol_error(
                    "CLOCK_UNAVAILABLE",
                    &error.to_string(),
                    true,
                    None,
                )),
            )
        }
    };
    if request.deadline_ms.is_some_and(|deadline| now > deadline) {
        return response(
            request,
            None,
            Some(protocol_error(
                "DEADLINE_EXCEEDED",
                "request deadline elapsed",
                false,
                None,
            )),
        );
    }
    let Some(operation) = request.operation.as_deref() else {
        return response(
            request,
            None,
            Some(protocol_error(
                "INVALID_REQUEST",
                "operation is required",
                false,
                None,
            )),
        );
    };
    if request.scope.as_ref() != Some(&session.bound_scope) {
        return response(
            request,
            None,
            Some(protocol_error(
                "SCOPE_DENIED",
                "request scope does not match the authenticated session scope",
                false,
                None,
            )),
        );
    }

    if state.read_only_recovery()
        && !RECOVERY_CONTROL_OPERATIONS.contains(&operation)
        && !RECOVERY_LIFECYCLE_OPERATIONS.contains(&operation)
    {
        return response(
            request,
            None,
            Some(protocol_error(
                "READ_ONLY_RECOVERY",
                "the durable memory store requires operator-reviewed recovery",
                false,
                Some(EffectState::NotStarted),
            )),
        );
    }

    if !session
        .accepted
        .principal
        .scopes
        .iter()
        .any(|scope| scope == operation)
    {
        return response(
            request,
            None,
            Some(protocol_error(
                "SCOPE_DENIED",
                "the authenticated principal lacks the operation scope",
                false,
                None,
            )),
        );
    }

    if operation == "passthrough" {
        return response(
            request,
            Some(json!({
                "mode": "passthrough",
                "payload": request.payload.clone().unwrap_or(Value::Null),
                "daemon_instance_id": state.daemon_instance_id,
            })),
            None,
        );
    }

    if MemoryRoutes::handles(operation) {
        let Some(routes) = state.memory_routes.as_ref() else {
            unreachable!("recovery-mode requests are refused before memory dispatch")
        };
        let reply = routes.dispatch(session, request, now);
        return response(request, reply.payload, reply.error);
    }
    if EffectRoutes::handles(operation) {
        let reply = state.effect_routes.dispatch(session, request, now);
        return response(request, reply.payload, reply.error);
    }
    if LifecycleRoutes::handles(operation) {
        let reply = state.lifecycle_routes.dispatch(session, request, now);
        return response(request, reply.payload, reply.error);
    }
    if CONTEXT_OPERATIONS.contains(&operation) {
        let Some(routes) = state.context_routes.as_ref() else {
            unreachable!("recovery-mode requests are refused before context dispatch")
        };
        let reply = routes.dispatch(session, request, now);
        return response(request, reply.payload, reply.error);
    }
    if BUS_OPERATIONS.contains(&operation) {
        return match state.bus_routes.execute(
            &session.accepted.session_id,
            &session.accepted.principal,
            &session.bound_scope,
            operation,
            request.payload.clone(),
        ) {
            Ok(payload) => response(request, Some(payload), None),
            Err(error) => response(
                request,
                error
                    .recovery_cursor()
                    .map(|cursor| json!({"recovery_cursor": cursor})),
                Some(protocol_error(
                    error.code(),
                    &error.to_string(),
                    false,
                    Some(EffectState::NotStarted),
                )),
            ),
        };
    }
    if WriterRoutes::handles(operation) {
        let Some(routes) = state.writer_routes.as_ref() else {
            unreachable!("recovery-mode requests are refused before writer dispatch")
        };
        let reply = routes.dispatch(session, request, now);
        return response(request, reply.payload, reply.error);
    }
    if SecurityRoutes::handles(operation) {
        let Some(routes) = state.security_routes.as_ref() else {
            unreachable!("recovery-mode requests are refused before security dispatch")
        };
        let reply = routes.dispatch(session, request, now);
        return response(request, reply.payload, reply.error);
    }
    if OperatorRoutes::handles(operation) {
        let Some(routes) = state.operator_routes.as_ref() else {
            unreachable!("recovery-mode requests are refused before operator dispatch")
        };
        let reply = routes.dispatch(session, request, now);
        return response(request, reply.payload, reply.error);
    }
    if McpControl::handles(operation) {
        let Some(mcp) = state.mcp_control() else {
            return response(
                request,
                None,
                Some(protocol_error(
                    "MCP_DISABLED",
                    "MCP control is not configured",
                    false,
                    Some(EffectState::NotStarted),
                )),
            );
        };
        if operation != "mcp.invoke" && operation != "mcp.effect.status" {
            let valid_empty = request
                .payload
                .as_ref()
                .and_then(Value::as_object)
                .is_some_and(serde_json::Map::is_empty);
            if !valid_empty {
                return response(
                    request,
                    None,
                    Some(protocol_error(
                        "INVALID_REQUEST",
                        "MCP control payload must be an empty object",
                        false,
                        Some(EffectState::NotStarted),
                    )),
                );
            }
        }
        let reply = match operation {
            "mcp.catalog" => {
                mcp.catalog(
                    &session.accepted.principal,
                    &session.bound_scope,
                    &session.accepted.session_id,
                )
                .await
            }
            "mcp.invoke" => {
                mcp.invoke(
                    &session.accepted.principal,
                    &session.bound_scope,
                    &session.accepted.session_id,
                    request,
                    now,
                )
                .await
            }
            "mcp.health" => {
                mcp.health(&session.accepted.principal, &session.accepted.session_id)
                    .await
            }
            "mcp.effect.status" => {
                mcp.effect_status(&session.accepted.principal, &session.bound_scope, request)
                    .await
            }
            "mcp.session.evict" => {
                mcp.evict(
                    &session.accepted.principal,
                    &session.bound_scope,
                    &session.accepted.session_id,
                )
                .await
            }
            _ => unreachable!("MCP operation was checked above"),
        };
        return response(request, reply.payload, reply.error);
    }

    let Some(scope) = request.scope.clone() else {
        return response(
            request,
            None,
            Some(protocol_error(
                "SCOPE_REQUIRED",
                "request scope is required",
                false,
                None,
            )),
        );
    };
    let control_request = match parse_request(
        request.message_id.clone(),
        operation,
        scope,
        request.payload.clone(),
    ) {
        Ok(request) => request,
        Err(error) => return response(request, None, Some(error)),
    };
    let reply = state.control.execute(
        &session.accepted.principal,
        &session.bound_scope,
        control_request,
        now,
    );
    response_from_control(state, request, reply)
}

fn response_from_control(state: &ServerState, request: &Envelope, reply: ControlReply) -> Envelope {
    let mut payload = reply.payload;
    if let Some(Value::Object(object)) = payload.as_mut() {
        object
            .entry("daemon_instance_id")
            .or_insert_with(|| json!(reply.generation.daemon_instance_id));
        object
            .entry("registry_generation")
            .or_insert_with(|| json!(reply.generation.registry_generation));
        if let Some(outcome) = reply.outcome {
            object
                .entry("outcome")
                .or_insert_with(|| json!(mutation_outcome_name(outcome)));
        }
        if request.operation.as_deref() == Some("server.describe") {
            object.insert("operations".into(), json!(state.operations()));
        }
    }
    response(request, payload, reply.error)
}

fn mutation_outcome_name(outcome: MutationOutcome) -> &'static str {
    match outcome {
        MutationOutcome::Applied => "applied",
        MutationOutcome::Refused => "refused",
        MutationOutcome::AlreadyApplied => "already_applied",
    }
}

fn response(request: &Envelope, payload: Option<Value>, error: Option<Error>) -> Envelope {
    Envelope {
        protocol: PROTOCOL.into(),
        kind: if matches!(request.kind, MessageKind::Ping) {
            MessageKind::Pong
        } else {
            MessageKind::Response
        },
        message_id: Id::new(
            random_id("response").unwrap_or_else(|_| "response-random-unavailable".into()),
        )
        .expect("generated response ids satisfy the Id contract"),
        sequence: 0,
        reply_to: Some(request.message_id.clone()),
        route_id: request.route_id.clone(),
        route_epoch: request.route_epoch,
        operation: request.operation.clone(),
        scope: request.scope.clone(),
        trace: request.trace.clone(),
        deadline_ms: None,
        payload,
        error,
    }
}

fn protocol_error(
    code: &str,
    message: &str,
    retryable: bool,
    effect_state: Option<EffectState>,
) -> Error {
    Error::new(code, message, retryable, None, effect_state)
        .expect("static daemon errors satisfy the shared contract")
}

fn random_id(prefix: &str) -> Result<String, DaemonError> {
    let mut bytes = [0_u8; 16];
    getrandom::fill(&mut bytes).map_err(|error| DaemonError::Random(error.to_string()))?;
    let mut value = String::with_capacity(prefix.len() + 33);
    value.push_str(prefix);
    value.push('-');
    for byte in bytes {
        use std::fmt::Write as _;
        write!(value, "{byte:02x}").expect("writing to String cannot fail");
    }
    Ok(value)
}

pub fn now_ms() -> Result<u64, DaemonError> {
    let elapsed = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| DaemonError::Clock(error.to_string()))?;
    u64::try_from(elapsed.as_millis())
        .map_err(|_| DaemonError::Clock("system time exceeds protocol range".into()))
}

#[derive(Debug, thiserror::Error)]
pub enum DaemonError {
    #[error(transparent)]
    Io(#[from] std::io::Error),
    #[error(transparent)]
    Transport(#[from] TransportError),
    #[error("secure random source failed: {0}")]
    Random(String),
    #[error("system clock failed: {0}")]
    Clock(String),
    #[error("durable store failed: {0}")]
    DurableStore(String),
    #[error("durable event bus failed: {0}")]
    Bus(String),
    #[error("MCP control failed: {0}")]
    Mcp(String),
    #[error("local operator enrollment failed: {0}")]
    Enrollment(String),
    #[error("authentication handshake timed out")]
    HandshakeTimeout,
    #[error("connection authorization expired")]
    AuthorizationExpired,
    #[error("invalid daemon configuration: {0}")]
    Configuration(String),
}

impl DaemonError {
    pub fn is_disconnect(&self) -> bool {
        matches!(
            self,
            Self::Transport(TransportError::Io(error)) | Self::Io(error)
                if matches!(
                    error.kind(),
                    std::io::ErrorKind::UnexpectedEof
                        | std::io::ErrorKind::ConnectionReset
                        | std::io::ErrorKind::BrokenPipe
                )
        )
    }
}
