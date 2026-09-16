"""Discover loopback listeners owned by processes inside a run workspace."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)

_LOOPBACK_ADDRS: frozenset[str] = frozenset(
    {
        "*",
        "127.0.0.1",
        "0.0.0.0",
        "::",
        "::1",
        "[::]",
        "[::1]",
        "localhost",
    }  # noqa: S104
)

_PROBE_TIMEOUT = 4.0

_MAX_PORTS = 24


@dataclass
class PreviewPort:
    """One previewable dev server: a port, the process behind it, and the URL to open."""

    port: int
    pid: int = 0
    command: str = ""
    address: str = ""

    @property
    def url(self) -> str:
        return f"http://localhost:{self.port}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "port": self.port,
            "url": self.url,
            "pid": self.pid,
            "command": self.command,
            "address": self.address,
        }


@dataclass
class PreviewScan:
    """The result of one scan. ``reason`` explains an empty ``ports`` list, always."""

    ports: list[PreviewPort] = field(default_factory=list)
    root: str = ""
    scanned: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ports": [p.to_dict() for p in self.ports],
            "root": self.root,
            "scanned": self.scanned,
            "reason": self.reason,
        }


def _run(argv: list[str]) -> str:
    """Best-effort capture of *argv*'s stdout. Never raises; returns "" on any failure."""
    captured = ""
    try:
        completed = (
            subprocess.run(  # noqa: S603 — fixed argv, no shell, no user-supplied words
                argv,
                capture_output=True,
                text=True,
                timeout=_PROBE_TIMEOUT,
                check=False,
            )
        )
    except (OSError, subprocess.SubprocessError):
        logger.debug("web_preview: probe failed: %s", argv[0], exc_info=True)
    else:
        captured = completed.stdout or ""
    return captured


def _coerce_pid(text: str) -> int:
    """A pid parsed from a probe line; 0 when the line does not carry a number."""
    try:
        return int(text)
    except ValueError:
        return 0


def _endpoint(text: str) -> tuple[str, int] | None:
    """Split ``<address>:<port>``; ``None`` unless the port is one a URL may name."""
    address, separator, digits = text.rpartition(":")
    if not separator:
        return None
    try:
        port = int(digits)
    except ValueError:
        return None
    if not 0 < port < 65536:
        return None
    return address, port


def _lsof_blocks(out: str) -> Iterator[tuple[int, list[str]]]:
    """Group ``-F`` output into ``(pid, [n-line payloads])`` blocks.

    A ``p<pid>`` line owns every following ``n`` line until the next ``p`` or the end of the
    output; blocks with no usable pid are dropped together with their payloads.
    """
    owner = 0
    payloads: list[str] = []
    for record in out.splitlines():
        if not record:
            continue
        if record.startswith("p"):
            if owner > 0:
                yield owner, payloads
            owner = _coerce_pid(record[1:])
            payloads = []
        elif record.startswith("n"):
            payloads.append(record[1:])
    if owner > 0:
        yield owner, payloads


def parse_lsof_listeners(out: str) -> list[tuple[int, str, int]]:
    """Parse ``lsof -nP -iTCP -sTCP:LISTEN -FpPn`` into ``(pid, address, port)`` triples.

    The ``-F`` field format emits a ``p<pid>`` line that OWNS every following ``n<addr>:<port>``
    line until the next ``p``. Parsed as a pure function so the format is testable without a
    live socket.
    """
    listeners: list[tuple[int, str, int]] = []
    for pid, payloads in _lsof_blocks(out):
        for payload in payloads:
            endpoint = _endpoint(payload)
            if endpoint is not None:
                listeners.append((pid, endpoint[0], endpoint[1]))
    return listeners


def _pid_markers(text: str) -> list[int]:
    """Every ``pid=<digits>`` owner mentioned in one ``ss`` row, in the order written."""
    markers: list[int] = []
    cursor = text.find("pid=")
    while cursor != -1:
        start = cursor + 4
        stop = start
        while stop < len(text) and text[stop].isdigit():
            stop += 1
        if stop > start:
            markers.append(int(text[start:stop]))
            cursor = text.find("pid=", stop)
        else:
            cursor = text.find("pid=", start)
    return markers


def parse_ss_listeners(out: str) -> list[tuple[int, str, int]]:
    """Parse ``ss -lntpH`` into ``(pid, address, port)`` triples (the Linux tier).

    A row's local address is field 3 and the process block looks like
    ``users:(("node",pid=4242,fd=24))``. Kept a pure function for the same reason as the
    ``lsof`` parser: this box is Darwin, so the format is asserted from a fixture rather
    than left unverified.
    """
    listeners: list[tuple[int, str, int]] = []
    for row in out.splitlines():
        columns = row.split()
        if len(columns) < 4:
            continue
        endpoint = _endpoint(columns[3])
        if endpoint is None:
            continue
        listeners.extend((pid, endpoint[0], endpoint[1]) for pid in _pid_markers(row))
    return listeners


def _listeners() -> tuple[list[tuple[int, str, int]], str]:
    """Every listening TCP socket with its owning pid. Returns (triples, reason-if-empty)."""
    for executable, argv, parse in (
        (
            "lsof",
            ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-FpPn"],
            parse_lsof_listeners,
        ),
        ("ss", ["ss", "-lntpH"], parse_ss_listeners),
    ):
        if shutil.which(executable):
            return parse(_run(argv)), ""
    return [], "no port scanner available on this host (install lsof)"


