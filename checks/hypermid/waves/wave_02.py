from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path

from gideon.cognition.context import PromptAssembler
from gideon.cognition.history import ConversationLog, HistoryConsolidator
from gideon.cognition.memory import MemoryJournal
from gideon.cognition.memory_service import MemoryService
from gideon.cognition.vector_memory import SemanticArchive
from gideon.hypermid.authority_operations import DaemonWriterLeaseAuthority
from gideon.hypermid.background_coordinator import (
    quiesce_summary_work,
    resume_summary_work,
)
from gideon.hypermid.client import HypermidClient, HypermidRemoteError
from gideon.hypermid.context import ConversationContextBridge, session_id_for_key
from gideon.hypermid.foundation import Digest, Id, Scope
from gideon.hypermid.memory import (
    HypermidMemoryProvider,
    install_as_memory_authority,
    uninstall_memory_authority,
)
from gideon.hypermid.primary_engine import PrimaryContextEngine, ThreadedPrimaryBridge
from gideon.hypermid.writer import GideonCutoverHooks, WriterCoordinator


CAPABILITY_ID = Id("capability-wave-02")
SESSION_KEY = "session-wave-02"
SUMMARY_KEY = "summary-wave-02"
EMBEDDING_ID = "embedding-wave-02"


def _daemon_binary() -> str:
    configured = os.environ.get("HYPERMID_DAEMON_BINARY")
    if configured and Path(configured).is_file():
        return str(Path(configured).resolve())
    target = Path(os.environ.get("CARGO_TARGET_DIR", "target"))
    candidate = target / "debug" / "hypermid-daemon"
    if candidate.is_file():
        return str(candidate.resolve())
    raise RuntimeError("focused journey requires a built hypermid-daemon")


