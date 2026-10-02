import pytest

from gideon.hypermid.diagnostics import MemoryRecoveryDiagnostics
from gideon.hypermid.foundation import Cursor, Digest, Id, Scope
from gideon.hypermid.recovery import (
    MemoryRestoreReceipt,
    MemorySnapshotReceipt,
    RecoveryError,
)


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
