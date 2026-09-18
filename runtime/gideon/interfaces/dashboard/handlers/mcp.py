"""MCP server management handlers — probe, sync, toggle, remove."""

import asyncio
import json
import logging
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from aiohttp import web

from gideon.core.cancellation import run_with_timeout
from gideon.extensions.providers.failure_copy import relayed_failure_copy
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.security.security import redact_credentials, redact_exfiltration_urls
from gideon.security.sel import sel

logger = logging.getLogger(__name__)

_VALID_MCP_NAME_RE = re.compile(r"^[@a-zA-Z0-9][@a-zA-Z0-9/_.-]*$")
_MAX_MCP_NAME_LEN = 128


def _is_valid_mcp_name(name: str) -> bool:
    """Return True if ``name`` is a well-formed, non-traversal MCP name."""
    if not name or len(name) > _MAX_MCP_NAME_LEN:
        return False
    if ".." in name:
        return False
    return bool(_VALID_MCP_NAME_RE.match(name))


def _canonical_mcp_json() -> Path:
    from gideon.core.config.loader import config_dir

    return config_dir() / "mcp.json"


def _legacy_mcp_json() -> Path:
    from gideon.core.config.loader import config_dir

    return config_dir() / "settings" / "mcp.json"


def _installed_agent_json() -> Path:
    from gideon.core.config.loader import config_dir
    from gideon.engine.agent import AGENT_FILENAME

    return config_dir() / "agents" / AGENT_FILENAME


def _migrate_legacy_mcp_json() -> None:
    """One-time fold of the legacy ``settings/mcp.json`` into the canonical file.

    Any server present only in the legacy file is copied into the canonical one
    (canonical wins on a name clash), then the legacy file is emptied so it can
    never re-diverge. No-op when the legacy file is absent/empty."""
    legacy = _legacy_mcp_json()
    try:
        if not legacy.is_file():
            return
        ldata = json.loads(legacy.read_text(encoding="utf-8"))
        lservers = ldata.get("mcpServers") or {}
        if not lservers:
            return
        canon = _canonical_mcp_json()
        cdata = json.loads(canon.read_text(encoding="utf-8")) if canon.is_file() else {}
        cservers = cdata.setdefault("mcpServers", {})
        moved = 0
        for name, spec in lservers.items():
            if name not in cservers:
                cservers[name] = spec
                moved += 1
        if moved:
            from gideon.engine.agent import _atomic_json_write

            canon.parent.mkdir(parents=True, exist_ok=True)
            _atomic_json_write(canon, cdata)
            logger.info(
                "mcp: migrated %d server(s) from legacy settings/mcp.json", moved
            )
        from gideon.engine.agent import _atomic_json_write

        _atomic_json_write(legacy, {"mcpServers": {}})
    except Exception:
        logger.debug("mcp: legacy migration skipped", exc_info=True)


_GLOBAL_MCP_JSON = _canonical_mcp_json()

_MCP_LOCK_PATH = _GLOBAL_MCP_JSON.with_suffix(".lock")


class _McpFileLock:
    """Async context manager wrapping fcntl.flock for mcp.json."""

    async def __aenter__(self) -> None:
        import fcntl

        _GLOBAL_MCP_JSON.parent.mkdir(parents=True, exist_ok=True)
        _MCP_LOCK_PATH.touch(exist_ok=True)
        self._fd = open(_MCP_LOCK_PATH, "r")
        await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: fcntl.flock(self._fd, fcntl.LOCK_EX),
        )

    async def __aexit__(self, *args: Any) -> None:
        import fcntl

        fcntl.flock(self._fd, fcntl.LOCK_UN)
        self._fd.close()


def _get_mcp_lock() -> _McpFileLock:
    """Return an MCP config file lock (compatible with bridges.py)."""
    return _McpFileLock()


def _write_mcp_json(data: dict) -> None:
    """Atomically write global mcp.json to prevent partial reads."""
    from gideon.engine.agent import _atomic_json_write

    _GLOBAL_MCP_JSON.parent.mkdir(parents=True, exist_ok=True)
    _atomic_json_write(_GLOBAL_MCP_JSON, data)


_mcp_probe_cache: list[dict] = []
_mcp_probe_ts: float = 0.0
_MCP_PROBE_CACHE_SECS = 600
_mcp_probe_in_progress = False


