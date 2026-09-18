"""WebSocket PTY handler for the built-in CLI panel."""

import asyncio
import fcntl
import json
import logging
import os
import pty as _pty
import shutil
import struct
import termios
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aiohttp import web

from gideon.core.cancellation import (
    run_with_timeout,
    terminate_and_reap,
    wait_with_timeout,
)
from gideon.core.config import loader as config_loader
from gideon.engine import tmux_substrate
from gideon.http_errors import json_error


def config_path():
    """The active home, re-resolved per call — see :func:`gideon.core.config.loader.config_path`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_path()


if TYPE_CHECKING:
    from gideon.interfaces.dashboard.state import ConsoleState

logger = logging.getLogger(__name__)

_MAX_SESSIONS = 3
_ORPHAN_TIMEOUT_S = 300

_pending_cwd: dict[str, str] = {}

_pending_sandbox: dict[str, str] = {}


def _sel():
    import gideon.interfaces.dashboard.handlers as _pkg

    return _pkg.sel()


@dataclass
class _TerminalSession:
    """Server-side state for one PTY session."""

    session_id: str
    master_fd: int
    proc: asyncio.subprocess.Process
    cols: int = 80
    rows: int = 24
    created_at: float = field(default_factory=time.monotonic)
    last_ws_disconnect: float | None = None
    ws: web.WebSocketResponse | None = None
    reader_task: asyncio.Task | None = None
    cwd: str = ""
    shell: str = ""
    persistent: bool = False


def _get_registry(request: web.Request) -> dict[str, _TerminalSession | None]:
    state: "ConsoleState" = request.app["state"]
    return state._terminal_sessions


def _get_config(request: web.Request) -> dict:
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
        return data.get("dashboard", {}).get("terminal", {})
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


_TMUX_SOCKET = tmux_substrate.TMUX_SOCKET


def _tmux_available() -> bool:
    """Whether the tmux binary is on PATH (macOS/Linux only; Windows has none)."""
    return shutil.which("tmux") is not None


def _persist_enabled(request: web.Request) -> bool:
    """Persistent (tmux-backed) terminals are ON only when BOTH the opt-in config flag
    and a usable tmux binary are present — else fall back to the in-process PTY unchanged.
    """
    return bool(_get_config(request).get("persist", False)) and _tmux_available()


def _tmux_session_name(session_id: str) -> str:
    """tmux session name for a Gideon terminal id. One implementation, in `tmux_substrate`.

    Terminal names share a namespace with EI-6's durable worker sessions (same socket, same
    `gideon-` prefix), so the mapping is defined once. Two copies would eventually disagree,
    and a disagreement here means the reaper kills a session the sweep is counting on.
    """
    return tmux_substrate.terminal_session_name(session_id)


def _is_enabled(request: web.Request) -> bool:
    """Terminal PTY is enabled by default — it is the user's own interactive
    shell, powering both the freeform CLI panel and the per-provider Sign-in
    terminal (the agent-runtime auth flow depends on it). Like every route it
    is authenticated at the WS handshake via ``token_auth_middleware``.

    Opt OUT explicitly via config.json:
    {"dashboard": {"terminal": {"enabled": false}}}
    Cached for 30s to avoid disk I/O per request.
    """
    now = time.monotonic()
    if now - _enabled_cache[1] < 30:
        return _enabled_cache[0]
    result = bool(_get_config(request).get("enabled", True))
    _enabled_cache[0] = result
    _enabled_cache[1] = now
    return result


_enabled_cache: list = [True, 0.0]


async def _safe_send(sess: "_TerminalSession", data: bytes) -> None:
    """Send PTY bytes to the WS, swallowing errors (the socket may close mid-send)."""
    try:
        if sess.ws and not sess.ws.closed:
            await sess.ws.send_bytes(data)
    except Exception:
        pass


async def _kill_session(sess: _TerminalSession) -> None:
    """Kill PTY process and close FDs for a session."""
    if sess.master_fd >= 0:
        try:
            asyncio.get_running_loop().remove_reader(sess.master_fd)
        except Exception:
            pass
        try:
            os.close(sess.master_fd)
        except OSError:
            pass
        sess.master_fd = -1
    if sess.reader_task is not None:
        sess.reader_task.cancel()
        try:
            await sess.reader_task
        except (asyncio.CancelledError, Exception):
            pass
    if sess.proc is not None and sess.proc.returncode is None:
        await terminate_and_reap(sess.proc, grace=5)


async def api_terminal_ws(request: web.Request) -> web.WebSocketResponse | web.Response:
    """WebSocket PTY for the built-in CLI panel.

    Protocol:
      - Binary frames: raw terminal I/O (both directions)
      - Text frames (JSON): control messages
        - Client→Server: {"type":"resize","cols":N,"rows":N}
        - Client→Server: {"type":"ping"}
        - Server→Client: {"type":"pong"}
    """
    caller = request.get("user")
    if not caller:
        _sel().log_api_access(
            caller="unknown",
            operation="terminal.ws.open",
            outcome="denied",
            source="dashboard",
            resources=str(request.remote),
        )
        return web.Response(status=401, text="Unauthorized")
    if not _is_enabled(request):
        _sel().log_api_access(
            caller=caller,
            operation="terminal.ws.open",
            outcome="denied",
            source="dashboard",
            resources="feature_disabled",
        )
        return web.Response(status=403, text="Terminal panel disabled")

    session_id = request.match_info.get("session_id", "")
    if not session_id or len(session_id) > 64:
        _sel().log_api_access(
            caller=caller,
            operation="terminal.ws.open",
            outcome="denied",
            source="dashboard",
            resources=f"invalid_session_id={session_id!r}",
        )
        return web.Response(status=400, text="Invalid session_id")

    registry = _get_registry(request)
    cfg = _get_config(request)
    max_sessions = cfg.get("max_sessions", _MAX_SESSIONS)

    existing = registry.get(session_id)
    if existing and existing.proc.returncode is not None:
        await _kill_session(existing)
        del registry[session_id]
        existing = None

    if not existing and len(registry) >= max_sessions:
        _sel().log_api_access(
            caller=caller,
            operation="terminal.ws.open",
            outcome="denied",
            source="dashboard",
            resources=f"max_sessions={max_sessions}",
        )
        return web.Response(status=429, text=f"Max {max_sessions} terminal sessions")

    placeholder = not existing
    if placeholder:
        registry[session_id] = None

    ws = web.WebSocketResponse(heartbeat=30, timeout=300)
    try:
        await ws.prepare(request)
    except Exception:
        if placeholder:
            registry.pop(session_id, None)  # type: ignore[arg-type]
        raise

    if existing:
        existing.ws = ws
        existing.last_ws_disconnect = None
        sess = existing
        _sel().log_api_access(
            caller=caller,
            operation="terminal.ws.reconnect",
            outcome="ok",
            source="dashboard",
            resources=f"session={session_id},pid={sess.proc.pid}",
        )
    else:
        master_fd, worker_fd = _pty.openpty()
        os.set_blocking(master_fd, False)
        try:
            fcntl.ioctl(
                worker_fd,
                termios.TIOCSWINSZ,
                struct.pack("HHHH", 24, 80, 0, 0),
            )
            shell = str(cfg.get("shell") or os.environ.get("SHELL", "/bin/bash"))
            _req_cwd = _pending_cwd.pop(session_id, "")
            cwd = cfg.get("cwd") or os.environ.get("HOME", "/")
            if _req_cwd and os.path.isdir(_req_cwd):
                cwd = _req_cwd
            env = {
                **os.environ,
                "TERM": "xterm-256color",
                "GIDEON_TERMINAL": "1",
                "DISABLE_AUTO_UPDATE": "true",
            }
            persistent = _persist_enabled(request)
            if persistent:
                tname = _tmux_session_name(session_id)
                argv = [
                    "tmux",
                    "-L",
                    _TMUX_SOCKET,
                    "new-session",
                    "-A",
                    "-s",
                    tname,
                    shell,
                    "-l",
                ]
            else:
                argv = [shell, "-l"]
            _req_sandbox = _pending_sandbox.pop(session_id, "")
            if _req_sandbox:
                from gideon.integrations.sandbox_providers import (
                    SandboxSpec,
                    SandboxUnavailableError,
                    get_provider,
                )

                _provider = get_provider(_req_sandbox)
                if _provider is not None:
                    try:
                        argv = _provider.wrap(
                            SandboxSpec(workspace_dir=cwd, egress_tier="all", env={}),
                            argv,
                        ).argv
                    except SandboxUnavailableError as _exc:
                        logger.info(
                            "terminal %s: sandbox %r unavailable (%s); host shell fallback",
                            session_id,
                            _req_sandbox,
                            _exc,
                        )
            from gideon.security.sandbox import PROFILE_NONE, create_subprocess_limited

            proc = await create_subprocess_limited(
                *argv,
                profile=PROFILE_NONE,
                stdin=worker_fd,
                stdout=worker_fd,
                stderr=worker_fd,
                start_new_session=True,
                cwd=cwd,
                env=env,
            )
        except Exception as exc:
            try:
                os.close(master_fd)
            except OSError:
                pass
            registry.pop(session_id, None)  # type: ignore[arg-type]
            if not ws.closed:
                await ws.send_str(json.dumps({"type": "error", "message": str(exc)}))
                await ws.close()
            return ws
        finally:
            os.close(worker_fd)

        sess = _TerminalSession(
            session_id=session_id,
            master_fd=master_fd,
            proc=proc,
            ws=ws,
            cwd=str(cwd),
            shell=shell,
            persistent=persistent,
        )
        registry[session_id] = sess
        _sel().log_api_access(
            caller=caller,
            operation="terminal.ws.open",
            outcome="ok",
            source="dashboard",
            resources=f"session={session_id},pid={proc.pid},shell={shell}",
        )

    async def handle_exit():
        if registry.get(session_id) is not sess:
            return
        try:
            await asyncio.wait_for(sess.proc.wait(), timeout=5)
        except Exception:
            pass
        code = sess.proc.returncode if sess.proc else None
        loop_ = asyncio.get_running_loop()
        if sess.master_fd >= 0:
            try:
                loop_.remove_reader(sess.master_fd)
            except Exception:
                pass
            try:
                os.close(sess.master_fd)
            except OSError:
                pass
            sess.master_fd = -1
        registry.pop(session_id, None)
        if sess.ws and not sess.ws.closed:
            try:
                await sess.ws.send_str(json.dumps({"type": "exited", "code": code}))
                await sess.ws.close()
            except Exception:
                pass

    def on_pty_readable():
        if sess.master_fd < 0:
            return
        try:
            data = os.read(sess.master_fd, 65536)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if not data:
            try:
                asyncio.get_running_loop().remove_reader(sess.master_fd)
            except Exception:
                pass
            asyncio.ensure_future(handle_exit())
            return
        if sess.ws and not sess.ws.closed:
            asyncio.ensure_future(_safe_send(sess, data))

    _loop = asyncio.get_running_loop()
    try:
        _loop.remove_reader(sess.master_fd)
    except Exception:
        pass
    _loop.add_reader(sess.master_fd, on_pty_readable)

    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.BINARY:
                try:
                    buf = msg.data
                    while buf and sess.master_fd >= 0:
                        try:
                            n = os.write(sess.master_fd, buf)
                            buf = buf[n:]
                        except BlockingIOError:
                            await asyncio.sleep(0)
                except OSError:
                    break
            elif msg.type == web.WSMsgType.TEXT:
                try:
                    ctrl = json.loads(msg.data)
                except (json.JSONDecodeError, ValueError):
                    continue
                if ctrl.get("type") == "resize":
                    try:
                        cols = min(max(int(ctrl.get("cols", 80)), 1), 500)
                        rows = min(max(int(ctrl.get("rows", 24)), 1), 200)
                    except (ValueError, TypeError):
                        continue
                    sess.cols = cols
                    sess.rows = rows
                    try:
                        fcntl.ioctl(
                            sess.master_fd,
                            termios.TIOCSWINSZ,
                            struct.pack("HHHH", rows, cols, 0, 0),
                        )
                    except OSError:
                        pass
                elif ctrl.get("type") == "ping":
                    if not ws.closed:
                        await ws.send_str(json.dumps({"type": "pong"}))
            elif msg.type in (web.WSMsgType.CLOSE, web.WSMsgType.ERROR):
                break
    finally:
        sess.ws = None
        sess.last_ws_disconnect = time.monotonic()
        _sel().log_api_access(
            caller=caller,
            operation="terminal.ws.disconnect",
            outcome="ok",
            source="dashboard",
            resources=f"session={session_id}",
        )

    return ws


async def api_sandbox_providers(request: web.Request) -> web.Response:
    """GET /api/sandbox/providers — the sandbox tiers the terminal picker offers (EI-4 §1.3(3)).

    Read-only. Each entry is ``{name, display_name, available}``: ``available`` is the tier's live
    probe, so a container/VM tier whose runtime is down comes back greyed-with-reason. The host
    ``none`` tier is always present and listed first; a container/VM tier appears only once its
    provider app is enabled (registered through ``SandboxTypeHandler``)."""
    caller = request.get("user")
    if not caller:
        return web.Response(status=401, text="Unauthorized")
    from gideon.integrations.sandbox_providers import list_providers, resolve_provider

    providers = []
    for pname in list_providers():
        prov = resolve_provider(pname)
        try:
            available = bool(prov.available())
        except Exception:
            available = False
        providers.append(
            {
                "name": prov.name,
                "display_name": getattr(prov, "display_name", "") or prov.name,
                "available": available,
            }
        )
    providers.sort(key=lambda p: (p["name"] != "none", p["name"]))
    return web.json_response({"providers": providers})


async def api_terminal_create(request: web.Request) -> web.Response:
    """POST /api/terminal/sessions — create a new terminal session (returns session_id)."""
    caller = request.get("user")
    if not caller:
        _sel().log_api_access(
            caller="unknown",
            operation="terminal.session.create",
            outcome="denied",
            source="dashboard",
            resources=str(request.remote),
        )
        return web.Response(status=401, text="Unauthorized")
    if not _is_enabled(request):
        _sel().log_api_access(
            caller=caller,
            operation="terminal.session.create",
            outcome="denied",
            source="dashboard",
            resources="feature_disabled",
        )
        return web.Response(status=403, text="Terminal panel disabled")

    registry = _get_registry(request)
    cfg = _get_config(request)
    max_sessions = cfg.get("max_sessions", _MAX_SESSIONS)

    if len(registry) >= max_sessions:
        _sel().log_api_access(
            caller=caller,
            operation="terminal.session.create",
            outcome="denied",
            source="dashboard",
            resources=f"max_sessions={max_sessions}",
        )
        return web.json_response(
            {"error": f"Max {max_sessions} sessions"},
            status=429,
        )

    session_id = uuid.uuid4().hex[:12]
    shell = cfg.get("shell") or os.environ.get("SHELL", "/bin/bash")
    requested_cwd = ""
    requested_sandbox = ""
    if request.body_exists:
        try:
            body = await request.json()
            if isinstance(body, dict):
                if isinstance(body.get("cwd"), str):
                    requested_cwd = body["cwd"]
                if isinstance(body.get("sandbox"), str):
                    requested_sandbox = body["sandbox"].strip()
        except Exception:
            pass
    if requested_cwd:
        from gideon.security.security import is_sensitive_path, is_system_path

        if is_sensitive_path(requested_cwd) or is_system_path(requested_cwd):
            _sel().log_api_access(
                caller=caller,
                operation="terminal.session.create",
                outcome="denied",
                source="dashboard",
                resources=f"unsafe_cwd:{requested_cwd}",
            )
            return web.json_response(
                {
                    "error": "Cannot open a terminal in a system or credential directory."
                },
                status=403,
            )
        _pending_cwd[session_id] = requested_cwd
    if requested_sandbox and requested_sandbox != "none":
        from gideon.integrations.sandbox_providers import get_provider

        if get_provider(requested_sandbox) is not None:
            _pending_sandbox[session_id] = requested_sandbox
        else:
            requested_sandbox = ""
    else:
        requested_sandbox = ""
    _sel().log_api_access(
        caller=caller,
        operation="terminal.session.create",
        outcome="ok",
        source="dashboard",
        resources=f"session={session_id}",
    )
    return web.json_response(
        {
            "session_id": session_id,
            "shell": shell,
            "cwd": requested_cwd or str(cfg.get("cwd") or os.environ.get("HOME", "/")),
            "sandbox": requested_sandbox,
        }
    )


async def api_terminal_delete(request: web.Request) -> web.Response:
    """DELETE /api/terminal/sessions/{session_id} — kill a terminal session."""
    caller = request.get("user")
    if not caller:
        _sel().log_api_access(
            caller="unknown",
            operation="terminal.session.delete",
            outcome="denied",
            source="dashboard",
            resources=str(request.remote),
        )
        return web.Response(status=401, text="Unauthorized")
    if not _is_enabled(request):
        _sel().log_api_access(
            caller=caller,
            operation="terminal.session.delete",
            outcome="denied",
            source="dashboard",
            resources="feature_disabled",
        )
        return web.Response(status=403, text="Terminal panel disabled")

    session_id = request.match_info.get("session_id", "")
    registry = _get_registry(request)
    sess = registry.pop(session_id, None)  # type: ignore[arg-type]

    persistent = sess.persistent if sess else _persist_enabled(request)
    if persistent:
        await _kill_tmux_session(session_id)

    if sess is None:
        if persistent:
            _sel().log_api_access(
                caller=caller,
                operation="terminal.session.delete",
                outcome="ok",
                source="dashboard",
                resources=f"session={session_id},detached",
            )
            return web.json_response({"deleted": session_id})
        return json_error("not_found", status=404)

    if sess.ws and not sess.ws.closed:
        await sess.ws.close()
    await _kill_session(sess)

    _sel().log_api_access(
        caller=caller,
        operation="terminal.session.delete",
        outcome="ok",
        source="dashboard",
        resources=f"session={session_id}",
    )
    return web.json_response({"deleted": session_id})


async def _kill_tmux_session(session_id: str) -> None:
    """`tmux kill-session` for a Gideon terminal id on our socket. Best-effort/never-raises."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "tmux",
            "-L",
            _TMUX_SOCKET,
            "kill-session",
            "-t",
            _tmux_session_name(session_id),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await wait_with_timeout(proc, 5)
    except (FileNotFoundError, asyncio.TimeoutError, OSError):
        pass


