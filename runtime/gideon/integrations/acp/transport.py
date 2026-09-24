"""Subprocess ownership, stream I/O, and descendant cleanup for ACP."""

from __future__ import annotations

import asyncio
import glob
import logging
import os
import signal
import stat
import subprocess as subprocess_mod
import sys
import time
from collections import deque
from pathlib import Path

from gideon.core.env import augmented_path
from gideon.integrations.acp.errors import AcpError, AcpProcessDied
from gideon.integrations.sandbox_providers.base import SandboxHandle

logger = logging.getLogger(__name__)
_ACP_TRACE = os.environ.get("GIDEON_ACP_TRACE") == "1"
_STDOUT_BUFFER_LIMIT = 10 * 1024 * 1024


def _acp_trace(direction: str, text: str) -> None:
    if _ACP_TRACE:
        logger.info("ACP-TRACE %s %s", direction, text[:600])


def _resolve_ssh_auth_sock(env: dict[str, str]) -> None:
    if os.path.exists(env.get("SSH_AUTH_SOCK", "")):
        return
    patterns = ["/tmp/com.apple.launchd.*/Listeners"]
    if sys.platform != "darwin":
        patterns = [
            "/tmp/ssh-*/agent.*",
            f"/run/user/{os.getuid()}/ssh-agent.socket",
            f"/run/user/{os.getuid()}/keyring/ssh",
        ]
    for pattern in patterns:
        sockets = []
        for candidate in glob.glob(pattern):
            try:
                metadata = os.stat(candidate)
            except OSError:
                continue
            if stat.S_ISSOCK(metadata.st_mode):
                sockets.append((metadata.st_mtime, candidate))
        if sockets:
            env["SSH_AUTH_SOCK"] = max(sockets, key=lambda item: item[0])[1]
            logger.debug("SSH agent socket selected: %s", env["SSH_AUTH_SOCK"])
            break


def _direct_children(pid: int) -> list[int]:
    if sys.platform == "linux":
        try:
            children = [
                int(number)
                for file in Path(f"/proc/{pid}/task").glob("*/children")
                for number in file.read_text().split()
            ]
            if children:
                return children
        except (OSError, ValueError):
            pass
    try:
        output = subprocess_mod.check_output(
            ["pgrep", "-P", str(pid)], stderr=subprocess_mod.DEVNULL
        )
        return list(map(int, output.split()))
    except Exception:
        return []


def _get_child_pids(
    parent_pid: int | None, _visited: set[int] | None = None
) -> list[int]:
    seen = _visited if _visited is not None else set()
    if not parent_pid or parent_pid in seen:
        return []
    seen.add(parent_pid)
    stack = list(reversed(_direct_children(parent_pid)))
    descendants = []
    while stack:
        candidate = stack.pop()
        if candidate in seen:
            continue
        seen.add(candidate)
        descendants.append(candidate)
        stack.extend(reversed(_direct_children(candidate)))
    return descendants


def _get_start_time(pid: int) -> int | None:
    try:
        if sys.platform == "linux":
            fields = Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()
            return int(fields[19])
        started = subprocess_mod.check_output(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            stderr=subprocess_mod.DEVNULL,
            timeout=2,
        )
        return hash(started.strip())
    except Exception:
        return None


def _is_our_child(pid: int, expected_start: int | None = None) -> bool:
    if expected_start is None:
        return False
    try:
        if sys.platform == "linux":
            executable = Path(f"/proc/{pid}/cmdline").read_bytes().partition(b"\0")[0]
        else:
            executable = subprocess_mod.check_output(
                ["ps", "-o", "comm=", "-p", str(pid)],
                stderr=subprocess_mod.DEVNULL,
                timeout=2,
            ).strip()
        name = executable.rpartition(b"/")[2]
        permitted = name.startswith((b"claude", b"node", b"npx", b"python", b"ruby"))
        permitted = permitted or name == b"uv" or b"mcp" in name
        return permitted and _get_start_time(pid) == expected_start
    except Exception:
        return False


