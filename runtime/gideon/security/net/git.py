"""Git over HTTPS through the egress guard, for a fetch whose URL came from someone else's data.

``net/client.fetch`` closes the DNS-rebind window by dialing the addresses it validated, which it
can do because it owns the socket. ``git`` owns its own: it resolves the name itself, after any
check made before it ran, and follows a redirect to wherever the server says. So a check in front
of ``git clone`` checks a name, not the connection.

This module hands git no socket to the outside world. It points git at a loopback HTTP CONNECT
tunnel (``http.proxy``), so every connection git makes (the first, and each redirect hop) arrives
here as ``CONNECT host:443``. The tunnel asks :func:`gideon.security.net.guard.evaluate` about that
host at that moment, dials only an address the guard returned, and refuses everything else. What
git may speak is pinned as well: HTTPS only (``GIT_ALLOW_PROTOCOL``), no saved credentials, no
configuration from the owner's global or system files, and none of the environment that would send
it around the tunnel (a proxy of the environment's own, ``NO_PROXY``, injected ``-c`` settings).

Only port 443. A registry listing's URL names no port (``app catalog policy``), so
another port can only arrive in a redirect, which is the server's choice and not the owner's.
"""

from __future__ import annotations

import ipaddress
import functools
import logging
import os
import re
import selectors
import shutil
import socket
import socketserver
import subprocess
import threading
import tempfile
from dataclasses import dataclass
from collections.abc import Sequence
from urllib.parse import urlsplit

from gideon.security.net.guard import GuardDecision, evaluate
from gideon.security.net.policy import EgressPolicy

logger = logging.getLogger(__name__)

HTTPS_PORT = 443
MIN_GIT_VERSION = (2, 12)
_GLOBAL_OPTIONS_WITH_VALUE = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env", "--attr-source"}
)
_DIFF_SUBCOMMANDS = frozenset({"diff", "diff-files", "diff-index", "diff-tree", "log", "show"})
_NEUTRAL_SETTINGS = (
    f"core.hooksPath={os.devnull}",
    "core.fsmonitor=false",
    "core.askPass=",
    "core.editor=true",
    "sequence.editor=true",
    "diff.external=",
    "protocol.ext.allow=never",
    "protocol.file.allow=never",
    "protocol.git.allow=never",
    "core.alternateRefsCommand=true",
    "commit.gpgSign=false",
    "tag.gpgSign=false",
    "tag.forceSignAnnotated=false",
    "push.gpgSign=false",
    "log.showSignature=false",
    "merge.verifySignatures=false",
    "format.pretty=medium",
    "gc.pruneExpire=never",
)


class GitTooOld(OSError):
    """The Git executable is too old to honor Gideon's safety settings."""


@functools.lru_cache(maxsize=8)
def _version_of(executable: str, mtime_ns: int) -> tuple[int, ...] | None:
    del mtime_ns  # Cache key: re-read the version when the executable changes.
    try:
        proc = subprocess.run(
            [executable, "version"],
            env=git_child_env(site="git-version"),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"git version (\d+(?:\.\d+)*)", proc.stdout or "")
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def git_version(git: str = "git") -> tuple[int, ...] | None:
    executable = shutil.which(git)
    if not executable:
        return None
    try:
        mtime_ns = os.stat(executable).st_mtime_ns
    except OSError:
        return None
    return _version_of(executable, mtime_ns)


def require_git(git: str = "git") -> None:
    version = git_version(git)
    if version is not None and version < MIN_GIT_VERSION:
        required = ".".join(map(str, MIN_GIT_VERSION))
        found = ".".join(map(str, version))
        raise GitTooOld(
            f"Gideon requires Git {required} or newer; found Git {found}. "
            "Upgrade Git before using repository operations."
        )


def git_child_env(*, site: str) -> dict[str, str]:
    """Build the child allowlist and remove inherited Git configuration overrides."""
    from gideon.security.sandbox import build_child_env

    env = {key: value for key, value in build_child_env(site=site).items() if not key.startswith("GIT_")}
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_TERMINAL_PROMPT="0",
        GIT_OPTIONAL_LOCKS="0",
        LANGUAGE="en",
    )
    return env


def _subcommand_index(args: Sequence[str]) -> int:
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in _GLOBAL_OPTIONS_WITH_VALUE:
            index += 2
        elif arg.startswith("-"):
            index += 1
        else:
            return index
    return len(args)


