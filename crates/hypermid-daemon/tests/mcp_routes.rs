use std::{
    collections::BTreeMap,
    ffi::OsString,
    fs,
    os::unix::fs::PermissionsExt,
    path::{Path, PathBuf},
    sync::Arc,
    time::Duration,
};

use hypermid_bus::{DurableEffectLedger, EffectStatus};
use hypermid_contracts::{Digest, Id, Scope};
use hypermid_daemon::{
    cancellation::RouteKey,
    child_journal::ChildJournal,
    connection::{ConnectionFlow, TerminalDecision},
    flow::RouteFlowConfig,
    manifest::{Operation, OverlapPolicy},
    mcp_routes::{
        McpCallPayload, McpModuleConfig, McpRouteBridge, McpRouteOutcome, McpSandboxPolicy,
    },
    registry::Registry,
    router::Router,
    supervisor::{SupervisedModuleSpec, Supervisor, SupervisorConfig},
};
use hypermid_mcp::StdioBudgets;
use hypermid_protocol::{Envelope, MessageKind, Principal, PrincipalKind, PROTOCOL};
use hypermid_sandbox::{FilesystemAccess, FilesystemGrant, NetworkGrant, ResourceCeilings};
use serde_json::json;
use sha2::{Digest as _, Sha256};
use tempfile::TempDir;
use tokio::time::{sleep, Instant};

fn digest(path: &Path) -> String {
    format!("{:x}", Sha256::digest(fs::read(path).unwrap()))
}

fn write_registry_module(root: &Path) -> PathBuf {
    let executable = root.join("module-host");
    fs::copy("/bin/sh", &executable).unwrap();
    fs::set_permissions(&executable, fs::Permissions::from_mode(0o755)).unwrap();
    let manifest = json!({
        "schema_version": 1,
        "module_id": "mcp-module",
        "version": "1.0.0",
        "executable": "module-host",
        "artifact_digest": digest(&executable),
        "protocol": PROTOCOL,
        "roles": ["operation_provider"],
        "operations": [{
            "name": "mcp.test.call",
            "effect": "durable",
            "remote": false,
            "required_scopes": ["tools.invoke"]
        }],
        "requires": [],
        "concurrency": "bounded",
        "max_concurrency": 2,
        "overlap": "exclusive",
        "restart": {
            "mode": "on_failure",
            "max_restarts": 1,
            "window_ms": 1000,
            "base_backoff_ms": 10,
            "max_backoff_ms": 20,
            "drain_timeout_ms": 20
        },
        "health": {
            "cadence_ms": 100,
            "deadline_ms": 50,
            "failure_threshold": 1,
            "action": "restart"
        }
    });
    fs::write(
        root.join("mcp-module.json"),
        serde_json::to_vec(&manifest).unwrap(),
    )
    .unwrap();
    executable
}

fn write_mcp_server(root: &Path) -> PathBuf {
    let script = root.join("server.py");
    fs::write(
        &script,
        r#"#!/usr/bin/python3
import json, pathlib, sys, time

def read():
    return json.loads(sys.stdin.readline())

def send(value):
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()

request = read()
send({"jsonrpc":"2.0","id":request["id"],"result":{"protocolVersion":"2025-06-18","capabilities":{"tools":{}},"serverInfo":{"name":"real-test-mcp","version":"1"}}})
read()
while True:
    request = read()
    if request.get("method") == "tools/list":
        send({"jsonrpc":"2.0","id":request["id"],"result":{"tools":[
            {"name":"echo","description":"Echo a value","inputSchema":{"type":"object"}},
            {"name":"slow_effect","description":"Commit then wait","inputSchema":{"type":"object"}}
        ]}})
    elif request.get("method") == "tools/call":
        name = request["params"]["name"]
        if name == "echo":
            send({"jsonrpc":"2.0","id":request["id"],"result":{"content":[{"type":"text","text":request["params"]["arguments"]["value"]}]}})
        else:
            pathlib.Path("effect-started").write_text("started\n")
            while True:
                time.sleep(1)
"#,
    )
    .unwrap();
    script
}

fn principal() -> Principal {
    Principal {
        id: Id::new("owner-principal").unwrap(),
        kind: PrincipalKind::LocalUser,
        scopes: vec!["tools.invoke".into()],
        module_id: None,
        spawn_generation: None,
    }
}

