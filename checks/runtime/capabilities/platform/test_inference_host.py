import asyncio
import json
import socket
import sys

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.integrations.llm.credentials import CredentialStore
from gideon.integrations.llm.registry import (
    ProviderEntry,
    ProviderRegistry,
    get_default_registry,
    set_default_registry,
)
from gideon.integrations.local_models import _sidecar_child
from gideon.integrations.local_models.sidecar import (
    SidecarRunner,
    register_runner,
    unregister_runner,
)
from gideon.interfaces.dashboard.handlers.capabilities_inference_host import register
from gideon.interfaces.dashboard.token_auth import (
    generate_token,
    token_auth_middleware,
    use_ephemeral_secret,
)
from gideon.workspace.capabilities.platform.inference_host import (
    HostError,
    InferenceHost,
)
from gideon.workspace.capabilities.platform.tools import create_provider

SECRET = "local-qualification-peer-key-1234567890"
PREFIX = "/api/capabilities/platform/inference-host"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.delenv("GIDEON_DEV_NO_AUTH", raising=False)
    monkeypatch.delenv("GIDEON_BYPASS_LOCAL_NETWORKS", raising=False)
    previous = get_default_registry()
    registry = ProviderRegistry()
    registry.register_entry(
        ProviderEntry(
            name="Configured inference",
            type="uninstalled-direct-provider",
            model="configured-model",
        )
    )
    set_default_registry(registry)
    CredentialStore(tmp_path).put("peer-key", {"type": "static_token", "value": SECRET})
    use_ephemeral_secret()
    yield tmp_path
    unregister_runner("missing-runtime")
    unregister_runner("contract-runtime")
    set_default_registry(previous)


def config(revision=0, **fields):
    return dict(
        revision=revision,
        provider="Configured inference",
        bind="127.0.0.1",
        port=0,
        capacity=1,
        peers={"peer-a": "peer-key"},
        **fields,
    )


def runtime_config(app):
    return {"app": app, "recipe": "openai-compatible-v1"}


def url(host):
    return f"http://127.0.0.1:{host.port}"


def headers(secret=SECRET):
    return {"Authorization": "Bearer " + secret}


def test_unconfigured_state_does_not_claim_serving_or_hardware(home):
    host = InferenceHost(home)
    state = host.view()
    assert state["enabled"] is False
    assert state["listening"] is False
    assert state["actual_port"] is None
    assert state["usage"] == []
    assert state["active"] == 0
    assert state["queued"] == 0
    assert state["credentials"] == ["peer-key"]
    assert state["providers"] == ["Configured inference"]
    assert state["runtime"]["status"] == "unconfigured"
    assert state["runtime_apps"] == []
    assert "no supervised runtime selected" in state["runtime_lifecycle"]
    assert SECRET not in json.dumps(state)
    assert not host.path.exists()


@pytest.mark.asyncio
async def test_provision_requires_real_registered_runtime_files_without_starting(home):
    runner = SidecarRunner(
        app="missing-runtime", worker=home / "missing.py", python=sys.executable
    )
    register_runner(runner)
    host = InferenceHost(home)
    configured = await host.configure(config(runtime=runtime_config(runner.app)))
    assert configured["runtime"]["status"] == "configured"
    assert configured["runtime_apps"] == ["missing-runtime"]
    with pytest.raises(HostError, match="files are unavailable") as failure:
        await host.provision()
    assert failure.value.status == 503
    assert host.view()["runtime"]["status"] == "unavailable"
    assert host.view()["runtime"]["evidence"] == {"reason": "runtime_files_missing"}
    assert runner.generation == 0
    assert runner.is_alive() is False
    assert host.runner is None
    assert host.state()["enabled"] is False