def git_argv(args: Sequence[str], *, https_only: bool = False, git: str = "git") -> list[str]:
    """Put repository-independent safety settings after caller Git options."""
    require_git(git)
    args = list(args)
    index = _subcommand_index(args)
    if index == len(args):
        raise ValueError(f"Git argv needs a subcommand, got {args!r}")
    settings = list(_NEUTRAL_SETTINGS)
    settings.extend(("core.sshCommand=ssh", "credential.helper="))
    if https_only:
        settings.extend(("protocol.allow=never", "protocol.https.allow=always"))
    else:
        settings.append("protocol.allow=never")
    neutral = ["--no-pager"]
    for setting in settings:
        neutral.extend(("-c", setting))
    tail = args[index:]
    if tail[0] in _DIFF_SUBCOMMANDS:
        tail = [tail[0], "--no-ext-diff", "--no-textconv", *tail[1:]]
    return [git, *args[:index], *neutral, *tail]

_HEAD_LIMIT = 16 * 1024  # a CONNECT request is one line and a few headers
_HEAD_TIMEOUT_S = 10.0
_DIAL_TIMEOUT_S = 15.0
_RELAY_CHUNK = 65536

#: Environment a plain ``git`` honours that would route it around the tunnel, feed it settings, or
#: run a program to ask for a password. A ``GIT_CONFIG_KEY_<n>``/``GIT_CONFIG_VALUE_<n>`` pair is
#: dropped by prefix in :func:`guarded_git_env`.
_SCRUBBED_ENV = (
    "http_proxy",
    "HTTP_PROXY",
    "https_proxy",
    "HTTPS_PROXY",
    "all_proxy",
    "ALL_PROXY",
    "no_proxy",
    "NO_PROXY",
    "GIT_PROXY_COMMAND",
    "GIT_SSH",
    "GIT_SSH_COMMAND",
    "GIT_ASKPASS",
    "SSH_ASKPASS",
    "GIT_CONFIG",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT",
)
_SCRUBBED_ENV_PREFIXES = ("GIT_",)


@dataclass(frozen=True)
class TunnelRefusal:
    """One connection git asked for and did not get."""

    host: str
    port: int
    # The forbidden address the host is or resolved to, "" when it was refused before resolving.
    address: str
    # The guard's category (``loopback``, ``private``, ``metadata``, ``deny_list``, …), or
    # ``port`` / ``method`` for a request that was never an HTTPS connection to 443.
    category: str
    reason: str


class GitEgressError(Exception):
    """A guarded git fetch that the tunnel did not let through."""


class GitEgressRefused(GitEgressError):
    """Git asked to reach an address the policy forbids. ``refusal`` is the first such request."""

    def __init__(self, refusal: TunnelRefusal) -> None:
        super().__init__(refusal.reason)
        self.refusal = refusal


class GitHostUnreachable(GitEgressError):
    """Git failed, and a host it asked for did not resolve or did not answer."""

    def __init__(self, host: str, reason: str) -> None:
        super().__init__(reason)
        self.host = host
        self.reason = reason


def guarded_git_env() -> dict[str, str]:
    """The environment a guarded git runs with (see the module docstring for why each part)."""
    source = {
        k: v
        for k, v in os.environ.items()
        if k not in _SCRUBBED_ENV and not k.startswith(_SCRUBBED_ENV_PREFIXES)
    }
    from gideon.security.sandbox import build_child_env

    env = build_child_env(site="app-registry-git", source=source)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    # Wins over any protocol.* setting, including one passed with -c.
    env["GIT_ALLOW_PROTOCOL"] = "https"
    return env


def _audit(policy: EgressPolicy, target: str, *, outcome: str, reason: str = "") -> None:
    """One SEL row per connection git asked for, allowed or not (best-effort, like net.fetch)."""
    try:
        from gideon.security.sel import sel

        sel().log_api_access(
            caller=f"net.git:{policy.name}",
            operation="egress_git",
            outcome=outcome,
            source="net",
            resources=target[:200],
            error=reason[:200],
        )
    except Exception:
        logger.debug("egress SEL audit failed", exc_info=True)


def _read_head(conn: socket.socket) -> tuple[bytes, bytes]:
    """The request head up to the blank line, and whatever arrived after it."""
    data = b""
    while b"\r\n\r\n" not in data:
        if len(data) > _HEAD_LIMIT:
            raise ValueError("request head too large")
        chunk = conn.recv(4096)
        if not chunk:
            raise ValueError("connection closed before the request head ended")
        data += chunk
    head, rest = data.split(b"\r\n\r\n", 1)
    return head, rest


_HOSTNAME = re.compile(r"[a-z0-9._-]+")


