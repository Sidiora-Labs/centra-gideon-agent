from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest

from checks.hypermid.evidence import ObservationWriter
from gideon.hypermid.client import HypermidClient, HypermidRemoteError
from gideon.hypermid.diagnostics import MemoryRecoveryDiagnostics
from gideon.hypermid.foundation import Cursor, Digest, Id, Scope
from gideon.hypermid.history import (
    ContextPart,
    HistoryError,
    HistoryJournal,
    PartKind,
    PendingContextItem,
    RawSourceJournal,
    Role,
)
from gideon.hypermid.recovery import (
    MemoryRestoreReceipt,
    MemorySnapshotReceipt,
    RecoveryError,
    rebuild_from_journal,
)


ROOT = Path(__file__).resolve().parents[2]


def _daemon_binary() -> Path:
    configured = os.environ.get("HYPERMID_DAEMON_BINARY")
    if configured:
        binary = Path(configured)
        if binary.is_file() and os.access(binary, os.X_OK):
            return binary.resolve()
        raise AssertionError("HYPERMID_DAEMON_BINARY is not an executable file")
    target = Path(os.environ.get("CARGO_TARGET_DIR", ROOT / "target"))
    binary = target / "debug" / "hypermid-daemon"
    if binary.is_file() and os.access(binary, os.X_OK):
        return binary.resolve()
    raise AssertionError("raw recovery observation requires a built hypermid-daemon")


