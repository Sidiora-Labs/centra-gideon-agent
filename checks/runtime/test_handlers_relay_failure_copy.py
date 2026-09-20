"""Internal failures speak guidance, not tracebacks — across the dashboard handlers.

#2431 established the rail for the model-provider surfaces: raw exception text is
operator diagnostics, not user copy, so a broad ``except`` that relays a failure
routes the wording through ``relayed_failure_copy``. The remaining handler files
(skills / mcp / files / agents / agent_marketplace / tools / auth) still shipped
``str(exc)`` in their 500 payloads, so a ``RuntimeError("KeyError: 'layers' in
/Users/x/.gideon/...")`` landed verbatim in a toast.

Two layers of pinning, mirroring ``test_provider_failure_copy``:

1. **Behavioral, one per touched handler file**: an internal crash surfacing
   through the handler's 500 path answers ``UNEXPECTED_FAILURE_COPY`` on the
   wire and never the raw text. The raw text stays the caller's to LOG — these
   tests assert only the wire, envelopes unchanged (`{"error"}` flat;
   tools keeps its ``{"ok": False, "error"}`` shape).
2. **Structural, whole-file**: an AST scan over every 500-status
   ``json_response`` in the seven files — any reference to the caught
   exception in the payload must sit inside ``relayed_failure_copy(...)``.
   This keeps the fix from regressing one call-site at a time (each file has
   more 500 sites than the one its behavioral test drives).

Authored refusals are out of scope on purpose: the 404 ``not_found`` messages,
ValueError 400s, and the 409/403 install refusals keep their own words, exactly
per the ``failure_copy`` module's boundaries.
"""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from aiohttp import streams, web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

from gideon.extensions.providers.failure_copy import UNEXPECTED_FAILURE_COPY

_SECRET = "secret traceback detail"

SRC = Path(__file__).resolve().parents[2] / "runtime" / "gideon"

_HANDLER_FILES = (
    "skills.py",
    "mcp.py",
    "files.py",
    "agents.py",
    "agent_marketplace.py",
    "tools.py",
    "auth.py",
)


def _body(resp: web.Response) -> dict:
    return json.loads(resp.body.decode())


def _assert_guidance_not_leak(payload: dict) -> None:
    assert payload["error"] == UNEXPECTED_FAILURE_COPY
    assert _SECRET not in json.dumps(payload)


class _ExplodingMarketplace:
    """The smallest registrable source; its search IS the internal crash."""

    marketplace_type = "stub"
    trust_tier = "community"

    def search(self, query: str, limit: int = 20):
        raise RuntimeError(_SECRET)


@pytest.mark.asyncio
async def test_skills_search_failure_speaks_guidance(monkeypatch) -> None:
    import gideon.interfaces.dashboard.handlers.skills as skills_h
    from gideon.extensions.skills import marketplace as mp_mod
    from gideon.extensions.skills.marketplace import SkillsRegistry

    registry = SkillsRegistry()
    registry.register("boom", _ExplodingMarketplace())
    monkeypatch.setattr(mp_mod, "get_default_skills_registry", lambda: registry)

    app = web.Application()
    app["state"] = SimpleNamespace()
    req = make_mocked_request(
        "GET", "/api/skills/search?q=postgres&marketplace=boom", app=app
    )

    resp = await skills_h.api_skills_search(req)

    assert resp.status == 500
    _assert_guidance_not_leak(_body(resp))


def _json_request(method: str, path: str, body: dict):
    raw = json.dumps(body).encode("utf-8")
    payload = streams.StreamReader(Mock(), 2**16, loop=asyncio.get_event_loop())
    payload.feed_data(raw)
    payload.feed_eof()
    return make_mocked_request(
        method,
        path,
        payload=payload,
        headers={"Content-Type": "application/json", "Content-Length": str(len(raw))},
    )


@pytest.mark.asyncio
async def test_mcp_toggle_write_failure_speaks_guidance(tmp_path, monkeypatch) -> None:
    import gideon.interfaces.dashboard.handlers.mcp as mcp_h

    mcp_json = tmp_path / "mcp.json"
    mcp_json.write_text(json.dumps({"mcpServers": {"srv": {"command": "x"}}}))
    monkeypatch.setattr(mcp_h, "_GLOBAL_MCP_JSON", mcp_json)
    monkeypatch.setattr(mcp_h, "_MCP_LOCK_PATH", tmp_path / "mcp.lock")

    def _boom(data: dict) -> None:
        raise RuntimeError(_SECRET)

    monkeypatch.setattr(mcp_h, "_write_mcp_json", _boom)

    resp = await mcp_h.api_mcp_toggle(
        _json_request("POST", "/api/mcp/toggle", {"name": "srv", "enabled": False})
    )

    assert resp.status == 500
    _assert_guidance_not_leak(_body(resp))