def _split_target(target: str) -> tuple[str, int] | None:
    """``host:port`` (``[v6]:port`` for an IPv6 literal) → ``(host, port)``, or None.

    The host must be a plain hostname or an IP literal: it is put into a URL for the guard, and a
    host carrying ``/``, ``@`` or ``#`` would have the guard judge a different host than the one
    named here (the dial goes to what the guard judged, so that is a refusal, not a bypass)."""
    host, sep, port = target.rpartition(":")
    if not sep or not host or not port.isdigit():
        return None
    host = host.lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            return None
    elif not _HOSTNAME.fullmatch(host):
        return None  # includes an unbracketed IPv6 literal, which is ambiguous
    return host, int(port)


def _relay(a: socket.socket, b: socket.socket, idle_timeout: float) -> None:
    """Copy bytes both ways until either side closes or the link sits idle for ``idle_timeout``."""
    selector = selectors.DefaultSelector()  # not select(): the gateway may hold >1024 descriptors
    try:
        selector.register(a, selectors.EVENT_READ, b)
        selector.register(b, selectors.EVENT_READ, a)
        while True:
            events = selector.select(idle_timeout)
            if not events:
                return
            for key, _mask in events:
                data = key.fileobj.recv(_RELAY_CHUNK)  # type: ignore[union-attr]
                if not data:
                    return
                key.data.sendall(data)
    except OSError:
        return
    finally:
        selector.close()
        a.close()
        b.close()


class _TunnelServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = False


