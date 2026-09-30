"""The dashboard may start a custom runner only after its owner grants the live definition."""

from __future__ import annotations

import json
import importlib
import os
import stat
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


@pytest.mark.asyncio
async def test_custom_runner_waits_for_exact_definition_consent(tmp_path, monkeypatch, request):
    home = tmp_path / "gideon-home"
    home.mkdir()
    marker = tmp_path / "runner-started.log"
    executable = tmp_path / "custom-runner"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "from pathlib import Path\n"
        f"marker = Path({str(marker)!r})\n"
        "if '--version' in sys.argv:\n"
        "    marker.open('a', encoding='utf-8').write('started\\n')\n"
        "    print('custom-runner 2.4.1')\n"
        "    raise SystemExit(0)\n"
        "marker.open('a', encoding='utf-8').write('session-started\\n')\n"
        "for line in sys.stdin:\n"
        "    frame = json.loads(line)\n"
        "    method = frame.get('method')\n"
        "    result = {'protocolVersion': 1, 'agentCapabilities': {'loadSession': False}} if method == 'initialize' else {'sessionId': 'fixture-session'} if method == 'session/new' else {}\n"
        "    print(json.dumps({'jsonrpc': '2.0', 'id': frame.get('id'), 'result': result}), flush=True)\n",
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CUSTOM_RUNNER_BIN", str(executable))
    monkeypatch.setenv("CUSTOM_RUNNER_BIN_CHANGED", str(executable))
    monkeypatch.setenv("GIDEON_AUTH_MODE", "local-token")

    from gideon.engine.agents import runners
    from gideon.interfaces.dashboard.api_version_gate import api_version_middleware
    from gideon.interfaces.dashboard.handlers.providers import (
        api_agent_provider_agents,
        api_agent_providers_list,
        api_agent_runners_list,
        runner_grant_tenant_middleware,
        register_runner_routes,
    )
    from gideon.interfaces.dashboard.token_auth import (
        generate_token,
        token_auth_middleware,
    )
    from gideon.security import runner_grants
    from gideon.security.approval_answer import OWNER, APP, Principal
    from gideon.integrations.llm.acp_agent import ACP_AGENT_CAPABILITY, AcpAgentProvider
    from gideon.integrations.llm.registry import ProviderEntry, get_default_registry

    catalog_dir = home / runners.USER_CATALOG_DIR_NAME
    catalog_dir.mkdir()
    catalog_file = catalog_dir / "local-helper.json"
    definition_data = {
        "id": "local-helper",
        "display_name": "Local Helper",
        "runtime_id": "acp:local-helper",
        "bin_names": ["local-helper"],
        "env_var": "CUSTOM_RUNNER_BIN",
        "version_args": ["--version"],
        "acp_args": ["--acp"],
        "dialect": "",
        "adapter": {
            "npm_pkg": "fixture/adapter",
            "env_var": "CUSTOM_RUNNER_ADAPTER_BIN",
            "bin_names": ["adapter-local-helper"],
            "version": "1.0.0",
            "integrity": "sha512-fixture-integrity",
        },
    }
    catalog_file.write_text(json.dumps(definition_data), encoding="utf-8")

    registry = get_default_registry()
    runtime_entry = ProviderEntry(
        name="acp:local-helper",
        type=ACP_AGENT_CAPABILITY.type,
        model="",
        options={"command": [str(executable), "--acp"], "runtime_id": "acp:local-helper"},
        declared_capabilities=ACP_AGENT_CAPABILITY.capabilities,
    )
    registry.register_entry(runtime_entry)
    request.addfinalizer(lambda: registry.unregister_entry("acp:local-helper"))

    app = web.Application(
        middlewares=[
            api_version_middleware(),
            token_auth_middleware(
                internal_paths=frozenset(),
                mixed_internal_paths=frozenset(),
                port=0,
            ),
            runner_grant_tenant_middleware,
        ]
    )
    app.router.add_get("/api/agent-runners", api_agent_runners_list)
    app.router.add_get("/api/agent-providers", api_agent_providers_list)
    app.router.add_get("/api/agent-providers/{id}/agents", api_agent_provider_agents)
    register_runner_routes(app)
    owner_token = generate_token("standalone-owner", kind="browser")
    app_token = generate_token("fixture-app", app="fixture-app")
    headers = {
        "Authorization": f"Bearer {owner_token}",
        "X-Gideon-API-Version": "1",
    }
    app_headers = {
        "Authorization": f"Bearer {app_token}",
        "X-Gideon-API-Version": "1",
    }

    import asyncio
    from gideon.core.config import AppConfig
    from gideon.engine.session import ConversationDirectory
    from gideon.integrations.acp.connection_pool import (
        AcpConnectionPool,
        get_acp_pool,
        set_acp_pool,
    )

    workspace = home / "workspace"
    workspace.mkdir()
    runtime_id = "acp:local-helper"
    previous_pool = get_acp_pool()
    pool = AcpConnectionPool(
        provider_builder=lambda selected_runtime: AcpAgentProvider(
            command=[str(executable), "--acp"],
            cwd=workspace,
            runtime_id=selected_runtime,
        ),
        start_sem=asyncio.Semaphore(1),
    )
    set_acp_pool(pool)
    sessions = ConversationDirectory(
        AppConfig(),
        provider_factory=lambda session_key=None, **kwargs: AcpAgentProvider(
            command=[str(executable), "--acp"],
            cwd=workspace,
            session_key=session_key,
            runtime_id=runtime_id,
        ),
    )

    async def spawn_runner_session(_request):
        assert await pool.warm(runtime_id) is True
        provider, created, _resumed = await sessions.get_or_create(
            "runner-owner-session",
            cwd=str(workspace),
            provider_kind=runtime_id,
        )
        sessions.release("runner-owner-session")
        if pool._background:
            await asyncio.gather(*tuple(pool._background))
        return web.json_response(
            {
                "created": created,
                "provider": provider.provider_id,
                "pid": provider._client._transport.pid,
            }
        )

    app.router.add_post("/api/test/runner-session", spawn_runner_session)

    async def close_runner_resources(_app):
        await sessions.close_all()
        await pool.shutdown()
        set_acp_pool(previous_pool)

    app.on_cleanup.append(close_runner_resources)

    async with TestClient(TestServer(app)) as client:
        assert await pool.warm("acp:local-helper") is False
        assert not marker.exists(), "startup prewarm started the custom CLI before consent"

        ready = await client.get("/api/agent-providers", headers=headers)
        assert ready.status == 200
        readiness = await ready.json()
        custom_ready = next(
            row for row in readiness["agent_providers"] if row["name"] == "acp:local-helper"
        )
        assert custom_ready["state"] == "needs_owner_approval"
        discovered = await client.get(
            "/api/agent-providers/acp:local-helper/agents", headers=headers
        )
        assert discovered.status == 409
        assert not marker.exists(), "readiness/discovery started the custom CLI before consent"

        response = await client.get("/api/agent-runners?probe=1", headers=headers)
        assert response.status == 200
        before = next(
            row for row in (await response.json())["runners"] if row["id"] == "local-helper"
        )
        assert before["health"]["probe"] == "owner-consent"
        assert before["owner_grant"]["required"] is True
        assert before["owner_grant"]["allowed"] is False
        assert not marker.exists(), "GET/readiness/check started the custom CLI before consent"

        denied = await client.post(
            "/api/agent-runners/local-helper/grant",
            headers=app_headers,
            json={"approved": True, "expected_revision": before["owner_grant"]["revision"]},
        )
        assert denied.status == 403
        assert not marker.exists()

        stale = await client.post(
            "/api/agent-runners/local-helper/grant",
            headers=headers,
            json={"approved": True, "expected_revision": "stale-revision"},
        )
        assert stale.status == 409
        assert not marker.exists()

        granted = await client.post(
            "/api/agent-runners/local-helper/grant",
            headers=headers,
            json={"approved": True, "expected_revision": before["owner_grant"]["revision"]},
        )
        assert granted.status == 200
        assert (await granted.json())["ok"] is True
        current_definition = runners.catalog()["local-helper"]
        runner_grants = importlib.reload(runner_grants)
        assert runner_grants.allowed(current_definition, "self-hosted")
        assert not runner_grants.allowed(current_definition), "an unbound process has no tenant authority"

        measured = await client.get("/api/agent-runners?probe=1", headers=headers)
        assert measured.status == 200
        after_grant = next(
            row for row in (await measured.json())["runners"] if row["id"] == "local-helper"
        )
        assert after_grant["health"]["ok"] is True
        assert after_grant["health"]["version"] == "2.4.1"
        assert marker.read_text(encoding="utf-8").splitlines() == ["started"]

        spawned = await client.post("/api/test/runner-session", headers=headers, json={})
        assert spawned.status == 200
        spawn_result = await spawned.json()
        assert spawn_result["created"] is True
        assert spawn_result["provider"] == runtime_id
        assert isinstance(spawn_result["pid"], int)
        session_spawn_lines = marker.read_text(encoding="utf-8").splitlines()
        assert session_spawn_lines.count("session-started") >= 1

        original_revision = after_grant["owner_grant"]["revision"]
        mutations = (
            ("version_args", ["--version", "--changed"]),
            ("env_var", "CUSTOM_RUNNER_BIN_CHANGED"),
            ("acp_args", ["--acp", "--changed"]),
            (
                "adapter",
                {
                    **definition_data["adapter"],
                    "version": "2.0.0",
                },
            ),
        )
        for field, changed in mutations:
            current = json.loads(catalog_file.read_text(encoding="utf-8"))
            current[field] = changed
            catalog_file.write_text(json.dumps(current), encoding="utf-8")
            current_definition = runners.catalog()["local-helper"]
            assert runner_grants.revision(current_definition) != original_revision
            response = await client.get("/api/agent-runners?probe=1", headers=headers)
            assert response.status == 200
            invalidated = next(
                row for row in (await response.json())["runners"] if row["id"] == "local-helper"
            )
            assert invalidated["owner_grant"]["allowed"] is False
            assert invalidated["health"]["probe"] == "owner-consent"
            assert marker.read_text(encoding="utf-8").splitlines() == session_spawn_lines

        executable.write_text(
            "#!/usr/bin/env python3\n"
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).open('a', encoding='utf-8').write('changed\\n')\n"
            "print('custom-runner 2.4.2')\n",
            encoding="utf-8",
        )
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        current_definition = runners.catalog()["local-helper"]
        assert not runner_grants.allowed(current_definition, "self-hosted")
        assert marker.read_text(encoding="utf-8").splitlines() == session_spawn_lines

        # Changing the definition invalidates the durable grant after reload too.
        current_definition = runners.catalog()["local-helper"]
        runner_grants = importlib.reload(runner_grants)
        assert not runner_grants.allowed(current_definition, "self-hosted")
        with pytest.raises(PermissionError):
            runner_grants.grant(
                current_definition,
                principal=Principal(APP, "fixture-app"),
                expected_revision=before["owner_grant"]["revision"],
            )
