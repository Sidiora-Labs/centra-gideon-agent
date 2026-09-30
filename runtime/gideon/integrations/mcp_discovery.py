"""MCP server discovery — detects configured MCP servers and checks liveness.

Scans the agent config (``agents/defaults.json``) for ``mcpServers`` entries,
then optionally probes each server by spawning the command and sending an
MCP ``initialize`` handshake.

Used by the dashboard to show live MCP server badges and by the heartbeat
to auto-sync newly discovered servers into the agent config.
"""

import asyncio
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp

from gideon.core.cancellation import terminate_and_reap
from gideon.core.env import augmented_path
from gideon.core.layout import package_path
from gideon.engine.hooks import safe_read_file

logger = logging.getLogger(__name__)

_PROBE_TIMEOUT_SECS = 15


def _get_probe_timeout() -> int:
    try:
        from gideon.core.config.loader import AppConfig

        return AppConfig.load().dashboard.mcp_probe_timeout_secs
    except Exception:
        return _PROBE_TIMEOUT_SECS


_PROBE_TTL_SECS = 1800

SCOPE_GIDEON = "gideon"
SCOPE_LEGACY_GLOBAL = "globalMcp"
SCOPE_CC_GLOBAL = "ccGlobal"


def _mcp_sources() -> tuple[tuple[Path, str], ...]:
    from gideon.core.config.loader import config_dir

    return ((config_dir() / "mcp.json", SCOPE_GIDEON),)


class _HomePath(os.PathLike[str]):
    """Resolve a user-home path only when a discovery operation uses it."""

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


_IMPORT_SOURCES: tuple[tuple[os.PathLike[str], str], ...] = (
    (_HomePath(".claude.json"), "Claude Code"),
)


def _mcp_json_paths() -> tuple[Path, ...]:
    return tuple(p for p, _ in _mcp_sources())


@dataclass
class _ProbeResult:
    """Cached probe result for a single server."""

    status: str
    tools: list[dict[str, Any]]
    error: str
    probed_at: float


_probe_cache: dict[str, _ProbeResult] = {}


def _get_cached(name: str) -> tuple[str, list[dict[str, Any]], str]:
    """Return (status, tools, error) from cache.

    If within TTL: returns original status + tools.
    If expired: returns "outdated" + tools (tools always preserved).
    If not cached: returns ("unknown", [], "").
    """
    cached = _probe_cache.get(name)
    if cached is None:
        return "unknown", [], ""
    age = time.monotonic() - cached.probed_at
    if age <= _PROBE_TTL_SECS:
        return cached.status, cached.tools, cached.error
    return "outdated", cached.tools, ""


def _cache_probe(server: "McpServerInfo") -> None:
    """Store probe result in cache."""
    _probe_cache[server.name] = _ProbeResult(
        status=server.status,
        tools=list(server.tools),
        error=server.error,
        probed_at=time.monotonic(),
    )


