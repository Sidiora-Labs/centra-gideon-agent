use std::{collections::BTreeMap, fs, thread, time::Duration};

use hypermid_contracts::Digest;
use hypermid_daemon::{
    child_journal::ChildJournal,
    containment::ProcessContainment,
    manifest::{OverlapPolicy, RestartMode, RestartPolicy},
    replacement::{ReplacementCoordinator, ReplacementOutcome},
    supervisor::{SupervisedModuleSpec, Supervisor, SupervisorConfig, SupervisorState},
};
use tempfile::TempDir;

fn shell_spec(module_id: &str, script: &str, overlap: OverlapPolicy) -> SupervisedModuleSpec {
    let executable = fs::canonicalize("/bin/sh").unwrap();
    SupervisedModuleSpec {
        module_id: module_id.into(),
        artifact_digest: Digest::sha256(fs::read(&executable).unwrap()),
        executable,
        arguments: vec!["-c".into(), script.into()],
        environment: BTreeMap::new(),
        restart: RestartPolicy {
            mode: RestartMode::OnFailure,
            max_restarts: 2,
            window_ms: 1_000,
            base_backoff_ms: 10,
            max_backoff_ms: 25,
            drain_timeout_ms: 20,
        },
        overlap,
    }
}

fn supervisor(root: &TempDir) -> Supervisor {
    Supervisor::new(
        ChildJournal::open(root.path().join("children.jsonl")).unwrap(),
        SupervisorConfig {
            stop_grace_ms: 20,
            ..SupervisorConfig::default()
        },
    )
}

fn stop(supervisor: &Supervisor, module_id: &str, now_ms: u64) {
    supervisor.set_enabled(module_id, false, now_ms).unwrap();
    for step in 0..20 {
        thread::sleep(Duration::from_millis(5));
        supervisor.tick(now_ms + 25 + step * 5).unwrap();
        if supervisor.snapshot().unwrap()[0].pid.is_none() {
            return;
        }
    }
    panic!("supervised child did not stop");
}

#[test]
fn real_crashes_back_off_and_retain_terminal_stderr() {
    let root = TempDir::new().unwrap();
    let supervisor = supervisor(&root);
    supervisor
        .configure(shell_spec(
            "crash-module",
            "echo crash-evidence >&2; exit 7",
            OverlapPolicy::Exclusive,
        ))
        .unwrap();
    supervisor.spawn("crash-module", 100).unwrap();
    thread::sleep(Duration::from_millis(20));
    assert!(supervisor.poll("crash-module", 110).unwrap());
    let first = supervisor.snapshot().unwrap().remove(0);
    assert_eq!(first.state, SupervisorState::Backoff);
    assert_eq!(first.restart_count, 1);
    let retry = first.next_retry_ms.unwrap();
    assert!((120..=135).contains(&retry));
    assert!(supervisor
        .stderr("crash-module")
        .unwrap()
        .iter()
        .any(|line| line.contains("crash-evidence")));

    supervisor.tick(retry).unwrap();
    thread::sleep(Duration::from_millis(20));
    supervisor.poll("crash-module", retry + 1).unwrap();
    let second = supervisor.snapshot().unwrap().remove(0);
    assert_eq!(second.state, SupervisorState::Backoff);
    assert_eq!(second.restart_count, 2);
    let retry = second.next_retry_ms.unwrap();
    supervisor.tick(retry).unwrap();
    thread::sleep(Duration::from_millis(20));
    supervisor.poll("crash-module", retry + 1).unwrap();
    assert_eq!(
        supervisor.snapshot().unwrap()[0].state,
        SupervisorState::Failed
    );
    assert_eq!(supervisor.terminals().unwrap().len(), 3);
}

#[test]
fn failed_warmup_keeps_incumbent_and_successful_cutover_retires_it() {
    let root = TempDir::new().unwrap();
    let supervisor = supervisor(&root);
    let incumbent = shell_spec(
        "replace-module",
        "trap 'exit 0' TERM; while :; do sleep 1; done",
        OverlapPolicy::Safe,
    );
    supervisor.configure(incumbent).unwrap();
    let incumbent_generation = supervisor.spawn("replace-module", 1_000).unwrap();
    supervisor
        .mark_ready("replace-module", incumbent_generation)
        .unwrap();
    let incumbent_pid = supervisor.snapshot().unwrap()[0].pid.unwrap();

    let failed = shell_spec("replace-module", "exit 9", OverlapPolicy::Safe);
    let failed_plan = ReplacementCoordinator::preview(&supervisor, &failed).unwrap();
    let failed_outcome =
        ReplacementCoordinator::apply(&supervisor, &failed_plan, failed, 1_100, 15).unwrap();
    assert!(matches!(failed_outcome, ReplacementOutcome::Refused { .. }));
    let still_serving = supervisor.snapshot().unwrap().remove(0);
    assert_eq!(still_serving.pid, Some(incumbent_pid));
    assert_eq!(still_serving.state, SupervisorState::Ready);

    let replacement = shell_spec(
        "replace-module",
        "trap 'exit 0' TERM; while :; do sleep 1; done",
        OverlapPolicy::Safe,
    );
    let plan = ReplacementCoordinator::preview(&supervisor, &replacement).unwrap();
    let outcome =
        ReplacementCoordinator::apply(&supervisor, &plan, replacement, 1_200, 15).unwrap();
    let ReplacementOutcome::Applied { generation } = outcome else {
        panic!("replacement was refused")
    };
    let active = supervisor.snapshot().unwrap().remove(0);
    assert_eq!(active.state, SupervisorState::Ready);
    assert_eq!(active.spawn_generation, generation);
    assert_ne!(active.pid, Some(incumbent_pid));
    stop(&supervisor, "replace-module", 1_300);
}

#[test]
fn stale_process_identity_is_never_signalled() {
    let containment = ProcessContainment::default();
    let mut identity = containment
        .capture(std::process::id(), Digest::sha256(b"test-process"))
        .unwrap();
    identity.start_identity.push_str("-reused");
    assert!(!containment.terminate_matching(&identity, true).unwrap());
}
