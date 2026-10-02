use hypermid_contracts::{Id, Scope};
use hypermid_control::{ControlOperation, ControlRequest, MutationOutcome};
use hypermid_daemon::{
    control::{bootstrap_health, ControlPlane},
    diagnostics::{DiagnosticsStore, StderrRecord},
    health::DurableStore,
    registry::Registry,
    router::Router,
};
use hypermid_protocol::{Principal, PrincipalKind};
use serde_json::json;

fn id(value: &str) -> Id {
    Id::new(value).unwrap()
}

fn scope() -> Scope {
    Scope::new(id("owner-1"), id("project-1"), None)
}

fn request(operation: ControlOperation, message_id: &str) -> ControlRequest {
    ControlRequest {
        message_id: id(message_id),
        operation,
        scope: scope(),
        payload: json!({}),
    }
}

#[test]
fn live_store_health_redaction_and_mutation_receipts_are_reported() {
    let root = tempfile::tempdir().unwrap();
    let registry = Registry::empty();
    let diagnostics = DiagnosticsStore::default();
    diagnostics.register_secret("live-secret");
    diagnostics.record_stderr(StderrRecord {
        module_id: id("module-1"),
        at_ms: 100,
        line: "failure token=live-secret".into(),
        scope: Some(scope()),
    });
    let control = ControlPlane::new(
        id("daemon-1"),
        10,
        registry.clone(),
        Router::new(registry),
        bootstrap_health(10),
        DurableStore::bootstrap(root.path().join("memory.sqlite3")).unwrap(),
        diagnostics,
    );
    let principal = Principal {
        id: id("principal-1"),
        kind: PrincipalKind::LocalUser,
        scopes: ControlPlane::operations()
            .into_iter()
            .map(str::to_owned)
            .collect(),
        module_id: None,
        spawn_generation: None,
    };

    let description = control.execute(
        &principal,
        &scope(),
        request(ControlOperation::ServerDescribe, "describe-1"),
        100,
    );
    let description = description.payload.unwrap();
    assert_eq!(description["storage_version"], "1");
    assert_eq!(description["digest_health"], "healthy");
    assert_eq!(description["health"]["status"], "healthy");

    let diagnostic = control.execute(
        &principal,
        &scope(),
        request(ControlOperation::DiagnosticsGet, "diagnostic-1"),
        101,
    );
    let line = diagnostic.payload.unwrap()["stderr"][0]["line"]
        .as_str()
        .unwrap()
        .to_owned();
    assert!(!line.contains("live-secret"));
    assert!(line.contains("<redacted>"));

    let mut mutation = request(ControlOperation::RegistryRescan, "rescan-1");
    mutation.payload = json!({"mode": "apply"});
    let applied = control.execute(&principal, &scope(), mutation.clone(), 102);
    assert_eq!(applied.outcome, Some(MutationOutcome::Applied));
    let replayed = control.execute(&principal, &scope(), mutation, 103);
    assert_eq!(replayed.outcome, Some(MutationOutcome::AlreadyApplied));
}