@dataclass
class McpServerInfo:
    """Metadata for a single MCP server (local stdio or remote HTTP)."""

    name: str
    command: str = ""
    args: list[str] | None = None
    env: dict[str, str] = field(default_factory=dict)
    cwd: str = ""
    url: str = ""
    transport: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    oauth: dict[str, Any] = field(default_factory=dict)
    allowElicitation: bool = False  # noqa: N815
    poolable: bool = False
    status: str = "unknown"
    tools: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    source: str = "agent"
    presence: dict[str, bool] = field(
        default_factory=lambda: {
            SCOPE_GIDEON: False,
            SCOPE_LEGACY_GLOBAL: False,
            SCOPE_CC_GLOBAL: False,
        }
    )
    disabled_tools: list[str] = field(default_factory=list)

    @property
    def is_remote(self) -> bool:
        """True for Streamable HTTP servers (url-based, no command)."""
        return bool(self.url) and not self.command

    def to_dict(self) -> dict[str, Any]:
        from gideon.security import mcp_grants

        try:
            allowed = mcp_grants.allowed(self)
            allow_revision = mcp_grants.revision(self)
            allow_question = mcp_grants.question(self)
            display = mcp_grants.display(self)
            display_valid = True
        except (mcp_grants.McpGrantDefinitionError, TypeError, ValueError):
            allowed = False
            allow_revision = ""
            allow_question = mcp_grants.WAITING_REASON
            display = {"command": "", "args": [], "url": "", "transport": "", "env": [], "headers": [], "header_credentials": [], "oauth": [], "poolable": False}
            display_valid = False
        from gideon.extensions.providers.mcp_instances import _display_args, _display_command
        from gideon.integrations.mcp_secret_refs import safe_display_url

        safe_command = _display_command(display["command"])
        safe_args = _display_args(display["args"])
        safe_url = safe_display_url(display["url"])
        if display_valid:
            safe_review = {
                "name": self.name,
                "source": self.source,
                "transport": display["transport"],
                "command": safe_command,
                "args": safe_args,
                "cwd": self.cwd,
                "url": safe_url,
                "env": {name: "" for name in display["env"]},
                "headers": {name: "" for name in display["headers"]},
                "oauth": {name: "" for name in display["oauth"]},
                "allowElicitation": self.allowElicitation,
                "poolable": display["poolable"],
            }
            allow_question = mcp_grants.question(safe_review)

        d: dict[str, Any] = {
            "name": self.name,
            "command": safe_command,
            "args": safe_args,
            "transport": display["transport"],
            "env": display["env"],
            "headers": display["headers"],
            "header_credentials": display["header_credentials"],
            "oauth": display["oauth"],
            "poolable": display["poolable"],
            "status": self.status,
            "tools": self.tools,
            "error": self.error,
            "source": self.source,
            "presence": dict(self.presence),
            "allowed": allowed,
            "allowRevision": allow_revision,
            "allowQuestion": allow_question,
        }
        if self.url:
            d["url"] = safe_url
        if self.cwd:
            d["cwd"] = self.cwd
        if self.disabled_tools:
            d["disabledTools"] = self.disabled_tools
        return d


