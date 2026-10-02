from __future__ import annotations

import subprocess
from pathlib import Path

from gideon.hypermid.file_index import (
    FileDecisionReason,
    FilePolicy,
    inspect_file,
    revalidate_for_publication as revalidate_file,
)
from gideon.hypermid.git_index import (
    cooldown_failure,
    revalidate_for_publication as revalidate_git,
    scan_repository,
)
from gideon.hypermid.message_index import (
    MessageIndexState,
    MessageSnapshot,
    prepare_message_documents,
)
from gideon.hypermid.models import Scope


def test_file_predicates_recheck_symlinks_digest_and_never_retain_rejected_content(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "private.txt").write_text("not authorized")
    (root / "escape.txt").symlink_to(outside / "private.txt")
    (root / ".env").write_text("TOKEN=secret")
    source = root / "notes.txt"
    source.write_text("approved material")
    policy = FilePolicy()

    escaped = inspect_file(root / "escape.txt", root, policy)
    secret = inspect_file(root / ".env", root, policy)
    accepted = inspect_file(source, root, policy)

    assert escaped.reason is FileDecisionReason.SYMLINK_ESCAPE
    assert secret.reason is FileDecisionReason.SECRET_POLICY
    assert escaped.content is None and secret.content is None
    assert accepted.allowed and accepted.content == "approved material"
    source.write_text("changed after scan")
    changed = revalidate_file(accepted)
    assert changed.reason is FileDecisionReason.CHANGED_DURING_READ
    assert changed.content is None


def test_message_dirty_floor_is_monotonic_and_reconciliation_covers_the_gap() -> None:
    state = MessageIndexState(cursor_sequence=10)
    state.record_failure(9)
    state.record_failure(4)
    state.record_failure(7)
    assert state.reconcile_from() == 4
    snapshots = [
        MessageSnapshot("session", "m5", 5, "fifth", 5),
        MessageSnapshot("session", "m4", 4, "fourth", 4),
    ]
    documents = prepare_message_documents(
        Scope("alice", "project"), snapshots, from_sequence=state.reconcile_from()
    )
    assert [document.metadata["sequence"] for document in documents] == [4, 5]
    try:
        state.publish_reconciliation(started_at=5, through_sequence=12)
    except ValueError as error:
        assert "earliest dirty" in str(error)
    else:
        raise AssertionError("a reconciliation gap advanced the cursor")
    state.publish_reconciliation(started_at=4, through_sequence=12)
    assert state.cursor_sequence == 12
    assert state.dirty_floor_sequence is None


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def test_git_scan_uses_real_repository_and_ref_publication_fence(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.email", "tests@example.invalid")
    _git(repository, "config", "user.name", "Hypermid Test")
    (repository / "record.txt").write_text("one")
    _git(repository, "add", "record.txt")
    _git(repository, "commit", "-q", "-m", "first memory index commit")

    scan = scan_repository(repository, ("HEAD",), max_commits=10)
    assert scan.documents[0].content == "first memory index commit"
    assert revalidate_git(scan)

    (repository / "record.txt").write_text("two")
    _git(repository, "add", "record.txt")
    _git(repository, "commit", "-q", "-m", "second memory index commit")
    assert not revalidate_git(scan)

    cooldown = cooldown_failure("not_repository", now_ms=100, cooldown_ms=500)
    assert cooldown.next_probe_at_ms == 600
