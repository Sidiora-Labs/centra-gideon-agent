"""Commands and identity rules for Gideon's dedicated tmux server."""

from __future__ import annotations

import asyncio
import logging
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


def tmux_available() -> bool:
    return shutil.which("tmux") is not None


def sanitize(part: str) -> str:
    token = _UNSAFE.sub("_", str(part))
    return token[:_PART_MAX] or "_"


def terminal_session_name(session_id: str) -> str:
    return f"gideon-{str(session_id).replace('.', '_')}"


def durable_session_name(project_id: str, run_id: str, session_slug: str) -> str:
    return "gideon-" + "-".join(map(sanitize, (project_id, run_id, session_slug)))


def _argv(*args: str) -> list[str]:
    return ["tmux", "-L", TMUX_SOCKET, *args]


@dataclass(frozen=True)
class TmuxCommand:
    arguments: tuple[str, ...]

    async def status(self) -> bool:
        try:
            process = await asyncio.create_subprocess_exec(
                *_argv(*self.arguments),
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
                _argv(*self.arguments), capture_output=True, timeout=PROBE_TIMEOUT_S
            ).stdout
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return b""
        except Exception:
            logger.debug("tmux synchronous listing failed", exc_info=True)
            return b""


async def new_session(
    name: str, *, workspace: str, command: list[str], env: dict[str, str] | None = None
) -> bool:
    if not name or _UNSAFE.search(name) or not command:
        return False
    environment = [
        argument
        for key, value in (env or {}).items()
        for argument in ("-e", f"{key}={value}")
    ]
    arguments = [
        "new-session",
        "-d",
        "-s",
        name,
        "-c",
        str(workspace or "."),
        *environment,
        *map(str, command),
    ]
    return await TmuxCommand(tuple(arguments)).status()


async def has_session(name: str) -> bool:
    return (
        await TmuxCommand(("has-session", "-t", f"={name}")).status() if name else False
    )


def has_session_sync(name: str) -> bool:
    return (
        TmuxCommand(("has-session", "-t", f"={name}")).status_sync() if name else False
    )


async def list_sessions() -> list[str]:
    return await TmuxCommand(("list-sessions", "-F", "#{session_name}")).lines()


def pane_paths_sync() -> list[tuple[str, str]]:
    output = TmuxCommand(
        ("list-panes", "-a", "-F", "#{session_name}\t#{pane_current_path}")
    ).output_sync()
    pairs = []
    for line in output.decode("utf-8", "replace").splitlines():
        name, _, path = line.partition("\t")
        pair = name.strip(), path.strip()
        if all(pair):
            pairs.append(pair)
    return pairs


async def kill_session(name: str) -> None:
    if name:
        await TmuxCommand(("kill-session", "-t", f"={name}")).status()