@pytest.mark.asyncio
async def test_create_dir_failure_speaks_guidance(tmp_path, monkeypatch) -> None:
    import os

    from gideon.interfaces.dashboard.handlers import api_create_dir

    target = tmp_path / "newdir"
    real_mkdir = os.mkdir

    def fake_mkdir(path, *args, **kwargs):
        if os.path.realpath(str(path)) == os.path.realpath(str(target)):
            raise PermissionError(13, _SECRET)
        return real_mkdir(path, *args, **kwargs)

    app = web.Application()
    app.router.add_post("/api/create-dir", api_create_dir)
    async with TestClient(TestServer(app)) as client:
        monkeypatch.setattr(os, "mkdir", fake_mkdir)
        resp = await client.post("/api/create-dir", json={"path": str(target)})
        assert resp.status == 500
        _assert_guidance_not_leak(await resp.json())


@pytest.mark.asyncio
async def test_agent_config_apply_failure_speaks_guidance(tmp_path) -> None:
    from gideon.interfaces.dashboard.handlers import api_agent_config

    installed = tmp_path / "gideon.json"
    installed.write_text(json.dumps({"name": "gideon"}))

    request = _json_request(
        "PUT",
        "/api/agent/config",
        {"config": {"name": "test", "tools": [], "allowedTools": []}},
    )

    with (
        patch(
            "gideon.interfaces.dashboard.handlers._installed_agent_config",
            return_value=installed,
        ),
        patch(
            "gideon.interfaces.dashboard.handlers._find_agent_config",
            return_value=tmp_path / "defaults.json",
        ),
        patch(
            "gideon.interfaces.dashboard.handlers._reset_all_sessions",
            new_callable=AsyncMock,
            side_effect=RuntimeError(_SECRET),
        ),
        patch(
            "gideon.interfaces.dashboard.handlers.config_path",
            return_value=tmp_path / "config.json",
        ),
        patch(
            "gideon.engine.agent.get_shipped_tools",
            return_value={"tools": [], "allowedTools": []},
        ),
    ):
        resp = await api_agent_config(request)

    assert resp.status == 500
    _assert_guidance_not_leak(_body(resp))


@pytest.mark.asyncio
async def test_agent_marketplace_test_failure_speaks_guidance(monkeypatch) -> None:
    import gideon.interfaces.dashboard.handlers.agent_marketplace as am_h

    class _ExplodingSessions:
        async def get_or_create(self, session_key, agent=None):
            raise RuntimeError(_SECRET)

        def release(self, session_key):  # pragma: no cover — not reached
            pass

    defn = SimpleNamespace(system_prompt="", provider_entry="")
    marketplace = SimpleNamespace(get=lambda name: defn)
    monkeypatch.setattr(
        am_h,
        "get_default_agent_registry",
        lambda: SimpleNamespace(get=lambda n: marketplace),
    )

    app = web.Application()
    app["state"] = SimpleNamespace(sessions=_ExplodingSessions())
    req = make_mocked_request(
        "POST",
        "/api/agent-marketplace/agents/helper/test",
        match_info={"name": "helper"},
        app=app,
    )

    resp = await am_h.api_agent_marketplace_test(req)

    assert resp.status == 500
    _assert_guidance_not_leak(_body(resp))


class _ExplodingProvider:
    name = "gideon-artifacts"

    async def list_tools(self):
        from gideon.integrations.tool_providers.base import ToolDefinition

        return [
            ToolDefinition(name="artifact_list", description="d", provider=self.name)
        ]

    async def invoke(self, name, arguments):
        raise RuntimeError(_SECRET)


@pytest.mark.asyncio
async def test_tool_invoke_failure_speaks_guidance(monkeypatch) -> None:
    import gideon.interfaces.dashboard.handlers.tools as tools_h

    provider = _ExplodingProvider()
    monkeypatch.setattr(
        "gideon.integrations.tool_providers.registry.get_provider",
        lambda name: provider if name == provider.name else None,
    )
    monkeypatch.setattr(
        "gideon.integrations.tool_providers.registry.list_providers", lambda: [provider]
    )
    monkeypatch.setattr(
        "gideon.integrations.tool_providers.tool_prefs.load_disabled", lambda: set()
    )
    monkeypatch.setattr(
        "gideon.integrations.tool_providers.tool_prefs.load_disabled_providers",
        lambda: set(),
    )

    resp = await tools_h.api_tool_invoke(
        _json_request("POST", "/api/tools/invoke", {"tool": "artifact_list"})
    )

    assert resp.status == 500
    payload = _body(resp)
    assert payload["ok"] is False
    _assert_guidance_not_leak(payload)


