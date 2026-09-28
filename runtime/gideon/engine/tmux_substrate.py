"""Commands and identity rules for Gideon's dedicated tmux server."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass

from gideon.core.cancellation import run_with_timeout, wait_with_timeout

logger = logging.getLogger(__name__)
TMUX_SOCKET = "gideon"
PROBE_TIMEOUT_S = 5.0
_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")
_PART_MAX = 32
KIND_OPTION = "@gideon_kind"
WORKER_KIND = "worker"
TERMINAL_KIND = "terminal"


def tmux_available() -> bool:
    return shutil.which("tmux") is not None


def _active_home():
    from gideon.core.config.loader import resolve_config_dir

    return resolve_config_dir()


def sanitize(part: str) -> str:
    token = _UNSAFE.sub("_", str(part))
    return token[:_PART_MAX] or "_"


def terminal_session_name(session_id: str) -> str:
    return f"gideon-{str(session_id).replace('.', '_')}"


def terminal_attach_argv(session_id: str, command: list[str]) -> list[str]:
    name = terminal_session_name(session_id)
    return [
        "tmux", "-L", socket_name(), "new-session", "-A", "-s",
        name, *(_literal(str(part)) for part in command),
        ";", "set-option", KIND_OPTION, TERMINAL_KIND,
    ]


def _literal(part: str) -> str:
    return part[:-1] + "\\;" if part.endswith(";") else part


def durable_session_name(project_id: str, run_id: str, session_slug: str) -> str:
    return "gideon-" + "-".join(map(sanitize, (project_id, run_id, session_slug)))


def socket_name() -> str:
    identity = hashlib.sha256(os.fsencode(_active_home())).hexdigest()[:16]
    return f"gideon-{identity}"


def command_env() -> dict[str, str]:
    from gideon.core.config.loader import config_dir

    home = config_dir()
    socket_dir = home / "tmux"
    socket_name_value = socket_name()
    if len(os.fsencode(socket_dir / f"tmux-{os.getuid()}" / socket_name_value)) > 103:
        socket_dir = None
    else:
        socket_dir.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    if socket_dir is None:
        environment["TMUX_TMPDIR"] = "/tmp"
    else:
        environment["TMUX_TMPDIR"] = str(socket_dir)
    return environment


def _argv(*args: str) -> list[str]:
    return ["tmux", "-L", socket_name(), *args]


@dataclass(frozen=True)
class TmuxCommand:
    arguments: tuple[str, ...]

    async def status(self) -> bool:
        try:
            process = await asyncio.create_subprocess_exec(
                *_argv(*self.arguments),
                env=command_env(),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            return await wait_with_timeout(process, PROBE_TIMEOUT_S) == 0
        except (FileNotFoundError, asyncio.TimeoutError, OSError):
            return False
        except Exception:
            logger.debug("tmux command failed: %s", self.arguments[0], exc_info=True)
            return False

    async def lines(self) -> list[str]:
        try:
            process = await asyncio.create_subprocess_exec(
                *_argv(*self.arguments),
                env=command_env(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            output, _ = await run_with_timeout(process, PROBE_TIMEOUT_S)
            return [
                line.strip()
                for line in output.decode("utf-8", "replace").splitlines()
                if line.strip()
            ]
        except (FileNotFoundError, asyncio.TimeoutError, OSError):
            return []
        except Exception:
            logger.debug("tmux listing failed: %s", self.arguments[0], exc_info=True)
            return []

    def status_sync(self) -> bool:
        try:
            completed = subprocess.run(
                _argv(*self.arguments),
                env=command_env(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=PROBE_TIMEOUT_S,
            )
            return completed.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return False
        except Exception:
            logger.debug("tmux synchronous probe failed", exc_info=True)
            return False

    def output_sync(self) -> bytes:
        try:
            return subprocess.run(
                _argv(*self.arguments), capture_output=True, timeout=PROBE_TIMEOUT_S, env=command_env()
            ).stdout
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return b""
        except Exception:
            logger.debug("tmux synchronous listing failed", exc_info=True)
            return b""


async def new_session(
    name: str, *, workspace: str, command: list[str], env: dict[str, str] | None = None
) -> bool:
    if (
        not name
        or re.search(r"[^A-Za-z0-9_@-]", name)
        or not command
        or not os.path.isdir(workspace)
    ):
        return False
    environment = [
        argument
        for key, value in (env or {}).items()
        for argument in ("-e", _literal(f"{key}={value}"))
    ]
    arguments = [
        "new-session",
        "-d",
        "-s",
        name,
        "-c",
        _literal(str(workspace or ".")),
        *environment,
        *(_literal(str(part)) for part in command),
        ";", "set-option", "-p", "remain-on-exit", "off",
        ";", "set-option", KIND_OPTION, WORKER_KIND,
    ]
    if not await TmuxCommand(tuple(arguments)).status():
        return False
    if await has_session(name):
        return True
    await kill_session(name)
    return False


async def has_session(name: str) -> bool:
    if not name:
        return False
    panes = await TmuxCommand(
        ("list-panes", "-s", "-t", f"={name}", "-F", "#{pane_dead}")
    ).lines()
    return "0" in panes


def has_session_sync(name: str) -> bool:
    if not name:
        return False
    output = TmuxCommand(
        ("list-panes", "-s", "-t", f"={name}", "-F", "#{pane_dead}")
    ).output_sync()
    return any(line.strip() == "0" for line in output.decode("utf-8", "replace").splitlines())


async def list_sessions() -> list[str]:
    return [name for name, _ in await list_session_kinds()]


async def list_session_kinds() -> list[tuple[str, str]]:
    lines = await TmuxCommand(
        ("list-sessions", "-F", f"#{{session_name}}\t#{{{KIND_OPTION}}}")
    ).lines()
    sessions = []
    for line in lines:
        name, _, kind = line.partition("\t")
        if name:
            sessions.append((name, kind))
    return sessions


def pane_paths_sync() -> list[tuple[str, str]]:
    output = TmuxCommand(
        ("list-panes", "-a", "-F", "#{session_name}\t#{pane_current_path}\t#{pane_dead}")
    ).output_sync()
    pairs = []
    for line in output.decode("utf-8", "replace").splitlines():
        name, _, rest = line.partition("\t")
        path, _, dead = rest.partition("\t")
        pair = name.strip(), path.strip()
        if all(pair) and dead.strip() == "0":
            pairs.append(pair)
    return pairs


async def kill_session(name: str) -> None:
    if name:
        await TmuxCommand(("kill-session", "-t", f"={name}")).status()
