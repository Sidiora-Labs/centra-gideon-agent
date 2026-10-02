"""Real Wave 3 Gideon context integration journey."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path

from gideon.cognition.context import PromptAssembler
from gideon.cognition.context_headroom import Component, Window, check
from gideon.cognition.history import ConversationLog, HistoryConsolidator
from gideon.cognition.memory import MemoryJournal
from gideon.cognition.memory_record import MemoryKind, MemoryRecord, MemoryScope
from gideon.cognition.vector_memory import ScopedHybridRecall, SemanticArchive
from gideon.cognition.bg_compress import (
    quiesce_background_compression,
    resume_background_compression,
)
from gideon.hypermid.authority_operations import DaemonWriterLeaseAuthority
from gideon.hypermid.cache import CacheGeneration, CachedRegion
from gideon.hypermid.client import HypermidClient
from gideon.hypermid.context import ConversationContextBridge
from gideon.hypermid.foundation import Id
from gideon.hypermid.inspect import (
    WriterStatus,
    inspect_primary_context,
    inspect_runtime,
)
from gideon.hypermid.memory import (
    HypermidMemoryProvider,
    install_as_memory_authority,
    uninstall_memory_authority,
)
from gideon.hypermid.models import Cursor, Scope, Trace
from gideon.hypermid.primary_engine import PrimaryContextEngine, ThreadedPrimaryBridge
from gideon.hypermid.writer import GideonCutoverHooks, WriterCoordinator
from gideon.integrations.llm.prompt_cache import (
    CACHE_HINT_KEY,
    CacheBinding,
    PromptCache,
    mark_cacheable_prefix,
)


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _daemon_binary() -> str:
    configured = os.environ.get("HYPERMID_DAEMON_BINARY")
    if configured and Path(configured).is_file():
        return str(Path(configured).resolve())
    target = Path(os.environ.get("CARGO_TARGET_DIR", "target"))
    candidate = target / "debug" / "hypermid-daemon"
    if candidate.is_file():
        return str(candidate.resolve())
    raise RuntimeError(
        "Build hypermid-daemon and set HYPERMID_DAEMON_BINARY before acceptance"
    )


async def _start_primary_daemon(
    root: Path, *, scope: Scope, capability_id: Id, record_id: Id
) -> tuple[asyncio.subprocess.Process, Path]:
    record = root / "connection.json"
    socket = root / "daemon.sock"
    command = [
        _daemon_binary(),
        "--socket",
        str(socket),
        "--connection-record",
        str(record),
        "--local-credential-id",
        "credential-wave-03",
        "--local-owner-id",
        str(scope.owner_id),
        "--local-project-id",
        str(scope.project_id),
        "--local-workspace-id",
        str(scope.workspace_id),
        "--local-capability-id",
        str(capability_id),
        "--local-capability-operation",
        "read",
        "--local-capability-operation",
        "append",
        "--local-capability-resource",
        str(record_id),
        "--local-capability-resource",
        "memory-records",
        "--local-capability-resource",
        "memory-list",
        "--local-capability-resource",
        "memory-search",
        "--local-capability-expires-ms",
        str(time.time_ns() // 1_000_000 + 300_000),
    ]
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    deadline = asyncio.get_running_loop().time() + 10
    while not record.is_file():
        if process.returncode is not None:
            stderr = await process.stderr.read() if process.stderr is not None else b""
            raise RuntimeError(
                f"Hypermid daemon exited before readiness ({process.returncode}): "
                f"{stderr.decode(errors='replace')}"
            )
        if asyncio.get_running_loop().time() >= deadline:
            process.terminate()
            await process.wait()
            raise TimeoutError("Hypermid daemon connection record was not published")
        await asyncio.sleep(0.02)
    return process, record


async def _stop_daemon(process: asyncio.subprocess.Process | None) -> None:
    if process is None or process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        process.kill()
        await process.wait()


def integrated_context_journey(root: Path) -> dict[str, object]:
    archive = SemanticArchive(db_path=root / "memory.db")
    archive.init()
    archive.graph_enabled = False
    secret = "swordfish-private-value"
    try:
        rejection = archive.set_semantic(
            "project.quantum.tool",
            f"Use the quantum wrench; credential {secret}",
            0.95,
            "user_explicit",
        )
        assert rejection is None
        scope = Scope("owner-1", "project-1", "workspace-1")
        recall = ScopedHybridRecall(archive, scope=scope).search(
            "quantum wrench",
            request_scope=scope,
            cursor=Cursor(3, 9),
            trace=Trace("trace-wave-03", "request-wave-03"),
            observed_at_ms=1_800_000_000_000,
            limit=4,
        )
        assert recall.degraded and recall.degradation_reason == "semantic_unavailable"
        assert recall.hits and recall.hits[0].scores["keyword"] > 0
        vector_arm = next(arm for arm in recall.arms if arm.name == "vector")
        assert vector_arm.state == "unavailable"
        try:
            ScopedHybridRecall(archive, scope=scope).search(
                "quantum wrench",
                request_scope=Scope("owner-1", "project-1"),
                cursor=Cursor(3, 9),
                trace=Trace("trace-wave-03", "request-scope-denied"),
                observed_at_ms=1_800_000_000_000,
            )
        except PermissionError:
            pass
        else:
            raise AssertionError("recall accepted a request outside its bound workspace")

        recall_text = "[Scoped recall]\n" + "\n".join(hit.content for hit in recall.hits) + "\n"
        recall_digest = _digest(recall_text.encode())
        projected = Component(
            "hypermid recall",
            recall_text,
            source="hypermid.memory",
            content_digest=recall_digest,
            covered_digest=recall_digest,
            policy_revision=7,
            cache_region="delta",
            source_cursor="3:9",
        )
        journal = MemoryJournal(workspace=root / "journal")
        journal.init()
        assembler = PromptAssembler(memory=journal)
        components: list[Component] = []
        prompt, _ = assembler.build_message(
            "Use the remembered project tool.",
            False,
            session_key="wave-03",
            system_prompt_override="You are Gideon.",
            components_out=components,
            hypermid_components=(projected,),
            hypermid_policy_revision=7,
            hypermid_source_cursor="3:9",
            hypermid_covered_digests={"hypermid recall": recall_digest},
        )
        assert recall_text in prompt
        assert [component.source_order for component in components] == list(range(len(components)))
        assert components[-1].name == "the user's request"
        recall_component = next(item for item in components if item.name == "hypermid recall")
        assert recall_component.cache_region == "delta"
        try:
            assembler.build_message(
                "Use the remembered project tool.",
                False,
                system_prompt_override="You are Gideon.",
                hypermid_components=(projected,),
                hypermid_policy_revision=8,
            )
        except ValueError as error:
            assert "policy revision mismatch" in str(error)
        else:
            raise AssertionError("prompt assembly accepted stale projected policy")

        headroom = check(
            components,
            window=Window(16_384, 1_024, 15_360, "wave-03-provider"),
        )
        assert headroom.headroom_tokens is not None and headroom.headroom_tokens > 0
        prompt_digest = _digest(prompt.encode())
        binding = CacheBinding(4, 7, prompt_digest, recall_digest)
        marked = mark_cacheable_prefix(
            [{"role": "system", "content": prompt}, {"role": "user", "content": "continue"}],
            PromptCache.EXPLICIT,
            binding=binding,
            policy_revision=7,
            covered_digest=recall_digest,
        )
        assert marked[-1][CACHE_HINT_KEY] == binding.to_hint()
        try:
            mark_cacheable_prefix(
                marked,
                PromptCache.EXPLICIT,
                binding=binding,
                policy_revision=7,
                covered_digest="0" * 64,
            )
        except ValueError as error:
            assert "covered digest" in str(error)
        else:
            raise AssertionError("prompt cache accepted stale covered bytes")

        empty = CachedRegion(_digest(b""), b"")
        cache = CacheGeneration(
            4,
            _digest(b"provider-profile"),
            7,
            CachedRegion(prompt_digest, prompt.encode(), token_mass=headroom.assembled_tokens),
            empty,
            empty,
        )
        inspection = inspect_runtime(
            scope=scope,
            writer="gideon",
            writer_status=WriterStatus("gideon-context", 3, 4),
            cursor=Cursor(3, 9),
            components=components,
            headroom=headroom,
            cache=cache,
            recall=recall,
            observed_at_ms=1_800_000_000_000,
        )
        encoded = json.dumps(inspection, sort_keys=True)
        assert inspection["state"] == "complete"
        assert inspection["digest_health"]["state"] == "healthy"
        assert inspection["cache"]["generation"] == 4
        assert inspection["recall_arms"] == [arm.to_dict() for arm in recall.arms]
        assert secret not in encoded and recall_text not in encoded
        return {
            "state": inspection["state"],
            "prompt_components": len(components),
            "keyword_fallback": True,
            "scope_denied": True,
            "cache_generation": 4,
            "redacted_inspection": True,
        }
    finally:
        archive.close()


async def primary_knowledge_journey(root: Path) -> dict[str, object]:
    scope = Scope("owner-wave-03", "project-wave-03", "workspace-wave-03")
    capability_id = Id("capability-wave-03")
    record_id = Id("memory-wave-03-primary")
    session_key = "wave-03-primary"
    query = "cobalt navigation"
    secret = "private-primary-evidence"
    process: asyncio.subprocess.Process | None = None
    writer_client: HypermidClient | None = None
    provider: HypermidMemoryProvider | None = None
    primary: PrimaryContextEngine | None = None
    coordinator: WriterCoordinator | None = None
    installed_service: object | None = None
    try:
        process, connection_record = await _start_primary_daemon(
            root,
            scope=scope,
            capability_id=capability_id,
            record_id=record_id,
        )
        provider = HypermidMemoryProvider(
            connection_record,
            scope=scope,
            capability_id=capability_id,
        )
        provider.init()
        writer_client = HypermidClient(connection_record, scope=scope)
        await writer_client.connect()

        memory = MemoryJournal(workspace=root / "memory-journal")
        memory.init()
        log = ConversationLog(base_dir=root / "sessions")
        log.append(session_key, "user", query)
        context_bridge = ConversationContextBridge(
            log, root / "context-journals", scope=scope
        )
        consolidator = HistoryConsolidator(log, memory)

        def install_writer(_lease) -> None:
            nonlocal installed_service
            assert coordinator is not None
            provider.bind_writer(coordinator)
            installed_service = install_as_memory_authority(provider)

        def uninstall_writer() -> None:
            nonlocal installed_service
            if installed_service is not None:
                uninstall_memory_authority(installed_service)
                installed_service = None

        hooks = GideonCutoverHooks(
            log=log,
            context=context_bridge,
            consolidator=consolidator,
            memory_flush=memory.read,
            summary_quiesce=lambda: quiesce_background_compression((session_key,)),
            summary_resume=lambda: resume_background_compression((session_key,)),
            install_writer=install_writer,
            uninstall_writer=uninstall_writer,
            session_keys=lambda: (session_key,),
        )
        coordinator = WriterCoordinator(
            mode="primary",
            scope=scope,
            authority=DaemonWriterLeaseAuthority(writer_client),
            hooks=hooks,
        )
        snapshot = await coordinator.activate_primary()
        assert snapshot.owns_writes and snapshot.lease is not None

        provider.put(
            [
                MemoryRecord(
                    id=str(record_id),
                    kind=MemoryKind.SEMANTIC,
                    text=f"Use the cobalt compass for primary navigation. {secret}",
                    value="cobalt compass",
                    importance=0.95,
                    confidence=0.99,
                    source="user_explicit",
                    scope=MemoryScope.WORKSPACE,
                    scope_ref=str(root),
                    category="navigation",
                )
            ]
        )
        stored = provider.get(str(record_id))
        assert stored is not None and "cobalt compass" in stored.text

        bridge = ThreadedPrimaryBridge(connection_record, scope)
        primary = PrimaryContextEngine(
            bridge,
            writer=coordinator,
            context_bridge=context_bridge,
            summary_capability_id=capability_id,
        )
        assembler = PromptAssembler(memory=memory, conversation_log=log)
        assembled = primary.assemble(
            assembler,
            query,
            is_new_session=True,
            session_key=session_key,
            system_prompt_override="You are Gideon.",
        )
        assert "cobalt compass" in assembled.message
        recalled = next(
            component
            for component in assembled.components
            if component.source == "hypermid.memory.daemon"
        )
        assert recalled.cache_region == "delta"
        hypermid = assembled.metadata["hypermid"]
        assert hypermid["recall"]["state"] == "daemon"
        assert hypermid["recall"]["arms"][0]["name"] == "keyword"
        assert hypermid["recall"]["arms"][0]["result_count"] >= 1
        assert "provenance" in hypermid["recall"]["hits"][0]["scores"]
        assert hypermid["summary"]["authorized_records"] == 0
        assert hypermid["summary"]["selected_records"] == 0

        projection = hypermid["projection"]
        inspection = inspect_primary_context(
            assembled,
            scope=scope,
            writer_status=WriterStatus(
                "hypermid-context",
                snapshot.lease.fence_epoch,
                projection["generation"],
            ),
            observed_at_ms=time.time_ns() // 1_000_000,
        )
        encoded = json.dumps(inspection, ensure_ascii=False, sort_keys=True)
        assert inspection["state"] == "complete"
        assert inspection["writer"] == "hypermid"
        assert inspection["digest_health"]["state"] == "healthy"
        assert inspection["cache"]["bytes"] > 0
        assert inspection["cache"]["bytes_known"]
        assert inspection["budget_evidence"]["within_limit"]
        assert inspection["budget_evidence"]["recall_tokens"] > 0
        assert inspection["recall"]["source"] == "hypermid.memory.daemon"
        assert inspection["summary"]["authorized_records"] == 0
        assert any(
            item["source"] == "hypermid.summary" and item["verified"]
            for item in inspection["provenance"]
        )
        assert secret not in encoded and "cobalt compass" not in encoded
        return {
            "state": inspection["state"],
            "writer": inspection["writer"],
            "writer_fence_epoch": snapshot.lease.fence_epoch,
            "daemon_knowledge_in_prompt": True,
            "recall_source": inspection["recall"]["source"],
            "inspection_redacted": True,
        }
    finally:
        if primary is not None:
            primary.close()
        if coordinator is not None and coordinator.snapshot().lease is not None:
            try:
                await coordinator.deactivate()
            except BaseException:
                if installed_service is not None:
                    uninstall_memory_authority(installed_service)
                    installed_service = None
                resume_background_compression((session_key,))
        elif installed_service is not None:
            uninstall_memory_authority(installed_service)
        if writer_client is not None:
            await writer_client.close()
        if provider is not None:
            provider.close()
        await _stop_daemon(process)