@pytest.mark.asyncio
async def test_real_sidecar_supervisor_refuses_non_runtime_worker_and_cleans_up(home):
    runner = SidecarRunner(
        app="contract-runtime", worker=_sidecar_child.__file__, python=sys.executable
    )
    register_runner(runner)
    host = InferenceHost(home)
    await host.configure(config(runtime=runtime_config(runner.app)))
    prepared = await host.provision()
    assert prepared["runtime"]["status"] == "prepared"
    assert prepared["runtime"]["evidence"] == {"recipe": "openai-compatible-v1"}
    assert runner.generation == 0
    with pytest.raises(HostError, match="refused its recipe") as failure:
        await host.start()
    assert failure.value.status == 503
    assert runner.generation == 1
    assert runner.is_alive() is False
    assert host.runner is None
    assert host.port is None
    assert host.state()["enabled"] is False
    assert host.view()["runtime"]["status"] == "unavailable"
    stopped = await host.stop()
    assert stopped["runtime"]["status"] == "stopped"
    assert stopped["runtime"]["evidence"] == {"alive": False}


@pytest.mark.asyncio
async def test_readiness_is_observational_and_does_not_spawn_stopped_runtime(home):
    runner = SidecarRunner(
        app="contract-runtime", worker=_sidecar_child.__file__, python=sys.executable
    )
    register_runner(runner)
    host = InferenceHost(home)
    await host.configure(config(runtime=runtime_config(runner.app)))
    await host.provision()
    result = await host.readiness()
    assert result["runtime"]["status"] == "stopped"
    assert result["runtime"]["evidence"] == {"alive": False}
    assert runner.generation == 0
    assert runner.is_alive() is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "runtime",
    [
        {"app": "contract-runtime"},
        {"recipe": "openai-compatible-v1"},
        {"app": "contract-runtime", "recipe": "unknown"},
        {"app": "", "recipe": "openai-compatible-v1"},
        {"app": "x" * 101, "recipe": "openai-compatible-v1"},
        {"app": "contract-runtime", "recipe": "openai-compatible-v1", "secret": SECRET},
    ],
)
async def test_runtime_binding_is_exact_bounded_and_never_persists_secrets(
    home, runtime
):
    host = InferenceHost(home)
    with pytest.raises(HostError):
        await host.configure(config(runtime=runtime))
    assert host.path.exists() is False
    assert host.runner is None
    assert SECRET not in json.dumps(host.view())


@pytest.mark.asyncio
async def test_runtime_binding_survives_restart_without_claiming_process_state(home):
    runner = SidecarRunner(
        app="contract-runtime", worker=_sidecar_child.__file__, python=sys.executable
    )
    register_runner(runner)
    host = InferenceHost(home)
    configured = await host.configure(config(runtime=runtime_config(runner.app)))
    assert configured["runtime"]["status"] == "configured"
    reopened = InferenceHost(home)
    state = reopened.view()
    assert state["runtime"]["app"] == "contract-runtime"
    assert state["runtime"]["recipe"] == "openai-compatible-v1"
    assert state["runtime"]["status"] == "configured"
    assert state["runtime"]["evidence"] == {}
    assert state["runtime_apps"] == ["contract-runtime"]
    assert state["listening"] is False
    assert state["enabled"] is False
    assert runner.generation == 0


@pytest.mark.asyncio
async def test_dashboard_runtime_actions_preserve_real_unavailable_state(home):
    runner = SidecarRunner(
        app="missing-runtime", worker=home / "absent-worker.py", python=sys.executable
    )
    register_runner(runner)
    app = web.Application(middlewares=[token_auth_middleware(port=8001)])
    register(app)
    auth = {"Cookie": "gideon_token_8001=" + generate_token("runtime-owner")}
    async with TestClient(TestServer(app)) as client:
        configured = await client.post(
            PREFIX,
            headers=auth,
            json={
                "action": "configure",
                "config": config(runtime=runtime_config(runner.app)),
            },
        )
        assert configured.status == 200
        assert (await configured.json())["runtime"]["status"] == "configured"
        provisioned = await client.post(
            PREFIX, headers=auth, json={"action": "provision"}
        )
        assert provisioned.status == 503
        assert (await provisioned.json()) == {
            "error": "Configured inference runtime files are unavailable"
        }
        readiness = await client.post(
            PREFIX, headers=auth, json={"action": "readiness"}
        )
        assert readiness.status == 200
        observed = await readiness.json()
        assert observed["runtime"]["status"] == "stopped"
        assert observed["runtime"]["evidence"] == {"alive": False}
        assert observed["listening"] is False
        assert observed["enabled"] is False
        stopped = await client.post(PREFIX, headers=auth, json={"action": "stop"})
        assert stopped.status == 200
        final = await stopped.json()
        assert final["runtime"]["status"] == "stopped"
        assert final["runtime"]["evidence"] == {"alive": False}
    assert runner.generation == 0
    assert runner.is_alive() is False


