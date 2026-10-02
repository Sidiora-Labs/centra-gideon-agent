from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import struct

import pytest

from gideon.hypermid.foundation import Digest, EffectState, Id, Scope
from gideon.hypermid.lifecycle_security import (
    BackupKey,
    LifecycleSecurity,
    LifecycleSecurityError,
    accept_worker_output,
    purge_record,
    tombstone_record,
)


ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE_SCHEMA = ROOT / "spec/hypermid/schemas/knowledge.sql"


def _scope(owner: str) -> Scope:
    return Scope(Id(owner), Id("lifecycle-project"))


def _scope_digest(scope: Scope) -> str:
    hasher = hashlib.sha256(b"hypermid.memory.scope.v1\0")
    for value in (scope.owner_id, scope.project_id):
        encoded = str(value).encode()
        hasher.update(len(encoded).to_bytes(8, "big"))
        hasher.update(encoded)
    hasher.update(b"\x00")
    return hasher.hexdigest()


def _revision_digest(
    record_id: str, content: str, author: str, authored_at_ms: int
) -> str:
    content_digest = hashlib.sha256(content.encode()).digest()
    metadata = b"{}"
    hasher = hashlib.sha256(b"hypermid.memory.revision.v1\0")
    encoded_id = record_id.encode()
    hasher.update(len(encoded_id).to_bytes(8, "big"))
    hasher.update(encoded_id)
    hasher.update((1).to_bytes(8, "big"))
    hasher.update(b"\x00")
    hasher.update(content_digest)
    hasher.update(len(metadata).to_bytes(8, "big"))
    hasher.update(metadata)
    hasher.update(bytes.fromhex(author))
    hasher.update(authored_at_ms.to_bytes(8, "big"))
    return hasher.hexdigest()


