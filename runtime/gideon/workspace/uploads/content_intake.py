"""Approve owned content snapshots before persistence or model admission.

The static check reads all bytes through 512 KiB, otherwise first and last
256 KiB. Binary upload windows are not inspected as prose; extracted text is.
An approval describes that bounded check, never complete malware clearance.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
import tempfile
import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from gideon.workspace.uploads.policy import check_upload
from gideon.workspace.uploads.scan_child import SCAN_WINDOW, WHOLE_FILE_BYTES

CHILD_MODULE = "gideon.workspace.uploads.scan_child"
SCAN_DEADLINE_SECS = 60.0
SCANS_AT_ONCE = 8
_gates: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)
_APPROVAL = object()


class IntakeRefused(Exception):
    def __init__(self, code: str, message: str, status: int):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status

    def response(self):
        from aiohttp import web

        return web.json_response(
            {"error": {"code": self.code, "message": self.message}}, status=self.status
        )


def _refuse(code: str, surface: str, *, status: int, message: str) -> IntakeRefused:
    from gideon.security.sel import sel

    try:
        sel().log_api_access(
            caller="content_intake",
            operation="upload_scan",
            outcome="rejected" if status != 503 else "error",
            source=surface,
            resources=code,
        )
    except Exception:
        pass
    return IntakeRefused(code, message, status)


def _window(
    head: bytes, tail: bytes, *, contiguous: bool, binary: bool
) -> bytes | None:
    chunks = [part for part in (head, tail) if part and not (binary and b"\0" in part)]
    if not chunks:
        return None
    return (b"" if contiguous else b"\n").join(chunks)


async def _scan(window: bytes | None, surface: str) -> None:
    if window is None:
        return
    import sys

    from gideon.core.cancellation import run_with_timeout
    from gideon.security.sandbox import build_child_env

    loop = asyncio.get_running_loop()
    gate = _gates.setdefault(loop, asyncio.Semaphore(SCANS_AT_ONCE))
    try:
        async with gate:
            env = build_child_env(site="content intake scan")
            # This path is the installed trusted runtime, not an input filename.
            env["PYTHONPATH"] = str(Path(__file__).resolve().parents[3])
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                CHILD_MODULE,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                start_new_session=True,
            )
            out, _ = await run_with_timeout(proc, SCAN_DEADLINE_SECS, payload=window)
            answer = json.loads(out)
            if (
                proc.returncode != 0
                or not isinstance(answer, dict)
                or type(answer.get("dangerous")) is not bool
            ):
                raise ValueError("invalid scan answer")
    except (OSError, ValueError, UnicodeError, asyncio.TimeoutError):
        raise _refuse(
            "upload_content_unchecked",
            surface,
            status=503,
            message="Content could not be checked, so nothing was made from it.",
        ) from None
    if answer["dangerous"]:
        raise _refuse(
            "upload_content_refused",
            surface,
            status=422,
            message="Content failed the safety scan, so nothing was made from it.",
        )


def run_owned_sync(factory):
    """Run contained admission from a synchronous worker, never block a host loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())
    raise _refuse(
        "upload_content_unchecked",
        "synchronous_intake",
        status=503,
        message="Content admission requires its owned worker; nothing was changed.",
    )


@dataclass(frozen=True)
class ApprovedText:
    text: str
    digest: str
    surface: str
    _seal: object = field(default=None, init=False, repr=False, compare=False)

    @property
    def scanned_head(self) -> str:
        """A model-facing prefix wholly inside the static scan's read window."""
        if self._seal is not _APPROVAL:
            raise PermissionError("The text has not passed intake.")
        data = self.text.encode("utf-8")
        return (
            self.text
            if len(data) <= WHOLE_FILE_BYTES
            else data[:SCAN_WINDOW].decode("utf-8", errors="ignore")
        )


