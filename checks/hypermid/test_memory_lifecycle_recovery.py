from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import struct
import subprocess
import time
from pathlib import Path

import pytest

from gideon.hypermid import HypermidClient
from gideon.hypermid.foundation import Digest, Scope
from gideon.hypermid.lifecycle_security import (
    BackupKey,
    LifecycleSecurity,
    LifecycleSecurityError,
    accept_worker_output,
    purge_record,
    tombstone_record,
)
from gideon.hypermid.sources import scope_digest

from checks.hypermid.evidence import ObservationWriter
from checks.hypermid.waves.local_journey import daemon_binary


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _revision_digest(
    record_id: str,
    content_digest: str,
    metadata_json: str,
    author_scope_digest: str,
    authored_at_ms: int,
) -> str:
    hasher = hashlib.sha256(b"hypermid.memory.revision.v1\0")
    encoded_id = record_id.encode()
    hasher.update(len(encoded_id).to_bytes(8, "big"))
    hasher.update(encoded_id)
    hasher.update((1).to_bytes(8, "big"))
    hasher.update(b"\0")
    hasher.update(bytes.fromhex(content_digest))
    encoded_metadata = metadata_json.encode()
    hasher.update(len(encoded_metadata).to_bytes(8, "big"))
    hasher.update(encoded_metadata)
    hasher.update(bytes.fromhex(author_scope_digest))
    hasher.update(authored_at_ms.to_bytes(8, "big"))
    return hasher.hexdigest()


def _seed_scope(
    connection: sqlite3.Connection, scope: Scope, records: list[tuple[str, str, str]]
) -> dict[str, str]:
    digest = str(scope_digest(scope))
    connection.execute(
        "INSERT INTO memory_scopes(scope_digest,scope_json,epoch,sequence,created_at_ms,updated_at_ms) VALUES(?,?,1,?,1000,1000)",
        (digest, _canonical(scope.to_wire()), len(records)),
    )
    revisions: dict[str, str] = {}
    for sequence, (record_id, kind, content) in enumerate(records, 1):
        content_digest = _sha256(content.encode())
        normalized_digest = _sha256(" ".join(content.split()).lower().encode())
        metadata_json = _canonical({"fixture": record_id})
        revision_digest = _revision_digest(
            record_id, content_digest, metadata_json, digest, 1000 + sequence
        )
        revisions[record_id] = revision_digest
        connection.execute(
            """
            INSERT INTO memory_records(
                record_id,owner_scope_digest,kind,category,status,current_revision,
                current_revision_digest,normalized_content_digest,importance,confidence,
                verification_state,valid_from_ms,valid_to_ms,observed_from_ms,
                observed_to_ms,expires_at_ms,retention_until_ms,created_at_ms,
                updated_at_ms,deleted_at_ms
            ) VALUES(?,?,?,'lifecycle','active',1,?,?,0.7,0.8,'unverified',NULL,NULL,NULL,NULL,NULL,NULL,?,?,NULL)
            """,
            (
                record_id,
                digest,
                kind,
                revision_digest,
                normalized_digest,
                1000 + sequence,
                1000 + sequence,
            ),
        )
        connection.execute(
            """
            INSERT INTO memory_revisions(
                record_id,revision,revision_digest,parent_revision_digest,content,
                content_digest,metadata_json,smart_predicate_json,author_scope_digest,
                authored_at_ms,immutable_anchor
            ) VALUES(?,1,?,NULL,?,?,?,NULL,?,?,0)
            """,
            (
                record_id,
                revision_digest,
                content,
                content_digest,
                metadata_json,
                digest,
                1000 + sequence,
            ),
        )
        connection.execute(
            """
            INSERT INTO memory_mutation_events(
                event_id,owner_scope_digest,epoch,sequence,operation,record_id,
                previous_revision_digest,result_revision_digest,actor_scope_digest,
                grant_id,authorization_basis,trace_json,created_at_ms
            ) VALUES(?,?,1,?,'create',?,NULL,?,?,NULL,'owner',?,?)
            """,
            (
                f"event-{record_id}",
                digest,
                sequence,
                record_id,
                revision_digest,
                digest,
                _canonical(
                    {
                        "trace_id": f"trace-{record_id}",
                        "request_id": f"request-{record_id}",
                    }
                ),
                1000 + sequence,
            ),
        )
    return revisions


