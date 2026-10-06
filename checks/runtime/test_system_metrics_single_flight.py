import asyncio
import subprocess
import threading
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard import handlers_system


@pytest.fixture(autouse=True)
def cold_metrics(monkeypatch):
    monkeypatch.setattr(handlers_system, "_metrics_cache", {})
    monkeypatch.setattr(handlers_system, "_metrics_cache_ts", 0.0)
    monkeypatch.setattr(handlers_system, "_metrics_inflight", None)


async def test_concurrent_http_pollers_share_real_host_collection(monkeypatch):
    original = handlers_system._collect_system_metrics
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def collect():
        nonlocal calls
        calls += 1
        started.set()
        if not release.wait(5):
            raise TimeoutError("pollers did not release collection")
        return original()

    monkeypatch.setattr(handlers_system, "_collect_system_metrics", collect)
    monkeypatch.setattr(handlers_system, "_METRICS_CACHE_TTL", 0.2)
    app = web.Application()
    app.router.add_get("/api/system", handlers_system.api_system)
    async with TestClient(TestServer(app)) as client:
        requests = [asyncio.create_task(client.get("/api/system")) for _ in range(5)]
        try:
            assert await asyncio.to_thread(started.wait, 5)
            await asyncio.sleep(0.3)
            inflight = handlers_system._metrics_inflight
            assert inflight is not None
            requests[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await requests[0]
            assert not inflight.cancelled()
            release.set()
            responses = await asyncio.gather(*requests[1:])
            bodies = [await response.json() for response in responses]
            assert calls == 1
            assert bodies[0]["hostname"]
            assert bodies[0]["proc_mem_mb"] > 0
            assert all(body == bodies[0] for body in bodies)
            cached = await client.get("/api/system")
            assert await cached.json() == bodies[0]
            assert calls == 1
            assert time.monotonic() - handlers_system._metrics_cache_ts < 0.2
        finally:
            release.set()
            await asyncio.gather(*requests, return_exceptions=True)


async def test_failed_collection_releases_next_real_request(monkeypatch):
    original = handlers_system._collect_system_metrics
    calls = 0

    def collect():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("host probe collection interrupted")
        return original()

    monkeypatch.setattr(handlers_system, "_collect_system_metrics", collect)
    app = web.Application()
    app.router.add_get("/api/system", handlers_system.api_system)
    async with TestClient(TestServer(app)) as client:
        failed = await client.get("/api/system")
        assert failed.status == 500
        assert handlers_system._metrics_inflight is None
        recovered = await client.get("/api/system")
        assert recovered.status == 200
        assert (await recovered.json())["hostname"]
        assert calls == 2


def test_failed_cpu_probe_omits_measurement_and_keeps_real_other_readings(monkeypatch):
    original = subprocess.check_output

    def probe(command, *args, **kwargs):
        if command[:3] == ["ps", "-A", "-o"]:
            raise subprocess.TimeoutExpired(command, 2)
        return original(command, *args, **kwargs)

    monkeypatch.setattr(handlers_system.subprocess, "check_output", probe)
    result = handlers_system._collect_system_metrics()
    assert "cpu_pct" not in result
    assert result["hostname"]
    assert result["proc_mem_mb"] > 0
    assert result["thread_count"] > 0
