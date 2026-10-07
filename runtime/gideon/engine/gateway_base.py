"""Publish and resolve the API address belonging to this runtime instance."""

from __future__ import annotations

import atexit
import hashlib
import json
import logging
import os
import shlex
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)
PORT_ENV = "GIDEON_PORT"
RUNTIME_FILE = "gateway.runtime.json"
CLAIM_FILE = "gateway.lock"
_claim = None


def home_fingerprint() -> str:
    from gideon.core.config.loader import config_dir

    return hashlib.sha256(os.fsencode(config_dir().resolve())).hexdigest()


class HomeAlreadyClaimed(RuntimeError):
    pass


class HomeClaim:
    def __init__(self, fd, home):
        self.fd, self.home = fd, home

    def close(self):
        global _claim
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        if _claim is self:
            _claim = None


def claim_home() -> HomeClaim:
    """Hold one kernel lock per home through startup and shutdown."""
    global _claim
    import fcntl

    from gideon.core.config.loader import config_dir

    home = config_dir().resolve()
    if _claim is not None:
        if _claim.home != home:
            raise HomeAlreadyClaimed("gateway already claimed another home")
        return _claim
    fd = os.open(
        home / CLAIM_FILE, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise HomeAlreadyClaimed(
                "gateway lock must be a regular file with one link"
            )
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            owner = os.pread(fd, 256, 0).decode("utf-8", "replace").strip()
            raise HomeAlreadyClaimed(
                f"gateway {owner or '(starting)'} already owns this home: {home}"
            ) from error
        os.fchmod(fd, 0o600)
        os.ftruncate(fd, 0)
        os.write(fd, f"pid {os.getpid()}".encode())
    except BaseException:
        os.close(fd)
        raise
    _claim = HomeClaim(fd, home)
    atexit.register(_claim.close)
    return _claim


class GatewayBaseUnresolved(RuntimeError):
    """No authoritative address is available for this runtime instance."""


def _runtime_path() -> Path:
    from gideon.core.config.loader import config_dir

    return config_dir().joinpath(RUNTIME_FILE)


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


@dataclass(frozen=True)
class ProcessFacts:
    argv: tuple[str, ...]
    identity: str
    state: str = ""


def process_facts(pid: int) -> ProcessFacts | None:
    if pid <= 0:
        return None
    proc = Path("/proc")
    if proc.is_dir():
        try:
            entry = proc / str(pid)
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            boot = (proc / "sys/kernel/random/boot_id").read_text().strip()
            argv = tuple(
                word.decode("utf-8", "replace")
                for word in (entry / "cmdline").read_bytes().split(b"\0")
                if word
            )
            return ProcessFacts(argv, f"{boot}:{fields[19]}", fields[0])
        except (OSError, IndexError, ValueError):
            return None
    try:
        result = subprocess.run(
            ["ps", "-ww", "-p", str(pid), "-o", "lstart=", "-o", "args="],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
        output = result.stdout.strip()
        if result.returncode or len(output) < 25:
            return None
        return ProcessFacts(tuple(shlex.split(output[24:].strip())), output[:24])
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


@dataclass(frozen=True)
class BoundGateway:
    port: int
    pid: int
    identity: str = ""

    def write(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = destination.with_suffix(".tmp")
        staging.write_text(
            json.dumps(dict(port=self.port, pid=self.pid, identity=self.identity)),
            encoding="utf-8",
        )
        os.replace(staging, destination)

    @classmethod
    def read(cls, source: Path) -> BoundGateway | None:
        try:
            text = source.read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            document = json.loads(text)
            return cls(
                int(document["port"]),
                int(document.get("pid", 0)),
                str(document.get("identity", "")),
            )
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            logger.debug("ignoring malformed %s", RUNTIME_FILE)
            return None

    def live_port(self) -> int | None:
        if 0 < self.port <= 65535 and _pid_is_alive(self.pid):
            if self.identity:
                facts = process_facts(self.pid)
                if (
                    facts is None
                    or facts.identity != self.identity
                    or facts.state == "Z"
                ):
                    return None
            return self.port
        return None


def publish(port: int, *, pid: int | None = None) -> None:
    if not isinstance(port, int) or isinstance(port, bool) or not 0 < port <= 65535:
        raise ValueError(
            f"gateway_base.publish() needs the bound port, got {port!r}. A gateway that cannot name its own socket cannot address its children."
        )
    os.environ[PORT_ENV] = str(port)
    owner = int(os.getpid() if pid is None else pid)
    facts = process_facts(owner)
    binding = BoundGateway(port, owner, facts.identity if facts else "")
    try:
        binding.write(_runtime_path())
    except OSError:
        logger.warning(
            "could not write %s; children outside this process tree will have to resolve the port from the environment",
            RUNTIME_FILE,
            exc_info=True,
        )


def unpublish() -> None:
    try:
        path = _runtime_path()
        binding = BoundGateway.read(path)
        facts = process_facts(os.getpid())
        if (
            binding
            and binding.pid == os.getpid()
            and facts
            and binding.identity == facts.identity
        ):
            path.unlink()
    except Exception:
        logger.debug("could not remove %s", RUNTIME_FILE, exc_info=True)


def live_port() -> int | None:
    recorded = BoundGateway.read(_runtime_path())
    return recorded.live_port() if recorded is not None else None


def _configured_port() -> int | None:
    try:
        from gideon.core.config.loader import AppConfig

        declared = str(AppConfig.load().dashboard.url or "").strip()
    except Exception:
        logger.debug("dashboard.url unreadable", exc_info=True)
        return None
    if not declared:
        return None
    address = declared if "://" in declared else "http://" + declared
    try:
        port = urlparse(address).port
    except ValueError:
        logger.warning(
            "dashboard.url %r has a malformed port; it declares nothing", address
        )
        return None
    return port if port is not None and port > 0 else None


def _environment_port() -> int | None:
    value = os.environ.get(PORT_ENV, "").strip()
    if not value:
        return None
    try:
        port = int(value)
    except ValueError:
        port = 0
    if port > 0:
        return port
    logger.warning("%s=%r is not a usable port; ignoring it", PORT_ENV, value)
    return None


def resolve_port() -> int:
    for read in (_environment_port, live_port, _configured_port):
        port = read()
        if port:
            return port
    raise GatewayBaseUnresolved(
        "cannot resolve this instance's gateway address: "
        f"{PORT_ENV} is unset, no live gateway record at {_runtime_path()}, "
        "and dashboard.url declares no port. Refusing to assume the default port — "
        "on a multi-instance host that would send this request to a DIFFERENT instance's "
        "gateway (issue #2539). Fix: start the gateway (it publishes its bound port), "
        f"or set {PORT_ENV}, or give dashboard.url an explicit port."
    )


def resolve_api_base() -> str:
    port = resolve_port()
    return f"http://127.0.0.1:{port}"