def _load_agent_config() -> dict[str, Any]:
    """Load the agent config to read mcpServers.

    Merges mcpServers from project-dir (if set), bundled defaults.json,
    AND the installed gideon.json — because defaults.json may not have
    mcpServers (they're added dynamically at install time by ``gideon setup``).
    """
    configs: list[dict[str, Any]] = []

    proj = os.environ.get("GIDEON_PROJECT_DIR")
    if proj:
        p = Path(proj) / "agents" / "defaults.json"
        if p.is_file():
            try:
                configs.append(json.loads(p.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                pass

    if not configs:
        bundled = package_path("core", "config", "defaults.json")
        if bundled.is_file():
            try:
                configs.append(json.loads(bundled.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                pass

    from gideon.core.config.loader import config_dir
    from gideon.engine.agent import (
        AGENT_FILENAME,
    )

    installed = config_dir() / "agents" / AGENT_FILENAME
    if installed.is_file():
        try:
            configs.append(json.loads(installed.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass

    if not configs:
        return {}

    merged = dict(configs[0])
    mcp: dict[str, Any] = dict(merged.get("mcpServers", {}))
    for cfg in configs[1:]:
        for name, spec in cfg.get("mcpServers", {}).items():
            if name not in mcp:
                mcp[name] = spec
    merged["mcpServers"] = mcp
    return merged


def _load_mcp_json_by_source() -> dict[str, dict[str, Any]]:
    """Return ``{scope: {name: spec}}`` keyed by scope name.

    Reads every well-known MCP config location and bucketizes servers by
    their origin scope.  Unlike :func:`_load_mcp_json`, no cross-source
    merging happens — callers that need per-scope presence use this.

    Iterates :func:`_mcp_sources` (path + scope pairs), so paths and scope
    labels can never drift.  When tests monkeypatch :func:`_mcp_json_paths`
    to a shorter tuple for isolation, the corresponding scopes are
    recovered by looking up each patched path in ``_mcp_sources()``; any
    unknown path falls back to :data:`SCOPE_GIDEON`.
    """
    result: dict[str, dict[str, Any]] = {
        SCOPE_GIDEON: {},
        SCOPE_LEGACY_GLOBAL: {},
    }
    path_to_scope = {p: scope for p, scope in _mcp_sources()}
    for p in _mcp_json_paths():
        scope = path_to_scope.get(p, SCOPE_GIDEON)
        if not p.is_file():
            continue
        try:
            if scope == SCOPE_GIDEON and p == _mcp_sources()[0][0]:
                from gideon.extensions.providers.mcp_instances import _load

                data = _load()
            else:
                data = json.loads(safe_read_file(str(p)))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load MCP config from %s: %s", p, exc)
            continue
        if not isinstance(data, dict):
            continue
        servers = data.get("mcpServers", {})
        if isinstance(servers, dict):
            bucket = result[scope]
            for name, spec in servers.items():
                bucket.setdefault(name, spec)
    return result


def _load_mcp_json() -> dict[str, Any]:
    """Load and merge mcpServers from all well-known mcp.json locations.

    Earlier paths take precedence — if the same server name appears in
    multiple files, the first definition wins (via ``setdefault``).
    Retained for callers that only need a merged view; use
    :func:`_load_mcp_json_by_source` when per-scope presence matters.
    """
    merged: dict[str, Any] = {}
    by_source = _load_mcp_json_by_source()
    for scope in (SCOPE_GIDEON, SCOPE_LEGACY_GLOBAL):
        for name, spec in by_source.get(scope, {}).items():
            merged.setdefault(name, spec)
    return merged


def _server_from_spec(name: str, spec: dict, source: str) -> McpServerInfo:
    return McpServerInfo(
        name=name,
        command=spec.get("command", ""),
        args=spec.get("args", []),
        env=spec.get("env", {}),
        cwd=spec.get("cwd", ""),
        url=spec.get("url", ""),
        transport=spec.get("transport", ""),
        headers=spec.get("headers", {}),
        oauth=spec.get("oauth", {}),
        allowElicitation=spec.get("allowElicitation") is True,
        poolable=spec.get("poolable") is True,
        source=source,
    )


_MANAGED_SERVER_NAMES = {"gideon-core"}

_resolved_managed_bin: str | None = None


def _fix_stale_managed_command(name: str, spec: dict) -> None:
    """Re-resolve command for managed MCP servers to the running binary.

    Always re-resolves — the stored path may exist as a file/symlink but
    still crash at runtime (e.g. stale build output after a reinstall).
    The running gateway knows its own binary, so we always overwrite.
    """
    if name not in _MANAGED_SERVER_NAMES:
        return
    global _resolved_managed_bin
    if _resolved_managed_bin:
        cmd = spec.get("command", "")
        if cmd != _resolved_managed_bin:
            logger.info(
                "Re-resolved %s command: %s → %s", name, cmd, _resolved_managed_bin
            )
            spec["command"] = _resolved_managed_bin
        return
    resolved: str | None = None
    if not resolved:
        resolved = shutil.which(
            "gideon", path=augmented_path(os.environ.get("PATH", ""))
        )
    if not resolved:
        return
    _resolved_managed_bin = resolved
    cmd = spec.get("command", "")
    if cmd != resolved:
        logger.info("Re-resolved %s command: %s → %s", name, cmd, resolved)
        spec["command"] = resolved


def list_servers() -> list[McpServerInfo]:
    """Return all known MCP servers from agent config + mcp.json + CC global.

    Merges cached probe results so status/tools survive across requests.
    Populates ``presence`` for each server with booleans for whether the
    server appears in each of the three scope config files.

    Servers that live only in a provider global (e.g. a user added one via
    directly to ``~/.claude.json``) still show up
    on the dashboard so users get a full inventory from one page.
    """
    servers: dict[str, McpServerInfo] = {}
    disabled_in_agent: set[str] = set()

    by_source = _load_mcp_json_by_source()
    owner_server_names = {
        name
        for scope in (SCOPE_GIDEON, SCOPE_LEGACY_GLOBAL)
        for name in by_source.get(scope, {})
    }
    agent_cfg = _load_agent_config()
    for name, spec in agent_cfg.get("mcpServers", {}).items():
        # The canonical MCP document is the source the native client executes.
        # Generated agent copies must not shadow it for status or consent.
        if name in owner_server_names:
            continue
        if isinstance(spec, dict):
            if spec.get("disabled"):
                disabled_in_agent.add(name)
            else:
                _fix_stale_managed_command(name, spec)
                servers[name] = _server_from_spec(name, spec, "agent")

    disabled_tools_claimed: set[str] = set()
    for scope in (SCOPE_GIDEON, SCOPE_LEGACY_GLOBAL):
        for name, spec in by_source.get(scope, {}).items():
            if not isinstance(spec, dict):
                continue
            if (
                not spec.get("disabled")
                and name not in servers
                and name not in disabled_in_agent
            ):
                servers[name] = _server_from_spec(name, spec, "mcp.json")

            if (
                name in servers
                and "disabledTools" in spec
                and name not in disabled_tools_claimed
            ):
                servers[name].disabled_tools = spec.get("disabledTools", [])
                disabled_tools_claimed.add(name)

    #    rebuild".  A server present in any Gideon scope source (or already in
    agent_names = set(agent_cfg.get("mcpServers", {}).keys())
    gideon_own = by_source.get(SCOPE_GIDEON, {})
    for name, server in servers.items():
        pc_disabled = (
            isinstance(gideon_own.get(name), dict)
            and gideon_own[name].get("disabled") is True
        )
        in_any_source = (
            name in agent_names
            or name in gideon_own
            or name in by_source.get(SCOPE_LEGACY_GLOBAL, {})
        )
        server.presence = {
            SCOPE_GIDEON: in_any_source and not pc_disabled,
            SCOPE_LEGACY_GLOBAL: name in by_source.get(SCOPE_LEGACY_GLOBAL, {}),
            SCOPE_CC_GLOBAL: False,
        }

    for s in servers.values():
        status, tools, error = _get_cached(s.name)
        s.status = status
        s.tools = tools
        s.error = error
        if not _server_allowed(s):
            s.status = "waiting"
            s.tools = []
            from gideon.security.mcp_grants import WAITING_REASON

            s.error = WAITING_REASON

    return list(servers.values())


async def _read_jsonrpc_response(resp: aiohttp.ClientResponse) -> dict:
    """Parse a JSON-RPC response from either JSON or SSE content-type.

    MCP Streamable HTTP servers may respond with ``application/json`` (single
    object) or ``text/event-stream`` (SSE with ``data:`` lines containing JSON).
    """
    ct = resp.content_type or ""
    if "text/event-stream" in ct:
        body = await resp.text()
        last: dict = {}
        for line in body.splitlines():
            if line.startswith("data:"):
                payload = line[len("data:") :].strip()
                if payload:
                    try:
                        parsed = json.loads(payload)
                        if isinstance(parsed, dict) and "id" in parsed:
                            last = parsed
                    except json.JSONDecodeError:
                        pass
        return last
    return await resp.json()


async def _probe_remote(server: McpServerInfo) -> McpServerInfo:
    """Probe a remote Streamable HTTP MCP server via POST."""
    if not _server_allowed(server):
        return _wait_for_owner(server)
    server.status = "probing"
    conn = None
    try:
        from gideon.integrations.mcp_client import McpServerConn, _gideon_mcp_specs

        spec = _gideon_mcp_specs().get(server.name)
        if not isinstance(spec, dict):
            raise ValueError("MCP server is no longer configured")
        conn = McpServerConn(server.name, spec, scope=server.source)
        tools = await conn.list_tools()
        if conn.error:
            server.status = "error"
            server.error = conn.error[:200]
        else:
            server.tools = [
                {"name": tool.name, "description": tool.description, "inputSchema": tool.input_schema}
                for tool in tools
            ]
            server.status = "ok"
        if server.status != "ok":
            _cache_probe(server)
            return server
        server.status = "ok"
    except asyncio.TimeoutError:
        server.status = "error"
        server.error = "timeout"
        logger.warning("MCP probe failed [%s]: timeout", server.name)
    except Exception as exc:
        server.status = "error"
        server.error = str(exc)[:200]
        logger.warning("MCP probe failed [%s]: %s", server.name, server.error)
    finally:
        if conn is not None:
            await conn.shutdown()

    _cache_probe(server)
    return server


async def _drain_stderr_reason(proc: Any) -> str:
    """Read whatever the server wrote to stderr, condensed to a one-line reason.

    Used when stdout is empty (server exited before responding) so the failure
    is legible — e.g. ``server exited: Node version 18 detected, requires >=20``
    instead of a bare ``no response``. Bounded read + short timeout so a server
    that holds stderr open can't hang the probe.
    """
    if proc is None or proc.stderr is None:
        return ""
    try:
        data = await asyncio.wait_for(proc.stderr.read(4096), timeout=2.0)
    except (asyncio.TimeoutError, Exception):  # noqa: BLE001
        return ""
    text = (data or b"").decode("utf-8", "replace").strip()
    if not text:
        return ""
    last = [ln.strip() for ln in text.splitlines() if ln.strip()]
    reason = last[-1] if last else text
    return f"server exited: {reason[:200]}"


async def probe_server(server: McpServerInfo) -> McpServerInfo:
    """Probe a single MCP server by spawning it and sending initialize.

    Updates server.status and server.tools in place and returns it.
    """
    if not _server_allowed(server):
        return _wait_for_owner(server)
    if server.is_remote:
        return await _probe_remote(server)

    if not server.command:
        server.status = "error"
        server.error = "no command"
        logger.warning("MCP probe failed [%s]: no command configured", server.name)
        return server

    server.status = "probing"
    proc = None
    try:
        from gideon.security.sandbox import build_child_env

        server_env = dict(server.env)
        if server.source in {"mcp.json", "agent"}:
            from gideon.extensions.providers.mcp_instances import resolve_server_credentials

            server_env = resolve_server_credentials(server.name, {"env": server_env}).get("env", {})
        extra_env = {k: v for k, v in server_env.items() if k != "PATH"}
        env = build_child_env(site=f"mcp-probe:{server.name}", extra=extra_env)
        env["PATH"] = augmented_path(os.environ.get("PATH", ""))
        if "PATH" in server_env:
            env["PATH"] = server_env["PATH"] + os.pathsep + env["PATH"]

        resolved = shutil.which(server.command, path=env.get("PATH"))
        if not resolved:
            server.status = "error"
            server.error = f"command not found: {server.command}"
            logger.warning(
                "MCP probe failed [%s]: command not found: %s",
                server.name,
                server.command,
            )
            return server

        from gideon.security.sandbox import PROFILE_TOOL, create_subprocess_limited

        proc = await create_subprocess_limited(
            resolved,
            *(server.args or []),
            profile=PROFILE_TOOL,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=server.cwd or None,
            limit=1024 * 1024,
        )

        init_req = (
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "gideon-probe", "version": "1.0.0"},
                    },
                }
            )
            + "\n"
        )

        assert proc.stdin is not None
        assert proc.stdout is not None
        proc.stdin.write(init_req.encode())
        await proc.stdin.drain()

        line = await asyncio.wait_for(
            proc.stdout.readline(), timeout=_get_probe_timeout()
        )
        if not line:
            server.status = "error"
            server.error = await _drain_stderr_reason(proc) or "no response"
            return server

        resp = json.loads(line.decode())
        if "error" in resp:
            server.status = "error"
            server.error = resp["error"].get("message", "unknown error")
            return server

        notif = (
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                }
            )
            + "\n"
        )
        proc.stdin.write(notif.encode())
        await proc.stdin.drain()

        list_req = (
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {},
                }
            )
            + "\n"
        )
        proc.stdin.write(list_req.encode())
        await proc.stdin.drain()

        line2 = await asyncio.wait_for(
            proc.stdout.readline(), timeout=_get_probe_timeout()
        )
        if line2:
            resp2 = json.loads(line2.decode())
            tools_data = resp2.get("result", {}).get("tools", [])
            server.tools = [
                {
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "inputSchema": t.get("inputSchema", {}),
                }
                for t in tools_data
                if isinstance(t, dict) and t.get("name")
            ]

        server.status = "ok"

    except asyncio.TimeoutError:
        server.status = "error"
        server.error = "timeout"
        logger.warning(
            "MCP probe failed [%s]: timeout after %ds",
            server.name,
            _get_probe_timeout(),
        )
    except FileNotFoundError:
        server.status = "error"
        server.error = f"command not found: {server.command}"
        logger.warning(
            "MCP probe failed [%s]: command not found: %s", server.name, server.command
        )
    except Exception as exc:
        server.status = "error"
        server.error = str(exc)[:200]
        logger.warning("MCP probe failed [%s]: %s", server.name, server.error)
    finally:
        if proc is not None and proc.returncode is None:
            await terminate_and_reap(proc, grace=5)

    _cache_probe(server)
    return server