@pytest.mark.asyncio
async def test_two_real_listeners_authenticate_discover_and_stop_independently(home):
    other = home / "second-allocation"
    CredentialStore(other).put(
        "peer-key", {"type": "static_token", "value": SECRET + "-other"}
    )
    first, second = InferenceHost(home), InferenceHost(other)
    await first.configure(config())
    await second.configure(config())
    await first.start()
    await second.start()
    old_url = url(first)
    try:
        assert first.port != second.port
        async with aiohttp.ClientSession() as session:
            denied = await session.get(url(first) + "/v1/models")
            assert denied.status == 401
            models = await session.get(url(first) + "/v1/models", headers=headers())
            assert models.status == 200
            data = await models.json()
            assert data["data"][0]["id"] == "configured-model"
            assert data["data"][0]["availability"] == "not_probed"
            cross = await session.get(url(second) + "/v1/models", headers=headers())
            assert cross.status == 401
            accepted = await session.get(
                url(second) + "/v1/models", headers=headers(SECRET + "-other")
            )
            assert accepted.status == 200
            await first.stop()
            assert not first.state()["enabled"]
            assert first.port is None
            with pytest.raises(aiohttp.ClientConnectorError):
                await session.get(old_url + "/v1/models", headers=headers())
            still_live = await session.get(
                url(second) + "/v1/models", headers=headers(SECRET + "-other")
            )
            assert still_live.status == 200
            assert second.state()["enabled"]
    finally:
        await first.stop()
        await second.stop()
    assert not InferenceHost(home).state()["enabled"]
    assert not InferenceHost(other).state()["enabled"]


@pytest.mark.asyncio
async def test_rotation_and_revocation_take_effect_without_restart(home):
    host = InferenceHost(home)
    await host.configure(config())
    await host.start()
    original_port = host.port
    try:
        async with aiohttp.ClientSession() as session:
            assert (
                await session.get(url(host) + "/v1/models", headers=headers())
            ).status == 200
            CredentialStore(home).put(
                "peer-key", {"type": "static_token", "value": SECRET + "-rotated"}
            )
            assert (
                await session.get(url(host) + "/v1/models", headers=headers())
            ).status == 401
            assert (
                await session.get(
                    url(host) + "/v1/models", headers=headers(SECRET + "-rotated")
                )
            ).status == 200
            CredentialStore(home).remove("peer-key")
            assert (
                await session.get(
                    url(host) + "/v1/models", headers=headers(SECRET + "-rotated")
                )
            ).status == 401
            assert host.port == original_port
            assert host.state()["usage"] == []
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_provider_failure_is_real_resolution_failure_and_persisted_unknown_usage(
    home,
):
    host = InferenceHost(home)
    await host.configure(config())
    await host.start()
    try:
        async with aiohttp.ClientSession() as session:
            response = await session.post(
                url(host) + "/v1/chat/completions",
                headers=headers(),
                json={
                    "model": "configured-model",
                    "messages": [
                        {"role": "user", "content": "Private prompt never persisted"}
                    ],
                },
            )
            assert response.status == 503
            assert await response.text() == "Configured inference provider unavailable"
        row = host.state()["usage"][0]
        assert row["peer"] == "peer-a"
        assert row["status"] == "failed"
        assert row["provider"] == "Configured inference"
        assert row["model"] == "configured-model"
        assert row["input_tokens"] is None
        assert row["output_tokens"] is None
        assert row["duration_ms"] >= 0
        assert host.view()["active"] == 0
        assert "Private prompt" not in host.path.read_text()
        assert SECRET not in host.path.read_text()
        assert not (home / "usage/turns.jsonl").exists()
    finally:
        await host.stop()
    assert InferenceHost(home).state()["usage"] == [row]