fn scope() -> Scope {
    Scope::new(Id::new("owner").unwrap(), Id::new("project").unwrap(), None)
}

fn request(
    route_id: &str,
    route_epoch: u64,
    message_id: &str,
    sequence: u64,
    tool: &str,
    effect_id: &str,
    value: &str,
) -> Envelope {
    let payload = McpCallPayload {
        session_id: Id::new("gideon-session").unwrap(),
        tool: format!("mcp/test/{tool}"),
        arguments: json!({"value": value}),
        effect_id: Some(Id::new(effect_id).unwrap()),
        input_digest: Some(Digest::sha256(value.as_bytes())),
    };
    Envelope {
        protocol: PROTOCOL.into(),
        kind: MessageKind::Request,
        message_id: Id::new(message_id).unwrap(),
        sequence,
        reply_to: None,
        route_id: Some(Id::new(route_id).unwrap()),
        route_epoch: Some(route_epoch),
        operation: Some("mcp.test.call".into()),
        scope: Some(scope()),
        trace: None,
        deadline_ms: Some(100_000),
        payload: Some(serde_json::to_value(payload).unwrap()),
        error: None,
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn real_mcp_route_invokes_and_cancels_with_unknown_effect() {
    let root = TempDir::new().unwrap();
    let executable = write_registry_module(root.path());
    let mcp_script = write_mcp_server(root.path());
    let registry = Registry::new(vec![root.path().to_path_buf()]).unwrap();
    registry.rescan().unwrap();
    let catalog: Vec<Operation> = registry.snapshot().unwrap().entries["mcp-module"]
        .manifest
        .manifest
        .operations
        .clone();
    let registry_generation = registry.prepare_spawn("mcp-module", &[3; 32]).unwrap();
    registry
        .authenticate_module("mcp-module", registry_generation, &[3; 32], &catalog)
        .unwrap();
    registry
        .mark_ready("mcp-module", registry_generation)
        .unwrap();

    let router = Router::new(registry.clone());
    let reservation = router
        .reserve_route(&principal(), "mcp.test.call", Some(scope()))
        .unwrap();
    let binding = router
        .acknowledge_bind(&reservation, "mcp-module", registry_generation)
        .unwrap();

    let supervisor = Supervisor::new(
        ChildJournal::open(root.path().join("children.jsonl")).unwrap(),
        SupervisorConfig {
            stop_grace_ms: 20,
            ..SupervisorConfig::default()
        },
    );
    supervisor
        .configure(SupervisedModuleSpec {
            module_id: "mcp-module".into(),
            executable: executable.clone(),
            arguments: vec![
                "-c".into(),
                "trap 'exit 0' TERM; while :; do sleep 1; done".into(),
            ],
            environment: BTreeMap::new(),
            artifact_digest: Digest::sha256(fs::read(&executable).unwrap()),
            restart: hypermid_daemon::manifest::RestartPolicy {
                mode: hypermid_daemon::manifest::RestartMode::OnFailure,
                max_restarts: 1,
                window_ms: 1_000,
                base_backoff_ms: 10,
                max_backoff_ms: 20,
                drain_timeout_ms: 20,
            },
            overlap: OverlapPolicy::Exclusive,
        })
        .unwrap();
    let supervisor_generation = supervisor.spawn("mcp-module", 1_000).unwrap();
    assert_eq!(supervisor_generation, registry_generation);
    supervisor
        .mark_ready("mcp-module", supervisor_generation)
        .unwrap();

    let flow = ConnectionFlow::new(8, 32).unwrap();
    let bridge = Arc::new(
        McpRouteBridge::new(
            registry,
            router,
            supervisor,
            flow,
            DurableEffectLedger::open(root.path().join("effects.jsonl"), 1_000).unwrap(),
            vec![McpModuleConfig {
                module_id: "mcp-module".into(),
                server_name: "test".into(),
                route_operation: "mcp.test.call".into(),
                sandbox: McpSandboxPolicy {
                    executable: PathBuf::from("/usr/bin/python3"),
                    executable_sha256: digest(Path::new("/usr/bin/python3")),
                    arguments: vec![OsString::from("/run/hypermid/grant-0/server.py")],
                    environment: BTreeMap::new(),
                    working_directory: root.path().to_path_buf(),
                    filesystem: vec![FilesystemGrant {
                        host_path: root.path().to_path_buf(),
                        access: FilesystemAccess::ReadWrite,
                    }],
                    network: NetworkGrant::Denied,
                    ceilings: ResourceCeilings::default(),
                    maximum_lifetime: Duration::from_secs(10),
                },
                budgets: StdioBudgets {
                    initialization: Duration::from_secs(2),
                    request: Duration::from_secs(2),
                    frame_bytes: 16 * 1024,
                    idle: Duration::from_secs(5),
                    shutdown: Duration::from_millis(100),
                    stderr_bytes: 4 * 1024,
                },
            }],
        )
        .unwrap(),
    );
    bridge
        .attach_route(
            &binding,
            RouteFlowConfig {
                request_credits: 2,
                byte_credits: 32 * 1024,
                max_queued_requests: 2,
                max_queued_bytes: 32 * 1024,
            },
        )
        .unwrap();

    let tools = bridge
        .catalog(&principal(), &binding, Id::new("gideon-session").unwrap())
        .await
        .unwrap();
    assert!(tools
        .iter()
        .any(|tool| tool.name == "mcp/test/echo" && tool.requires_approval));
    let committed = bridge
        .invoke(
            &principal(),
            request(
                &binding.route_id,
                binding.route_epoch,
                "request-echo",
                1,
                "echo",
                "effect-echo",
                "hello",
            ),
            2_000,
        )
        .await
        .unwrap();
    assert!(matches!(committed, McpRouteOutcome::Committed(_)));
    let connected = bridge.health().await.unwrap();
    assert_eq!(connected[0].connected_sessions, 1);
    assert_eq!(connected[0].active_calls, 0);
    bridge
        .evict_session(
            &Id::new("owner-principal").unwrap(),
            &Id::new("gideon-session").unwrap(),
        )
        .await
        .unwrap();
    let evicted = bridge.health().await.unwrap();
    assert_eq!(evicted[0].connected_sessions, 0);
    assert_eq!(evicted[0].active_calls, 0);

    let invoking = {
        let bridge = Arc::clone(&bridge);
        let binding = binding.clone();
        tokio::spawn(async move {
            bridge
                .invoke(
                    &principal(),
                    request(
                        &binding.route_id,
                        binding.route_epoch,
                        "request-slow",
                        2,
                        "slow_effect",
                        "effect-slow",
                        "mutation",
                    ),
                    3_000,
                )
                .await
        })
    };
    let marker_deadline = Instant::now() + Duration::from_secs(2);
    while !root.path().join("effect-started").is_file() {
        assert!(
            Instant::now() < marker_deadline,
            "real MCP effect did not begin"
        );
        sleep(Duration::from_millis(10)).await;
    }
    let cancelled = bridge
        .cancel_for_session(
            &principal(),
            &Id::new("gideon-session").unwrap(),
            Envelope {
                protocol: PROTOCOL.into(),
                kind: MessageKind::Cancel,
                message_id: Id::new("cancel-slow").unwrap(),
                sequence: 3,
                reply_to: Some(Id::new("request-slow").unwrap()),
                route_id: Some(Id::new(&binding.route_id).unwrap()),
                route_epoch: Some(binding.route_epoch),
                operation: None,
                scope: Some(scope()),
                trace: None,
                deadline_ms: None,
                payload: None,
                error: None,
            },
            3_001,
        )
        .await
        .unwrap();
    assert!(matches!(cancelled, TerminalDecision::Won(_)));
    assert_eq!(invoking.await.unwrap().unwrap(), McpRouteOutcome::Unknown);
    assert!(matches!(
        bridge.effect_status(&Id::new("effect-slow").unwrap()).await,
        Some(EffectStatus::Unknown { .. })
    ));
    assert_eq!(bridge.health().await.unwrap()[0].active_calls, 0);
    bridge.shutdown(4_000).await.unwrap();

    let route = RouteKey {
        route_id: binding.route_id,
        route_epoch: binding.route_epoch,
    };
    let _ = route;
    let _ = mcp_script;
}