def _open_store(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    schema = KNOWLEDGE_SCHEMA.read_text()
    connection.executescript(schema)
    schema_digest = hashlib.sha256(schema.encode()).hexdigest()
    connection.execute(
        "INSERT INTO hypermid_schema_version VALUES(1,1,1,?,1)",
        (schema_digest,),
    )
    connection.execute(
        "INSERT INTO hypermid_migration_journal VALUES(1,?,1,1,NULL,'finished',NULL)",
        (schema_digest,),
    )
    connection.commit()
    return connection


def _insert_record(
    connection: sqlite3.Connection,
    scope: Scope,
    record_id: str,
    content: str,
    *,
    with_derivatives: bool = False,
) -> Digest:
    owner = _scope_digest(scope)
    now_ms = 100
    connection.execute(
        "INSERT OR IGNORE INTO memory_scopes VALUES(?,?,1,0,1,1)",
        (owner, json.dumps(scope.to_wire(), separators=(",", ":"), sort_keys=True)),
    )
    revision = _revision_digest(record_id, content, owner, now_ms)
    normalized = hashlib.sha256(" ".join(content.split()).lower().encode()).hexdigest()
    connection.execute(
        """INSERT INTO memory_records(
               record_id,owner_scope_digest,kind,category,status,current_revision,
               current_revision_digest,normalized_content_digest,importance,confidence,
               verification_state,created_at_ms,updated_at_ms)
           VALUES(?,?,'fact','general','active',1,?,?,0.5,0.5,'unverified',?,?)""",
        (record_id, owner, revision, normalized, now_ms, now_ms),
    )
    connection.execute(
        """INSERT INTO memory_revisions(
               record_id,revision,revision_digest,parent_revision_digest,content,
               content_digest,metadata_json,smart_predicate_json,author_scope_digest,
               authored_at_ms,immutable_anchor)
           VALUES(?,1,?,NULL,?,?,'{}',NULL,?,?,0)""",
        (
            record_id,
            revision,
            content,
            hashlib.sha256(content.encode()).hexdigest(),
            owner,
            now_ms,
        ),
    )
    if with_derivatives:
        connection.execute(
            "INSERT INTO memory_retrieval_stats VALUES(?,1,100,1,0,100)",
            (record_id,),
        )
        registration = f"embedding-{record_id}"
        connection.execute(
            """INSERT INTO embedding_registrations
               VALUES(?,?,'local','test','model',1,'cosine',1,?,'active',1,NULL)""",
            (registration, owner, "1" * 64),
        )
        connection.execute(
            "INSERT INTO memory_embeddings VALUES(?,?,?,?,1,1.0,1)",
            (record_id, revision, registration, struct.pack("f", 1.0)),
        )
        connection.execute(
            "INSERT INTO memory_fts(record_id,owner_scope_digest,category,content) VALUES(?,?,?,?)",
            (record_id, owner, "general", content),
        )
        connection.execute(
            "INSERT INTO memory_fts_rows VALUES(?,?,?)",
            (connection.execute("SELECT last_insert_rowid()").fetchone()[0], record_id, revision),
        )
    connection.commit()
    return Digest(revision)


def _code(error: pytest.ExceptionInfo[LifecycleSecurityError]) -> str:
    return error.value.code


def test_encrypted_restore_rejects_wrong_key_corruption_and_unknown_effects(
    tmp_path: Path,
) -> None:
    owner = _scope("owner-a")
    foreign = _scope("owner-b")
    source_path = tmp_path / "source.sqlite"
    active_path = tmp_path / "active.sqlite"
    source = _open_store(source_path)
    active = _open_store(active_path)
    _insert_record(source, owner, "owner-record", "operator private launch plan")
    _insert_record(source, foreign, "source-foreign", "source foreign content")
    _insert_record(active, owner, "old-owner-record", "old active content", with_derivatives=True)
    _insert_record(active, foreign, "active-foreign", "active foreign content")
    key = BackupKey.generate()
    lifecycle = LifecycleSecurity()
    artifact = tmp_path / "backup.hmbk"
    backup = lifecycle.export_encrypted(source, artifact, owner, key)
    source.close()
    active.close()

    artifact_bytes = artifact.read_bytes()
    assert b"operator private launch plan" not in artifact_bytes
    assert b"captured_content" not in artifact_bytes
    before = hashlib.sha256(active_path.read_bytes()).hexdigest()
    with pytest.raises(LifecycleSecurityError) as wrong_key:
        lifecycle.stage_restore(
            active_path,
            artifact,
            owner,
            BackupKey.generate(),
            expected_artifact_digest=backup.artifact_digest,
            supported_schema=1,
        )
    assert _code(wrong_key) == "BACKUP_AUTHENTICATION_FAILED"
    assert hashlib.sha256(active_path.read_bytes()).hexdigest() == before

    corrupt = tmp_path / "corrupt.hmbk"
    corrupted = bytearray(artifact_bytes)
    corrupted[-1] ^= 1
    corrupt.write_bytes(corrupted)
    with pytest.raises(LifecycleSecurityError) as changed:
        lifecycle.stage_restore(
            active_path,
            corrupt,
            owner,
            key,
            expected_artifact_digest=backup.artifact_digest,
            supported_schema=1,
        )
    assert _code(changed) == "ARTIFACT_DIGEST_MISMATCH"
    with pytest.raises(LifecycleSecurityError) as corrupt_chunk:
        lifecycle.stage_restore(
            active_path,
            corrupt,
            owner,
            key,
            expected_artifact_digest=Digest.sha256(bytes(corrupted)),
            supported_schema=1,
        )
    assert _code(corrupt_chunk) == "BACKUP_AUTHENTICATION_FAILED"
    assert hashlib.sha256(active_path.read_bytes()).hexdigest() == before

    staged = lifecycle.stage_restore(
        active_path,
        artifact,
        owner,
        key,
        expected_artifact_digest=backup.artifact_digest,
        supported_schema=1,
    )
    with pytest.raises(LifecycleSecurityError) as unknown:
        lifecycle.activate(
            staged.staging_id,
            staged.active_digest,
            effect_states=[EffectState.UNKNOWN],
        )
    assert _code(unknown) == "UNKNOWN_EFFECT_BLOCKS_RESTORE"
    assert hashlib.sha256(active_path.read_bytes()).hexdigest() == before
    receipt = lifecycle.activate(staged.staging_id, staged.active_digest)
    assert receipt.effect_state is EffectState.COMMITTED

    restored = sqlite3.connect(active_path)
    assert restored.execute(
        "SELECT content FROM memory_revisions WHERE record_id='owner-record'"
    ).fetchone() == ("operator private launch plan",)
    assert restored.execute(
        "SELECT content FROM memory_revisions WHERE record_id='active-foreign'"
    ).fetchone() == ("active foreign content",)
    assert restored.execute(
        "SELECT COUNT(*) FROM memory_retrieval_stats WHERE record_id='old-owner-record'"
    ).fetchone() == (0,)
    restored.close()


def test_interrupted_stage_discard_preserves_active_authority(tmp_path: Path) -> None:
    owner = _scope("owner-a")
    source_path = tmp_path / "source.sqlite"
    active_path = tmp_path / "active.sqlite"
    source = _open_store(source_path)
    active = _open_store(active_path)
    _insert_record(source, owner, "new-record", "new authority")
    _insert_record(active, owner, "active-record", "active authority")
    key = BackupKey.generate()
    lifecycle = LifecycleSecurity()
    artifact = tmp_path / "backup.hmbk"
    backup = lifecycle.export_encrypted(source, artifact, owner, key)
    source.close()
    active.close()
    before = active_path.read_bytes()
    staged = lifecycle.stage_restore(
        active_path,
        artifact,
        owner,
        key,
        expected_artifact_digest=backup.artifact_digest,
        supported_schema=1,
    )
    lifecycle.discard(staged.staging_id)
    assert active_path.read_bytes() == before


def test_tombstone_removes_derivatives_and_purge_blocks_stale_restore(
    tmp_path: Path,
) -> None:
    owner = _scope("owner-a")
    active_path = tmp_path / "active.sqlite"
    active = _open_store(active_path)
    revision = _insert_record(
        active, owner, "purged-record", "sensitive durable memory", with_derivatives=True
    )
    key = BackupKey.generate()
    lifecycle = LifecycleSecurity()
    artifact = tmp_path / "pre-purge.hmbk"
    backup = lifecycle.export_encrypted(active, artifact, owner, key)

    tombstone_record(
        active,
        owner,
        owner,
        "purged-record",
        revision,
        now_ms=200,
        retention_until_ms=300,
    )
    assert active.execute(
        "SELECT COUNT(*) FROM memory_embeddings WHERE record_id='purged-record'"
    ).fetchone() == (0,)
    assert active.execute(
        "SELECT COUNT(*) FROM memory_fts_rows WHERE record_id='purged-record'"
    ).fetchone() == (0,)
    assert active.execute(
        "SELECT COUNT(*) FROM memory_retrieval_stats WHERE record_id='purged-record'"
    ).fetchone() == (0,)
    with pytest.raises(LifecycleSecurityError) as stale_worker:
        accept_worker_output(active, owner, "purged-record", revision)
    assert _code(stale_worker) == "STALE_WORKER_OUTPUT"
    with pytest.raises(LifecycleSecurityError) as early:
        purge_record(active, owner, owner, "purged-record", now_ms=299)
    assert _code(early) == "RETENTION_NOT_ELAPSED"
    purge_record(active, owner, owner, "purged-record", now_ms=300)
    assert active.execute(
        "SELECT COUNT(*) FROM memory_records WHERE record_id='purged-record'"
    ).fetchone() == (0,)
    active.close()

    before = active_path.read_bytes()
    with pytest.raises(LifecycleSecurityError) as stale_backup:
        lifecycle.stage_restore(
            active_path,
            artifact,
            owner,
            key,
            expected_artifact_digest=backup.artifact_digest,
            supported_schema=1,
        )
    assert _code(stale_backup) == "STALE_BACKUP_TOMBSTONE"
    assert active_path.read_bytes() == before
