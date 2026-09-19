"""Locked process journals, orphan admission and provider process retirement."""

import ctypes
import ctypes.util
import fcntl
import logging
import os
import signal
import struct
import subprocess
import sys
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from gideon.core.config import loader as config_loader
from gideon.integrations.llm.base import ModelProvider

logger = logging.getLogger(__name__)
_PID_FILE = "agent_pids.txt"
_SESSION_PID_FILE = "session_pids.txt"
_libproc: ctypes.CDLL | None = None


def config_dir() -> Path:
    return config_loader.config_dir()


def _pid_file_path() -> Path:
    return config_dir().joinpath(_PID_FILE)


def _session_pid_file_path() -> Path:
    return config_dir().joinpath(_SESSION_PID_FILE)


@dataclass(frozen=True)
class _PidJournal:
    path: Path

    @contextmanager
    def lock(self):
        location = self.path.with_suffix(".lock")
        location.parent.mkdir(parents=True, exist_ok=True)
        with location.open("w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def read(self) -> list[str]:
        return (
            self.path.read_text(encoding="utf-8").splitlines()
            if self.path.exists()
            else []
        )

    def snapshot_if_available(self) -> list[str]:
        if not self.path.exists():
            return []
        location = self.path.with_suffix(".lock")
        location.parent.mkdir(parents=True, exist_ok=True)
        try:
            handle = location.open("w")
        except OSError:
            return []
        with handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return []
            try:
                return self.read()
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def append(self, entries, *, unique: str = "") -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        current = self.read() if unique else []
        seen = set("\n".join(current).split() if unique == "tokens" else current)
        with self.path.open("a", encoding="utf-8") as destination:
            for entry in entries:
                if unique and entry in seen:
                    continue
                destination.write(entry + "\n")
                seen.add(entry)

    def replace(self, lines) -> None:
        rows = list(lines)
        text = "\n".join(rows)
        self.path.write_text(text + "\n" if rows else "", encoding="utf-8")

    def retain(self, predicate) -> None:
        if self.path.exists():
            self.replace([line for line in self.read() if predicate(line)])


@contextmanager
def _session_pid_file_lock():
    with _PidJournal(_session_pid_file_path()).lock():
        yield


@contextmanager
def _pid_file_lock():
    with _PidJournal(_pid_file_path()).lock():
        yield


def _track_session_pid(pid: int) -> None:
    with _session_pid_file_lock():
        _PidJournal(_session_pid_file_path()).append(
            [f"{os.getpid()}:{pid}"], unique="tokens"
        )


def _track_pid(pid: int) -> None:
    with _pid_file_lock():
        _PidJournal(_pid_file_path()).append([str(pid)])


def _track_child_pids(pids: dict[int, int | None], parent_pid: int = 0) -> None:
    if pids:
        with _pid_file_lock():
            _PidJournal(_pid_file_path()).append(
                (f"{pid}:{parent_pid}" for pid in pids), unique="lines"
            )


def _untrack_pid(pid: int) -> None:
    with _pid_file_lock():
        _PidJournal(_pid_file_path()).retain(lambda line: line.strip() != str(pid))


def _untrack_session_pid(pid: int) -> None:
    owned = f"{os.getpid()}:{pid}"
    with _session_pid_file_lock():
        _PidJournal(_session_pid_file_path()).retain(lambda line: line.strip() != owned)


def _untrack_child_pids(pids: dict[int, int | None]) -> None:
    if pids:
        identifiers = {str(pid) for pid in pids}
        with _pid_file_lock():
            _PidJournal(_pid_file_path()).retain(
                lambda line: ":" not in line.strip()
                or line.strip().split(":", 1)[0] not in identifiers
            )


def _write_back_pid_file(killed_or_dead: set[str]) -> None:
    with _session_pid_file_lock():
        _PidJournal(_session_pid_file_path()).retain(
            lambda line: bool(line.strip()) and line.strip() not in killed_or_dead
        )


def _probe(pid: int) -> bool | None:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        logger.debug("No permission to signal PID %s — skipping", pid)
        return None
    except OSError:
        return None
    return True


def _is_managed_agent_process(pid: int) -> bool:
    try:
        if sys.platform == "linux":
            command = Path(f"/proc/{pid}/cmdline").read_bytes()
        else:
            command = subprocess.check_output(
                ["ps", "-o", "command=", "-p", str(pid)],
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
        return b"claude" in command
    except Exception:
        return False


def _collect_active_pids(sessions: "dict") -> tuple[set[int], bool]:
    active: set = set()
    for session in sessions.values():
        provider = session.provider
        client = getattr(provider, "client", None)
        if client is not None:
            try:
                value = client._pid
            except Exception:
                logger.warning(
                    "Failed to read PID for session — skipping orphan sweep this cycle"
                )
                return active, False
            if not isinstance(value, int):
                logger.warning(
                    "PID for session is not an int (%r) — skipping orphan sweep this cycle",
                    value,
                )
                return active, False
            active.add(value)
        for attribute in ("_proc", "_active_proc"):
            process = getattr(provider, attribute, None)
            if process is not None and process.returncode is None:
                active.add(process.pid)
    return active, True


def _kill_agent(pid: int) -> bool:
    if pid <= 0 or not _is_managed_agent_process(pid):
        return False
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        return False
    return True


def _kill_pid_tree(pid: int) -> tuple[int, bool]:
    if pid <= 0:
        return 0, False
    descendants = 0
    try:
        from gideon.integrations.acp.client import _get_child_pids

        for child in reversed(_get_child_pids(pid)):
            descendants += int(_kill_agent(child))
    except Exception:
        logger.debug("Error killing children of PID %s", pid, exc_info=True)
    root = _kill_agent(pid)
    return descendants + int(root), root


@dataclass(frozen=True)
class _SessionClaim:
    text: str
    pid: int
    owner: int | None

    @classmethod
    def parse(cls, text: str) -> "_SessionClaim":
        owner, separator, child = text.partition(":")
        pid = int(child if separator else owner)
        gateway = int(owner) if separator else None
        if pid <= 0 or (gateway is not None and gateway <= 0):
            raise ValueError(text)
        return cls(text, pid, gateway)


@dataclass
class _SweepLedger:
    killed: int = 0
    retired: set[str] = field(default_factory=set)
    candidates: list[int] = field(default_factory=list)

    def inspect(self, claim: _SessionClaim, managed, preview: bool) -> None:
        alive = _probe(claim.pid)
        if alive is False:
            self.retired.add(claim.text)
            return
        if alive is None or (managed is not None and managed(claim.pid)):
            return
        if not _is_managed_agent_process(claim.pid):
            self.retired.add(claim.text)
            return
        if preview:
            self.candidates.append(claim.pid)
            return
        count, root = _kill_pid_tree(claim.pid)
        self.killed += count
        if root or _probe(claim.pid) is False:
            self.retired.add(claim.text)


def _sweep_pid_entries(
    lines: list[str],
    *,
    should_skip_tagged: "Callable[[int, int], bool]",
    should_skip_bare: "Callable[[int], bool]",
    is_managed: "Callable[[int], bool] | None" = None,
    dry_run: bool = False,
) -> tuple[int, set[str], list[int]]:
    sweep = _SweepLedger()
    for line in lines:
        text = line.strip()
        if not text:
            continue
        try:
            try:
                claim = _SessionClaim.parse(text)
            except ValueError:
                sweep.retired.add(text)
                continue
            skip = (
                should_skip_bare(claim.pid)
                if claim.owner is None
                else should_skip_tagged(claim.owner, claim.pid)
            )
            if not skip:
                sweep.inspect(claim, is_managed, dry_run)
        except Exception:
            logger.debug("Error processing PID entry %s", text, exc_info=True)
    return sweep.killed, sweep.retired, sweep.candidates


def _periodic_pid_sweep(
    my_gw_pid: int, active_pids: set[int]
) -> tuple[set[str], list[int]]:
    snapshot = _PidJournal(_session_pid_file_path()).snapshot_if_available()
    if not snapshot:
        return set(), []
    _, retired, candidates = _sweep_pid_entries(
        snapshot,
        should_skip_tagged=lambda owner, child: owner != my_gw_pid
        and _probe(owner) is not False,
        should_skip_bare=lambda pid: False,
        is_managed=active_pids.__contains__,
        dry_run=True,
    )
    return retired, candidates


def _kill_confirmed_and_writeback(
    my_gw_pid: int, confirmed: list[int], killed_or_dead: set[str]
) -> int:
    total = 0
    for pid in confirmed:
        count, root = _kill_pid_tree(pid)
        total += count
        if root or _probe(pid) is False:
            killed_or_dead.add(f"{my_gw_pid}:{pid}")
    if killed_or_dead:
        _write_back_pid_file(killed_or_dead)
    return total


@dataclass(frozen=True)
class _ProviderProcess:
    pid: int
    group: int | None

    @classmethod
    def locate(cls, provider: ModelProvider) -> "_ProviderProcess | None":
        client = getattr(provider, "_client", None)
        pid = getattr(client, "_pid", None) if client else None
        group = getattr(client, "_pgid", None) if client else None
        for attribute in ("_proc", "_active_proc"):
            if pid is not None:
                break
            process = getattr(provider, attribute, None)
            if process is not None and process.returncode is None:
                pid = process.pid
        if pid is None:
            return None
        if group is None:
            try:
                group = os.getpgid(pid)
            except OSError:
                group = None
        return cls(pid, group)

    def terminate(self) -> None:
        if self.group:
            self._terminate_group()
        else:
            self._terminate_pid()

    def _terminate_group(self) -> None:
        import time

        group = self.group
        if group is None:
            return
        for signum in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(group, signum)
            except OSError:
                break
            if signum == signal.SIGTERM:
                time.sleep(0.1)
        logger.warning(
            "_sync_kill_provider: killed pgid %d (root PID %d) for leaked provider",
            self.group,
            self.pid,
        )

    def _terminate_pid(self) -> None:
        for signum in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.kill(self.pid, signum)
            except OSError:
                return
            if signum == signal.SIGTERM:
                try:
                    os.waitpid(self.pid, os.WNOHANG)
                except ChildProcessError:
                    return
        logger.warning(
            "_sync_kill_provider: killed PID %d for leaked provider", self.pid
        )


def _sync_kill_provider(provider: ModelProvider) -> None:
    process = _ProviderProcess.locate(provider)
    if process is not None:
        process.terminate()


def _get_ppid_libproc(pid: int) -> int:
    global _libproc
    if _libproc is None:
        library = ctypes.util.find_library("proc")
        if library is None:
            raise OSError("libproc not found")
        _libproc = ctypes.CDLL(library)
        _libproc.proc_pidinfo.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint64,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        _libproc.proc_pidinfo.restype = ctypes.c_int
    storage = ctypes.create_string_buffer(136)
    received = _libproc.proc_pidinfo(pid, 3, 0, storage, len(storage))
    return struct.unpack_from("<I", storage.raw, 16)[0] if received > 0 else -1


def _parent_of(pid: int) -> int:
    try:
        if sys.platform != "linux":
            return _get_ppid_libproc(pid)
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("PPid:"):
                return int(line.split()[1])
    except Exception:
        pass
    return -1


@dataclass(frozen=True)
class _ChildClaim:
    child: int
    parent: int

    def retire(self) -> tuple[bool, int]:
        alive = _probe(self.child)
        if alive is False:
            return True, 0
        if alive is None or _probe(self.parent) is not False:
            return False, 0
        if _parent_of(self.child) not in (1, self.parent):
            return True, 0
        try:
            os.kill(self.child, signal.SIGKILL)
        except OSError:
            return True, 0
        return True, 1


def _cleanup_orphaned_mcp_servers() -> int:
    journal = _PidJournal(_pid_file_path())
    if not journal.path.exists():
        return 0
    with _pid_file_lock():
        lines = journal.path.read_text(encoding="utf-8").splitlines()
        retired, count = set(), 0
        for raw in lines:
            text = raw.strip()
            if not text:
                continue
            first, separator, second = text.partition(":")
            try:
                if not separator:
                    if _probe(int(first)) is False:
                        retired.add(text)
                    continue
                claim = _ChildClaim(int(first), int(second))
            except ValueError:
                continue
            remove, killed = claim.retire()
            count += killed
            if remove:
                retired.add(text)
        if retired:
            journal.replace([line for line in lines if line.strip() not in retired])
    return count


def _remove_stale_notes(home: Path) -> int:
    count = 0
    for note in home.glob("session_pid_*.txt"):
        try:
            pid = int(note.stem.removeprefix("session_pid_"))
        except ValueError:
            logger.debug("Removing malformed pid file: %s", note.name)
            try:
                note.unlink(missing_ok=True)
                count += 1
            except OSError:
                logger.debug("Could not remove malformed pid file: %s", note.name)
            continue
        if _probe(pid) is False:
            note.unlink(missing_ok=True)
            count += 1
    return count


def _remove_empty_workspaces(home: Path) -> int:
    root = home.joinpath("sessions")
    if not root.exists():
        return 0
    count = 0
    for directory in root.iterdir():
        if directory.is_dir() and not any(directory.iterdir()):
            try:
                directory.rmdir()
                count += 1
            except OSError:
                pass
    return count


def cleanup_orphaned_sessions() -> None:
    with _session_pid_file_lock():
        entries = _PidJournal(_session_pid_file_path()).read()
    killed, retired, _ = _sweep_pid_entries(
        entries,
        should_skip_tagged=lambda owner, child: _probe(owner) is not False,
        should_skip_bare=lambda pid: False,
    )
    if retired:
        _write_back_pid_file(retired)
    if killed:
        logger.info("Cleaned up %d orphaned ACP agent processes", killed)
    mcp = _cleanup_orphaned_mcp_servers()
    if mcp:
        logger.info("Cleaned up %d orphaned MCP server processes", mcp)
    notes = _remove_stale_notes(config_dir())
    if notes:
        logger.info("Cleaned up %d stale session PID files", notes)
    directories = _remove_empty_workspaces(config_dir())
    if directories:
        logger.info("Cleaned up %d empty session workspace dirs", directories)
