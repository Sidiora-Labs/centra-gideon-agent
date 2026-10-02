use hypermid_context::durable_writer::{
    DurableWriterAuthority, QuiescentJournalBarrier, WriterAuthorityKind, WriterReconciliation,
};
use hypermid_contracts::{Cursor, Digest, Id, Scope};
use std::process::Command;

const CHILD_PATH: &str = "HYPERMID_WRITER_CHILD_PATH";

fn id(value: &str) -> Id {
    Id::new(value).unwrap()
}

fn scope() -> Scope {
    Scope::new(id("owner-a"), id("project-a"), Some(id("workspace-a")))
}

fn barrier(value: &[u8], cursor: Cursor) -> QuiescentJournalBarrier {
    let digest = Digest::sha256(value);
    QuiescentJournalBarrier {
        quiescent: true,
        before_digest: digest,
        after_digest: digest,
        cursor,
    }
}

#[test]
fn child_process_cannot_open_live_authority() {
    let Some(path) = std::env::var_os(CHILD_PATH) else {
        return;
    };
    let error = match DurableWriterAuthority::open(path) {
        Ok(_) => panic!("a second process acquired the live writer authority"),
        Err(error) => error,
    };
    assert!(error.to_string().contains("another live writer"));
}

#[test]
fn two_writers_are_fenced_and_restart_preserves_cutover_and_monotonic_epochs() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("writer-authority.sqlite3");
    let authority = DurableWriterAuthority::open(&path).unwrap();
    let scope = scope();

    let first = authority.acquire(&scope, id("acquire-1"), 1).unwrap();
    assert_eq!(first.fence_epoch, 2);
    assert!(authority
        .acquire(&scope, id("acquire-contender"), 1)
        .is_err());

    let child = Command::new(std::env::current_exe().unwrap())
        .arg("--exact")
        .arg("child_process_cannot_open_live_authority")
        .arg("--nocapture")
        .env(CHILD_PATH, &path)
        .status()
        .unwrap();
    assert!(child.success());

    let cutover = authority
        .cutover(
            &scope,
            id("cutover-1"),
            &first,
            &barrier(b"journal-at-cutover", Cursor::new(1, 5).unwrap()),
        )
        .unwrap();
    assert_eq!(cutover.lease.cursor, Cursor::new(1, 5).unwrap());
    let validated = authority.validate(&scope, &cutover.lease, 10_000).unwrap();
    assert_eq!(validated.journal_digest, cutover.journal_digest);
    assert_eq!(
        authority.reconcile(&id("cutover-1")).unwrap(),
        Some(WriterReconciliation::Cutover(cutover.clone()))
    );

    drop(authority);
    let restarted = DurableWriterAuthority::open(&path).unwrap();
    let status = restarted.status(&scope).unwrap();
    assert_eq!(status.authority, WriterAuthorityKind::Hypermid);
    assert_eq!(status.active_lease, Some(cutover.lease.clone()));
    restarted.validate(&scope, &cutover.lease, 20_000).unwrap();

    let restored = restarted
        .restore(
            &scope,
            id("restore-1"),
            &cutover.lease,
            &barrier(b"journal-at-restore", Cursor::new(1, 6).unwrap()),
        )
        .unwrap();
    assert!(restored.gideon_epoch > cutover.lease.fence_epoch);
    assert_eq!(
        restarted.reconcile(&id("restore-1")).unwrap(),
        Some(WriterReconciliation::Restore(restored.clone()))
    );
    let status = restarted.status(&scope).unwrap();
    assert_eq!(status.authority, WriterAuthorityKind::Gideon);
    assert!(status.active_lease.is_none());

    let replacement = restarted.acquire(&scope, id("acquire-2"), 1).unwrap();
    assert!(replacement.fence_epoch > restored.gideon_epoch);
    assert!(restarted.validate(&scope, &cutover.lease, 30_000).is_err());
}
