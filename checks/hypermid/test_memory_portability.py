import hashlib
import json

import pytest

from gideon.hypermid.foundation import Cursor, Digest, Id, Scope, Trace
from gideon.hypermid.portability import (
    ContextPortabilityError,
    MemoryExportBundle,
    MemoryExportEntry,
    MemoryExportManifest,
    MemoryImportBatch,
    MemoryImportManifest,
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _stream_digest(entries: tuple[MemoryExportEntry, ...]) -> Digest:
    digest = hashlib.sha256(b"hypermid.memory.export.v1\0")
    for entry in entries:
        encoded = _canonical(entry.to_mapping())
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return Digest(digest.hexdigest())


def test_memory_portability_contract_verifies_stream_and_strict_batches() -> None:
    scope = Scope(Id("owner-portable"), Id("project-portable"))
    trace = Trace(Id("trace-portable"), Id("request-portable"))
    payload = {
        "record_id": "record-portable",
        "owner_scope_digest": "a" * 64,
        "content": "portable authoritative content",
    }
    entry = MemoryExportEntry(
        "record:record-portable",
        "record",
        payload,
        Digest.sha256(_canonical(payload)),
    )
    entries = (entry,)
    manifest = MemoryExportManifest(
        Id("export-portable"),
        1,
        scope,
        1,
        1,
        _stream_digest(entries),
        100,
        Cursor(1, 1),
        trace,
        False,
    )
    bundle = MemoryExportBundle(manifest, entries)
    assert MemoryExportBundle.from_mapping(bundle.to_mapping()) == bundle

    import_manifest = MemoryImportManifest(
        1,
        Digest.sha256(_canonical(bundle.to_mapping())),
        scope,
        scope,
        1,
        {},
        200,
        trace,
    )
    batch = MemoryImportBatch(
        Id("batch-portable"), import_manifest, "validated", (), False
    )
    assert MemoryImportBatch.from_mapping(batch.to_mapping()) == batch

    bad_manifest = MemoryExportManifest(
        Id("export-bad"),
        1,
        scope,
        1,
        1,
        Digest.sha256(b"different"),
        100,
        Cursor(1, 1),
        trace,
        False,
    )
    with pytest.raises(ContextPortabilityError, match="stream digest"):
        MemoryExportBundle(bad_manifest, entries)

    forbidden = {"record_id": "record-bad", "embedding_vector": [0.1, 0.2]}
    with pytest.raises(ContextPortabilityError, match="transient or secret-bearing"):
        MemoryExportEntry(
            "record:record-bad",
            "record",
            forbidden,
            Digest.sha256(_canonical(forbidden)),
        )