def parse_lsof_cwds(out: str) -> dict[int, str]:
    """Parse ``lsof -a -p <pids> -d cwd -Fn`` into ``{pid: cwd}``."""
    cwds: dict[int, str] = {}
    for pid, payloads in _lsof_blocks(out):
        if payloads:
            cwds[pid] = payloads[-1]
    return cwds


def _proc_cwds(pids: list[int]) -> dict[int, str]:
    """The pids whose working directory ``/proc`` can name; {} where it cannot."""
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return {}
    proc_cwds: dict[int, str] = {}
    for pid in pids:
        try:
            proc_cwds[pid] = os.readlink(str(proc_root / str(pid) / "cwd"))
        except OSError:
            continue
    return proc_cwds


def _cwds(pids: list[int]) -> dict[int, str]:
    """The working directory of each pid in *pids*.

    ``pids`` MUST be non-empty: ``lsof -p ""`` does not select nothing, it selects EVERY
    process — measured while building this, and it would have turned a scoped probe into a
    host-wide one. The guard is here rather than at the call site so it cannot be forgotten.
    """
    if not pids:
        return {}
    observed = _proc_cwds(pids)
    if observed:
        return observed
    if not shutil.which("lsof"):
        return observed
    joined = ",".join(str(pid) for pid in pids)
    return parse_lsof_cwds(_run(["lsof", "-a", "-p", joined, "-d", "cwd", "-Fn"]))


def _command(pid: int) -> str:
    """A short command label for *pid* — never its full argv.

    Truncated and stripped of arguments deliberately: a dev-server command line routinely
    carries tokens and paths, and this string is rendered in the cockpit and copied into bug
    reports.
    """
    reported = _run(["ps", "-o", "comm=", "-p", str(pid)]).strip()
    if not reported:
        return ""
    executable = Path(reported.splitlines()[0]).name
    return executable[:64]


def _within(path: str, root: Path) -> bool:
    """Whether *path* resolves inside *root*. Resolved, so a symlink cannot smuggle a match."""
    if not path:
        return False
    try:
        resolved = Path(path).resolve()
    except (OSError, ValueError):
        return False
    try:
        resolved.relative_to(root)
    except (OSError, ValueError):
        return False
    return True


def _previewable(
    listeners: list[tuple[int, str, int]],
    workspace: Path,
    self_pid: int,
    cwds: dict[int, str],
) -> Iterator[tuple[int, str, int]]:
    """Listener triples owned by a process working in *workspace*, one per port."""
    claimed: set[int] = set()
    for pid, address, port in listeners:
        if pid == self_pid or port in claimed:
            continue
        if not _within(cwds.get(pid, ""), workspace):
            continue
        claimed.add(port)
        yield pid, address, port


def discover_ports(root: Path | str) -> PreviewScan:
    """Listening, localhost-reachable ports owned by processes working inside *root*.

    The scan is two probes, not one per process: one for every listening socket, then one
    batched cwd lookup for just the pids that turned up. A host with fifty listeners costs
    two subprocess calls.
    """
    target = str(root)
    try:
        base = Path(root).resolve()
    except OSError:
        return PreviewScan(
            root=target,
            reason="the run's workspace path could not be resolved",
        )
    if not base.is_dir():
        return PreviewScan(
            root=target,
            reason="the run's workspace is gone, so nothing can be running in it",
        )

    triples, unavailable = _listeners()
    if unavailable:
        return PreviewScan(root=target, reason=unavailable)

    scan = PreviewScan(root=target, scanned=True)
    reachable = sorted(
        (listener for listener in triples if listener[1] in _LOOPBACK_ADDRS),
        key=lambda listener: listener[2],
    )
    if not reachable:
        scan.reason = "no dev server is listening in this run's workspace"
        return scan

    self_pid = os.getpid()
    cwds = _cwds(sorted({pid for pid, _address, _port in reachable if pid != self_pid}))
    for pid, address, port in _previewable(reachable, base, self_pid, cwds):
        scan.ports.append(
            PreviewPort(port=port, pid=pid, command=_command(pid), address=address)
        )
        if len(scan.ports) >= _MAX_PORTS:
            break
    if not scan.ports:
        scan.reason = "no dev server is listening in this run's workspace"
    return scan


def preview_scan(run: Any) -> PreviewScan:
    """The preview scan for one run record.

    Reads the workspace path through :func:`~gideon.automation.workflows.provisioning.workspace_state`
    — the one reader for that block — so this never becomes a second spelling of
    ``worktree_path``. An inline run has no isolated workspace and honestly says so rather
    than scanning the whole host.
    """
    from gideon.automation.workflows import provisioning

    try:
        state = provisioning.workspace_state(run)
    except Exception:  # noqa: BLE001 — a preview is an aid; it must not break the panel
        logger.debug("web_preview: workspace_state failed", exc_info=True)
        return PreviewScan(reason="this run's workspace could not be read")

    path = str(state.get("path", "") or "")
    if path:
        try:
            return discover_ports(path)
        except Exception:  # noqa: BLE001
            logger.debug("web_preview: scan failed for %s", path, exc_info=True)
            return PreviewScan(root=path, reason="the port scan failed on this host")
    return PreviewScan(
        reason="this run has no isolated workspace, so there is nothing to preview"
    )
