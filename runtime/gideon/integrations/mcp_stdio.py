"""Resource-limited MCP process streams and bounded first-start completion."""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from gideon.core.cancellation import terminate_and_reap
from gideon.integrations.mcp_status import STDERR_TAIL_BYTES

FINISH_SECS = 600.0
_END_GRACE_SECS = 2.0
_left_unanswered: set[str] = set()


@dataclass
class StdioRun:
    returncode: int | None = None
    stopped: bool = False
    spoke: bool = False
    abandoned: bool = False
    left_to_finish: bool = False
    waiting: bool = False
    _stderr: bytearray = field(default_factory=bytearray)
    _pending_stderr: bytes = b""
    _secrets: tuple[bytes, ...] = ()

    @property
    def stderr(self) -> str:
        return self._stderr.decode("utf-8", "replace")

    @property
    def exited(self) -> bool:
        return self.returncode is not None and not self.stopped

    def stop_waiting(self) -> None:
        self.abandoned = True

    def keep(self, data: bytes) -> None:
        """Hold any possible secret prefix until later bytes prove or complete it."""
        pending = self._pending_stderr + data
        emitted = bytearray()
        while pending:
            matches = [
                (position, -len(secret), secret)
                for secret in self._secrets
                if (position := pending.find(secret)) >= 0
            ]
            if matches:
                position, _length, secret = min(matches)
                emitted.extend(pending[:position])
                emitted.extend(b"[REDACTED: credential]")
                pending = pending[position + len(secret) :]
                continue
            held = 0
            for secret in self._secrets:
                for width in range(min(len(secret) - 1, len(pending)), held, -1):
                    if pending.endswith(secret[:width]):
                        held = width
                        break
            if held:
                emitted.extend(pending[:-held])
                pending = pending[-held:]
            else:
                emitted.extend(pending)
                pending = b""
            break
        self._pending_stderr = pending
        self._stderr.extend(emitted)
        del self._stderr[:-STDERR_TAIL_BYTES]

    def finish_stderr(self) -> None:
        if self._pending_stderr:
            self._stderr.extend(b"[REDACTED: credential]")
            self._pending_stderr = b""
            del self._stderr[:-STDERR_TAIL_BYTES]


class CommandNotFound(ValueError):
    pass


@dataclass
class _Finishing:
    server: str
    task: asyncio.Task[None]


_finishing: dict[tuple[str, ...], _Finishing] = {}


def forget_left(server: str) -> None:
    _left_unanswered.discard(server)


async def _discard(stream: Any) -> None:
    with contextlib.suppress(Exception):
        while await stream.read(65536):
            pass


async def _finish(server: str, identity: tuple[str, ...], proc: Any) -> None:
    readers = [
        asyncio.create_task(_discard(stream))
        for stream in (proc.stdout, proc.stderr)
        if stream is not None
    ]
    ended = False
    try:
        try:
            await asyncio.wait_for(proc.wait(), FINISH_SECS)
        except TimeoutError:
            await terminate_and_reap(proc)
        ended = True
    except asyncio.CancelledError:
        await asyncio.shield(terminate_and_reap(proc))
        raise
    finally:
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        held = _finishing.get(identity)
        if held is not None and held.task is asyncio.current_task():
            _finishing.pop(identity, None)
    if ended:
        from gideon.integrations.mcp_discovery import look_again

        look_again(server)


async def stop_finishing(match: Callable[[str], bool]) -> None:
    tasks = [entry.task for entry in tuple(_finishing.values()) if match(entry.server)]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def stop_finishing_soon(match: Callable[[str], bool]) -> None:
    for entry in tuple(_finishing.values()):
        if match(entry.server) and not entry.task.done():
            entry.task.get_loop().call_soon_threadsafe(entry.task.cancel)