def _server_allowed(server: McpServerInfo) -> bool:
    from gideon.security.mcp_grants import allowed

    return allowed(server)


def _wait_for_owner(server: McpServerInfo) -> McpServerInfo:
    from gideon.security.mcp_grants import WAITING, WAITING_REASON

    server.status = WAITING
    server.tools = []
    server.error = WAITING_REASON
    _cache_probe(server)
    return server


async def agent_callable_status(
    servers: list[McpServerInfo],
) -> dict[str, dict[str, Any]]:
    """Project whether configured MCP tools survive the agent's real catalog policy."""
    from gideon.integrations.tool_providers.registry import (
        ConfiguredMcpToolProvider,
        get_ownership_refusals,
        list_providers,
        resolve_tool_catalog,
    )
    from gideon.integrations.tool_providers import tool_prefs

    providers = list_providers()
    server_providers = {
        provider.name: provider
        for provider in providers
        if isinstance(provider, ConfiguredMcpToolProvider)
    }
    catalog = await resolve_tool_catalog(providers)
    disabled = tool_prefs.load_disabled()
    disabled_providers = tool_prefs.load_disabled_providers()
    callable_counts: dict[str, int] = {}
    for definition in catalog.definitions:
        provider = catalog.providers.get(definition.name)
        server = getattr(provider, "name", "")
        if server not in server_providers or server in disabled_providers:
            continue
        if tool_prefs.is_disabled(
            definition.provider or server,
            definition.name,
            disabled,
            disabled_providers,
        ):
            continue
        callable_counts[server] = callable_counts.get(server, 0) + 1

    refusals = get_ownership_refusals()
    status: dict[str, dict[str, Any]] = {}
    for server in servers:
        health_status = server.status
        health_error = server.error
        count = callable_counts.get(server.name, 0)
        if server.status == "disabled":
            reason = "MCP server is disabled."
            projected_status = "disabled"
        elif not _server_allowed(server):
            reason = server.error or "MCP owner approval is required."
            projected_status = server.status or "waiting"
        elif count:
            reason = ""
            projected_status = "ready"
        else:
            refused = next(
                (item for item in refusals if item.get("provider") == server.name),
                None,
            )
            if refused:
                reason = "MCP tools were refused by canonical ownership policy."
            elif server.name not in server_providers:
                reason = "MCP server is not present on the configured agent surface."
            elif health_status == "error":
                reason = "The connection is unavailable, so no agent-callable tool is exposed."
            else:
                reason = "No enabled portable tools are present on the agent-callable surface."
            projected_status = "unserved"
        status[server.name] = {
            "status": projected_status,
            "healthStatus": health_status,
            "healthError": health_error,
            "agentCallable": count > 0,
            "agentCallableToolCount": count,
            "unservedReason": reason,
        }
    return status


