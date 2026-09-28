"""MCP server management handlers — probe, sync, toggle, remove."""

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from aiohttp import web

from gideon.core.http_request import read_json_body, string_field
from gideon.extensions.providers.failure_copy import relayed_failure_copy
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.security.security import (
    redact_credentials,
    redact_exfiltration_urls,
    redact_values_for_display,
)
from gideon.security.sel import sel

logger = logging.getLogger(__name__)

_VALID_MCP_NAME_RE = re.compile(r"^[@a-zA-Z0-9][@a-zA-Z0-9/_.-]*$")
_MAX_MCP_NAME_LEN = 128


def _mcp_owner(request: web.Request):
    from gideon.security.approval_answer import OWNER, of_request

    principal = of_request(request)
    return principal if principal.kind == OWNER and principal.name else None


def _mcp_owner_required(request: web.Request) -> web.Response | None:
    if _mcp_owner(request) is None:
        return web.json_response(
            {"error": "Only the authenticated owner may change MCP servers."},
            status=403,
        )
    return None


def _redact_mcp_projection(value: Any) -> Any:
    """Mask displayed MCP content while keeping server and credential references stable."""
    if isinstance(value, list):
        return [_redact_mcp_projection(item) for item in value]
    if not isinstance(value, dict):
        return redact_values_for_display(value)
    redacted = redact_values_for_display(value)
    for key in ("name", "header_credentials"):
        if key in value:
            redacted[key] = value[key]
    return redacted


def _is_valid_mcp_name(name: str) -> bool:
    """Return True if ``name`` is a well-formed, non-traversal MCP name."""
    if not name or len(name) > _MAX_MCP_NAME_LEN:
        return False
    if ".." in name:
        return False
    return "/" not in name and bool(_VALID_MCP_NAME_RE.match(name))


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
    staged: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    canonical_written = False
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
                if isinstance(spec, dict):
                    prepared = _store_mcp_spec(name, spec)
                    cservers[name] = prepared
                    staged.append((name, spec, prepared))
                else:
                    cservers[name] = spec
                moved += 1
        if moved:
            from gideon.engine.agent import _atomic_json_write

            canon.parent.mkdir(parents=True, exist_ok=True)
            _atomic_json_write(canon, cdata)
            canonical_written = True
            for name, _original, prepared in staged:
                _purge_unused_mcp_credentials(name, prepared)
            logger.info(
                "mcp: migrated %d server(s) from legacy settings/mcp.json", moved
            )
        from gideon.engine.agent import _atomic_json_write

        _atomic_json_write(legacy, {"mcpServers": {}})
    except Exception:
        if not canonical_written:
            for name, original, _prepared in staged:
                _purge_unused_mcp_credentials(name, original)
        logger.debug("mcp: legacy migration skipped", exc_info=True)


class _CanonicalMcpPath(os.PathLike[str]):
    """Resolve the canonical MCP file only when an operation uses it."""

    def __init__(self, suffix: str | None = None) -> None:
        self._suffix = suffix

    def _current(self) -> Path:
        path = _canonical_mcp_json()
        return path.with_suffix(self._suffix) if self._suffix else path

    def __fspath__(self) -> str:
        return os.fspath(self._current())

    def __str__(self) -> str:
        return str(self._current())

    def __getattr__(self, name: str):
        return getattr(self._current(), name)


class _HomeMcpPath(os.PathLike[str]):
    """Resolve an external MCP store against the current user home."""

    def __init__(self, relative: str) -> None:
        self._relative = relative

    def _current(self) -> Path:
        return Path.home() / self._relative

    def __fspath__(self) -> str:
        return os.fspath(self._current())

    def __str__(self) -> str:
        return str(self._current())

    def __getattr__(self, name: str):
        return getattr(self._current(), name)


_GLOBAL_MCP_JSON = _CanonicalMcpPath()
_MCP_LOCK_PATH = _CanonicalMcpPath(".lock")


