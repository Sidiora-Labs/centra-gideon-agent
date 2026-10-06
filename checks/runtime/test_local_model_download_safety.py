from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import shutil
from pathlib import Path

import pytest

from gideon.integrations.local_models.fit import disk_precheck, size_text

_MIB = 1024 * 1024


def test_real_fresh_cache_refusal_names_measured_need_and_free(tmp_path):
    cache = tmp_path / "fresh" / "weights"
    free = shutil.disk_usage(tmp_path).free
    result = disk_precheck(free // _MIB + 2, cache)

    assert not result.ok
    assert result.measured
    assert result.code == "insufficient_disk_space"
    assert result.need_bytes > result.free_bytes
    assert size_text(result.need_bytes) in result.reason
    assert size_text(result.free_bytes) in result.reason
    assert not cache.exists()


def test_real_broken_cache_link_warns_without_refusing(tmp_path):
    cache = tmp_path / "missing-cache"
    cache.symlink_to(tmp_path / "absent-target")

    result = disk_precheck(75, cache)

    assert result.ok
    assert not result.measured
    assert result.need_bytes == 75 * _MIB
    assert result.warning
    assert not cache.exists()


def test_unknown_cache_filesystem_warns_without_measuring_root():
    result = disk_precheck(75, None)

    assert result.ok
    assert not result.measured
    assert result.need_bytes == 75 * _MIB
    assert result.free_bytes == 0
    assert result.warning


@pytest.mark.asyncio
async def test_real_external_local_provider_warning_survives_http_job_failure(
    monkeypatch, tmp_path
):
    if os.environ.get("GIDEON_TEST_LOCAL_MODEL_WARNING_GATE") != "1":
        pytest.skip(
            "Set GIDEON_TEST_LOCAL_MODEL_WARNING_GATE=1 and "
            "GIDEON_TEST_LOCAL_MODEL_APP_SOURCE to run the genuine provider HTTP regression."
        )

    source = Path(os.environ.get("GIDEON_TEST_LOCAL_MODEL_APP_SOURCE", ""))
    if not source.is_file():
        pytest.fail(
            "GIDEON_TEST_LOCAL_MODEL_APP_SOURCE must name the genuine local-model "
            "provider source file for this explicitly enabled regression."
        )

    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setenv("GIDEON_AUTH_MODE", "none")
    monkeypatch.delenv("HF_TOKEN", raising=False)

    spec = importlib.util.spec_from_file_location(
        "gideon_external_local_model_warning_provider", source
    )
    if spec is None or spec.loader is None:
        pytest.fail("The configured genuine provider source could not be loaded.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    provider = module.create_provider({})

    from aiohttp import ClientSession

    from gideon.core.config import AppConfig
    from gideon.engine.session import ConversationDirectory
    from gideon.integrations.local_models.provider import LocalModelProvider
    from gideon.integrations.local_models.registry import (
        capabilities_for,
        get_provider,
        register_provider,
        unregister_provider,
    )
    from gideon.interfaces.dashboard.server import start_dashboard

    assert isinstance(provider, LocalModelProvider)
    assert provider.cache_dir() is None
    assert not provider._hf_token()
    models = await provider.list_models()
    assert len(models) == 1
    model = models[0]
    assert model.gated and not model.downloaded and model.size_mb > 0

    name = provider.name
    prior = get_provider(name)
    prior_capabilities = capabilities_for(name)
    register_provider(provider, ["diarization"], name=name)
    runner = None
    try:
        sessions = ConversationDirectory(AppConfig.load())
        runner, _state = await start_dashboard(sessions=sessions, port=0)
        port = runner.addresses[0][1]
        base = f"http://127.0.0.1:{port}"
        headers = {
            "X-Session-Key": "dashboard:ui",
            "X-Gideon-API-Version": "1",
        }
        async with ClientSession() as client:
            async with client.get(
                f"{base}/api/models/available", headers=headers
            ) as response:
                assert response.status == 200
                catalog = await response.json()
            catalog_provider = next(
                p for p in catalog["providers"] if p["name"] == name
            )
            catalog_model = next(
                m for m in catalog_provider["models"] if m["name"] == model.name
            )
            assert catalog_model["gated"] and not catalog_model["downloaded"]
            assert catalog_model["capabilities"] == ["diarization"]

            async with client.post(
                f"{base}/api/models/downloads",
                headers=headers,
                json={"provider": name, "model": model.name},
            ) as response:
                assert response.status == 202
                created = await response.json()
            assert created["warning"] == (
                "Free space could not be checked, so the download was not verified to fit."
            )

            job = None
            for _ in range(50):
                async with client.get(
                    f"{base}/api/models/downloads", headers=headers
                ) as response:
                    assert response.status == 200
                    downloads = (await response.json())["downloads"]
                job = next(
                    (item for item in downloads if item["id"] == created["id"]), None
                )
                if job and job["state"] == "error":
                    break
                await asyncio.sleep(0.05)
            assert job is not None and job["state"] == "error"
            assert job["warning"] == created["warning"]
            assert job["reason"] == "download_failed"
            assert "retry" in job["error"].lower()

            async with client.get(
                f"{base}/api/models/downloads/{created['id']}/stream",
                headers=headers,
                timeout=5,
            ) as response:
                assert response.status == 200
                assert response.content_type == "text/event-stream"
                frame = await asyncio.wait_for(
                    response.content.readuntil(b"\n\n"), timeout=3
                )
            assert b"event: snapshot" in frame
            snapshot = json.loads(
                next(
                    line[6:]
                    for line in frame.decode().splitlines()
                    if line.startswith("data: ")
                )
            )
            assert snapshot["state"] == "error"
            assert snapshot["warning"] == created["warning"]
    finally:
        if runner is not None:
            await runner.cleanup()
        unregister_provider(name)
        if prior is not None:
            register_provider(prior, prior_capabilities, name=name)