async def approve_text(text: str, *, surface: str) -> ApprovedText:
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    data = text.encode("utf-8", errors="replace")
    head = data[:SCAN_WINDOW]
    tail = data[SCAN_WINDOW:] if len(data) <= WHOLE_FILE_BYTES else data[-SCAN_WINDOW:]
    await _scan(
        _window(head, tail, contiguous=len(data) <= WHOLE_FILE_BYTES, binary=False),
        surface,
    )
    approved = ApprovedText(
        data.decode("utf-8"), hashlib.sha256(data).hexdigest(), surface
    )
    object.__setattr__(approved, "_seal", _APPROVAL)
    return approved


@dataclass(frozen=True)
class ApprovedFile:
    filename: str
    mime: str | None
    size: int
    digest: str
    surface: str
    _fd: int = field(repr=False, compare=False)
    _seal: object = field(default=None, init=False, repr=False, compare=False)

    def require_approved(self) -> None:
        if self._seal is not _APPROVAL:
            raise PermissionError("The content snapshot has not passed intake.")

    def close(self) -> None:
        fd = self._fd
        object.__setattr__(self, "_fd", -1)
        try:
            os.close(fd)
        except OSError:
            pass

    def _persist(self, dest: Path) -> None:
        self.require_approved()
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as out:
                offset = 0
                while offset < self.size:
                    data = os.pread(
                        self._fd, min(1024 * 1024, self.size - offset), offset
                    )
                    if not data:
                        raise OSError("snapshot incomplete")
                    out.write(data)
                    offset += len(data)
        except BaseException:
            dest.unlink(missing_ok=True)
            raise

    def read_bytes(self) -> bytes:
        """Read only this approved snapshot, for formats kept as inline text."""
        self.require_approved()
        chunks = []
        offset = 0
        while offset < self.size:
            chunk = os.pread(self._fd, min(1024 * 1024, self.size - offset), offset)
            if not chunk:
                raise OSError("snapshot incomplete")
            chunks.append(chunk)
            offset += len(chunk)
        return b"".join(chunks)

    async def persist(self, dest: Path) -> None:
        task = asyncio.create_task(asyncio.to_thread(self._persist, dest))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                await task
            except Exception:
                # The worker cleans only a file it created. An O_EXCL refusal
                # must never remove somebody else's existing destination.
                pass
            else:
                dest.unlink(missing_ok=True)
            raise

    def _stage_file(self, suffix: str) -> Path:
        self.require_approved()
        fd, name = tempfile.mkstemp(prefix="gideon-approved-", suffix=suffix)
        path = Path(name)
        try:
            with os.fdopen(fd, "wb") as output:
                offset = 0
                while offset < self.size:
                    data = os.pread(
                        self._fd, min(1024 * 1024, self.size - offset), offset
                    )
                    if not data:
                        raise OSError("snapshot incomplete")
                    output.write(data)
                    offset += len(data)
            path.chmod(0o400)
            return path
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    async def stage_file(self, *, suffix: str = ".zip") -> Path:
        """Give an existing importer an owned read-only path it must finally remove."""
        task = asyncio.create_task(asyncio.to_thread(self._stage_file, suffix))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                path = await task
            except Exception:
                pass
            else:
                path.unlink(missing_ok=True)
            raise

    @contextmanager
    def materialize(self):
        """A reader's private, read-only copy, owned for the reader's lifetime."""
        with tempfile.TemporaryDirectory(prefix="gideon-reader-") as directory:
            path = Path(directory) / ("input" + Path(self.filename).suffix)
            self._persist(path)
            os.chmod(path, 0o400)
            yield str(path)

    @asynccontextmanager
    async def reader_path(self):
        reader = self.materialize()
        task = asyncio.create_task(asyncio.to_thread(reader.__enter__))
        try:
            path = await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            await asyncio.to_thread(reader.__exit__, None, None, None)
            raise
        try:
            yield path
        finally:
            await asyncio.to_thread(reader.__exit__, None, None, None)