def _kill_escaped_children(
    child_pids: dict[int, int | None],
    *,
    pgid: int | None = None,
    root_pid: int | None = None,
) -> None:
    def terminate(pid: int, started: int | None) -> None:
        try:
            os.kill(pid, 0)
            if _is_our_child(pid, expected_start=started):
                os.kill(pid, signal.SIGKILL)
                logger.debug("Stopped escaped ACP descendant %d", pid)
        except OSError:
            pass

    for pid in reversed(child_pids):
        terminate(pid, child_pids[pid])
    extra = set(_get_child_pids(root_pid)) if root_pid else set()
    if pgid:
        try:
            members = subprocess_mod.check_output(
                ["pgrep", "-g", str(pgid)], stderr=subprocess_mod.DEVNULL
            )
            extra.update(map(int, members.split()))
        except Exception:
            pass
    extra.difference_update(child_pids)
    extra.discard(root_pid)
    for pid in sorted(extra, reverse=True):
        terminate(pid, _get_start_time(pid))


def _redacted(text: str) -> str:
    from gideon.security.security import redact_credentials, redact_exfiltration_urls

    sanitized, _ = redact_exfiltration_urls(text)
    return redact_credentials(sanitized)[0]


def _close_pipes(process) -> None:
    if process is None:
        return
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(process, name, None)
        close = getattr(stream, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


class AcpProcess:
    def __init__(
        self,
        *,
        command: list[str],
        work_dir: Path,
        sandbox_mode: str = "auto",
        extra_env: dict[str, str] | None = None,
        session_key: str | None = None,
        channel_id: str | None = None,
        ceiling_profile: str = "session_host",
        sandbox: str = "none",
    ) -> None:
        self._command = list(command or ())
        self._work_dir = Path(work_dir)
        self._sandbox_mode = sandbox_mode
        self._ceiling_profile = ceiling_profile
        self._sandbox = sandbox or "none"
        self._extra_env = dict(extra_env or {})
        self._session_key = session_key
        self._channel_id = channel_id
        self._process: asyncio.subprocess.Process | None = None
        self._pid: int | None = None
        self._pgid: int | None = None
        self._start_time: int | None = None
        self._child_pids: dict[int, int | None] = {}
        self._sandbox_handle: SandboxHandle | None = None
        self._stderr_lines: deque[str] = deque(maxlen=20)
        self.first_stderr_tail = ""
        self._stderr_task: asyncio.Task | None = None
        self._last_activity = time.monotonic()

    @property
    def process(self) -> asyncio.subprocess.Process | None:
        return self._process

    @property
    def pid(self) -> int | None:
        return self._pid

    @property
    def exit_code(self) -> int | None:
        return None if self._process is None else self._process.returncode

    @property
    def last_activity(self) -> float:
        return self._last_activity

    def is_alive(self) -> bool:
        return self._process is not None and self.exit_code is None

    def is_responsive(self, stale_threshold: float = 600.0) -> bool:
        return (
            self.is_alive() and time.monotonic() - self.last_activity < stale_threshold
        )

    def touch(self) -> None:
        self._last_activity = time.monotonic()

    def _stream(self, name: str):
        stream = getattr(self._process, name, None)
        if stream is None:
            raise AcpError("ACP process not running")
        return stream

    async def write(self, data: str) -> None:
        stream = self._stream("stdin")
        _acp_trace(">>", data.strip())
        try:
            stream.write(data.encode())
            await stream.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise AcpProcessDied(f"ACP process pipe broken: {exc}") from exc
        self.touch()

    async def readline(self) -> bytes:
        return await self._stream("stdout").readline()

    def stderr_tail(self) -> str:
        return _redacted("; ".join(self._stderr_lines)) if self._stderr_lines else ""

    def _environment(self) -> dict[str, str]:
        environment = dict(os.environ)
        environment.update(self._extra_env or {})
        environment["PATH"] = augmented_path(environment.get("PATH", ""))
        for key, value in (
            ("GIDEON_SESSION_KEY", self._session_key),
            ("GIDEON_CHANNEL_ID", self._channel_id),
        ):
            environment.pop(key, None)
            if value:
                environment[key] = value
        _resolve_ssh_auth_sock(environment)
        return environment

    def _remember_descendants(self, descendants: list[int]) -> None:
        for pid in descendants:
            if pid not in self._child_pids:
                self._child_pids[pid] = _get_start_time(pid)
        if self._child_pids:
            from gideon.engine.session import _track_child_pids

            _track_child_pids(self._child_pids, parent_pid=self._pid or 0)

    async def spawn(self) -> None:
        from gideon.integrations.sandbox_providers import resolve_provider
        from gideon.integrations.sandbox_providers.base import SandboxSpec

        self._work_dir.mkdir(parents=True, exist_ok=True)
        if not self._command:
            raise AcpError(
                "AcpProcess requires a non-empty command argv to spawn an ACP agent"
            )
        spec = SandboxSpec(mode=self._sandbox_mode, profile=self._ceiling_profile)
        handle = resolve_provider(self._sandbox).wrap(spec, self._command.copy())
        self._sandbox_handle = handle
        try:
            options: dict = {
                name: asyncio.subprocess.PIPE for name in ("stdin", "stdout", "stderr")
            }
            options.update(
                cwd=str(self._work_dir),
                limit=_STDOUT_BUFFER_LIMIT,
                start_new_session=True,
                env=self._environment(),
            )
            self._process = await handle.exec(**options)
            self._pid = self._process.pid
            self._start_time = _get_start_time(self._pid)
            try:
                self._pgid = os.getpgid(self._pid)
            except OSError:
                self._pgid = self._pid
            from gideon.engine.session import _track_pid, _track_session_pid

            _track_pid(self._pid)
            _track_session_pid(self._pid)
            executable = Path(handle.argv[0]).name if handle.argv else "acp-agent"
            logger.info("Started ACP executable %s (PID %d)", executable, self._pid)
            if self._process.stderr is not None:
                self._stderr_task = asyncio.ensure_future(
                    self._drain_stderr(self._process.stderr)
                )
            await asyncio.sleep(0.3)
            self._remember_descendants(_get_child_pids(self._pid))
        except BaseException:
            try:
                await self.kill(force=True)
            finally:
                self.teardown()
            raise

    async def _drain_stderr(self, stderr: asyncio.StreamReader) -> None:
        label = Path(self._command[0]).name if self._command else "acp-agent"
        while line := await stderr.readline():
            message = line.decode(errors="replace").strip()
            if not message:
                continue
            self._stderr_lines.append(message)
            self.touch()
            logger.warning("%s stderr: %s", label, _redacted(message))

    async def snapshot_process_tree(self) -> None:
        descendants = _get_child_pids(self._pid)
        if not descendants:
            await asyncio.sleep(0.5)
            descendants = _get_child_pids(self._pid)
        self._remember_descendants(descendants)

    def _signal(self, sig: int, *, fallback: bool = False) -> None:
        pid = self._pid
        if pid is None:
            return
        try:
            os.killpg(self._pgid or os.getpgid(pid), sig)
        except OSError:
            if fallback and self._process is not None:
                try:
                    self._process.kill()
                except OSError:
                    pass

    async def _wait_exit(self, seconds: float) -> bool:
        process = self._process
        if process is None:
            return True
        try:
            await asyncio.wait_for(process.wait(), timeout=seconds)
            return True
        except asyncio.TimeoutError:
            return False

    async def kill(self, *, force: bool = False) -> None:
        if self._process is None:
            return
        _close_pipes(self._process)
        descendants = dict(self._child_pids)
        for pid in _get_child_pids(self._pid):
            if pid not in descendants:
                descendants[pid] = _get_start_time(pid)
        try:
            if self._process.returncode is not None:
                return
            if not force:
                self._signal(signal.SIGTERM)
                if await self._wait_exit(3.0):
                    return
            self._signal(signal.SIGKILL, fallback=True)
            if not await self._wait_exit(1.0):
                logger.warning("ACP PID %s survived force-stop deadline", self._pid)
        except asyncio.CancelledError:
            self._signal(signal.SIGKILL, fallback=True)
            raise
        finally:
            _kill_escaped_children(descendants, pgid=self._pgid, root_pid=self._pid)

    def teardown(self) -> None:
        from gideon.engine import session

        _close_pipes(self._process)
        handle, self._sandbox_handle = self._sandbox_handle, None
        if handle is not None:
            try:
                handle.cleanup()
            except Exception:
                logger.debug("ACP sandbox cleanup failed", exc_info=True)
        if self._stderr_task is not None and not self._stderr_task.done():
            self._stderr_task.cancel()
        self._stderr_task = None
        identifiers = (
            ("_untrack_child_pids", self._child_pids),
            ("_untrack_pid", self._pid),
            ("_untrack_session_pid", self._pid),
        )
        self._process = None
        self._pid = self._pgid = self._start_time = None
        self._child_pids = {}
        if not self.first_stderr_tail:
            self.first_stderr_tail = self.stderr_tail()
        self._stderr_lines.clear()
        for operation, value in identifiers:
            if value is not None and value != {}:
                try:
                    getattr(session, operation)(value)
                except Exception:
                    logger.debug(
                        "ACP PID tracking cleanup failed: %s", operation, exc_info=True
                    )