@pytest.mark.asyncio
async def test_restart_arm_disarm_idempotency_and_stale_configuration(home):
    host = InferenceHost(home)
    configured = await host.configure(config())
    assert configured["revision"] == 1
    with pytest.raises(HostError, match="changed"):
        await host.configure(config())
    active = await host.start()
    again = await host.start()
    assert active["actual_port"] == again["actual_port"]
    assert active["revision"] == again["revision"]
    with pytest.raises(HostError, match="Disarm"):
        await host.configure(config(active["revision"]))
    await host.stop(disarm=False)
    assert host.state()["enabled"]
    restarted = InferenceHost(home)
    await restarted.start()
    try:
        assert restarted.view()["listening"]
        assert restarted.view()["enabled"]
        assert restarted.view()["actual_port"] is not None
    finally:
        await restarted.stop()
    stopped = restarted.state()
    await restarted.stop()
    assert restarted.state()["revision"] == stopped["revision"]
    assert not restarted.state()["enabled"]
    assert restarted.state()["peers"] == {"peer-a": "peer-key"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("bind", "0.0.0.0"),
        ("bind", "8.8.8.8"),
        ("capacity", 0),
        ("capacity", 9),
        ("port", -1),
        ("port", 70000),
        ("peers", {}),
        ("peers", {"peer": "missing"}),
    ],
)
async def test_invalid_host_configuration_never_arms(home, field, value):
    host = InferenceHost(home)
    body = config()
    body[field] = value
    with pytest.raises((ValueError, KeyError)):
        await host.configure(body)
    assert not host.state()["enabled"]
    assert not host.path.exists()
    assert host.runner is None


@pytest.mark.asyncio
async def test_bind_collision_leaves_no_new_listener_or_enabled_marker(home):
    host = InferenceHost(home)
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        body = config()
        body["port"] = occupied.getsockname()[1]
        await host.configure(body)
        with pytest.raises(OSError):
            await host.start()
        assert not host.state()["enabled"]
        assert host.runner is None
        assert host.port is None


@pytest.mark.asyncio
async def test_request_validation_rejects_stream_tools_model_switch_and_bad_messages(
    home,
):
    host = InferenceHost(home)
    await host.configure(config())
    await host.start()
    valid = {
        "model": "configured-model",
        "messages": [{"role": "user", "content": "Hello"}],
    }
    try:
        async with aiohttp.ClientSession() as session:
            for payload in [
                {**valid, "stream": True},
                {**valid, "model": "another"},
                {**valid, "tools": []},
                {**valid, "messages": []},
                {**valid, "messages": [{"role": "tool", "content": "data"}]},
            ]:
                response = await session.post(
                    url(host) + "/v1/chat/completions", headers=headers(), json=payload
                )
                assert response.status == 400
            assert host.state()["usage"] == []
            assert host.view()["active"] == 0
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_dashboard_and_native_control_actual_listener_and_cleanup(home):
    app = web.Application(middlewares=[token_auth_middleware(port=8000)])
    register(app)
    auth = {"Cookie": "gideon_token_8000=" + generate_token("host-owner")}
    async with TestClient(TestServer(app)) as client:
        assert (await client.get(PREFIX)).status == 403
        configured = await client.post(
            PREFIX, headers=auth, json={"action": "configure", "config": config()}
        )
        assert configured.status == 200
        started = await client.post(PREFIX, headers=auth, json={"action": "start"})
        assert started.status == 200
        data = await started.json()
        assert data["listening"]
        assert data["enabled"]
        remote = f"http://127.0.0.1:{data['actual_port']}/v1/models"
        async with aiohttp.ClientSession() as session:
            assert (await session.get(remote, headers=headers())).status == 200
        tool = await create_provider().invoke("platform_inference_host", {})
        assert tool.success
        assert json.loads(tool.output)["actual_port"] == data["actual_port"]
        assert SECRET not in tool.output
        stopped = await client.post(PREFIX, headers=auth, json={"action": "stop"})
        assert stopped.status == 200
        assert not (await stopped.json())["enabled"]
        assert (
            await client.post(PREFIX, headers=auth, json={"action": "bad"})
        ).status == 400
    assert not InferenceHost(home).state()["enabled"]