def source_stamp(path: str | Path) -> tuple[int, ...]:
    """An identity check, not authorization to read the source."""
    info = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode):
        raise PermissionError("Source must be a regular file without a symlink.")
    return _stamp(info)


def _stamp(info) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


async def approve_path(
    path: str | Path,
    mime: str | None = None,
    *,
    surface: str,
    expected_stamp: tuple[int, ...] | None = None,
    filename: str | None = None,
) -> ApprovedFile:
    """Read a permitted source once into an owned snapshot, rejecting a moved source.

    The caller still enforces its workspace/root grants. A scan adds no permission.
    Sensitive and symlink sources remain denied before any byte is copied.
    """
    from gideon.security.security import is_sensitive_path

    if is_sensitive_path(str(path)):
        raise PermissionError("Sensitive files cannot be extracted.")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    approved = None
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise PermissionError("Source must be a regular file.")
        stamp = _stamp(before)
        if expected_stamp is not None and stamp != expected_stamp:
            raise _refuse(
                "upload_content_changed",
                surface,
                status=409,
                message="The file changed before it could be read. Please attach it again.",
            )

        async def chunks():
            while True:
                read = asyncio.create_task(asyncio.to_thread(os.read, fd, 1024 * 1024))
                try:
                    chunk = await asyncio.shield(read)
                except asyncio.CancelledError:
                    await read
                    raise
                if not chunk:
                    return
                yield chunk

        approved = await approve_stream(
            chunks(), filename or Path(path).name, mime, surface=surface
        )
        if (
            _stamp(os.fstat(fd)) != stamp
            or source_stamp(path) != stamp
            or approved.size != before.st_size
        ):
            raise _refuse(
                "upload_content_changed",
                surface,
                status=409,
                message="The file changed while it was read, so nothing was made from it.",
            )
        result, approved = approved, None
        return result
    finally:
        os.close(fd)
        if approved is not None:
            approved.close()


async def approve_stream(
    chunks: AsyncIterator[bytes],
    filename: str,
    mime: str | None = None,
    *,
    surface: str,
) -> ApprovedFile:
    size = 0
    digest = hashlib.sha256()
    read_fd = None
    # Private staging is never indexed or exposed by the upload API. No source
    # path survives approval: only a read-only handle to an unlinked inode.
    try:
        with tempfile.TemporaryDirectory(prefix="gideon-intake-") as directory:
            path = Path(directory) / "payload"
            with path.open("xb") as out:
                os.chmod(path, 0o600)
                async for chunk in chunks:
                    chunk = bytes(chunk)
                    size += len(chunk)
                    policy = check_upload(filename, mime, size=size)
                    if not policy.ok:
                        raise _refuse(
                            "upload_too_large",
                            surface,
                            status=policy.status,
                            message=policy.reason,
                        )
                    digest.update(chunk)
                    # Await each write before staging is closed or removed.
                    task = asyncio.create_task(asyncio.to_thread(out.write, chunk))
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        await task
                        raise
            if not size:
                raise _refuse(
                    "upload_empty", surface, status=400, message="The file is empty."
                )
            read_fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            path.unlink()
        head = os.pread(read_fd, min(size, SCAN_WINDOW), 0)
        tail = (
            os.pread(read_fd, size - SCAN_WINDOW, SCAN_WINDOW)
            if size <= WHOLE_FILE_BYTES and size > SCAN_WINDOW
            else (
                os.pread(read_fd, SCAN_WINDOW, size - SCAN_WINDOW)
                if size > WHOLE_FILE_BYTES
                else b""
            )
        )
        await _scan(
            _window(head, tail, contiguous=size <= WHOLE_FILE_BYTES, binary=True),
            surface,
        )
        approved = ApprovedFile(
            filename, mime, size, digest.hexdigest(), surface, read_fd
        )
        object.__setattr__(approved, "_seal", _APPROVAL)
        read_fd = None
        return approved
    finally:
        if read_fd is not None:
            os.close(read_fd)