async def probe_one(name: str) -> McpServerInfo | None:
    """Probe a SINGLE configured MCP server by name — backs per-provider reconnect
    so a user can recover one timed-out server without re-probing the whole fleet
    (a slow/erroring server shouldn't force a full re-probe). Returns the probed
    info, or None if no server by that name is configured."""
    server = next((s for s in list_servers() if s.name == name), None)
    if server is None:
        return None
    try:
        return await probe_server(server)
    except Exception as exc:  # noqa: BLE001
        server.status = "error"
        server.error = str(exc)[:200]
        logger.warning("MCP probe failed [%s]: %s", name, server.error)
        return server


async def probe_all() -> list[McpServerInfo]:
    """Discover and probe all configured MCP servers."""
    servers = list_servers()
    if not servers:
        return []
    results = await asyncio.gather(
        *(probe_server(s) for s in servers),
        return_exceptions=True,
    )
    out: list[McpServerInfo] = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            servers[i].status = "error"
            servers[i].error = str(r)[:200]
            logger.warning(
                "MCP probe failed [%s]: %s", servers[i].name, servers[i].error
            )
            out.append(servers[i])
        else:
            out.append(r)  # type: ignore[arg-type]
    return out


def discover_servers_to_sync() -> list[McpServerInfo]:
    """Find MCP servers in mcp.json that need syncing to the agent config.

    Returns new servers not yet in the agent config, plus existing servers
    whose env, command, or args have diverged from the mcp.json source.
    """
    agent_cfg = _load_agent_config()
    agent_mcp = agent_cfg.get("mcpServers", {})
    agent_names = set(agent_mcp.keys())
    mcp_servers = _load_mcp_json()

    out: list[McpServerInfo] = []
    for name, spec in mcp_servers.items():
        if not isinstance(spec, dict):
            continue
        info = McpServerInfo(
            name=name,
            command=spec.get("command", ""),
            args=spec.get("args"),
            env=spec.get("env") or {},
            source="discovered",
        )
        if name not in agent_names:
            out.append(info)
        else:
            existing = agent_mcp[name]
            if not isinstance(existing, dict) or info.is_remote:
                continue
            existing_env = existing.get("env", {})
            if not isinstance(existing_env, dict):
                existing_env = {}
            if (
                not all(existing_env.get(k) == v for k, v in info.env.items())
                or info.command != existing.get("command", "")
                or (info.args is not None and info.args != existing.get("args", []))
            ):
                out.append(info)
    return out