class GuardedTunnel:
    """A loopback CONNECT proxy that lets git reach only what ``policy`` allows, per connection.

    Use as a context manager; ``port`` is where it listens. ``refused`` lists the connections it
    refused on the policy's grounds and ``unreachable`` the hosts it could not resolve or dial.
    """

    def __init__(self, policy: EgressPolicy) -> None:
        self.policy = policy
        self.refused: list[TunnelRefusal] = []
        self.unreachable: list[tuple[str, str]] = []
        self._server: _TunnelServer | None = None

    @property
    def port(self) -> int:
        if self._server is None:
            raise RuntimeError("the tunnel is not running")
        return int(self._server.server_address[1])

    def __enter__(self) -> "GuardedTunnel":
        tunnel = self

        class _Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                tunnel._serve(self.request)

        self._server = _TunnelServer(("127.0.0.1", 0), _Handler)
        threading.Thread(
            target=self._server.serve_forever, name="gideon-git-tunnel", daemon=True
        ).start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def _refuse(self, conn: socket.socket, status: str, refusal: TunnelRefusal) -> None:
        self.refused.append(refusal)
        _audit(
            self.policy, f"{refusal.host}:{refusal.port}", outcome="denied", reason=refusal.reason
        )
        _answer(conn, status)

    def _serve(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(_HEAD_TIMEOUT_S)
            try:
                head, early = _read_head(conn)
            except (OSError, ValueError):
                return
            parts = head.split(b"\r\n", 1)[0].decode("latin-1").split()
            if len(parts) != 3:
                _answer(conn, "400 Bad Request")
                return
            method, target = parts[0].upper(), parts[1]
            if method != "CONNECT":
                host = (urlsplit(target).hostname or target).lower()
                reason = "only HTTPS connections are allowed"
                self._refuse(
                    conn, "405 Method Not Allowed", TunnelRefusal(host, 0, "", "method", reason)
                )
                return
            split = _split_target(target)
            if split is None:
                _answer(conn, "400 Bad Request")
                return
            host, port = split
            if port != HTTPS_PORT:
                reason = f"only port {HTTPS_PORT} is allowed, not {port}"
                self._refuse(conn, "403 Forbidden", TunnelRefusal(host, port, "", "port", reason))
                return
            self._connect(conn, host, port, early)
        finally:
            conn.close()

    def _connect(self, conn: socket.socket, host: str, port: int, early: bytes) -> None:
        url_host = f"[{host}]" if ":" in host else host
        decision = evaluate(f"https://{url_host}/", self.policy)
        target = f"{host}:{port}"
        if not decision.allow:
            category = getattr(decision, "category", "policy")
            if category == "unresolvable":
                self.unreachable.append((host, "it does not resolve"))
                _audit(self.policy, target, outcome="failed", reason=decision.reason)
                _answer(conn, "502 Bad Gateway")
                return
            refusal = TunnelRefusal(
                host,
                port,
                getattr(decision, "address", ""),
                category,
                decision.reason,
            )
            self._refuse(conn, "403 Forbidden", refusal)
            return
        upstream: socket.socket | None = None
        failure = ""
        for ip in decision.pinned_ips:
            try:
                upstream = socket.create_connection((ip, port), timeout=_DIAL_TIMEOUT_S)
                break
            except OSError as exc:
                failure = f"{ip}: {exc}"
        if upstream is None:
            reason = failure or "no address answered"
            self.unreachable.append((host, reason))
            _audit(self.policy, target, outcome="failed", reason=reason)
            _answer(conn, "502 Bad Gateway")
            return
        _audit(self.policy, target, outcome="allowed")
        try:
            conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            if early:
                upstream.sendall(early)
        except OSError:
            upstream.close()
            return
        conn.settimeout(None)
        upstream.settimeout(None)
        _relay(conn, upstream, idle_timeout=self.policy.timeout_s)


def _answer(conn: socket.socket, status: str) -> None:
    try:
        conn.sendall(
            f"HTTP/1.1 {status}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode()
        )
    except OSError:
        pass


def preflight(url: str, policy: EgressPolicy) -> GuardDecision:
    """Judge *url* before git runs, and audit a refusal the way the tunnel does.

    Resolves the host now and checks every address, so a refusal lands before a process is
    spawned. It is not what makes the fetch safe: the tunnel judges the host again when git
    connects to it, which is the check a name that rebinds in between cannot pass."""
    decision = evaluate(url, policy)
    if not decision.allow:
        outcome = "failed" if getattr(decision, "category", "policy") == "unresolvable" else "denied"
        _audit(policy, url, outcome=outcome, reason=decision.reason)
    return decision


def run_git_guarded(
    args: list[str], *, policy: EgressPolicy, timeout: float, cwd: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run ``git <args>`` with every connection it makes held to ``policy``.

    Returns the finished process, whatever its exit status, when the tunnel refused nothing.
    Raises :class:`GitEgressRefused` when git asked for a forbidden address (even if git then
    exited 0: something tried to reach where it may not), :class:`GitHostUnreachable` when git
    failed and a host did not resolve or answer, and ``subprocess.TimeoutExpired`` past ``timeout``.
    """
    with tempfile.TemporaryDirectory(prefix="gideon-git-home-") as git_home, GuardedTunnel(policy) as tunnel:
        env = guarded_git_env()
        env.update(HOME=git_home, XDG_CONFIG_HOME=git_home, CURL_HOME=git_home)
        proc = subprocess.run(
            git_argv(
                [
                "-c",
                f"http.proxy=http://127.0.0.1:{tunnel.port}",
                # A listing fetch presents none of the owner's saved credentials to anyone.
                "-c",
                "credential.helper=",
                *args,
                ],
                https_only=True,
            ),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
    if tunnel.refused:
        raise GitEgressRefused(tunnel.refused[0])
    if proc.returncode != 0 and tunnel.unreachable:
        raise GitHostUnreachable(*tunnel.unreachable[0])
    return proc


@dataclass(frozen=True, slots=True)
class LocalGitSnapshot:
    repository_root: str
    head: str
    refs: tuple[str, ...]
    commits: tuple[str, ...]


def read_local_git_snapshot(
    repository: str | os.PathLike[str], *, max_commits: int = 64, timeout: float = 10.0
) -> LocalGitSnapshot:
    """Read a bounded local repository view through Gideon's Git process policy."""
    if isinstance(max_commits, bool) or not 1 <= max_commits <= 512:
        raise ValueError("max_commits must be between 1 and 512")
    if not 0.0 < timeout <= 60.0:
        raise ValueError("timeout must be between zero and 60 seconds")
    root = os.path.realpath(os.fspath(repository))
    if not os.path.isdir(root):
        raise ValueError("Git source must be an existing directory")

    def read(arguments: list[str]) -> str:
        completed = subprocess.run(
            git_argv(["-C", root, *arguments]),
            env=git_child_env(site="hypermid-git-source"),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if completed.returncode != 0:
            message = (completed.stderr or "Git source read failed").strip()
            raise RuntimeError(message[:1024])
        if len(completed.stdout.encode("utf-8")) > 1024 * 1024:
            raise ValueError("Git source read exceeded the output limit")
        return completed.stdout

    repository_root = os.path.realpath(read(["rev-parse", "--show-toplevel"]).strip())
    if repository_root != root:
        raise ValueError("Git source must identify the repository root")
    head = read(["rev-parse", "--verify", "HEAD"]).strip()
    if re.fullmatch(r"[0-9a-f]{40,64}", head) is None:
        raise ValueError("Git source HEAD is invalid")
    refs = tuple(
        line
        for line in read(
            [
                "for-each-ref",
                "--count=512",
                "--format=%(refname)%00%(objectname)",
                "refs/heads",
                "refs/tags",
            ]
        ).splitlines()
        if line
    )
    commits = tuple(
        line
        for line in read(
            [
                "log",
                f"--max-count={max_commits}",
                "--format=%H%x00%aI%x00%s",
                "--no-decorate",
                "--no-show-signature",
            ]
        ).splitlines()
        if line
    )
    return LocalGitSnapshot(repository_root, head, refs, commits)