def _server_in_agent_config(name: str) -> bool:
    """Whether ``name`` is in the installed agent config's mcpServers — the
    ``source="agent"`` MCP servers live there, not in mcp.json, so a delete must
    check here to report honestly + actually remove them (via _sync remove)."""
    from gideon.interfaces.dashboard.handlers.agents import _installed_agent_config

    try:
        cfg = json.loads(_installed_agent_config().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    return name in (cfg.get("mcpServers") or {})


def _sync_mcp_to_agent(name: str, enabled: bool, *, remove: bool = False) -> None:
    """Sync MCP server state to gideon.json mcpServers (not tools/allowedTools)."""
    from gideon.interfaces.dashboard.handlers.agents import _installed_agent_config

    path = _installed_agent_config()
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logger.warning("Cannot read agent config %s, skipping sync: %s", path, exc)
        return

    if enabled and not remove:
        mcp_servers = cfg.setdefault("mcpServers", {})
        tool_ref = f"@{name}"
        changed = False
        if name not in mcp_servers:
            spec = _find_server_spec_anywhere(name)
            if spec:
                mcp_servers[name] = spec
                changed = True
            else:
                return
        for key in ("tools", "allowedTools"):
            lst = cfg.setdefault(key, [])
            if tool_ref not in lst:
                lst.append(tool_ref)
                changed = True
        if not changed:
            return
        sel().log_api_access(
            caller="system",
            operation="mcp_tools_added",
            outcome="ok",
            source="dashboard",
            resources=f"{tool_ref} added to tools/allowedTools",
        )
    if not enabled or remove:
        tool_ref = f"@{name}"
        cfg["tools"] = [t for t in cfg.get("tools", []) if t != tool_ref]
        cfg["allowedTools"] = [t for t in cfg.get("allowedTools", []) if t != tool_ref]
        sel().log_api_access(
            caller="system",
            operation="mcp_tools_removed",
            outcome="ok",
            source="dashboard",
            resources=f"{tool_ref} removed from tools/allowedTools",
        )
    if remove:
        cfg.get("mcpServers", {}).pop(name, None)
    try:
        from gideon.engine.agent import _atomic_json_write

        _atomic_json_write(path, cfg)
    except OSError as exc:
        logger.warning("Cannot write agent config %s: %s", path, exc)


def _sync_mcp_to_agent_batch(names: list[str], enabled: bool) -> None:
    """Batch sync multiple MCP servers to gideon.json in a single read-modify-write."""
    from gideon.interfaces.dashboard.handlers.agents import _installed_agent_config

    path = _installed_agent_config()
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logger.warning(
            "Cannot read agent config %s, skipping batch sync: %s", path, exc
        )
        return

    changed = False
    if enabled:
        mcp_servers = cfg.setdefault("mcpServers", {})
        try:
            gdata = json.loads(_GLOBAL_MCP_JSON.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            gdata = {}
        for name in names:
            if name not in mcp_servers:
                spec = gdata.get("mcpServers", {}).get(name, {})
                if not isinstance(spec, dict) or not spec:
                    continue
                mcp_servers[name] = {k: v for k, v in spec.items() if k != "disabled"}
                changed = True
            tool_ref = f"@{name}"
            for key in ("tools", "allowedTools"):
                lst = cfg.setdefault(key, [])
                if tool_ref not in lst:
                    lst.append(tool_ref)
                    changed = True
        if changed:
            sel().log_api_access(
                caller="system",
                operation="mcp_tools_added",
                outcome="ok",
                source="dashboard",
                resources=f"{', '.join(f'@{n}' for n in names)} added to tools/allowedTools",
            )
    else:
        refs_to_remove = {f"@{name}" for name in names}
        cfg["tools"] = [t for t in cfg.get("tools", []) if t not in refs_to_remove]
        cfg["allowedTools"] = [
            t for t in cfg.get("allowedTools", []) if t not in refs_to_remove
        ]
        changed = True
        sel().log_api_access(
            caller="system",
            operation="mcp_tools_removed",
            outcome="ok",
            source="dashboard",
            resources=f"{', '.join(sorted(refs_to_remove))} removed from tools/allowedTools",
        )
    if not changed:
        return
    try:
        from gideon.engine.agent import _atomic_json_write

        _atomic_json_write(path, cfg)
    except OSError as exc:
        logger.warning("Cannot write agent config %s: %s", path, exc)


async def _bg_mcp_probe() -> None:
    """Background MCP probe — populates cache at startup."""
    global _mcp_probe_ts, _mcp_probe_in_progress
    try:
        from gideon.integrations.mcp_discovery import probe_server  # noqa: F811
        from gideon.integrations.mcp_discovery import list_servers

        global_mcps: dict[str, Any] = {}
        try:
            data = json.loads(_GLOBAL_MCP_JSON.read_text(encoding="utf-8"))
            global_mcps = data.get("mcpServers", {})
        except (FileNotFoundError, json.JSONDecodeError):
            pass

        all_servers = list_servers()
        probed = await asyncio.gather(
            *(probe_server(s) for s in all_servers), return_exceptions=True
        )
        result: list[dict[str, Any]] = []
        for i, r in enumerate(probed):
            if isinstance(r, BaseException):
                s = all_servers[i]
                s.status = "error"
                s.error = str(r)[:200]
            else:
                s = r
            d = s.to_dict()
            spec = global_mcps.get(s.name, {})
            d["enabled"] = not (isinstance(spec, dict) and spec.get("disabled"))
            if isinstance(spec, dict) and spec.get("disabledTools"):
                d["disabledTools"] = spec["disabledTools"]
            result.append(d)
        _mcp_probe_cache[:] = result
        _mcp_probe_ts = time.time()
        logger.info("MCP probe complete: %d servers", len(result))
    except Exception:
        logger.debug("Background MCP probe failed", exc_info=True)
    finally:
        _mcp_probe_in_progress = False


async def api_mcp_servers(request: web.Request) -> web.Response:
    """GET /api/mcp — list configured MCP servers with enabled state.

    Reads from ``~/.gideon/mcp.json`` — the global MCP config.
    Agent-level ``mcpServers`` and ``includeMcpJson`` are merged at runtime.
    """
    global _mcp_probe_in_progress
    from gideon.integrations.mcp_discovery import list_servers

    now = time.time()
    should_reprobe = (
        now - _mcp_probe_ts > _MCP_PROBE_CACHE_SECS and not _mcp_probe_in_progress
    )

    servers = list_servers()

    cached_by_name: dict[str, dict] = {s["name"]: s for s in _mcp_probe_cache}

    if not should_reprobe and not _mcp_probe_in_progress:
        for srv in servers:
            if srv.name not in cached_by_name:
                should_reprobe = True
                break

    if should_reprobe:
        _mcp_probe_in_progress = True
        state: ConsoleState = request.app["state"]
        task = asyncio.create_task(_bg_mcp_probe())
        state._background_tasks.add(task)
        task.add_done_callback(state._background_tasks.discard)

    global_mcps: dict[str, Any] = {}
    try:
        data = json.loads(_GLOBAL_MCP_JSON.read_text(encoding="utf-8"))
        global_mcps = data.get("mcpServers", {})
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    result: list[dict] = []
    for s in servers:
        d = s.to_dict()
        cached = cached_by_name.get(s.name)
        if cached and d["status"] in ("outdated", "unknown"):
            d["status"] = cached.get("status", d["status"])
            d["tools"] = cached.get("tools", d["tools"])
            d["error"] = cached.get("error", d["error"])
        spec = global_mcps.get(s.name, {})
        is_disabled = isinstance(spec, dict) and spec.get("disabled")
        d["enabled"] = not is_disabled
        if is_disabled:
            d["status"] = "disabled"
        err = d.get("error")
        if err:
            err, _ = redact_credentials(err)
            err, _ = redact_exfiltration_urls(err)
            d["error"] = err
        result.append(d)
    return web.json_response(result)


async def api_mcp_active(request: web.Request) -> web.Response:
    """GET /api/mcp/active — return MCP servers for the current agent.

    For non-gideon agents, reads ``mcpServers`` from the agent's config
    in ``~/.gideon/agents/`` — these are the only servers the ACP agent loads
    when ``--agent <name>`` is passed.  For gideon (or no agent),
    reads from global ``~/.gideon/mcp.json`` as before.
    """
    from gideon.engine.agent import AGENTS_DIR  # noqa: F811

    agent = request.query.get("agent", "")

    if agent:
        try:
            from gideon.core.config.loader import resolve_agent_bindings  # noqa: F811
            from gideon.core.config.loader import AppConfig

            cfg = AppConfig.load()
            bindings = resolve_agent_bindings(cfg, agent)
            agent = bindings.provider_agent
        except Exception:
            pass

    if agent and agent != "gideon":
        for f in AGENTS_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data.get("name") == agent:
                    agent_mcps = data.get("mcpServers", {})
                    return web.json_response(
                        [{"name": n, "enabled": True} for n in sorted(agent_mcps)]
                    )
            except (json.JSONDecodeError, OSError):
                continue
        return web.json_response([])

    # Gideon / default: read from global mcp.json
    from gideon.integrations.mcp_discovery import list_servers  # noqa: F811

    global_mcps: dict[str, Any] = {}
    try:
        data = json.loads(_GLOBAL_MCP_JSON.read_text(encoding="utf-8"))
        global_mcps = data.get("mcpServers", {})
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    servers = list_servers()
    result: list[dict] = []
    for s in servers:
        spec = global_mcps.get(s.name, {})
        enabled = not (isinstance(spec, dict) and spec.get("disabled"))
        result.append({"name": s.name, "enabled": enabled})
    names = {r["name"] for r in result}
    for builtin in ("gideon-core",):
        if builtin not in names:
            result.insert(0, {"name": builtin, "enabled": True})
    return web.json_response(result)


async def api_mcp_probe(request: web.Request) -> web.Response:
    """POST /api/mcp/probe — probe all MCP servers and return live status.

    Merges ``enabled`` and ``disabledTools`` from global mcp.json so
    probe results don't reset user's previous enable/disable choices.
    """
    global _mcp_probe_ts
    from gideon.integrations.mcp_discovery import probe_all  # noqa: F811

    servers = await probe_all()
    global_mcps: dict[str, Any] = {}
    try:
        data = json.loads(_GLOBAL_MCP_JSON.read_text(encoding="utf-8"))
        global_mcps = data.get("mcpServers", {})
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    result: list[dict[str, Any]] = []
    for s in servers:
        d = s.to_dict()
        spec = global_mcps.get(s.name, {})
        d["enabled"] = not (isinstance(spec, dict) and spec.get("disabled"))
        if isinstance(spec, dict) and spec.get("disabledTools"):
            d["disabledTools"] = spec["disabledTools"]
        result.append(d)
    _mcp_probe_cache[:] = result
    _mcp_probe_ts = time.time()
    return web.json_response(result)


async def api_mcp_probe_one(request: web.Request) -> web.Response:
    """POST /api/mcp/probe/{name} — reconnect (re-probe) a SINGLE MCP server.

    Lets the user recover one timed-out/errored provider without re-probing the
    whole fleet (a slow server shouldn't force an all-provider re-probe). Updates
    just this server's entry in the probe cache + merges its enabled/disabledTools
    so the page reflects it immediately. 404 if no server by that name."""
    global _mcp_probe_ts
    name = request.match_info["name"].strip()
    if not name:
        return web.json_response({"error": "server name is required"}, status=400)
    from gideon.integrations.mcp_discovery import probe_one  # noqa: F811

    info = await probe_one(name)
    if info is None:
        return web.json_response(
            {"error": f"no MCP server {name!r} configured"}, status=404
        )
    d = info.to_dict()
    try:
        data = json.loads(_GLOBAL_MCP_JSON.read_text(encoding="utf-8"))
        spec = data.get("mcpServers", {}).get(name, {})
        d["enabled"] = not (isinstance(spec, dict) and spec.get("disabled"))
        if isinstance(spec, dict) and spec.get("disabledTools"):
            d["disabledTools"] = spec["disabledTools"]
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    replaced = False
    for i, row in enumerate(_mcp_probe_cache):
        if row.get("name") == name:
            _mcp_probe_cache[i] = d
            replaced = True
            break
    if not replaced:
        _mcp_probe_cache.append(d)
    _mcp_probe_ts = time.time()
    return web.json_response(d)


async def api_mcp_probe_cached(request: web.Request) -> web.Response:
    """GET /api/mcp/probe — return cached probe results (non-blocking)."""
    global _mcp_probe_in_progress
    now = time.time()
    if now - _mcp_probe_ts > _MCP_PROBE_CACHE_SECS and not _mcp_probe_in_progress:
        _mcp_probe_in_progress = True
        state: ConsoleState = request.app["state"]
        task = asyncio.create_task(_bg_mcp_probe())
        state._background_tasks.add(task)
        task.add_done_callback(state._background_tasks.discard)
    return web.json_response(_mcp_probe_cache)


async def api_mcp_pool_stats(request: web.Request) -> web.Response:
    """GET /api/mcp/pool-stats — the in-process MCP connection-pool observability tile
    (P23d): live/shared/session connection counts + lifetime spawn/reap/served/reuse
    counters. Returns ``{available:false}`` when the ``mcp`` SDK extra is absent (no
    pool exists) so the FE can show a graceful 'MCP not installed' state."""
    from gideon.integrations.mcp_client import get_mcp_client_registry

    reg = get_mcp_client_registry()
    if reg is None:
        return web.json_response({"available": False})
    return web.json_response({"available": True, **reg.pool_stats()})


async def api_mcp_importable(request: web.Request) -> web.Response:
    """GET /api/mcp/importable — MCP servers configured in an external backend
    (e.g. Claude Code) that aren't yet in any Gideon scope.

    These are NOT loaded by Gideon — the native loop can't reach a
    backend-only server. The Tools UI lists them as import suggestions; choosing
    one POSTs ``/api/mcp/apply`` with ``gideon: true`` to copy the spec
    into ``~/.gideon/mcp.json`` so it becomes a first-class Gideon server.
    """
    from gideon.integrations.mcp_discovery import discover_importable_servers

    try:
        servers = await asyncio.to_thread(discover_importable_servers)
    except Exception as exc:
        logger.warning("discover_importable_servers failed: %s", exc)
        servers = []
    return web.json_response({"servers": servers})


async def api_mcp_sync(request: web.Request) -> web.Response:
    """POST /api/mcp/sync — apply MCP config changes and restart sessions.

    1. Discovers new MCP servers from mcp.json sources.
    2. Adds them to both gideon agent config AND global mcp.json
       (ACP agent only reads the global config).
    3. Resets all sessions so changes take effect.
    """
    from gideon.integrations.mcp_discovery import (
        discover_servers_to_sync,
        register_servers_for_cc,
        sync_to_agent_config,
    )

    to_sync = discover_servers_to_sync()
    synced = 0
    if to_sync:
        ok = sync_to_agent_config(to_sync)
        if ok:
            synced = len(to_sync)
        register_servers_for_cc(to_sync)
        async with _get_mcp_lock():
            try:
                gdata = json.loads(_GLOBAL_MCP_JSON.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                gdata = {"mcpServers": {}}
            gservers = gdata.setdefault("mcpServers", {})
            for s in to_sync:
                if s.name not in gservers:
                    entry: dict[str, Any] = {"command": s.command}
                    if s.args:
                        entry["args"] = s.args
                    if s.env:
                        entry["env"] = s.env
                    gservers[s.name] = entry
            _GLOBAL_MCP_JSON.parent.mkdir(parents=True, exist_ok=True)
            _write_mcp_json(gdata)

    from gideon.interfaces.dashboard.handlers.sessions import (  # noqa: F811
        _reset_all_sessions,
    )

    sessions_reset = await _reset_all_sessions(request)
    return web.json_response(
        {
            "ok": True,
            "synced": synced,
            "servers": [s.name for s in to_sync],
            "sessions_reset": sessions_reset,
        }
    )


async def api_mcp_toggle(request: web.Request) -> web.Response:
    """POST /api/mcp/toggle — enable or disable an MCP server globally.

    1. Sets ``disabled`` in ``~/.gideon/mcp.json`` (ACP runtime).
    2. Syncs ``tools``/``allowedTools`` in ``gideon.json`` (non-ACP mode).
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    name = body.get("name", "").strip()
    enabled = body.get("enabled", True)
    if not name:
        return web.json_response({"error": "name is required"}, status=400)

    async with _get_mcp_lock():
        try:
            data = json.loads(_GLOBAL_MCP_JSON.read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {"mcpServers": {}}
        except json.JSONDecodeError:
            return web.json_response(
                {"error": "cannot parse global mcp.json"}, status=500
            )

        servers = data.setdefault("mcpServers", {})
        if name not in servers:
            from gideon.integrations.mcp_discovery import list_servers as _ls

            known = {s.name for s in _ls()}
            if name not in known:
                return web.json_response(
                    {"error": f"server {name!r} not found"}, status=404
                )
            servers[name] = {}

        spec = servers[name]
        if not isinstance(spec, dict):
            if isinstance(spec, str):
                servers[name] = spec = {"command": spec}
            else:
                return web.json_response(
                    {
                        "error": f"server {name!r} has invalid config type: {type(spec).__name__}"
                    },
                    status=500,
                )
        if enabled:
            spec.pop("disabled", None)
        else:
            spec["disabled"] = True

        try:
            _write_mcp_json(data)
        except Exception as exc:
            logger.warning("mcp: failed to write global mcp.json", exc_info=True)
            return web.json_response({"error": relayed_failure_copy(exc)}, status=500)

        from gideon.interfaces.dashboard.handlers.agents import (  # noqa: F811
            _get_config_lock,
        )

        async with _get_config_lock():
            _sync_mcp_to_agent(name, enabled)

    return web.json_response(
        {"ok": True, "name": name, "enabled": enabled, "applied": True}
    )


async def api_mcp_toggle_tool(request: web.Request) -> web.Response:
    """POST /api/mcp/toggle-tool — enable or disable a specific tool in an MCP server.

    Updates ``disabledTools`` in ``~/.gideon/mcp.json``.
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    server = body.get("server", "").strip()
    tool = body.get("tool", "").strip()
    enabled = body.get("enabled", True)
    if not server or not tool:
        return web.json_response({"error": "server and tool are required"}, status=400)

    async with _get_mcp_lock():
        try:
            data = json.loads(_GLOBAL_MCP_JSON.read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {"mcpServers": {}}
        except json.JSONDecodeError:
            return web.json_response(
                {"error": "cannot parse global mcp.json"}, status=500
            )

        servers = data.setdefault("mcpServers", {})
        if server not in servers:
            from gideon.integrations.mcp_discovery import list_servers as _ls

            known = {s.name for s in _ls()}
            if server not in known:
                return web.json_response(
                    {"error": f"server {server!r} not found"}, status=404
                )
            servers[server] = {}

        spec = servers[server]
        if not isinstance(spec, dict):
            if isinstance(spec, str):
                servers[server] = spec = {"command": spec}
            else:
                return web.json_response(
                    {
                        "error": f"server {server!r} has invalid config type: {type(spec).__name__}"
                    },
                    status=500,
                )
        disabled_tools: list[str] = spec.get("disabledTools", [])
        if enabled:
            disabled_tools = [t for t in disabled_tools if t != tool]
        else:
            if tool not in disabled_tools:
                disabled_tools.append(tool)
        if disabled_tools:
            spec["disabledTools"] = disabled_tools
        else:
            spec.pop("disabledTools", None)

        try:
            _write_mcp_json(data)
        except Exception as exc:
            logger.warning("mcp: failed to write global mcp.json", exc_info=True)
            return web.json_response({"error": relayed_failure_copy(exc)}, status=500)
    return web.json_response(
        {"ok": True, "server": server, "tool": tool, "enabled": enabled}
    )


async def api_mcp_toggle_all(request: web.Request) -> web.Response:
    """POST /api/mcp/toggle-all — enable or disable all MCP servers."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    enabled = body.get("enabled", True)

    async with _get_mcp_lock():
        try:
            data = json.loads(_GLOBAL_MCP_JSON.read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {"mcpServers": {}}
        except json.JSONDecodeError:
            return web.json_response(
                {"error": "cannot parse global mcp.json"}, status=500
            )

        servers = data.get("mcpServers", {})
        toggled: list[str] = []
        for name, spec in servers.items():
            if not isinstance(spec, dict):
                continue
            if enabled:
                spec.pop("disabled", None)
            else:
                spec["disabled"] = True
            toggled.append(name)

        try:
            _write_mcp_json(data)
        except Exception as exc:
            logger.warning("mcp: failed to write global mcp.json", exc_info=True)
            return web.json_response({"error": relayed_failure_copy(exc)}, status=500)

        from gideon.interfaces.dashboard.handlers.agents import (  # noqa: F811
            _get_config_lock,
        )

        async with _get_config_lock():
            _sync_mcp_to_agent_batch(toggled, enabled)

    return web.json_response({"ok": True, "enabled": enabled, "count": len(servers)})


async def api_mcp_remove(request: web.Request) -> web.Response:
    """POST /api/mcp/remove — uninstall an MCP server.

    Removes from ``~/.gideon/mcp.json``
    and syncs gideon.json.
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    name = body.get("name", "").strip()
    if not name:
        return web.json_response({"error": "name is required"}, status=400)

    logger.info("MCP remove: %s", name)

    try:
        proc = await asyncio.create_subprocess_exec(
            "gideon",
            "skills",
            "mcp",
            "uninstall",
            name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await run_with_timeout(proc, 30)
        rc = proc.returncode
        out = (stdout or b"").decode(errors="replace").strip()
        err = (stderr or b"").decode(errors="replace").strip()
        logger.info(
            "MCP uninstall via marketplace: rc=%d out=%s err=%s",
            rc,
            out[:100],
            err[:100],
        )
    except FileNotFoundError:
        logger.debug("gideon CLI not in PATH")
    except asyncio.TimeoutError:
        logger.warning("marketplace mcp uninstall timed out for %s", name)
    except Exception as exc:
        logger.warning("marketplace mcp uninstall failed for %s: %s", name, exc)

    async with _get_mcp_lock():
        try:
            data = json.loads(_GLOBAL_MCP_JSON.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            data = {"mcpServers": {}}
        removed = data.get("mcpServers", {}).pop(name, None) is not None
        if removed:
            _write_mcp_json(data)
            logger.info("MCP remove: removed %s from global mcp.json", name)
        else:
            logger.warning("MCP remove: %s not found in global mcp.json", name)

        from gideon.interfaces.dashboard.handlers.agents import (  # noqa: F811
            _get_config_lock,
        )

        async with _get_config_lock():
            _sync_mcp_to_agent(name, False, remove=True)

    return web.json_response({"ok": True, "name": name, "removed": removed})


async def api_mcp_server_detail(request: web.Request) -> web.Response:
    """PUT/DELETE /api/mcp/servers/{name} — register or remove an MCP server.

    PUT registers (or updates) an MCP server definition in the global
    ``~/.gideon/mcp.json`` config.  Requires localhost + X-Internal-Secret.

    Body (PUT)::

        { "command": "node", "args": ["server.js"], "env": {"KEY": "val"} }

    DELETE removes the server from the config.
    """
    name = request.match_info["name"]
    if not name or not name.strip():
        return web.json_response({"error": "server name is required"}, status=400)
    name = name.strip()
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}(:[a-zA-Z0-9_-]{1,64})?", name):
        return web.json_response(
            {
                "error": "MCP server name must be letters/digits/dashes/underscores, optionally one ':' namespace"  # noqa: E501
            },
            status=400,
        )

    if request.method == "DELETE":
        if ":" in name:
            app_name = name.split(":", 1)[0]
            from gideon.extensions.apps.manager import _read_installed

            if _read_installed(app_name) is not None:
                return web.json_response(
                    {
                        "ok": False,
                        "name": name,
                        "removed": False,
                        "ownedByApp": app_name,
                        "error": f"This MCP server is provided by the '{app_name}' app. Uninstall that app "  # noqa: E501
                        f"(Store → Library) to remove it.",
                    },
                    status=409,
                )
        async with _get_mcp_lock():
            removed = False
            for store in (_canonical_mcp_json(), _GLOBAL_MCP_JSON):
                try:
                    data = json.loads(store.read_text(encoding="utf-8"))
                except (FileNotFoundError, json.JSONDecodeError):
                    continue
                if data.get("mcpServers", {}).pop(name, None) is not None:
                    _atomic_write(store, data)
                    removed = True
        in_agent = _server_in_agent_config(name)
        _sync_mcp_to_agent(name, False, remove=True)
        removed = removed or in_agent
        sel().log_api_access(
            caller="dashboard",
            operation="mcp_server_remove",
            outcome="completed" if removed else "not_found",
            resources=name,
        )
        status = 200 if removed else 404
        return web.json_response(
            {"ok": removed, "name": name, "removed": removed}, status=status
        )

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    command = body.get("command", "")
    if not command:
        return web.json_response({"error": "command is required"}, status=400)

    entry: dict[str, Any] = {"command": command}
    if body.get("args"):
        entry["args"] = body["args"]
    if body.get("env"):
        entry["env"] = body["env"]

    async with _get_mcp_lock():
        data = _load_json_for_update(_canonical_mcp_json())
        data.setdefault("mcpServers", {})[name] = entry
        _atomic_write(_canonical_mcp_json(), data)

    _sync_mcp_to_agent(name, True)

    logger.info("MCP register via REST: %s command=%s", name, command)
    sel().log_api_access(
        caller="dashboard",
        operation="mcp_server_register",
        outcome="completed",
        resources=name,
    )
    return web.json_response({"ok": True, "name": name}, status=200)


_CC_GLOBAL_JSON = Path.home() / ".claude.json"


def _load_json_or_empty(path: Path) -> dict[str, Any]:
    """Load JSON from a path; return empty dict on missing/malformed/unreadable.

    Catches the broad ``OSError`` (not just ``FileNotFoundError``) so a
    ``PermissionError`` or ``IsADirectoryError`` on a user-owned file like
    ``~/.claude.json`` won't crash ``api_mcp_apply`` mid-batch and leave
    partially-applied changes without a rebuild.

    ⚠️ READ-ONLY. Never feed the result of this into a write: see
    ``_load_json_for_update`` for why collapsing "absent" and "unreadable" into ``{}`` is safe
    for a lookup and destructive for a read-modify-write.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


class ConfigUnreadable(Exception):
    """A config file EXISTS but could not be read or parsed — distinct from absent.

    That distinction is the entire point. ``_load_json_or_empty`` collapses both into ``{}``,
    which is right for a LOOKUP (a missing server is missing either way) and catastrophic for a
    READ-MODIFY-WRITE: an empty dict that gains one key and is written back **replaces the
    file's whole contents**.

    The file most at risk is ``~/.claude.json``, which Gideon does not own. Claude Code
    keeps far more than ``mcpServers`` there — projects, history, auth state — so one transient
    read failure (a permission blip, a lock, a concurrent write caught mid-flush and therefore
    momentarily invalid JSON) turned "enable one MCP server" into "replace the user's entire
    Claude Code config with a one-server dict", taking every other server's API keys with it.
    The write is atomic, which is exactly why there was no partial-file evidence afterwards.
    """


def _load_json_for_update(path: Path) -> dict[str, Any]:
    """Load JSON for a read-modify-write cycle. Absent → ``{}``; unreadable → raise.

    ``{}`` is only safe when the file genuinely does not exist, because then writing it CREATES
    rather than destroys. Every other failure mode must stop the write.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise ConfigUnreadable(f"{path} exists but could not be read: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigUnreadable(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigUnreadable(f"{path} holds {type(data).__name__}, not a JSON object")
    return data


def _atomic_write(path: Path, data: dict) -> None:
    """Atomic JSON write; reuses the agent helper."""
    from gideon.engine.agent import _atomic_json_write

    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_json_write(path, data)


def _find_server_spec_anywhere(name: str) -> dict | None:
    """Locate a server's full spec from any known source.

    Search order matches the Gideon merge: agent config → ~/.gideon/mcp.json
    → global settings → claude-code global.  Returns a shallow copy with
    ``disabled`` stripped (the caller decides whether to disable in its target scope).
    """
    candidates = [
        _installed_agent_json(),
        _canonical_mcp_json(),
        _GLOBAL_MCP_JSON,
        _CC_GLOBAL_JSON,
    ]
    for p in candidates:
        spec = _load_json_or_empty(p).get("mcpServers", {}).get(name)
        if isinstance(spec, dict) and (spec.get("command") or spec.get("url")):
            return {k: v for k, v in spec.items() if k != "disabled"}
    return None


def _scope_has_entry(name: str, path: Path) -> bool:
    return isinstance(_load_json_or_empty(path).get("mcpServers", {}).get(name), dict)


def _set_gideon_entry(name: str, *, enabled: bool, spec: dict | None = None) -> str:
    """Set the server's ``disabled`` state in ``~/.gideon/mcp.json``.

    When ``enabled`` is True and ``spec`` is provided, upserts the full spec
    (used for preservation copies).  When enabled is False, adds/updates the
    entry to carry ``disabled: true`` — preserves existing command/args/env
    if already present; otherwise uses ``spec`` as the seed.

    Returns a short label describing what happened: ``"added"``, ``"enabled"``,
    ``"disabled"``, or ``"noop"``.
    """
    try:
        data = _load_json_for_update(_canonical_mcp_json())
    except ConfigUnreadable as exc:
        logger.warning("mcp: refusing to rewrite %s — %s", _canonical_mcp_json(), exc)
        raise
    servers = data.setdefault("mcpServers", {})
    existing = servers.get(name)
    existing = existing if isinstance(existing, dict) else None

    if enabled:
        if existing is None and spec is None:
            return "noop"
        if existing is None:
            servers[name] = {k: v for k, v in (spec or {}).items() if k != "disabled"}
            action = "added"
        else:
            if existing.get("disabled") is True:
                existing.pop("disabled", None)
                action = "enabled"
            else:
                return "noop"
    else:
        if existing is None:
            base = spec or _find_server_spec_anywhere(name) or {}
            entry = {k: v for k, v in base.items() if k != "disabled"}
            entry["disabled"] = True
            servers[name] = entry
            action = "disabled"
        elif existing.get("disabled") is True:
            return "noop"
        else:
            existing["disabled"] = True
            action = "disabled"

    _atomic_write(_canonical_mcp_json(), data)
    return action


def _remove_gideon_entry(name: str) -> bool:
    """Delete the server from ``~/.gideon/mcp.json`` entirely.  Returns True on change."""
    try:
        data = _load_json_for_update(_canonical_mcp_json())
    except ConfigUnreadable as exc:
        logger.warning("mcp: refusing to rewrite %s — %s", _canonical_mcp_json(), exc)
        raise
    servers = data.get("mcpServers", {})
    if name not in servers:
        return False
    del servers[name]
    _atomic_write(_canonical_mcp_json(), data)
    return True


def _remove_from_agent_file(path: Path, name: str) -> bool:
    """Delete a server entry from a rendered agent file.

    Used by the uninstall path so the entry doesn't linger in
    ``~/.gideon/agents/gideon.json`` / ``~/.claude/agents/gideon.mcp.json``
    — the rebuild uses the existing agent file as its merge base, so without
    this targeted delete, additive merging would keep the entry alive.
    Returns True when the file was modified.
    """
    if not path.is_file():
        return False
    data = _load_json_or_empty(path)
    servers = data.get("mcpServers", {})
    if not isinstance(servers, dict) or name not in servers:
        return False
    del servers[name]
    _atomic_write(path, data)
    return True


def _set_scope_entry(
    path: Path, name: str, *, enabled: bool, spec: dict | None = None
) -> str:
    """Add/remove a server from a provider global file (global settings or claude-code).

    When enabled=True and the server is absent, adds the spec.  When
    enabled=False, removes the entry entirely (NOT soft-disable — the
    dashboard badge treats absent and disabled identically).

    🔴 Returns ``"unreadable"`` and writes NOTHING when the target file exists but cannot be read
    or parsed. This used to load ``{}`` and write it back with one server in it, which replaced
    the file — and one of the two files this function is called with is ``~/.claude.json``,
    which Gideon does not own. See ``ConfigUnreadable``.
    """
    try:
        data = _load_json_for_update(path)
    except ConfigUnreadable as exc:
        logger.warning("mcp: refusing to rewrite %s — %s", path, exc)
        return "unreadable"
    servers = data.setdefault("mcpServers", {})
    present = name in servers and isinstance(servers[name], dict)

    if enabled:
        if present:
            s = servers[name]
            if isinstance(s, dict) and s.get("disabled") is True:
                s.pop("disabled", None)
                _atomic_write(path, data)
                return "enabled"
            return "noop"
        if spec is None:
            spec = _find_server_spec_anywhere(name)
        if spec is None:
            return "missing_spec"
        servers[name] = {k: v for k, v in spec.items() if k != "disabled"}
        _atomic_write(path, data)
        return "added"
    if not present:
        return "noop"
    del servers[name]
    _atomic_write(path, data)
    return "removed"


def _set_tool_overrides(name: str, tool_overrides: dict[str, bool]) -> list[str]:
    """Apply per-tool enable/disable overrides to a server's entry in
    ``~/.gideon/mcp.json``.

    ``tool_overrides`` maps tool name → desired enabled state.  Disabled
    tools are added to the entry's ``disabledTools`` list; re-enabling
    removes them.  Creates the entry if absent (sourcing full spec from
    any scope so the server keeps loading).

    Returns a list of tool names whose state changed.
    """
    if not tool_overrides:
        return []
    try:
        data = _load_json_for_update(_canonical_mcp_json())
    except ConfigUnreadable as exc:
        logger.warning("mcp: refusing to rewrite %s — %s", _canonical_mcp_json(), exc)
        raise
    servers = data.setdefault("mcpServers", {})
    entry = servers.get(name)
    if not isinstance(entry, dict):
        base = _find_server_spec_anywhere(name) or {}
        entry = {k: v for k, v in base.items() if k != "disabled"}
        servers[name] = entry

    disabled = list(entry.get("disabledTools") or [])
    changed: list[str] = []
    for tool, tool_enabled in tool_overrides.items():
        if tool_enabled and tool in disabled:
            disabled.remove(tool)
            changed.append(tool)
        elif (not tool_enabled) and tool not in disabled:
            disabled.append(tool)
            changed.append(tool)

    if disabled:
        entry["disabledTools"] = disabled
    else:
        entry.pop("disabledTools", None)

    if changed:
        _atomic_write(_canonical_mcp_json(), data)
    return changed


async def api_mcp_apply(request: web.Request) -> web.Response:
    """POST /api/mcp/apply — batched per-scope apply for MCP servers.

    Request body::

        {
          "changes": [
            {
              "name": "my-mcp-server",
              "gideon": true,     // desired Gideon visibility
              "globalMcp": true,   // desired presence in ~/.gideon/mcp.json
              "ccGlobal": false,    // desired presence in ~/.claude.json
              "uninstall": false,   // optional: remove from all scopes + marketplace
              "toolOverrides": {    // optional: per-tool enable/disable
                "SkillsTool": false,
                "ReadFile": true
              }
            }
          ]
        }

    Each change is processed in the order Gideon → agent config → claude-code, with a
    preservation step first: if the user is removing the server from its
    only source AND Gideon is desired on, the full spec is copied into
    ``~/.gideon/mcp.json`` before the removal so Gideon keeps its config.

    After all changes are written, ``rebuild_agent_config`` is called once
    so the provider-native agent files (``~/.gideon/agents/gideon.json`` and
    ``~/.claude/agents/gideon.md`` + ``gideon.mcp.json``) reflect the
    new merged state.  Returns a summary with per-change outcomes.
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    changes = body.get("changes")
    if not isinstance(changes, list):
        return web.json_response({"error": "changes must be a list"}, status=400)

    results: list[dict] = []

    async with _get_mcp_lock():
        for change in changes:
            name = str(change.get("name", "")).strip()
            if not name:
                results.append({"error": "empty name", "change": change})
                continue
            if not _is_valid_mcp_name(name):
                results.append({"error": "invalid name", "name": name})
                sel().log_api_access(
                    caller="dashboard",
                    operation="mcp_apply_rejected_name",
                    outcome="denied",
                    resources=name[:64],
                )
                continue

            outcome: dict[str, Any] = {"name": name, "actions": {}}

            if change.get("uninstall"):
                outcome["actions"]["gideon"] = (
                    "removed" if _remove_gideon_entry(name) else "noop"
                )
                outcome["actions"]["globalMcp"] = _set_scope_entry(
                    _GLOBAL_MCP_JSON, name, enabled=False
                )
                outcome["actions"]["ccGlobal"] = _set_scope_entry(
                    _CC_GLOBAL_JSON, name, enabled=False
                )
                _remove_from_agent_file(_installed_agent_json(), name)
                _remove_from_agent_file(
                    Path.home() / ".claude" / "agents" / "gideon.mcp.json", name
                )
                mkt_cli = shutil.which("gideon")
                if mkt_cli:
                    try:
                        await asyncio.to_thread(
                            subprocess.run,
                            [mkt_cli, "skills", "mcp", "uninstall", name],
                            capture_output=True,
                            text=True,
                            timeout=30,
                        )
                        outcome["actions"]["marketplace"] = "uninstall_attempted"
                    except Exception as exc:
                        _urls_clean, _ = redact_exfiltration_urls(str(exc))
                        _redacted, _ = redact_credentials(_urls_clean)
                        outcome["actions"]["marketplace_error"] = _redacted
                sel().log_api_access(
                    caller="dashboard",
                    operation="mcp_uninstall",
                    outcome="ok",
                    resources=name,
                )
                results.append(outcome)
                continue

            desired_mc = bool(change.get("gideon", True))
            desired_global = bool(change.get("globalMcp", False))
            desired_cc = bool(change.get("ccGlobal", False))

            # Gideon owns a runnable copy. This is purely additive — it never
            # would be a no-op (nothing to enable in the Gideon scope).
            preserved_spec: dict | None = None
            if desired_mc and not _scope_has_entry(name, _canonical_mcp_json()):
                preserved_spec = _find_server_spec_anywhere(name)

            outcome["actions"]["gideon"] = _set_gideon_entry(
                name,
                enabled=desired_mc,
                spec=preserved_spec,
            )

            resolved_spec = _find_server_spec_anywhere(name)
            outcome["actions"]["globalMcp"] = _set_scope_entry(
                _GLOBAL_MCP_JSON,
                name,
                enabled=desired_global,
                spec=resolved_spec,
            )
            outcome["actions"]["ccGlobal"] = _set_scope_entry(
                _CC_GLOBAL_JSON,
                name,
                enabled=desired_cc,
                spec=resolved_spec,
            )

            tool_overrides = change.get("toolOverrides")
            if isinstance(tool_overrides, dict) and tool_overrides:
                sanitized: dict[str, bool] = {}
                rejected: list[str] = []
                for k, v in tool_overrides.items():
                    tool_name = str(k)
                    if _is_valid_mcp_name(tool_name):
                        sanitized[tool_name] = bool(v)
                    else:
                        rejected.append(tool_name[:64])
                if rejected:
                    outcome["actions"]["tools_rejected"] = rejected
                    sel().log_api_access(
                        caller="dashboard",
                        operation="mcp_apply_rejected_tool_name",
                        outcome="denied",
                        resources=f"{name}:{','.join(rejected)[:128]}",
                    )
                if sanitized:
                    changed_tools = _set_tool_overrides(name, sanitized)
                    if changed_tools:
                        outcome["actions"]["tools"] = changed_tools

            sel().log_api_access(
                caller="dashboard",
                operation="mcp_scope_apply",
                outcome="ok",
                resources=(
                    f"{name} "
                    f"mc={'on' if desired_mc else 'off'} "
                    f"global={'on' if desired_global else 'off'} "
                    f"cc={'on' if desired_cc else 'off'}"
                ),
            )

            results.append(outcome)

    rebuild_ok = False
    rebuild_error: str | None = None
    try:
        from gideon.engine.agent import rebuild_agent_config  # noqa: F811

        await asyncio.to_thread(rebuild_agent_config)
        rebuild_ok = True
    except Exception as exc:
        _urls_clean, _ = redact_exfiltration_urls(str(exc))
        rebuild_error, _ = redact_credentials(_urls_clean)
        logger.warning("rebuild_agent_config failed after apply: %s", exc)

    return web.json_response(
        {
            "ok": True,
            "applied": len(results),
            "results": results,
            "rebuild": {"ok": rebuild_ok, "error": rebuild_error},
        }
    )