class _McpFileLock:
    """Async context manager wrapping fcntl.flock for mcp.json."""

    async def __aenter__(self) -> None:
        import fcntl

        path = _MCP_LOCK_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        self._fd = open(path, "r")
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


def _purge_mcp_credentials(name: str, spec: dict[str, Any] | None = None) -> None:
    from gideon.core.config.secret_refs import SecretOwner, purge
    from gideon.integrations.mcp_oauth import purge_server

    url = str((spec or {}).get("url") or (spec or {}).get("endpoint") or "")
    purge([SecretOwner("MCP", name).prefix, SecretOwner("MCP_OAUTH", name).prefix])
    purge_server(name, url)


def _mcp_auth_values(spec: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(spec, dict):
        return {}
    return {
        **(spec.get("env") if isinstance(spec.get("env"), dict) else {}),
        **(spec.get("headers") if isinstance(spec.get("headers"), dict) else {}),
        **(spec.get("oauth") if isinstance(spec.get("oauth"), dict) else {}),
    }


def _store_mcp_spec(name: str, spec: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    from gideon.extensions.providers.mcp_instances import store_server_credentials

    return store_server_credentials(name, spec, previous=previous)


def _owner_allowed_mcp_spec(name: str, spec: dict[str, Any] | None) -> bool:
    if not isinstance(spec, dict):
        return False
    from gideon.security.mcp_grants import allowed
    from gideon.security.mcp_grants import revision

    current = _load_json_or_empty(_canonical_mcp_json()).get("mcpServers", {}).get(name)
    if not isinstance(current, dict):
        return False
    candidate = {**current, "name": name, "source": "mcp.json"}
    proposed = {**spec, "name": name, "source": "mcp.json"}
    try:
        return revision(candidate) == revision(proposed) and allowed(candidate)
    except (TypeError, ValueError):
        return False


def _purge_unused_mcp_credentials(name: str, spec: dict[str, Any] | None) -> None:
    from gideon.core.config.secret_refs import SecretOwner, purge_unused

    purge_unused(SecretOwner("MCP", name), _mcp_auth_values(spec))


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
        source_spec = _load_json_or_empty(_canonical_mcp_json()).get("mcpServers", {}).get(name)
        if not _owner_allowed_mcp_spec(name, source_spec):
            changed = mcp_servers.pop(name, None) is not None
            remove = True
        else:
            candidate = {k: v for k, v in source_spec.items() if k != "disabled"}
            stored_spec = _store_mcp_spec(name, candidate)
            if mcp_servers.get(name) != stored_spec:
                mcp_servers[name] = stored_spec
                changed = True
        for key in ("tools", "allowedTools"):
            lst = cfg.setdefault(key, [])
            if not remove and tool_ref not in lst:
                lst.append(tool_ref)
                changed = True
            elif remove and tool_ref in lst:
                lst[:] = [item for item in lst if item != tool_ref]
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
        if enabled and not remove and name in cfg.get("mcpServers", {}):
            _purge_unused_mcp_credentials(name, cfg["mcpServers"][name])
    except OSError as exc:
        if enabled and not remove:
            _purge_unused_mcp_credentials(name, source_spec)
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
    staged_specs: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    if enabled:
        mcp_servers = cfg.setdefault("mcpServers", {})
        try:
            gdata = json.loads(_GLOBAL_MCP_JSON.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            gdata = {}
        for name in names:
            spec = gdata.get("mcpServers", {}).get(name, {})
            if not isinstance(spec, dict) or not _owner_allowed_mcp_spec(name, spec):
                if mcp_servers.pop(name, None) is not None:
                    changed = True
                for field in ("tools", "allowedTools"):
                    before = cfg.get(field, [])
                    cfg[field] = [item for item in before if item != f"@{name}"]
                    changed |= cfg[field] != before
                continue
            candidate = {k: v for k, v in spec.items() if k != "disabled"}
            stored_spec = _store_mcp_spec(name, candidate)
            if mcp_servers.get(name) != stored_spec:
                mcp_servers[name] = stored_spec
                staged_specs[name] = (candidate, stored_spec)
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
        for name, (_candidate, stored_spec) in staged_specs.items():
            _purge_unused_mcp_credentials(name, stored_spec)
    except OSError as exc:
        for name, (candidate, _stored_spec) in staged_specs.items():
            _purge_unused_mcp_credentials(name, candidate)
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
        result.append(_redact_mcp_projection(d))
    return web.json_response(_redact_mcp_projection(result))


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
        result.append(_redact_mcp_projection(d))
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
    return web.json_response(_redact_mcp_projection(d))


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
    return web.json_response(_redact_mcp_projection(_mcp_probe_cache))


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

    Executable values stay on the server. The response contains only a short-lived
    opaque selection ID and redacted display metadata.
    """
    from gideon.integrations.mcp_discovery import discover_importable_servers

    try:
        servers = await asyncio.to_thread(discover_importable_servers)
    except Exception as exc:
        logger.warning("discover_importable_servers failed: %s", exc)
        servers = []
    return web.json_response({"servers": _redact_mcp_projection(servers)})


async def api_mcp_sync(request: web.Request) -> web.Response:
    """POST /api/mcp/sync — apply MCP config changes and restart sessions.

    1. Discovers new MCP servers from mcp.json sources.
    2. Adds them to both gideon agent config AND global mcp.json
       (ACP agent only reads the global config).
    3. Resets all sessions so changes take effect.
    """
    denied = _mcp_owner_required(request)
    if denied is not None:
        return denied
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
    denied = _mcp_owner_required(request)
    if denied is not None:
        return denied
    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    name = string_field(body, "name")
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
    denied = _mcp_owner_required(request)
    if denied is not None:
        return denied
    try:
        body = await read_json_body(request)
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
    denied = _mcp_owner_required(request)
    if denied is not None:
        return denied
    try:
        body = await read_json_body(request)
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
    denied = _mcp_owner_required(request)
    if denied is not None:
        return denied
    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    name = string_field(body, "name")
    if not name:
        return web.json_response({"error": "name is required"}, status=400)

    logger.info("MCP remove: %s", name)

    async with _get_mcp_lock():
        try:
            data = json.loads(_GLOBAL_MCP_JSON.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            data = {"mcpServers": {}}
        removed_spec = data.get("mcpServers", {}).pop(name, None)
        removed = removed_spec is not None
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

    if removed:
        if isinstance(removed_spec, dict):
            from gideon.security.mcp_grants import revoke

            try:
                revoke({**removed_spec, "name": name, "source": "mcp.json"})
            except (TypeError, ValueError):
                pass
        _purge_mcp_credentials(name, removed_spec if isinstance(removed_spec, dict) else None)

    return web.json_response({"ok": True, "name": name, "removed": removed})


async def api_mcp_server_detail(request: web.Request) -> web.Response:
    """PUT/DELETE /api/mcp/servers/{name} — register or remove an MCP server.

    PUT registers (or updates) an MCP server definition in the global
    ``~/.gideon/mcp.json`` config.  Requires localhost + X-Internal-Secret.

    Body (PUT)::

        { "command": "node", "args": ["server.js"], "env": {"KEY": "val"} }

    DELETE removes the server from the config.
    """
    denied = _mcp_owner_required(request)
    if denied is not None:
        return denied
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
            removed_spec = None
            for store in (_canonical_mcp_json(), _GLOBAL_MCP_JSON):
                try:
                    data = json.loads(store.read_text(encoding="utf-8"))
                except (FileNotFoundError, json.JSONDecodeError):
                    continue
                candidate = data.get("mcpServers", {}).pop(name, None)
                if candidate is not None:
                    _atomic_write(store, data)
                    removed = True
                    removed_spec = candidate
        in_agent = _server_in_agent_config(name)
        _sync_mcp_to_agent(name, False, remove=True)
        removed = removed or in_agent
        if removed:
            if isinstance(removed_spec, dict):
                from gideon.security.mcp_grants import revoke

                try:
                    revoke({**removed_spec, "name": name, "source": "mcp.json"})
                except (TypeError, ValueError):
                    pass
            _purge_mcp_credentials(name, removed_spec if isinstance(removed_spec, dict) else None)
            from gideon.integrations.mcp_client import get_mcp_client_registry

            get_mcp_client_registry()
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
        body = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    async with _get_mcp_lock():
        data = _load_json_for_update(_canonical_mcp_json())
        servers = data.setdefault("mcpServers", {})
        previous = servers.get(name)
        previous = previous if isinstance(previous, dict) else {}
        has_new_endpoint = "command" in body or "url" in body or "endpoint" in body
        if not previous and not has_new_endpoint:
            return web.json_response({"error": "a new server needs a command or remote URL"}, status=400)
        if "command" in body and ("url" in body or "endpoint" in body):
            return web.json_response({"error": "specify only one command or remote URL"}, status=400)
        entry = dict(previous)
        if "command" in body:
            command = body.get("command")
            if not isinstance(command, str):
                return web.json_response({"error": "command must be a string"}, status=400)
            entry.pop("url", None)
            entry["command"] = command
        if "url" in body or "endpoint" in body:
            url = body.get("url", body.get("endpoint"))
            if not isinstance(url, str):
                return web.json_response({"error": "url must be a string"}, status=400)
            entry.pop("command", None)
            entry["url"] = url
        for field in ("args", "env", "headers", "oauth", "transport"):
            if field in body:
                entry[field] = body[field]
        if bool(entry.get("command")) == bool(entry.get("url")):
            return web.json_response({"error": "specify exactly one command or remote URL"}, status=400)

        from gideon.integrations.mcp_secret_refs import store_server_credentials
        from gideon.core.config.secret_refs import SecretOwner, purge_unused

        try:
            entry = store_server_credentials(name, entry, previous=previous)
            servers[name] = entry
            _atomic_write(_canonical_mcp_json(), data)
        except (OSError, ValueError) as exc:
            retained = {
                **(previous.get("env") if isinstance(previous.get("env"), dict) else {}),
                **(previous.get("headers") if isinstance(previous.get("headers"), dict) else {}),
                **(previous.get("oauth") if isinstance(previous.get("oauth"), dict) else {}),
            }
            purge_unused(SecretOwner("MCP", name), retained)
            return web.json_response({"error": str(exc)}, status=400 if isinstance(exc, ValueError) else 500)
        retained = {
            **(entry.get("env") if isinstance(entry.get("env"), dict) else {}),
            **(entry.get("headers") if isinstance(entry.get("headers"), dict) else {}),
            **(entry.get("oauth") if isinstance(entry.get("oauth"), dict) else {}),
        }
        purge_unused(SecretOwner("MCP", name), retained)

        if previous:
            from gideon.security.mcp_grants import revoke

            try:
                revoke({**previous, "name": name, "source": "mcp.json"})
            except (TypeError, ValueError):
                pass
            old_url = str(previous.get("url") or previous.get("endpoint") or "")
            new_url = str(entry.get("url") or entry.get("endpoint") or "")
            if old_url and old_url != new_url:
                from gideon.integrations.mcp_oauth import purge_server

                purge_server(name, old_url)

    _sync_mcp_to_agent(name, True)

    logger.info("MCP register via REST: %s transport=%s", name, entry.get("transport", "stdio"))
    sel().log_api_access(
        caller="dashboard",
        operation="mcp_server_register",
        outcome="completed",
        resources=name,
    )
    from gideon.integrations.mcp_discovery import McpServerInfo

    info = McpServerInfo(
        name=name,
        command=entry.get("command", ""),
        args=entry.get("args", []),
        env=entry.get("env", {}),
        url=entry.get("url", ""),
        headers=entry.get("headers", {}),
        transport=entry.get("transport", ""),
        source="mcp.json",
    )
    from gideon.security import mcp_grants

    row = info.to_dict()
    row.update({
        "allowed": False,
        "allowRevision": mcp_grants.revision({**entry, "name": name, "source": "mcp.json"}),
        "allowQuestion": mcp_grants.question({**entry, "name": name, "source": "mcp.json"}),
    })
    from gideon.integrations.mcp_client import get_mcp_client_registry

    get_mcp_client_registry()
    return web.json_response({"ok": True, "name": name, "server": row}, status=200)


async def api_mcp_server_allow(request: web.Request) -> web.Response:
    """Allow the exact current owner-managed MCP definition after explicit review."""
    principal = _mcp_owner(request)
    if principal is None:
        return web.json_response(
            {"error": "Only the authenticated owner may allow an MCP server."},
            status=403,
        )
    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    name = request.match_info["name"].strip()
    from gideon.security import mcp_grants
    async with _get_mcp_lock():
        try:
            current = _load_json_for_update(_canonical_mcp_json()).get("mcpServers", {}).get(name)
        except ConfigUnreadable as exc:
            return web.json_response({"error": str(exc)}, status=409)
        if not isinstance(current, dict):
            return web.json_response({"error": "MCP server not found"}, status=404)
        server = {**current, "name": name, "source": "mcp.json"}
        try:
            revision = mcp_grants.revision(server)
            question = mcp_grants.question(server)
        except (mcp_grants.McpGrantDefinitionError, TypeError, ValueError) as exc:
            return web.json_response({"error": str(exc)}, status=400)
        if body.get("revision") != revision:
            return web.json_response(
                {"error": "MCP definition changed; review the current definition again", "revision": revision, "question": question},
                status=409,
            )
        if body.get("question") != question or body.get("confirmed") is not True:
            return web.json_response(
                {"error": "Explicit confirmation of the current MCP question is required", "revision": revision, "question": question},
                status=409,
            )
        try:
            mcp_grants.give(server, principal)
        except (PermissionError, OSError, ValueError) as exc:
            return web.json_response({"error": str(exc)}, status=403)
    try:
        from gideon.engine.agent import rebuild_agent_config

        await asyncio.to_thread(rebuild_agent_config)
    except Exception:
        logger.warning("MCP owner grant saved but agent config refresh failed", exc_info=True)
    return web.json_response(
        {"ok": True, "name": name, "allowed": True, "revision": revision}
    )


_CC_GLOBAL_JSON = _HomeMcpPath(".claude.json")


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

    entry = servers.get(name)
    if isinstance(entry, dict):
        from gideon.core.config.secret_refs import SecretOwner, purge_unused
        from gideon.extensions.providers.mcp_instances import store_server_credentials

        previous = existing if isinstance(existing, dict) else {}
        try:
            prepared = store_server_credentials(name, entry, previous=previous)
            servers[name] = prepared
            _atomic_write(_canonical_mcp_json(), data)
        except Exception:
            retained = {
                **(previous.get("env") if isinstance(previous.get("env"), dict) else {}),
                **(previous.get("headers") if isinstance(previous.get("headers"), dict) else {}),
                **(previous.get("oauth") if isinstance(previous.get("oauth"), dict) else {}),
            }
            purge_unused(SecretOwner("MCP", name), retained)
            raise
        retained = {
            **(prepared.get("env") if isinstance(prepared.get("env"), dict) else {}),
            **(prepared.get("headers") if isinstance(prepared.get("headers"), dict) else {}),
            **(prepared.get("oauth") if isinstance(prepared.get("oauth"), dict) else {}),
        }
        purge_unused(SecretOwner("MCP", name), retained)
    else:
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
    spec = servers.pop(name)
    _atomic_write(_canonical_mcp_json(), data)
    _purge_mcp_credentials(name, spec if isinstance(spec, dict) else None)
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
        if spec is None:
            spec = _find_server_spec_anywhere(name)
        if not _owner_allowed_mcp_spec(name, spec):
            if present:
                del servers[name]
                _atomic_write(path, data)
            return "waiting_for_owner"
        if present:
            s = servers[name]
            if isinstance(s, dict) and s.get("disabled") is True:
                s.pop("disabled", None)
                _atomic_write(path, data)
                return "enabled"
            return "noop"
        from gideon.cognition.onboarding_import.floors import strip_secrets

        safe_spec, _ = strip_secrets(spec)
        if not isinstance(safe_spec, dict):
            return "missing_spec"
        safe_spec.pop("headers", None)
        servers[name] = {k: v for k, v in safe_spec.items() if k != "disabled"}
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
    previous = dict(entry)

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
        from gideon.core.config.secret_refs import SecretOwner, purge_unused

        try:
            prepared = _store_mcp_spec(name, entry, previous)
            servers[name] = prepared
            _atomic_write(_canonical_mcp_json(), data)
        except Exception:
            purge_unused(SecretOwner("MCP", name), _mcp_auth_values(previous))
            raise
        purge_unused(SecretOwner("MCP", name), _mcp_auth_values(prepared))
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
    denied = _mcp_owner_required(request)
    if denied is not None:
        return denied
    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    if "import_id" in body:
        if set(body) != {"import_id"}:
            return web.json_response({"error": "select one server using only import_id"}, status=400)
        from gideon.integrations.mcp_secret_refs import select_import, store_server_credentials
        from gideon.integrations.mcp_discovery import McpServerInfo
        from gideon.security import mcp_grants
        from gideon.core.config.secret_refs import SecretOwner, purge_unused

        try:
            name, selected = select_import(body.get("import_id"))
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=409)
        if not _is_valid_mcp_name(name):
            return web.json_response({"error": "imported MCP server name is invalid"}, status=400)
        allowed_fields = {"command", "args", "env", "cwd", "url", "transport", "headers", "oauth"}
        entry = {key: value for key, value in selected.items() if key in allowed_fields}
        if bool(entry.get("command")) == bool(entry.get("url")):
            return web.json_response({"error": "import must define one command or remote URL"}, status=400)

        async with _get_mcp_lock():
            try:
                data = _load_json_for_update(_canonical_mcp_json())
            except ConfigUnreadable as exc:
                return web.json_response({"error": str(exc)}, status=409)
            servers = data.setdefault("mcpServers", {})
            if name in servers or _server_in_agent_config(name):
                return web.json_response({"error": "server is already configured; refresh the list"}, status=409)
            try:
                entry = store_server_credentials(name, entry)
                grant_server = {**entry, "name": name, "source": "mcp.json"}
                revision = mcp_grants.revision(grant_server)
                question = mcp_grants.question(grant_server)
                servers[name] = entry
                _atomic_write(_canonical_mcp_json(), data)
            except (OSError, ValueError, TypeError) as exc:
                purge_unused(SecretOwner("MCP", name), _mcp_auth_values(entry))
                return web.json_response({"error": str(exc)}, status=400)
        _sync_mcp_to_agent(name, True)
        info = McpServerInfo(
            name=name, command=entry.get("command", ""), args=entry.get("args", []),
            env=entry.get("env", {}), url=entry.get("url", ""),
            headers=entry.get("headers", {}), transport=entry.get("transport", ""),
            source="mcp.json",
        )
        row = info.to_dict()
        row.update({"allowed": False, "allowRevision": revision, "allowQuestion": question})
        return web.json_response({"ok": True, "name": name, "server": row})

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

            if change.get("gideon") and name not in _load_json_or_empty(_canonical_mcp_json()).get("mcpServers", {}):
                results.append({"name": name, "error": "new MCP servers must be selected by opaque import_id"})
                continue

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