async def api_terminal_list(request: web.Request) -> web.Response:
    """GET /api/terminal/sessions — list active terminal sessions."""
    caller = request.get("user")
    if not caller:
        _sel().log_api_access(
            caller="unknown",
            operation="terminal.session.list",
            outcome="denied",
            source="dashboard",
            resources=str(request.remote),
        )
        return web.Response(status=401, text="Unauthorized")
    if not _is_enabled(request):
        _sel().log_api_access(
            caller=caller,
            operation="terminal.session.list",
            outcome="denied",
            source="dashboard",
            resources="feature_disabled",
        )
        return web.json_response({"enabled": False, "sessions": []})

    registry = _get_registry(request)
    sessions = []
    seen: set[str] = set()
    for sid, sess in registry.items():
        if sess is None:
            continue
        seen.add(sid)
        sessions.append(
            {
                "session_id": sid,
                "pid": sess.proc.pid if sess.proc else None,
                "alive": sess.proc.returncode is None if sess.proc else False,
                "cols": sess.cols,
                "rows": sess.rows,
                "connected": sess.ws is not None and not sess.ws.closed,
                "cwd": sess.cwd,
                "shell": sess.shell,
                "persistent": sess.persistent,
            }
        )
    if _persist_enabled(request):
        for tname in await _list_tmux_sessions():
            if not tname.startswith("gideon-"):
                continue
            sid = tname[len("gideon-") :]
            if sid in seen:
                continue
            sessions.append(
                {
                    "session_id": sid,
                    "pid": None,
                    "alive": True,
                    "cols": 80,
                    "rows": 24,
                    "connected": False,
                    "cwd": "",
                    "shell": "",
                    "persistent": True,
                    "detached": True,
                }
            )
    _sel().log_api_access(
        caller=caller,
        operation="terminal.session.list",
        outcome="ok",
        source="dashboard",
        resources=f"count={len(sessions)}",
    )
    return web.json_response({"enabled": True, "sessions": sessions})