async def _start_daemon(root: Path, scope: Scope) -> tuple[asyncio.subprocess.Process, Path]:
    daemon_root = root / "daemon"
    daemon_root.mkdir(parents=True, exist_ok=True)
    connection = daemon_root / "connection.json"
    socket = daemon_root / "hypermid.sock"
    connection.unlink(missing_ok=True)
    socket.unlink(missing_ok=True)
    command = [
        _daemon_binary(),
        "--socket",
        str(socket),
        "--connection-record",
        str(connection),
        "--local-credential-id",
        "credential-wave-02",
        "--local-owner-id",
        str(scope.owner_id),
        "--local-project-id",
        str(scope.project_id),
        "--local-workspace-id",
        str(scope.workspace_id),
        "--local-capability-id",
        str(CAPABILITY_ID),
    ]
    for operation in ("read", "append", "revise", "archive", "delete", "restore"):
        command.extend(("--local-capability-operation", operation))
    for resource in ("memory-records", "memory-embedding", EMBEDDING_ID):
        command.extend(("--local-capability-resource", resource))
    command.extend(
        (
            "--local-capability-expires-ms",
            str(time.time_ns() // 1_000_000 + 300_000),
        )
    )
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    deadline = asyncio.get_running_loop().time() + 10
    while not connection.is_file():
        if process.returncode is not None:
            stderr = await process.stderr.read() if process.stderr is not None else b""
            raise RuntimeError(
                f"Hypermid daemon exited before readiness ({process.returncode}): "
                f"{stderr.decode(errors='replace')}"
            )
        if asyncio.get_running_loop().time() >= deadline:
            process.kill()
            await process.wait()
            raise TimeoutError("Hypermid daemon connection record was not published")
        await asyncio.sleep(0.02)
    return process, connection


async def _stop_daemon(
    process: asyncio.subprocess.Process | None, *, crash: bool = False
) -> None:
    if process is None or process.returncode is not None:
        return
    process.kill() if crash else process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        process.kill()
        await process.wait()


def _edit_first_message(log: ConversationLog, key: str, content: str) -> None:
    path = log._path(key)
    rows = path.read_text(encoding="utf-8").splitlines()
    message = json.loads(rows[1])
    message["content"] = content
    rows[1] = json.dumps(message)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    log._invalidate_cache(key)


def _assert_exact_expansion(
    engine: PrimaryContextEngine,
    context: ConversationContextBridge,
    log: ConversationLog,
) -> int:
    items = context.journal(SESSION_KEY).all_items()
    response = engine.bridge.expand(
        {
            "session_id": str(session_id_for_key(SESSION_KEY)),
            "item_ids": [str(item.item_id) for item in items],
            "reclaim_tags": [],
        }
    )
    expanded = response["items"]
    assert len(expanded) == len(items)
    expected = {
        event.source_event_id: (event.raw_bytes, event.source_digest)
        for event in log.source_events(SESSION_KEY)
    }
    for entry in expanded:
        source_id = str(entry["item"]["source_event_id"])
        raw, digest = expected[source_id]
        assert bytes(entry["source_bytes"]) == raw
        assert entry["source_digest"] == digest == str(Digest.sha256(raw))
    return len(expanded)


def _memory_surface(service: MemoryService, provider: HypermidMemoryProvider) -> dict[str, object]:
    assert service.set_semantic("semantic.user.name", "Tony", 1.0, "user_explicit") is None
    assert service.write_episodic(
        "Recovered a committed memory after daemon interruption.",
        conversation_id=SESSION_KEY,
        tags=["recovery"],
        source="user_explicit",
    )
    assert service.write_lesson("Validate source digests before cutover", category="operations")
    assert service.slot_append("persona", "Answer with exact evidence.")
    entity_id = service.graph_add_entity("Hypermid", "project", aliases=["context ledger"])
    assert entity_id
    assert provider.graph.add_link(
        from_kind="semantic",
        from_ref="semantic.user.name",
        link_type="mentions",
        to_entity=entity_id,
        source="user_explicit",
    )
    assert service.graph_record_links("sem:semantic.user.name")

    assert service.set_semantic("semantic.undo", "before", 1.0, "user_explicit") is None
    assert service.set_semantic("semantic.undo", "after", 1.0, "user_explicit") is None
    event_cursor = provider.append_event(
        event_type="semantic_updated",
        memory_type="semantic",
        memory_key="semantic.undo",
        old_value="before",
        new_value="after",
        source="user_explicit",
    )
    assert event_cursor > 0
    event = service.get_events(limit=1)[0]
    assert service.undo_event(event["id"])[0]
    assert service.get_semantic("semantic.undo")["value"] == "before"

    assert service.set_semantic("semantic.old", "old", 1.0, "user_explicit") is None
    assert service.set_semantic("semantic.new", "new", 1.0, "user_explicit") is None
    assert service.supersede_semantic("semantic.old", "semantic.new", "supersede")
    assert provider.restore("semantic.old")

    registration = service.register_embedding(
        {
            "registration_id": EMBEDDING_ID,
            "mode": "local",
            "provider_identity": "gideon-host",
            "model_id": "qualified-local",
            "dimensions": 3,
            "normalized": False,
        }
    )
    assert "cursor" in registration
    assert service.embedding_registration()["registration_id"] == EMBEDDING_ID
    assert service.retire_embedding(EMBEDDING_ID)["cursor"]

    page = service.record_page(limit=100)
    assert page is not None and page.records
    evidence = service.search_with_evidence("source digests", limit=8)
    assert evidence is not None and evidence.hits
    fallback = service.fts_fallback_search("source digests", k=8)
    assert fallback
    stats = service.memory_stats()
    preview = service.context_preview("source digests")
    assert stats["active"] >= 6 and preview["text"]
    with provider.privacy("incognito"):
        before = service.memory_stats()["cursor"]
        assert not service.write_episodic("must not persist", source="user_explicit")
        assert service.memory_stats()["cursor"] == before
    with provider.privacy("temporary"):
        assert service.get_semantic("semantic.user.name") is None
        assert not service.write_lesson("must not persist")
    return {
        "record_count": len(page.records),
        "cursor": stats["cursor"],
        "keyword_hits": len(fallback),
        "entity_id": entity_id,
    }


async def durable_memory_context_journey(root: Path) -> dict[str, object]:
    scope = Scope(Id("owner-wave-02"), Id("project-wave-02"), Id("workspace-wave-02"))
    memory_journal = MemoryJournal(root / "gideon-memory")
    memory_journal.init()
    legacy_archive = SemanticArchive(root / "gideon-memory.sqlite3")
    legacy_archive.init()
    legacy_service = MemoryService(legacy_archive, vector_store=legacy_archive)
    log = ConversationLog(root / "sessions")
    log.append(SESSION_KEY, "user", "Keep the cobalt instrument ready.")
    log.append(
        SESSION_KEY,
        "tool",
        "inventory_lookup",
        meta={
            "tool_call_id": "call-wave-02",
            "input": '{"item":"cobalt instrument"}',
            "done": True,
            "output": '{"available":true}',
        },
    )
    log.append(SESSION_KEY, "assistant", "The cobalt instrument is ready.")
    log.append(SUMMARY_KEY, "user", "original covered source")
    log.append(SUMMARY_KEY, "assistant", "acknowledged")
    summary_context = ConversationContextBridge(log, root / "summary-context", scope=scope)
    summary_context.publish_summary(
        SUMMARY_KEY,
        summary="The original source was acknowledged.",
        summarized=1,
        reduced=2,
    )
    assert log.read_summary(SUMMARY_KEY) is not None
    _edit_first_message(log, SUMMARY_KEY, "edited covered source")
    assert log.read_summary(SUMMARY_KEY) is None

    process: asyncio.subprocess.Process | None = None
    client: HypermidClient | None = None
    provider: HypermidMemoryProvider | None = None
    engine: PrimaryContextEngine | None = None
    coordinator: WriterCoordinator | None = None
    installed: object | None = None
    first_epoch = 0
    first_context_cursor = 0
    first_memory: dict[str, object] = {}
    expanded = 0

    async def cleanup(*, crash: bool) -> None:
        nonlocal process, client, provider, engine, installed
        if engine is not None:
            engine.close()
            engine = None
        if installed is not None:
            uninstall_memory_authority(installed)
            installed = None
        if provider is not None:
            provider.close()
            provider = None
        if client is not None:
            await client.close()
            client = None
        await _stop_daemon(process, crash=crash)
        process = None

    def construct(connection: Path) -> tuple[
        HypermidMemoryProvider,
        ConversationContextBridge,
        HistoryConsolidator,
    ]:
        created_provider = HypermidMemoryProvider(
            connection, scope=scope, capability_id=CAPABILITY_ID
        )
        created_provider.init()
        context = ConversationContextBridge(log, root / "context", scope=scope)
        consolidator = HistoryConsolidator(log, memory_journal, vector_store=legacy_archive)
        return created_provider, context, consolidator

    try:
        process, connection = await _start_daemon(root, scope)
        provider, context, consolidator = construct(connection)
        assert not provider.write_episodic("shadow must stay read-only", source="user_explicit")
        client = HypermidClient(connection, scope=scope)
        await client.connect()

        def install_writer(_lease: object) -> None:
            nonlocal installed
            assert coordinator is not None and provider is not None
            provider.bind_writer(coordinator)
            installed = install_as_memory_authority(provider)

        def uninstall_writer() -> None:
            nonlocal installed
            if installed is not None:
                uninstall_memory_authority(installed)
                installed = None

        hooks = GideonCutoverHooks(
            log=log,
            context=context,
            consolidator=consolidator,
            memory_flush=legacy_service.flush_for_cutover,
            summary_quiesce=quiesce_summary_work,
            summary_resume=resume_summary_work,
            install_writer=install_writer,
            uninstall_writer=uninstall_writer,
            session_keys=lambda: (SESSION_KEY,),
        )
        coordinator = WriterCoordinator(
            mode="primary",
            scope=scope,
            authority=DaemonWriterLeaseAuthority(client),
            hooks=hooks,
        )
        await coordinator.reconcile_startup()
        first = await coordinator.activate_primary()
        assert first.owns_writes and first.lease is not None
        first_epoch = first.lease.fence_epoch
        tool_item = next(
            item for item in context.journal(SESSION_KEY).all_items() if item.role.value == "tool"
        )
        assert [part.kind.value for part in tool_item.parts] == ["tool_call", "tool_result"]
        assert tool_item.parts[0].call_id == tool_item.parts[1].call_id
        first_memory = _memory_surface(installed, provider)

        bridge = ThreadedPrimaryBridge(connection, scope)
        engine = PrimaryContextEngine(
            bridge, writer=coordinator, context_bridge=context
        )
        assembled = engine.assemble(
            PromptAssembler(memory=memory_journal, conversation_log=log),
            "Which instrument is ready and what memory rule applies?",
            is_new_session=True,
            session_key=SESSION_KEY,
            system_prompt_override="You are Gideon.",
        )
        evidence = assembled.metadata["hypermid"]
        first_context_cursor = int(evidence["cursor"]["sequence"])
        assert first_context_cursor == len(context.journal(SESSION_KEY).all_items())
        assert "TOOL CALL" in assembled.message and "TOOL RESULT" in assembled.message
        assert evidence["recall"]["state"] == "daemon"
        expanded = _assert_exact_expansion(engine, context, log)

        challenger = HypermidClient(connection, scope=scope)
        await challenger.connect()
        try:
            try:
                await DaemonWriterLeaseAuthority(challenger).acquire(
                    scope=scope,
                    request_id=Id("writer-challenger-wave-02"),
                    minimum_fence_epoch=first_epoch + 1,
                )
            except HypermidRemoteError:
                pass
            else:
                raise AssertionError("a second writer acquired authority while primary was active")
        finally:
            await challenger.close()

        await cleanup(crash=True)

        process, connection = await _start_daemon(root, scope)
        provider, context, consolidator = construct(connection)
        client = HypermidClient(connection, scope=scope)
        await client.connect()

        def install_recovered(_lease: object) -> None:
            nonlocal installed
            assert coordinator is not None and provider is not None
            provider.bind_writer(coordinator)
            installed = install_as_memory_authority(provider)

        def uninstall_recovered() -> None:
            nonlocal installed
            if installed is not None:
                uninstall_memory_authority(installed)
                installed = None

        recovered_hooks = GideonCutoverHooks(
            log=log,
            context=context,
            consolidator=consolidator,
            memory_flush=legacy_service.flush_for_cutover,
            summary_quiesce=quiesce_summary_work,
            summary_resume=resume_summary_work,
            install_writer=install_recovered,
            uninstall_writer=uninstall_recovered,
            session_keys=lambda: (SESSION_KEY,),
        )
        coordinator = WriterCoordinator(
            mode="primary",
            scope=scope,
            authority=DaemonWriterLeaseAuthority(client),
            hooks=recovered_hooks,
        )
        reconciled = await coordinator.reconcile_startup()
        assert reconciled.writer == "gideon" and reconciled.authority_epoch > first_epoch
        recovered = await coordinator.activate_primary()
        assert recovered.owns_writes and recovered.lease is not None
        assert recovered.lease.fence_epoch > first_epoch
        installed_service = installed
        assert isinstance(installed_service, MemoryService)
        assert installed_service.get_semantic("semantic.user.name")["value"] == "Tony"
        recovered_search = installed_service.search_with_evidence("source digests", limit=8)
        assert recovered_search is not None and recovered_search.hits
        recovered_stats = installed_service.memory_stats()
        assert recovered_stats["cursor"] == first_memory["cursor"]

        engine = PrimaryContextEngine(
            ThreadedPrimaryBridge(connection, scope),
            writer=coordinator,
            context_bridge=context,
        )
        replayed = engine.assemble(
            PromptAssembler(memory=memory_journal, conversation_log=log),
            "Recover the same context without duplicate ingestion.",
            is_new_session=True,
            session_key=SESSION_KEY,
            system_prompt_override="You are Gideon.",
        )
        replay_cursor = int(replayed.metadata["hypermid"]["cursor"]["sequence"])
        assert replay_cursor == first_context_cursor
        assert _assert_exact_expansion(engine, context, log) == expanded
        active_epoch = recovered.lease.fence_epoch
        handback = await coordinator.deactivate()
        assert handback.writer == "gideon"
        assert handback.restore_receipt is not None
        assert handback.restore_receipt.gideon_epoch > active_epoch
        return {
            "state": "complete",
            "first_writer_epoch": first_epoch,
            "recovered_writer_epoch": active_epoch,
            "gideon_restore_epoch": handback.restore_receipt.gideon_epoch,
            "memory_cursor": recovered_stats["cursor"],
            "context_cursor": replay_cursor,
            "expanded_sources": expanded,
            "record_count": first_memory["record_count"],
            "keyword_hits": first_memory["keyword_hits"],
            "summary_invalidated": True,
            "second_writer_fenced": True,
            "restart_deduplicated": True,
        }
    finally:
        if coordinator is not None and coordinator.snapshot().lease is not None and client is not None:
            try:
                await coordinator.deactivate()
            except BaseException:
                pass
        await cleanup(crash=False)
        resume_summary_work()
        legacy_archive.close()
