"""Actual native guarded rebind with an installed local embedding adapter."""
import asyncio
import json
import sqlite3
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from checks.runtime.test_embedding_hot_lifecycle import embedding_home, load_ollama
from checks.hypermid.test_memory_release import _start_daemon, _open_memory, _draft, _mutation
from gideon.extensions.providers import media_scanners
from gideon.extensions.providers.use_cases import save_active_models
from gideon.hypermid.contracts import MemoryOperation, RecordKind, RevisionPrecondition, SearchMode
from gideon.hypermid.foundation import Id, Scope
from gideon.hypermid.memory import HypermidMemoryProvider, _trace
from gideon.hypermid.client import HypermidRemoteError
from gideon.hypermid.providers import GideonEmbeddingProviderAuthority
from gideon.interfaces.dashboard.embedding_reindex import ReindexRegistry


@pytest.mark.asyncio
async def test_native_model_rebind_backfill_search_and_stale_publication(embedding_home):
    module = load_ollama()
    requests = []
    blocked = asyncio.Event()
    release = asyncio.Event()
    delay = False
    async def embed(request):
        body = await request.json()
        requests.append(body)
        if delay and body["input"] == ["new cobalt knowledge"]:
            blocked.set()
            await release.wait()
        vector = [1., 0.] if body["model"] == "a" else [0., 1., 0.]
        return web.json_response({"embeddings": [vector for _ in body["input"]]})
    app = web.Application()
    app.router.add_post("/api/embed", embed)
    async with TestServer(app) as server:
        endpoint = str(server.make_url("/")).rstrip("/")
        (embedding_home / "config.json").write_text(json.dumps({"providers": [
            {"name": "ollama-models", "type": "ollama", "options": {"endpoint": endpoint}}]}))
        def scanner(entries):
            return [module.create_provider(entry["options"]) for entry in entries]
        media_scanners.register_scanner("embedding", scanner)
        save_active_models({"embedding": ["ollama-models:a"]})
        scope = Scope(Id("embedding-owner"), Id("embedding-project"), Id("embedding-workspace"))
        cap = Id("embedding-capability")
        record_id = Id("embedding-record")
        registration_id = Id("embedding-initial")
        daemon = _start_daemon(embedding_home, "native-daemon", scope, cap,
            {record_id, registration_id, Id("memory-records"), Id("memory-embedding")})
        client, memory = await _open_memory(daemon, scope, cap)
        provider = None
        try:
            await memory.create(_mutation(scope, MemoryOperation.CREATE, record_id, "create"),
                _draft(scope, record_id, RecordKind.FACT, "retained cobalt knowledge"),
                now_ms=time.time_ns() // 1_000_000)
            provider = await asyncio.to_thread(HypermidMemoryProvider, daemon.record, scope=scope, capability_id=cap)
            await asyncio.to_thread(provider.embedding_register, {
                "registration_id": str(registration_id), "mode": "local", "provider_identity": "ollama-models",
                "model_id": "a", "dimensions": 2, "normalized": False})
            initial = (await asyncio.to_thread(provider.embedding_active))["registration"]
            record = (await asyncio.to_thread(provider.record_page)).records[0]
            authority = GideonEmbeddingProviderAuthority()
            binding = await authority.active_binding(_trace())
            output = await authority.embed(record.current.content, binding, _trace())
            await asyncio.to_thread(provider.embedding_publish, record, initial, output)
            save_active_models({"embedding": ["ollama-models:b"]})
            registry = ReindexRegistry()
            job, error = registry.start("ollama-models:b", None, provider, None, None)
            assert error is None
            await registry._running[job.id].task
            assert job.status == "done", job.error
            assert job.memory == 1
            active = (await asyncio.to_thread(provider.embedding_active))["registration"]
            assert active["fingerprint"] != initial["fingerprint"]
            assert active["dimensions"] == 3
            response = await asyncio.to_thread(provider.search_with_evidence, "unrelated query", mode=SearchMode.SEMANTIC)
            assert not response.degraded
            assert str(response.hits[0].id) == str(record_id)
            assert (await asyncio.to_thread(provider.record_page)).records[0].current.content == "retained cobalt knowledge"
            connection = sqlite3.connect(daemon.record.parent / "state" / "memory.sqlite3")
            try:
                assert connection.execute("SELECT COUNT(*) FROM memory_embeddings WHERE record_id=?", (str(record_id),)).fetchone()[0] == 2
            finally:
                connection.close()
            old_record = (await asyncio.to_thread(provider.record_page)).records[0]
            new_binding = await authority.active_binding(_trace())
            stale_output = await authority.embed(old_record.current.content, new_binding, _trace())
            await memory.update(_mutation(scope, MemoryOperation.UPDATE, record_id, "revise",
                RevisionPrecondition.match(str(old_record.current.digest))),
                _draft(scope, record_id, RecordKind.FACT, "new cobalt knowledge"),
                now_ms=time.time_ns() // 1_000_000)
            with pytest.raises(HypermidRemoteError) as failure:
                await asyncio.to_thread(provider.embedding_publish, old_record, active, stale_output)
            assert failure.value.error.code == "EMBEDDING_STALE_RESULT"
            assert (await asyncio.to_thread(provider.record_page)).records[0].current.content == "new cobalt knowledge"
            delay = True
            cancelled = asyncio.create_task(provider.embedding_reembed_all())
            await asyncio.wait_for(blocked.wait(), 5)
            cancelled.cancel()
            with pytest.raises(asyncio.CancelledError):
                await cancelled
            release.set()
            connection = sqlite3.connect(daemon.record.parent / "state" / "memory.sqlite3")
            try:
                assert connection.execute("SELECT COUNT(*) FROM memory_embeddings WHERE record_id=?", (str(record_id),)).fetchone()[0] == 2
            finally:
                connection.close()
            blocked.clear()
            release.clear()
            next_job, error = registry.start("ollama-models:b", None, provider, None, None)
            assert error is None
            await asyncio.wait_for(blocked.wait(), 5)
            inserted_id = Id("a-concurrent-record")
            await memory.create(_mutation(scope, MemoryOperation.CREATE, inserted_id, "concurrent"),
                _draft(scope, inserted_id, RecordKind.FACT, "concurrently retained knowledge"),
                authority_resource=Id("memory-records"), now_ms=time.time_ns() // 1_000_000)
            release.set()
            await registry._running[next_job.id].task
            assert next_job.status == "error"
            assert "skipped changed records" in next_job.error
            assert len((await asyncio.to_thread(provider.record_page)).records) == 2
            assert any(request["model"] == "b" and request["input"] == ["retained cobalt knowledge"] for request in requests)
        finally:
            release.set()
            if provider is not None:
                await asyncio.to_thread(provider.close)
            await client.close()
            daemon.stop()