@contextlib.asynccontextmanager
async def stdio_streams(
    server: str, spec: Mapping[str, Any], *, run: StdioRun, seal: str = ""
) -> AsyncIterator[tuple[Any, Any]]:
    import anyio
    from mcp import types
    from mcp.shared.message import SessionMessage

    from gideon.core.env import augmented_path
    from gideon.integrations.mcp_argument_secrets import known_values
    from gideon.security.sandbox import (
        PROFILE_TOOL,
        build_child_env,
        create_subprocess_limited,
    )

    run._secrets = tuple(value.encode("utf-8") for value in known_values(spec))
    extra = dict(spec.get("env") or {})
    env = build_child_env(
        site=f"mcp:{server}", extra={k: v for k, v in extra.items() if k != "PATH"}
    )
    env["PATH"] = augmented_path(os.environ.get("PATH", ""))
    if extra.get("PATH"):
        env["PATH"] = extra["PATH"] + os.pathsep + env["PATH"]
    command = str(spec.get("command") or "")
    cwd = str(spec.get("cwd") or "") or None
    lookup = (
        os.path.join(cwd, command)
        if cwd and os.sep in command and not os.path.isabs(command)
        else command
    )
    resolved = shutil.which(lookup, path=env.get("PATH"))
    if not resolved:
        raise CommandNotFound(f"command not found: {command}")
    args = [str(arg) for arg in spec.get("args") or []]
    identity = (server, seal, resolved, cwd or "", *args)
    earlier = _finishing.get(identity)
    if earlier is not None and earlier.task.get_loop() is asyncio.get_running_loop():
        run.waiting = True
        await asyncio.wait({earlier.task})
        run.waiting = False
    proc = await create_subprocess_limited(
        resolved,
        *args,
        profile=PROFILE_TOOL,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=cwd,
        start_new_session=True,
    )
    read_send, read_recv = anyio.create_memory_object_stream[Any](0)
    write_send, write_recv = anyio.create_memory_object_stream[Any](0)
    stdout_ended = anyio.Event()
    stderr_done = anyio.Event()

    async def read_output() -> None:
        pending = b""
        async with read_send:
            while chunk := await proc.stdout.read(65536):
                *lines, pending = (pending + chunk).split(b"\n")
                for line in lines:
                    if not line.strip():
                        continue
                    run.spoke = True
                    _left_unanswered.discard(server)
                    item: SessionMessage | Exception
                    try:
                        item = SessionMessage(
                            types.JSONRPCMessage.model_validate_json(line)
                        )
                    except Exception as error:
                        item = error
                    try:
                        await read_send.send(item)
                    except (anyio.ClosedResourceError, anyio.BrokenResourceError):
                        return
            stdout_ended.set()

    async def write_input() -> None:
        async with write_recv:
            async for message in write_recv:
                try:
                    proc.stdin.write(
                        message.message.model_dump_json(
                            by_alias=True, exclude_none=True
                        ).encode()
                        + b"\n"
                    )
                    await proc.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    return

    async def read_errors() -> None:
        try:
            while chunk := await proc.stderr.read(4096):
                run.keep(chunk)
        finally:
            run.finish_stderr()
            stderr_done.set()

    try:
        async with anyio.create_task_group() as pumps:
            pumps.start_soon(read_output)
            pumps.start_soon(write_input)
            pumps.start_soon(read_errors)
            try:
                yield read_recv, write_send
            finally:
                with anyio.CancelScope(shield=True):
                    if proc.stdin is not None:
                        with contextlib.suppress(Exception):
                            proc.stdin.close()
                    if (
                        proc.returncode is None
                        and run.abandoned
                        and not run.spoke
                        and not stdout_ended.is_set()
                        and server not in _left_unanswered
                    ):
                        run.left_to_finish = True
                    else:
                        try:
                            await asyncio.wait_for(proc.wait(), _END_GRACE_SECS)
                        except TimeoutError:
                            run.stopped = proc.returncode is None
                            await terminate_and_reap(proc)
                        run.returncode = proc.returncode
                        with anyio.move_on_after(1):
                            await stderr_done.wait()
                pumps.cancel_scope.cancel()
                write_send.close()
                read_recv.close()
    finally:
        if run.left_to_finish:
            _left_unanswered.add(server)
            task = asyncio.create_task(
                _finish(server, identity, proc), name=f"mcp-finish:{server}"
            )
            _finishing[identity] = _Finishing(server, task)