@pytest.mark.asyncio
async def test_set_password_store_failure_speaks_guidance(monkeypatch) -> None:
    from gideon.interfaces.dashboard.handlers import auth as auth_h
    from gideon.security.auth import credentials as creds

    def _boom(username: str, password: str) -> None:
        raise creds.CredentialError(
            f"could not write /home/u/credentials.json: {_SECRET}"
        )

    monkeypatch.setattr(creds, "set_password", _boom)

    app = web.Application()
    app["port"] = 10000
    app["allowed_origins"] = {"http://localhost:10000"}
    app.router.add_post("/api/auth/password", auth_h.api_auth_set_password)
    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/api/auth/password",
            json={"username": "jordan", "password": "long-enough-pass"},
        )
        assert resp.status == 500
        _assert_guidance_not_leak(await resp.json())


def _is_json_response_call(node: ast.Call) -> bool:
    fn = node.func
    return (
        isinstance(fn, ast.Attribute)
        and fn.attr == "json_response"
        and isinstance(fn.value, ast.Name)
        and fn.value.id == "web"
    )


def _names_of(tree: ast.AST, ident: str) -> list[ast.Name]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id == ident]


def _leaky_500_sites(source: str, filename: str) -> list[str]:
    """Every 500-status ``web.json_response`` inside an ``except X as name`` whose
    payload references ``name`` OUTSIDE a ``relayed_failure_copy(...)`` call."""
    leaks: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.ExceptHandler) and node.name):
            continue
        exc_name = node.name
        for call in ast.walk(node):
            if not (isinstance(call, ast.Call) and _is_json_response_call(call)):
                continue
            status = next(
                (
                    kw.value.value
                    for kw in call.keywords
                    if kw.arg == "status" and isinstance(kw.value, ast.Constant)
                ),
                200,
            )
            if status != 500:
                continue
            payload_refs = [n for arg in call.args for n in _names_of(arg, exc_name)]
            relayed_refs = [
                n
                for arg in call.args
                for sub in ast.walk(arg)
                if isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Name)
                and sub.func.id == "relayed_failure_copy"
                for n in _names_of(sub, exc_name)
            ]
            if len(payload_refs) != len(relayed_refs):
                leaks.append(f"{filename}:{call.lineno}")
    return leaks


def test_no_handler_ships_exception_text_in_a_500_payload() -> None:
    for name in _HANDLER_FILES:
        path = SRC / "interfaces" / "dashboard" / "handlers" / name
        src = path.read_text(encoding="utf-8")
        leaks = _leaky_500_sites(src, name)
        assert not leaks, f"raw exception text reaches a 500 payload at: {leaks}"
        assert (
            "from gideon.extensions.providers.failure_copy import relayed_failure_copy"
            in src
        ), name


def test_self_check_the_scan_still_sees_a_leak() -> None:
    """Vacuity guard: the scanner recognises the shapes this rail bans."""
    bad = (
        "async def h(request):\n"
        "    try:\n"
        "        work()\n"
        "    except Exception as exc:\n"
        '        return web.json_response({"error": str(exc)[:500]}, status=500)\n'
    )
    assert _leaky_500_sites(bad, "bad.py") == ["bad.py:5"]
    fstring = (
        "async def h(request):\n"
        "    try:\n"
        "        work()\n"
        "    except Exception as redact_err:\n"
        '        return web.json_response({"error": f"failed: {redact_err}"}, status=500)\n'
    )
    assert _leaky_500_sites(fstring, "bad.py") == ["bad.py:5"]
    good = (
        "async def h(request):\n"
        "    try:\n"
        "        work()\n"
        "    except Exception as exc:\n"
        '        return web.json_response({"error": relayed_failure_copy(exc)}, status=500)\n'
    )
    assert _leaky_500_sites(good, "good.py") == []
    authored = (
        "async def h(request):\n"
        "    try:\n"
        "        work()\n"
        "    except ValueError as exc:\n"
        '        return web.json_response({"error": str(exc)}, status=400)\n'
    )
    assert _leaky_500_sites(authored, "authored.py") == []