_IMPORT_JSON_PATHS: tuple[tuple[os.PathLike[str], str], ...] = _IMPORT_SOURCES


def discover_importable_servers() -> list[dict[str, Any]]:
    """Return MCP servers configured in an external backend (e.g. Claude Code)
    that are NOT yet present in any Gideon scope — i.e. candidates the
    user can *import* into ``~/.gideon/mcp.json`` to make them callable by
    the native loop.

    Gideon does not silently load these (the native loop can't reach a
    Claude-Code-only server). The UI offers each as an explicit "Import" action
    backed by ``/api/mcp/apply``, which copies the spec into the Gideon scope.

    Each entry contains a random process-local selection ID and safe display
    metadata. Executable values remain on the server and are revalidated when
    the owner selects an entry.
    """
    # Servers already known to Gideon (own scope + legacy global + agent config)
    # are not "importable" — they're already first-class.
    known: set[str] = set(_load_mcp_json().keys())
    known |= set(_load_agent_config().get("mcpServers", {}).keys())

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path, backend in _IMPORT_JSON_PATHS:
        if not path.is_file():
            continue
        try:
            data = json.loads(safe_read_file(str(path)))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read import source %s: %s", path, exc)
            continue
        if not isinstance(data, dict):
            continue
        servers = data.get("mcpServers", {})
        if not isinstance(servers, dict):
            continue
        for name, spec in servers.items():
            if not isinstance(spec, dict) or name in known or name in seen:
                continue
            if not (spec.get("command") or spec.get("url")):
                continue
            seen.add(name)
            env = spec.get("env") if isinstance(spec.get("env"), dict) else {}
            headers = spec.get("headers") if isinstance(spec.get("headers"), dict) else {}
            from gideon.integrations.mcp_secret_refs import issue_import_id, safe_import_projection

            safe_env, skipped = {}, 0
            try:
                from gideon.cognition.onboarding_import.floors import strip_secrets

                safe_env, skipped = strip_secrets(env)
            except Exception:
                skipped = len(env)
            skipped += len(headers)
            out.append(
                {
                    "id": issue_import_id(path, backend, name, spec),
                    **safe_import_projection(
                        name=name,
                        backend=backend,
                        spec=spec,
                        secrets_skipped=skipped,
                    ),
                }
            )
    return out


