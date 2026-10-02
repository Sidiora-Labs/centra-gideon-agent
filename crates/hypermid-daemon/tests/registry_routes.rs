use std::{fs, path::Path};

use hypermid_daemon::{
    manifest::Operation,
    registry::Registry,
    router::{RouteError, Router},
};
use hypermid_protocol::{Envelope, Id, MessageKind, Principal, PrincipalKind, PROTOCOL};
use serde_json::json;
use sha2::{Digest as _, Sha256};
use tempfile::TempDir;

fn write_module(root: &Path, module_id: &str, operation: &str) {
    let executable = root.join(format!("{module_id}.bin"));
    let bytes = format!("module executable for {module_id}\n");
    fs::write(&executable, bytes.as_bytes()).unwrap();
    let digest = format!("{:x}", Sha256::digest(bytes.as_bytes()));
    let manifest = json!({
        "schema_version": 1,
        "module_id": module_id,
        "version": "1.0.0",
        "executable": executable.file_name().unwrap().to_str().unwrap(),
        "artifact_digest": digest,
        "protocol": PROTOCOL,
        "roles": ["operation_provider"],
        "operations": [{
            "name": operation,
            "effect": "query",
            "remote": false,
            "required_scopes": ["invoke.tools"]
        }],
        "requires": [],
        "concurrency": "bounded",
        "max_concurrency": 2,
        "overlap": "safe",
        "restart": {
            "mode": "on_failure",
            "max_restarts": 3,
            "window_ms": 10000,
            "base_backoff_ms": 10,
            "max_backoff_ms": 1000,
            "drain_timeout_ms": 500
        },
        "health": {
            "cadence_ms": 1000,
            "deadline_ms": 100,
            "failure_threshold": 2,
            "action": "restart"
        }
    });
    fs::write(
        root.join(format!("{module_id}.json")),
        serde_json::to_vec_pretty(&manifest).unwrap(),
    )
    .unwrap();
}

fn ready_registry(root: &TempDir) -> (Registry, Vec<Operation>, u64) {
    write_module(root.path(), "module-a", "tool.run");
    let registry = Registry::new(vec![root.path().to_path_buf()]).unwrap();
    assert_eq!(registry.rescan().unwrap(), 1);
    let catalog = registry.snapshot().unwrap().entries["module-a"]
        .manifest
        .manifest
        .operations
        .clone();
    let generation = registry.prepare_spawn("module-a", &[7; 32]).unwrap();
    registry
        .authenticate_module("module-a", generation, &[7; 32], &catalog)
        .unwrap();
    registry.mark_ready("module-a", generation).unwrap();
    (registry, catalog, generation)
}

fn principal() -> Principal {
    Principal {
        id: Id::new("owner").unwrap(),
        kind: PrincipalKind::LocalUser,
        scopes: vec!["invoke.tools".into()],
        module_id: None,
        spawn_generation: None,
    }
}

fn request(route_id: &str, epoch: u64, message_id: &str) -> Envelope {
    Envelope {
        protocol: PROTOCOL.into(),
        kind: MessageKind::Request,
        message_id: Id::new(message_id).unwrap(),
        sequence: 1,
        reply_to: None,
        route_id: Some(Id::new(route_id).unwrap()),
        route_epoch: Some(epoch),
        operation: Some("tool.run".into()),
        scope: None,
        trace: None,
        deadline_ms: Some(10_000),
        payload: Some(json!({"input": "real"})),
        error: None,
    }
}

#[test]
fn registry_validation_is_atomic_and_launch_proof_is_one_use() {
    let root = TempDir::new().unwrap();
    let (registry, catalog, generation) = ready_registry(&root);
    assert!(registry
        .authenticate_module("module-a", generation, &[7; 32], &catalog)
        .is_err());

    let invalid = root.path().join("broken.json");
    let source = fs::read_to_string(root.path().join("module-a.json")).unwrap();
    let mut value: serde_json::Value = serde_json::from_str(&source).unwrap();
    value["module_id"] = json!("module-b");
    value["artifact_digest"] = json!("00".repeat(32));
    fs::write(&invalid, serde_json::to_vec(&value).unwrap()).unwrap();
    assert!(registry.preview_rescan().is_err());
    assert_eq!(registry.snapshot().unwrap().generation, 1);
    assert_eq!(
        registry.resolve_operation("tool.run").unwrap().module_id,
        "module-a"
    );
}

#[test]
fn route_visibility_and_reuse_are_fenced_by_bind_ack_and_epoch() {
    let root = TempDir::new().unwrap();
    let (registry, _, spawn_generation) = ready_registry(&root);
    let router = Router::new(registry);
    let first = router
        .reserve_route(&principal(), "tool.run", None)
        .unwrap();
    assert!(router.snapshot().unwrap().routes.is_empty());
    let first_binding = router
        .acknowledge_bind(&first, "module-a", spawn_generation)
        .unwrap();
    router
        .dispatch(
            &principal(),
            request(
                &first_binding.route_id,
                first_binding.route_epoch,
                "request-1",
            ),
            1,
        )
        .unwrap();
    router
        .close_route(&first_binding.route_id, first_binding.route_epoch)
        .unwrap();

    let second = router
        .reserve_route(&principal(), "tool.run", None)
        .unwrap();
    assert_eq!(second.route_id, first_binding.route_id);
    assert!(second.route_epoch > first_binding.route_epoch);
    router
        .acknowledge_bind(&second, "module-a", spawn_generation)
        .unwrap();

    let stale = router.dispatch(
        &principal(),
        request(
            &first_binding.route_id,
            first_binding.route_epoch,
            "request-stale",
        ),
        1,
    );
    assert!(matches!(stale, Err(RouteError::StaleEpoch)));
}