def _seed_target_derivatives(
    connection: sqlite3.Connection,
    scope: Scope,
    record_id: str,
    revision_digest: str,
    parent_id: str,
    parent_revision_digest: str,
    source_content: str,
    indexed_content: str,
) -> None:
    digest = str(scope_digest(scope))
    source_id = f"source-{record_id}"
    source_digest = _sha256(source_content.encode())
    connection.execute(
        "INSERT INTO memory_sources(source_id,owner_scope_digest,source_kind,source_digest,locator,captured_content,capture_method,observed_at_ms,created_at_ms) VALUES(?,?,'message',?,'session:item',?,'exact',1000,1000)",
        (source_id, digest, source_digest, source_content),
    )
    connection.execute(
        "INSERT INTO memory_provenance(record_id,revision,source_id,span_start,span_end,quoted_digest) VALUES(?,1,?,0,?,?)",
        (record_id, source_id, len(source_content.encode("utf-8")), source_digest),
    )
    connection.execute(
        "INSERT INTO memory_lineage(child_record_id,child_revision,parent_record_id,parent_revision_digest,relation,created_at_ms) VALUES(?,1,?,?,'derived_from',1000)",
        (record_id, parent_id, parent_revision_digest),
    )
    connection.execute(
        "INSERT INTO summary_details(record_id,input_set_digest,summary_level,decay_half_life_ms,refreshed_at_ms,stale_at_ms) VALUES(?,?,'standard',60000,1000,NULL)",
        (record_id, _sha256(b"summary-input")),
    )
    registration_id = f"registration-{record_id}"
    connection.execute(
        "INSERT INTO embedding_registrations(registration_id,owner_scope_digest,mode,provider_identity,model_id,dimensions,metric,normalized,fingerprint,state,created_at_ms,retired_at_ms) VALUES(?,?,'local','acceptance','fixture',1,'cosine',1,?,'active',1000,NULL)",
        (registration_id, digest, _sha256(b"embedding-registration")),
    )
    connection.execute(
        "INSERT INTO memory_embeddings(record_id,revision_digest,registration_id,vector_f32,dimensions,norm,created_at_ms) VALUES(?,?,?,X'0000803F',1,1.0,1000)",
        (record_id, revision_digest, registration_id),
    )
    connection.execute(
        "INSERT INTO memory_retrieval_stats(record_id,explicit_retrieval_count,last_explicit_retrieval_at_ms,useful_count,not_useful_count,updated_at_ms) VALUES(?,1,1000,1,0,1000)",
        (record_id,),
    )
    cursor = connection.execute(
        "INSERT INTO memory_fts(record_id,owner_scope_digest,category,content) VALUES(?,?,?,?)",
        (record_id, digest, "lifecycle", indexed_content),
    )
    connection.execute(
        "INSERT INTO memory_fts_rows(rowid,record_id,revision_digest) VALUES(?,?,?)",
        (cursor.lastrowid, record_id, revision_digest),
    )


def _file_digest(path: Path) -> Digest:
    return Digest.sha256(path.read_bytes())