def _start_daemon(root: Path, scope: Scope) -> tuple[subprocess.Popen[str], Path]:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    socket = root / "hypermid.sock"
    record = root / "connection.json"
    socket.unlink(missing_ok=True)
    record.unlink(missing_ok=True)
    arguments = [
        str(_daemon_binary()),
        "--socket",
        str(socket),
        "--connection-record",
        str(record),
        "--local-credential-id",
        "raw-recovery-credential",
        "--local-owner-id",
        str(scope.owner_id),
        "--local-project-id",
        str(scope.project_id),
        "--local-capability-id",
        "raw-recovery-capability",
        "--local-capability-operation",
        "read",
        "--local-capability-resource",
        "memory",
        "--local-capability-expires-ms",
        str(time.time_ns() // 1_000_000 + 300_000),
    ]
    process = subprocess.Popen(
        arguments,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 10
    while not record.is_file():
        if process.poll() is not None:
            stderr = process.stderr.read() if process.stderr is not None else ""
            raise AssertionError(
                f"hypermid-daemon exited before readiness ({process.returncode}): {stderr}"
            )
        if time.monotonic() >= deadline:
            process.kill()
            process.wait(timeout=5)
            raise AssertionError("hypermid-daemon connection record timed out")
        time.sleep(0.01)
    return process, record


def _stop_daemon(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


async def _describe_future_store(record: Path, scope: Scope) -> dict[str, object]:
    async with HypermidClient(record, scope=scope) as client:
        described = await client.describe()
        assert isinstance(described, dict)
        try:
            await client.request("memory.health", {})
        except HypermidRemoteError as error:
            blocked_code = error.error.code
            blocked_effect_state = (
                error.error.effect_state.value
                if error.error.effect_state is not None
                else None
            )
        else:
            raise AssertionError("future memory store accepted a normal operation")
    return {
        "recovery_mode": described.get("recovery_mode"),
        "blocked_code": blocked_code,
        "blocked_effect_state": blocked_effect_state,
    }


def _append_history_item(
    journal: HistoryJournal,
    raw: RawSourceJournal,
    *,
    scope: Scope,
    session_id: Id,
    sequence: int,
) -> None:
    event_id = Id(f"raw-event-{sequence}")
    source = json.dumps(
        {"sequence": sequence, "text": f"recover-{sequence}"},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    raw.append(scope, session_id, event_id, source)
    journal.append(
        expected_cursor=Cursor(1, sequence - 1),
        idempotency_key=Id(f"raw-append-{sequence}"),
        item=PendingContextItem(
            item_id=Id(f"raw-item-{sequence}"),
            source_event_id=event_id,
            source_digest=Digest.sha256(source),
            scope=scope,
            session_id=session_id,
            role=Role.USER,
            parts=(
                ContextPart(
                    part_id=Id(f"raw-part-{sequence}"),
                    kind=PartKind.TEXT,
                    content_digest=Digest.sha256(f"recover-{sequence}".encode()),
                    text=f"recover-{sequence}",
                ),
            ),
            relations=(),
            created_at=f"2026-10-02T12:00:0{sequence}Z",
            recoverable=True,
        ),
        source_snapshot=source,
    )


def _populate_raw_sources(
    path: Path, scope: Scope, session_id: Id
) -> RawSourceJournal:
    raw = RawSourceJournal(path)
    for sequence in (1, 2):
        source = json.dumps(
            {"sequence": sequence, "text": f"recover-{sequence}"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        raw.append(scope, session_id, Id(f"raw-event-{sequence}"), source)
    return raw


def _inject_source_damage(path: Path, *, orphan: bool) -> None:
    with sqlite3.connect(path) as connection:
        if orphan:
            connection.execute("DROP TRIGGER raw_source_events_no_delete")
            connection.execute(
                "DELETE FROM raw_source_events WHERE source_event_id=?",
                ("raw-event-2",),
            )
        else:
            connection.execute("DROP TRIGGER raw_source_events_no_update")
            connection.execute(
                "UPDATE raw_source_events SET source_bytes=? WHERE source_event_id=?",
                (b"altered-authoritative-source", "raw-event-1"),
            )


def _observe_raw_recovery(tmp_path: Path) -> dict[str, object]:
    scope = Scope(Id("raw-recovery-owner"), Id("raw-recovery-project"))
    session_id = Id("raw-recovery-session")
    raw_path = tmp_path / "raw.sqlite3"
    raw = RawSourceJournal(raw_path)
    journal = HistoryJournal(
        tmp_path / "history.sqlite3", scope=scope, session_id=session_id
    )
    for sequence in (1, 2):
        _append_history_item(
            journal,
            raw,
            scope=scope,
            session_id=session_id,
            sequence=sequence,
        )
    rebuilt = rebuild_from_journal(journal, raw)
    committed_ids = {str(item.item_id) for item in journal.all_items()}
    recovered_ids = {str(item.item.item_id) for item in rebuilt.items}
    accepted_digest_mismatches = sum(
        Digest.sha256(item.source_bytes) != item.item.source_digest
        for item in rebuilt.items
    )
    orphaned_committed_records = len(committed_ids - recovered_ids)

    corrupt_path = tmp_path / "raw-corrupt.sqlite3"
    corrupt = _populate_raw_sources(corrupt_path, scope, session_id)
    _inject_source_damage(corrupt_path, orphan=False)
    with pytest.raises(HistoryError) as mismatch:
        rebuild_from_journal(journal, corrupt)
    assert mismatch.value.code == "SOURCE_DIGEST_MISMATCH"

    orphan_path = tmp_path / "raw-orphan.sqlite3"
    orphan = _populate_raw_sources(orphan_path, scope, session_id)
    _inject_source_damage(orphan_path, orphan=True)
    with pytest.raises(HistoryError) as missing:
        rebuild_from_journal(journal, orphan)
    assert missing.value.code == "SOURCE_UNAVAILABLE"

    daemon_root = tmp_path / "future-daemon"
    process, _ = _start_daemon(daemon_root, scope)
    _stop_daemon(process)
    memory_path = daemon_root / "state" / "memory.sqlite3"
    assert memory_path.is_file()
    with sqlite3.connect(memory_path) as connection:
        current_version = connection.execute(
            "SELECT current_version FROM hypermid_schema_version WHERE singleton=1"
        ).fetchone()[0]
        future_version = current_version + 1
        connection.execute(
            "UPDATE hypermid_schema_version SET current_version=? WHERE singleton=1",
            (future_version,),
        )
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    before = hashlib.sha256(memory_path.read_bytes()).hexdigest()
    process, record = _start_daemon(daemon_root, scope)
    try:
        future = asyncio.run(_describe_future_store(record, scope))
    finally:
        _stop_daemon(process)
    after = hashlib.sha256(memory_path.read_bytes()).hexdigest()
    assert future == {
        "recovery_mode": True,
        "blocked_code": "READ_ONLY_RECOVERY",
        "blocked_effect_state": "not_started",
    }
    assert after == before

    return {
        "schema_version": 1,
        "gate": "raw_recovery",
        "scope": scope.to_wire(),
        "journal": {
            "cursor": rebuilt.cursor.to_wire(),
            "committed_records": len(committed_ids),
            "recovered_records": len(recovered_ids),
            "verified_source_digests": len(rebuilt.items),
            "accepted_digest_mismatches": accepted_digest_mismatches,
            "orphaned_committed_records": orphaned_committed_records,
        },
        "damage_probes": {
            "digest_mismatch_rejections": 1,
            "digest_mismatch_code": mismatch.value.code,
            "orphan_rejections": 1,
            "orphan_code": missing.value.code,
        },
        "future_store": {
            "schema_version": future_version,
            "read_only_recovery": future["recovery_mode"],
            "normal_operation_code": future["blocked_code"],
            "normal_operation_effect_state": future["blocked_effect_state"],
            "physical_digest_before": before,
            "physical_digest_after": after,
            "bytes_preserved": before == after,
        },
    }


def test_memory_recovery_receipts_and_diagnostics_are_strict_and_content_free() -> None:
    scope = Scope(Id("owner-recovery"), Id("project-recovery"))
    cursor = Cursor(1, 3)
    diagnostics = MemoryRecoveryDiagnostics(
        schema_version=1,
        compatibility_floor=1,
        schema_digest=Digest.sha256(b"schema"),
        scope=scope,
        cursor=cursor,
        authoritative_digest=Digest.sha256(b"authority"),
        record_count=2,
        revision_count=3,
        source_count=1,
        lineage_count=1,
        memory_fts_count=2,
        source_fts_count=0,
        embedding_count=0,
    )
    wire = diagnostics.to_wire()
    assert MemoryRecoveryDiagnostics.from_wire(wire) == diagnostics
    assert not ({"content", "credential", "secret", "token"} & set(wire))

    snapshot = MemorySnapshotReceipt(
        scope,
        cursor,
        Digest.sha256(b"manifest"),
        Digest.sha256(b"image"),
        4096,
    )
    assert MemorySnapshotReceipt.from_mapping(snapshot.to_mapping()) == snapshot
    restore = MemoryRestoreReceipt(
        scope,
        cursor,
        snapshot.manifest_digest,
        Digest.sha256(b"active"),
        4096,
    )
    assert MemoryRestoreReceipt.from_mapping(restore.to_mapping()) == restore

    invalid = snapshot.to_mapping()
    invalid["content"] = "must never appear in diagnostics"
    with pytest.raises(RecoveryError, match="not canonical"):
        MemorySnapshotReceipt.from_mapping(invalid)


def test_raw_recovery_observes_verified_sources_orphans_and_future_store(
    tmp_path: Path,
) -> None:
    manifest = _observe_raw_recovery(tmp_path)
    journal = manifest["journal"]
    probes = manifest["damage_probes"]
    future = manifest["future_store"]
    assert isinstance(journal, dict)
    assert isinstance(probes, dict)
    assert isinstance(future, dict)
    assert journal["committed_records"] == journal["recovered_records"] == 2
    assert journal["verified_source_digests"] == 2
    assert journal["accepted_digest_mismatches"] == 0
    assert journal["orphaned_committed_records"] == 0
    assert probes["digest_mismatch_rejections"] == 1
    assert probes["orphan_rejections"] == 1
    assert future["read_only_recovery"] is True
    assert future["bytes_preserved"] is True

    manifest_path = tmp_path / "raw-recovery-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    writer = ObservationWriter.from_env("raw_recovery")
    if writer is not None:
        writer.measure(
            "raw-digest-mismatches",
            journal["accepted_digest_mismatches"],
            "eq",
            0,
            "mismatches",
        )
        writer.measure(
            "orphaned-committed-records",
            journal["orphaned_committed_records"],
            "eq",
            0,
            "records",
        )
        writer.measure(
            "future-state-read-only",
            future["read_only_recovery"],
            "eq",
            True,
            "boolean",
        )
        writer.artifact(
            "raw-recovery-manifest", manifest_path, "application/json"
        )
        writer.finish()