def sync_to_agent_config(servers: list[McpServerInfo]) -> bool:
    """Sync discovered MCP servers into the agent config.

    Delegates to ``rebuild_agent_config()`` which is the single authoritative merge
    function.  ``rebuild_agent_config()`` reads all source files
    (``~/.gideon/mcp.json``), merges them with correct priority, resolves
    commands, injects fresh marketplace skill paths, and writes the final
    ``gideon.json``.

    Returns True if any servers were added or the config was refreshed.
    """
    from gideon.engine.agent import AGENT_FILENAME, AGENTS_DIR, rebuild_agent_config

    config_path = AGENTS_DIR / AGENT_FILENAME

    existing_names: set[str] = set()
    try:
        pre = json.loads(config_path.read_text(encoding="utf-8"))
        if isinstance(pre, dict):
            existing_names = set(pre.get("mcpServers", {}).keys())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass

    new_servers = [s for s in servers if s.name not in existing_names]
    added = bool(new_servers)

    rebuild_agent_config()

    try:
        from gideon.security.sel import sel

        sel().log_api_access(
            caller="system",
            operation="mcp_server_config_sync",
            outcome="ok",
            source="agent",
            resources=", ".join(s.name for s in servers),
        )
    except Exception:
        logger.debug("SEL audit log failed for mcp_server_config_sync", exc_info=True)

    return added or bool(servers)