async def _initialize_store(root: Path, scope: Scope) -> Path:
    root.mkdir(mode=0o700, parents=True)
    record = root / "connection.json"
    socket = root / "daemon.sock"
    expires_ms = int(time.time() * 1000) + 600_000
    process = subprocess.Popen(
        [
            daemon_binary(),
            "--socket",
            str(socket),
            "--connection-record",
            str(record),
            "--local-credential-id",
            "lifecycle-credential",
            "--local-owner-id",
            str(scope.owner_id),
            "--local-project-id",
            str(scope.project_id),
            "--local-workspace-id",
            str(scope.workspace_id),
            "--local-capability-id",
            "lifecycle-capability",
            "--local-capability-operation",
            "read",
            "--local-capability-operation",
            "append",
            "--local-capability-operation",
            "delete",
            "--local-capability-operation",
            "export",
            "--local-capability-operation",
            "restore",
            "--local-capability-resource",
            "memory-service",
            "--local-capability-resource",
            "memory-portability",
            "--local-capability-expires-ms",
            str(expires_ms),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    client: HypermidClient | None = None
    try:
        deadline = time.monotonic() + 10
        while not record.is_file():
            if process.poll() is not None:
                stderr = process.stderr.read() if process.stderr is not None else ""
                raise AssertionError(
                    f"hypermid-daemon exited before lifecycle readiness ({process.returncode}): {stderr}"
                )
            if time.monotonic() >= deadline:
                raise AssertionError("hypermid-daemon lifecycle readiness timed out")
            await asyncio.sleep(0.01)
        client = HypermidClient(record, scope=scope)
        description = await client.request("server.describe", {})
        assert isinstance(description, dict)
        assert description.get("daemon_instance_id")
    finally:
        if client is not None:
            await client.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    active = root / "state" / "memory.sqlite3"
    assert active.is_file()
    return active


def _chunk_offsets(artifact: bytes) -> list[int]:
    magic = b"HMBK1\0"
    assert artifact.startswith(magic)
    header_start = len(magic) + 4
    header_length = struct.unpack(">I", artifact[len(magic) : header_start])[0]
    header_end = header_start + header_length
    header = json.loads(artifact[header_start:header_end])
    offsets: list[int] = []
    offset = header_end
    for chunk in header["chunks"]:
        offsets.append(offset)
        offset += chunk["bytes"]
    assert offset == len(artifact)
    return offsets


def _assert_error(code: str, operation: object) -> None:
    with pytest.raises(LifecycleSecurityError) as caught:
        operation()  # type: ignore[operator]
    assert caught.value.code == code


@pytest.mark.asyncio
async def test_memory_lifecycle_recovery_uses_real_daemon_stores(tmp_path: Path) -> None:
    observation = ObservationWriter.from_env("deletion_export_backup_restore")
    workspace = "shared-workspace"
    owner_a = Scope("owner-a", "shared-project", workspace)
    owner_b = Scope("owner-b", "shared-project", workspace)
    source_root = tmp_path / "source"
    active = await _initialize_store(source_root, owner_a)
    with sqlite3.connect(f"file:{active}?mode=ro", uri=True) as connection:
        supported_schema = int(connection.execute(
            "SELECT current_version FROM hypermid_schema_version WHERE singleton=1"
        ).fetchone()[0])

    private_marker = "uniquetargettoken owner-a-private-revision-" + "x" * 1_100_000
    captured_marker = "owner-a-captured-source-" + "y" * 1_100_000
    connection = sqlite3.connect(active)
    try:
        revisions_a = _seed_scope(
            connection,
            owner_a,
            [
                ("a-parent", "note", "owner A parent record"),
                ("a-target", "summary", private_marker),
            ],
        )
        _seed_target_derivatives(
            connection,
            owner_a,
            "a-target",
            revisions_a["a-target"],
            "a-parent",
            revisions_a["a-parent"],
            captured_marker,
            private_marker,
        )
        _seed_scope(
            connection,
            owner_b,
            [("b-private", "note", "owner B must never enter owner A backup")],
        )
        connection.commit()
    finally:
        connection.close()

    lifecycle_security = LifecycleSecurity()
    key = BackupKey.generate()
    before_purge_backup = tmp_path / "owner-a-before-purge.hmbk"
    connection = sqlite3.connect(active)
    try:
        before_receipt = lifecycle_security.export_encrypted(
            connection, before_purge_backup, owner_a, key
        )
    finally:
        connection.close()
    artifact = before_purge_backup.read_bytes()
    offsets = _chunk_offsets(artifact)
    altered_backup_chunks_accepted = 0
    assert len(offsets) >= 3
    assert private_marker.encode() not in artifact
    assert captured_marker.encode() not in artifact
    assert b"captured_content" not in artifact
    active_before_failures = active.read_bytes()

    _assert_error(
        "BACKUP_AUTHENTICATION_FAILED",
        lambda: lifecycle_security.stage_restore(
            active,
            before_purge_backup,
            owner_a,
            BackupKey.generate(),
            expected_artifact_digest=before_receipt.artifact_digest,
            supported_schema=supported_schema,
        ),
    )
    assert active.read_bytes() == active_before_failures

    _assert_error(
        "SCOPE_MISMATCH",
        lambda: lifecycle_security.stage_restore(
            active,
            before_purge_backup,
            owner_b,
            key,
            expected_artifact_digest=before_receipt.artifact_digest,
            supported_schema=supported_schema,
        ),
    )
    assert active.read_bytes() == active_before_failures

    for index, offset in enumerate(offsets):
        altered = bytearray(artifact)
        altered[offset] ^= 0x01
        altered_path = tmp_path / f"owner-a-altered-{index}.hmbk"
        altered_path.write_bytes(altered)
        try:
            lifecycle_security.stage_restore(
                active,
                altered_path,
                owner_a,
                key,
                expected_artifact_digest=Digest.sha256(bytes(altered)),
                supported_schema=supported_schema,
            )
        except LifecycleSecurityError as error:
            assert error.code == "BACKUP_AUTHENTICATION_FAILED"
        else:
            altered_backup_chunks_accepted += 1
        assert active.read_bytes() == active_before_failures
    assert altered_backup_chunks_accepted == 0

    connection = sqlite3.connect(active)
    try:
        assert connection.execute(
            "SELECT rowid FROM memory_fts WHERE memory_fts MATCH 'uniquetargettoken'"
        ).fetchall()
        _assert_error(
            "AUTHORIZATION_DENIED",
            lambda: tombstone_record(
                connection,
                owner_b,
                owner_a,
                "a-target",
                Digest(revisions_a["a-target"]),
                now_ms=2_000,
                retention_until_ms=3_000,
            ),
        )
        tombstone_record(
            connection,
            owner_a,
            owner_a,
            "a-target",
            Digest(revisions_a["a-target"]),
            now_ms=2_000,
            retention_until_ms=3_000,
        )
        assert connection.execute(
            "SELECT status,deleted_at_ms,retention_until_ms FROM memory_records WHERE record_id='a-target'"
        ).fetchone() == ("tombstoned", 2_000, 3_000)
        for table in ("memory_embeddings", "memory_retrieval_stats", "memory_fts_rows"):
            assert connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE record_id='a-target'"
            ).fetchone() == (0,)
        assert connection.execute(
            "SELECT rowid FROM memory_fts WHERE memory_fts MATCH 'uniquetargettoken'"
        ).fetchall() == []
        _assert_error(
            "STALE_WORKER_OUTPUT",
            lambda: accept_worker_output(
                connection, owner_a, "a-target", Digest(revisions_a["a-target"])
            ),
        )
        _assert_error(
            "RETENTION_NOT_ELAPSED",
            lambda: purge_record(
                connection, owner_a, owner_a, "a-target", now_ms=2_999
            ),
        )
        _assert_error(
            "AUTHORIZATION_DENIED",
            lambda: purge_record(
                connection, owner_b, owner_a, "a-target", now_ms=3_000
            ),
        )
        purge_record(connection, owner_a, owner_a, "a-target", now_ms=3_000)
        derived_copy_counts: dict[str, int] = {}
        for table in (
            "memory_records",
            "memory_revisions",
            "memory_provenance",
            "memory_lineage",
            "summary_details",
        ):
            column = "child_record_id" if table == "memory_lineage" else "record_id"
            count = int(connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {column}='a-target'"
            ).fetchone()[0])
            derived_copy_counts[table] = count
            assert count == 0
        source_copies = int(connection.execute(
            "SELECT COUNT(*) FROM memory_sources WHERE source_id='source-a-target'"
        ).fetchone()[0])
        derived_copy_counts["memory_sources"] = source_copies
        assert source_copies == 0
        for table in (
            "memory_embeddings",
            "memory_retrieval_stats",
            "memory_fts_rows",
        ):
            count = int(connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE record_id='a-target'"
            ).fetchone()[0])
            derived_copy_counts[table] = count
            assert count == 0
        indexed_copies = int(connection.execute(
            "SELECT COUNT(*) FROM memory_fts WHERE memory_fts MATCH 'uniquetargettoken'"
        ).fetchone()[0])
        derived_copy_counts["memory_fts"] = indexed_copies
        assert indexed_copies == 0
        derived_copies_after_purge = sum(derived_copy_counts.values())
        assert derived_copies_after_purge == 0
        assert connection.execute(
            "SELECT purged_at_ms FROM hypermid_lifecycle_tombstones WHERE record_id='a-target'"
        ).fetchone() == (3_000,)
    finally:
        connection.close()

    after_purge = active.read_bytes()
    _assert_error(
        "STALE_BACKUP_TOMBSTONE",
        lambda: lifecycle_security.stage_restore(
            active,
            before_purge_backup,
            owner_a,
            key,
            expected_artifact_digest=before_receipt.artifact_digest,
            supported_schema=supported_schema,
        ),
    )
    assert active.read_bytes() == after_purge

    current_backup = tmp_path / "owner-a-current.hmbk"
    connection = sqlite3.connect(active)
    try:
        current_receipt = lifecycle_security.export_encrypted(
            connection, current_backup, owner_a, key
        )
    finally:
        connection.close()

    restore_root = tmp_path / "restore"
    restored_active = await _initialize_store(restore_root, owner_a)
    pristine = restored_active.read_bytes()
    stage = lifecycle_security.stage_restore(
        restored_active,
        current_backup,
        owner_a,
        key,
        expected_artifact_digest=current_receipt.artifact_digest,
        supported_schema=supported_schema,
    )
    assert restored_active.read_bytes() == pristine
    lifecycle_security.discard(stage.staging_id)
    assert restored_active.read_bytes() == pristine

    stage = lifecycle_security.stage_restore(
        restored_active,
        current_backup,
        owner_a,
        key,
        expected_artifact_digest=current_receipt.artifact_digest,
        supported_schema=supported_schema,
    )
    restored = lifecycle_security.activate(stage.staging_id, _file_digest(restored_active))
    assert restored.scope == owner_a
    assert restored.cursor == current_receipt.cursor
    assert restored.source_digest == current_receipt.source_digest
    assert restored.artifact_digest == current_receipt.artifact_digest
    assert restored.active_digest == _file_digest(restored_active)
    connection = sqlite3.connect(restored_active)
    try:
        assert connection.execute(
            "SELECT record_id FROM memory_records ORDER BY record_id"
        ).fetchall() == [("a-parent",)]
        owner_b_digest = str(scope_digest(owner_b))
        cross_owner_export_counts = {
            "memory_scopes": int(connection.execute(
                "SELECT COUNT(*) FROM memory_scopes WHERE scope_digest=?",
                (owner_b_digest,),
            ).fetchone()[0]),
            "memory_records": int(connection.execute(
                "SELECT COUNT(*) FROM memory_records WHERE owner_scope_digest=?",
                (owner_b_digest,),
            ).fetchone()[0]),
            "memory_sources": int(connection.execute(
                "SELECT COUNT(*) FROM memory_sources WHERE owner_scope_digest=?",
                (owner_b_digest,),
            ).fetchone()[0]),
            "memory_mutation_events": int(connection.execute(
                "SELECT COUNT(*) FROM memory_mutation_events WHERE owner_scope_digest=?",
                (owner_b_digest,),
            ).fetchone()[0]),
            "hypermid_lifecycle_tombstones": int(connection.execute(
                "SELECT COUNT(*) FROM hypermid_lifecycle_tombstones WHERE owner_scope_digest=?",
                (owner_b_digest,),
            ).fetchone()[0]),
        }
        cross_owner_export_entries = sum(cross_owner_export_counts.values())
        assert cross_owner_export_entries == 0
        assert connection.execute(
            "SELECT purged_at_ms FROM hypermid_lifecycle_tombstones WHERE record_id='a-target'"
        ).fetchone() == (3_000,)
    finally:
        connection.close()

    if observation is not None:
        report_path = observation.directory / ".deletion-export-backup-restore-report.json"
        report_path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "gate": "deletion_export_backup_restore",
                    "observations": {
                        "altered-backup-chunks-accepted": altered_backup_chunks_accepted,
                        "cross-owner-export-entries": cross_owner_export_entries,
                        "derived-copies-after-purge": derived_copies_after_purge,
                    },
                    "purge_copy_counts": derived_copy_counts,
                    "cross_owner_export_counts": cross_owner_export_counts,
                    "altered_backup_chunks_tested": len(offsets),
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        observation.measure(
            "derived-copies-after-purge",
            derived_copies_after_purge,
            "eq",
            0,
            unit="copies",
        )
        observation.measure(
            "cross-owner-export-entries",
            cross_owner_export_entries,
            "eq",
            0,
            unit="entries",
        )
        observation.measure(
            "altered-backup-chunks-accepted",
            altered_backup_chunks_accepted,
            "eq",
            0,
            unit="chunks",
        )
        observation.artifact(
            "lifecycle-integrity-report", report_path, "application/json"
        )
