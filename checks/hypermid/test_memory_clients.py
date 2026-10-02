from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from gideon.hypermid.client import HypermidClient, HypermidRemoteError
from gideon.hypermid.contracts import (
    AccessRequest,
    GrantOperation,
    MemoryOperation,
    RecordDraft,
    RecordKind,
    RevisionPrecondition,
    SearchMode,
    SearchRequest,
    MutationRequest,
)
from gideon.hypermid.foundation import Id, Scope, Trace
from gideon.hypermid.memory_client import MemoryClient


CAPABILITY_ID = Id("capability-memory-client-test")
RECORD_ID = Id("record-memory-client-test")
SCOPE = Scope(Id("owner-memory-client-test"), Id("project-memory-client-test"))


def _daemon_binary() -> Path:
    candidates: list[Path] = []
    explicit = os.environ.get("HYPERMID_DAEMON_BIN")
    if explicit:
        candidates.append(Path(explicit))
    target = os.environ.get("CARGO_TARGET_DIR")
    if target:
        candidates.append(Path(target) / "debug" / "hypermid-daemon")
    candidates.append(Path("target/debug/hypermid-daemon"))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    raise AssertionError("focused gate requires the built hypermid-daemon binary")


@pytest.fixture
def live_memory_daemon(tmp_path: Path) -> Iterator[Path]:
    socket_path = tmp_path / "run" / "hypermid.sock"
    record_path = tmp_path / "state" / "connection.json"
    stderr_path = tmp_path / "daemon.stderr"
    stderr_handle = stderr_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [
            str(_daemon_binary()),
            "--socket",
            str(socket_path),
            "--connection-record",
            str(record_path),
            "--local-owner-id",
            str(SCOPE.owner_id),
            "--local-project-id",
            str(SCOPE.project_id),
            "--local-capability-id",
            str(CAPABILITY_ID),
            "--local-credential-id",
            "credential-memory-client-test",
            "--local-capability-operation",
            "append",
            "--local-capability-operation",
            "read",
            "--local-capability-resource",
            str(RECORD_ID),
            "--local-capability-resource",
            "memory-service",
            "--local-capability-expires-ms",
            str(int(time.time() * 1000) + 120_000),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=stderr_handle,
        text=True,
    )
    deadline = time.monotonic() + 10
    while not record_path.is_file():
        if process.poll() is not None:
            stderr_handle.flush()
            stderr = stderr_path.read_text(encoding="utf-8")
            raise AssertionError(
                f"hypermid-daemon exited before readiness ({process.returncode}): {stderr}"
            )
        if time.monotonic() >= deadline:
            process.terminate()
            process.wait(timeout=5)
            raise AssertionError("hypermid-daemon connection record timed out")
        time.sleep(0.01)
    try:
        yield record_path
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        stderr_handle.close()


@pytest.mark.asyncio
async def test_real_daemon_memory_client_preserves_scope_trace_and_authority(
    live_memory_daemon: Path,
) -> None:
    client = HypermidClient(live_memory_daemon, scope=SCOPE)
    memory = MemoryClient(client, capability_id=CAPABILITY_ID)
    now_ms = int(time.time() * 1000)

    health = await memory.health(trace=Trace(Id("trace-health"), Id("request-health")))
    assert health.state == "ready"
    assert health.durable is True
    assert health.lexical_available is True

    create_trace = Trace(Id("trace-create"), Id("request-create"))
    created = await memory.create(
        MutationRequest(
            operation=MemoryOperation.CREATE,
            actor_scope=SCOPE,
            target_scope=SCOPE,
            record_id=RECORD_ID,
            category="test",
            revision=RevisionPrecondition.must_not_exist(),
            trace=create_trace,
        ),
        RecordDraft(
            id=RECORD_ID,
            scope=SCOPE,
            kind=RecordKind.NOTE,
            category="test",
            content="A durable winter memory with exact UTF-8: snowman ☃",
            metadata={"source": "real-daemon"},
            importance=0.75,
            confidence=0.875,
        ),
        now_ms=now_ms,
    )
    assert created.record is not None
    assert created.record.id == RECORD_ID
    assert created.record.current.content.endswith("snowman ☃")
    assert created.cursor.sequence >= 1

    get_trace = Trace(Id("trace-get"), Id("request-get"))
    access = AccessRequest(
        operation=GrantOperation.READ,
        actor_scope=SCOPE,
        target_scope=SCOPE,
        resource_id=RECORD_ID,
        trace=get_trace,
    )
    record, cursor = await memory.get(access)
    assert record == created.record
    assert cursor == created.cursor

    search_trace = Trace(Id("trace-search"), Id("request-search"))
    search = await memory.search(
        AccessRequest(
            operation=GrantOperation.SEARCH,
            actor_scope=SCOPE,
            target_scope=SCOPE,
            resource_id=RECORD_ID,
            trace=search_trace,
        ),
        SearchRequest(
            query="durable winter",
            mode=SearchMode.LEXICAL,
            limit=10,
            trace=search_trace,
            now_ms=now_ms,
        ),
    )
    assert search.trace == search_trace
    assert [hit.id for hit in search.hits] == [RECORD_ID]
    assert search.hits[0].scores.lexical > 0.0

    diagnostics = await memory.diagnostics(
        AccessRequest(
            operation=GrantOperation.READ,
            actor_scope=SCOPE,
            target_scope=SCOPE,
            resource_id=Id("memory-service"),
            trace=Trace(Id("trace-diagnostics"), Id("request-diagnostics")),
        )
    )
    assert diagnostics.record_count == 1

    denied = MemoryClient(client, capability_id=Id("capability-not-installed"))
    with pytest.raises(HypermidRemoteError) as refused:
        await denied.get(access)
    assert refused.value.error.effect_state is not None
    assert refused.value.error.effect_state.value == "not_started"

    await client.close()