async def _list_tmux_sessions() -> list[str]:
    """Live tmux session names on our dedicated socket, or [] if tmux/none. Never raises."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "tmux",
            "-L",
            _TMUX_SOCKET,
            "list-sessions",
            "-F",
            "#{session_name}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await run_with_timeout(proc, 5)
        return [
            ln.strip()
            for ln in out.decode("utf-8", "replace").splitlines()
            if ln.strip()
        ]
    except (FileNotFoundError, asyncio.TimeoutError, OSError):
        return []


async def reap_orphaned_terminals(app: web.Application) -> None:
    """Background task: kill PTY sessions with no WS connection for >5 min."""
    try:
        while True:
            await asyncio.sleep(60)
            state = app.get("state")
            if not state or not hasattr(state, "_terminal_sessions"):
                continue
            registry: dict[str, _TerminalSession] = state._terminal_sessions
            now = time.monotonic()
            to_remove = []
            for sid, sess in registry.items():
                if sess is None:
                    continue
                if (
                    sess.last_ws_disconnect
                    and (now - sess.last_ws_disconnect) > _ORPHAN_TIMEOUT_S
                ):
                    to_remove.append(sid)
                elif sess.proc.returncode is not None:
                    to_remove.append(sid)
            for sid in to_remove:
                removed = registry.pop(sid, None)
                if removed is not None:
                    await _kill_session(removed)
                    logger.info("Reaped orphaned terminal session %s", sid)
    except asyncio.CancelledError:
        pass