def register_servers_for_cc(
    servers: list[McpServerInfo],
    mcp_json_path: Path | None = None,
) -> bool:
    """Register MCP servers in CC format (.mcp.json).

    Adds entries without removing existing ones. CC-side complement
    to sync_to_agent_config() which handles agent-side registration.

    Returns True if any servers were added or updated.
    """
    if mcp_json_path is None:
        mcp_json_path = Path.home() / ".mcp.json"

    existing: dict = {}
    if mcp_json_path.is_file():
        try:
            existing = json.loads(mcp_json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = {}

    mcp = existing.setdefault("mcpServers", {})
    changed = False

    for s in servers:
        if s.is_remote:
            entry: dict = {"url": s.url, "type": "streamable-http"}
            if s.headers:
                entry["headers"] = s.headers
        else:
            entry = {"command": s.command, "args": s.args or [], "type": "stdio"}
            if s.env:
                entry["env"] = s.env

        if s.name not in mcp or mcp[s.name] != entry:
            mcp[s.name] = entry
            changed = True
            logger.info("Registered MCP server for CC: %s", s.name)

    if changed:
        mcp_json_path.parent.mkdir(parents=True, exist_ok=True)
        from gideon.engine.agent import _atomic_json_write

        _atomic_json_write(mcp_json_path, existing)

    return changed
