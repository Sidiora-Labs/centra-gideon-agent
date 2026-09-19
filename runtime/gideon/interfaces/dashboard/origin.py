"""Shared origin-validation helpers for CSRF and WebSocket checks.

Centralises dashboard URL parsing, bind-address resolution, origin-set
construction, and per-request origin validation so that ``server.py``
(CSRF middleware), ``ws.py`` (WebSocket handshake), and ``gateway.py``
(startup messages) all share a single source of truth.

The only user-facing config is ``dashboard.url`` — a single URL like
``http://my-host.example.com:8080``.  Everything
else (port, bind address, allowed origins) is derived from it.
"""

import ipaddress
import logging
import os
import shutil
import socket
from collections.abc import Iterable
from urllib.parse import parse_qs, quote, urlparse

from aiohttp import web

from gideon.core.config.loader import _DEFAULT_PORT
from gideon.security.auth.modes import AuthConfig, AuthMode, effective_bind

logger = logging.getLogger(__name__)

_BIND_LOCAL = "127.0.0.1"
_BIND_ALL = "0.0.0.0"

_BIND_HOST_ENV = "GIDEON_BIND_HOST"


def machine_hostname() -> str | None:
    """Return the machine hostname, or ``None`` on failure."""
    try:
        return socket.gethostname()
    except Exception:
        return None


def is_loopback(host: str) -> bool:
    """Return ``True`` if *host* is a loopback address (127.0.0.1, ::1, etc.)."""
    if host in ("localhost", "127.0.0.1", "::1", "gideon.localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_private_network(host: str) -> bool:
    """Return ``True`` if *host* is a non-public address (loopback, RFC1918, link-local,
    ULA, multicast, reserved, unspecified).

    Used by the optional ``GIDEON_BYPASS_LOCAL_NETWORKS`` token-auth bypass so
    requests from trusted home/dev LANs can skip the token gate. Delegates to
    ``net.guard.classify_host`` — the ONE authoritative "is this IP public" table shared
    with the outbound egress guard — so inbound and outbound agree on what "private" means
    (the old local definition covered only private+link-local, missing e.g. the
    IPv4-mapped-IPv6 case the shared classifier handles).
    """
    if is_loopback(host):
        return True
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    from gideon.security.net.guard import classify_host

    return not classify_host(host).public


def parse_dashboard_url(url: str) -> tuple[str, int]:
    """Parse ``dashboard.url`` into ``(hostname, port)``.

    Returns ``("", _DEFAULT_PORT)`` when *url* is empty.
    ``GIDEON_PORT`` env var always overrides the port (dev mode).
    """
    if not url:
        host, port = "", _DEFAULT_PORT
    else:
        url = _ensure_scheme(url)
        parsed = urlparse(url)
        host = parsed.hostname or ""
        port = parsed.port or _DEFAULT_PORT
    env_port = os.environ.get("GIDEON_PORT")
    if env_port:
        try:
            port = int(env_port)
        except ValueError:
            logger.warning(
                "GIDEON_PORT=%r is not a valid integer; using port %d from config",
                env_port,
                port,
            )
    return host, port


def _ensure_scheme(url: str) -> str:
    """Prepend ``http://`` if *url* has no scheme."""
    return url if "://" in url else f"http://{url}"


def dashboard_origin(url: str) -> str:
    """Return the browser-facing origin for *url*, or ``""`` if invalid.

    Reuses the same scheme-defaulting logic as :func:`parse_dashboard_url`
    so that bare hostnames (``myhost:8080``) are normalised to ``http://``.
    Default ports (80 for http, 443 for https) are stripped to match
    browser ``Origin`` header behaviour.
    """
    if not url:
        return ""
    url = _ensure_scheme(url)
    try:
        parsed = urlparse(url)
        scheme = parsed.scheme
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        logger.warning("Ignoring malformed dashboard_url: %s", url)
        return ""
    if not host:
        return ""
    if scheme not in ("http", "https"):
        logger.warning("Ignoring non-HTTP dashboard_url scheme: %s", scheme)
        return ""
    if ":" in host:
        host = f"[{host}]"
    default_port = {"http": 80, "https": 443}.get(scheme)
    if port == default_port:
        port = None
    return f"{scheme}://{host}:{port}" if port else f"{scheme}://{host}"


def devspaces_proxy_url(port: int) -> str | None:
    """Return the DevSpaces proxy base URL, or ``None`` if not running in DevSpaces."""
    ds_id = os.environ.get("DEVPROXY_ID")
    region = os.environ.get("AWS_REGION")
    if ds_id and region:
        return f"https://{ds_id}--{port}.{region}.prod.proxy.devproxy.example.com"
    return None


def resolve_bind_host(auth_cfg: AuthConfig | None = None) -> str:
    """Return the TCP bind address string for aiohttp.

    Resolution order:

    1. ``GIDEON_BIND_HOST`` env var (explicit corp-host escape hatch)
       — preserved for dev proxy / reverse-proxy setups
       where the gateway must listen on a non-loopback interface.
    2. ``effective_bind(auth_cfg)`` — when ``auth_cfg.mode == AuthMode.NONE``
       this always returns ``127.0.0.1`` (loopback invariant: auth-disabled
       must never bind a non-loopback interface).
    3. ``127.0.0.1`` when *auth_cfg* is omitted.
    """
    env_host = os.environ.get(_BIND_HOST_ENV, "").strip()
    if env_host:
        return env_host
    if auth_cfg is None:
        return _BIND_LOCAL
    return effective_bind(auth_cfg)


def is_local_bind(bind_host: str) -> bool:
    """Return ``True`` if *bind_host* is the loopback address."""
    return bind_host == _BIND_LOCAL


_TAILNET_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def _local_addresses() -> list[str]:
    """Best-effort enumeration of this machine's local IPv4/IPv6 addresses.

    Uses ``getaddrinfo(gethostname())`` — stdlib only, no network round-trip.
    Never raises: address discovery failing is an empty list, not an error (the
    caller treats "no tailnet found" the same either way).
    """
    addrs: set[str] = set()
    try:
        host = socket.gethostname()
    except Exception:
        return []
    try:
        for info in socket.getaddrinfo(host, None):
            sockaddr = info[4]
            if sockaddr and sockaddr[0]:
                addrs.add(str(sockaddr[0]))
    except Exception:
        pass
    return sorted(addrs)


def tailnet_ip(addresses: Iterable[str] | None = None) -> str:
    """Return this machine's tailnet address (100.64.0.0/10), or ``""`` if none.

    *addresses* is an injectable seam: pass an explicit iterable to test without
    touching the network (a fixture feeds ``["100.101.102.103"]`` for present,
    ``["192.168.1.5"]`` for absent). When omitted, local addresses are discovered
    via :func:`_local_addresses`.
    """
    candidates = list(addresses) if addresses is not None else _local_addresses()
    for addr in candidates:
        bare = addr.split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(bare)
        except ValueError:
            continue
        if ip.version == 4 and ip in _TAILNET_CGNAT:
            return str(ip)
    return ""


def tailscale_cli_present() -> bool:
    """Return ``True`` if the ``tailscale`` CLI is on PATH.

    Corroborating evidence only — the CLI being installed does NOT mean this
    machine is currently on a tailnet (it may be logged out). :func:`tailnet_ip`
    is the authoritative "on a tailnet now" signal.
    """
    return shutil.which("tailscale") is not None


def auth_is_off(auth_cfg: AuthConfig | None = None) -> bool:
    """Return ``True`` when the gateway serves requests with NO authentication.

    Two ways auth is genuinely off: ``AuthMode.NONE`` (pass-through), or the
    blanket ``GIDEON_DEV_NO_AUTH=1`` middleware skip. Both are dev-only and
    both are normally safe because ``effective_bind`` forces NONE to loopback —
    but the ``GIDEON_BIND_HOST`` escape hatch can override that bind, which
    is exactly the exposed-without-auth misconfiguration the reachability probe
    warns about. ``local_token``/``api_key``/``oauth2`` are NOT "off": a
    non-loopback bind under those still requires a token/credential.
    """
    if os.environ.get("GIDEON_DEV_NO_AUTH") == "1":
        return True
    cfg = auth_cfg if auth_cfg is not None else AuthConfig.from_env()
    return cfg.mode == AuthMode.NONE


def loopback_requires_token(auth_cfg: AuthConfig | None = None) -> bool:
    """Return ``True`` when a request from loopback still needs a token.

    A loopback (indeed any private-network) request skips the token gate only in
    the three cases the ``token_auth`` middleware short-circuits on: auth is
    genuinely off (``AuthMode.NONE`` / ``GIDEON_DEV_NO_AUTH=1`` — both via
    :func:`auth_is_off`) or the opt-in local-network bypass
    (``GIDEON_BYPASS_LOCAL_NETWORKS=1``). Under the default ``local_token``
    mode a token IS required even on loopback — the middleware returns
    ``403 {"error": "Token required"}`` for a tokenless loopback request. This is
    the predicate ``doctor`` must consult before claiming "no token required": a
    local *bind* is not the same fact as a token-free loopback.
    """
    if auth_is_off(auth_cfg):
        return False
    if os.environ.get("GIDEON_BYPASS_LOCAL_NETWORKS") == "1":
        return False
    return True


def resolve_dashboard_host(local_only: bool, configured_host: str = "") -> str:
    """Return the hostname users should use to reach the dashboard.

    For the auto-open URL (browser on the same machine), ``localhost`` is always
    correct — it works whether binding to 127.0.0.1 or 0.0.0.0. Using
    ``machine_hostname()`` was wrong: raw system hostnames (e.g. Docker-style
    ``b0f1d879fa5a``) aren't DNS-resolvable from the browser → the auto-open tab
    can't connect. The machine hostname is only useful for the "Remote: ssh -L…"
    log hint (``format_dashboard_urls`` handles that separately).
    """
    if configured_host:
        return configured_host
    try:
        socket.getaddrinfo("gideon.localhost", None)
        return "gideon.localhost"
    except socket.gaierror:
        return "localhost"


def build_dashboard_url(
    base_url: str, token: str = "", *, local_only: bool = True
) -> str:
    """Build the authenticated dashboard URL."""
    if local_only is not True and not token:
        raise ValueError("token is required when dashboard is not local-only")
    return f"{base_url}?token={quote(token, safe='')}" if token else base_url


def format_dashboard_urls(
    authed_url: str,
    *,
    port: int,
    local_only: bool = True,
    has_custom_host: bool = False,
) -> list[str]:
    """Return startup log lines describing how to reach the dashboard."""
    parsed_query = urlparse(authed_url).query
    _qs = f"?{parsed_query}" if parsed_query else ""
    if local_only is not True and "token" not in parse_qs(parsed_query):
        raise ValueError("token is required when dashboard is not local-only")
    _is_remote = bool(os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_CLIENT"))

    if _is_remote:
        mh = machine_hostname() or "localhost"
        lines: list[str] = [
            f"Dashboard: ssh -L {port}:localhost:{port} {mh}",
            f"             then open http://localhost:{port}{_qs}",
        ]
    else:
        lines = ["Dashboard:", f"   {authed_url}"]

    if local_only and not has_custom_host and not _is_remote:
        mh_local = machine_hostname()
        if mh_local and mh_local != "localhost":
            try:
                ip = socket.gethostbyname(mh_local)
                if ip and ip != "127.0.0.1":
                    lines.append(
                        f"Remote:    ssh -L {port}:localhost:{port} {mh_local}"
                    )
            except Exception:
                pass

    proxy = devspaces_proxy_url(port)
    if proxy and not local_only:
        lines.append(f"Proxy:     {proxy}{_qs}")

    if _is_remote:
        lines.append(
            "Run 24/7:  see docs/REMOTE_DESKTOP_SETUP.md for systemd service setup"
        )

    return lines


def build_allowed_origins(
    port: int, local_only: bool, configured_host: str = "", dashboard_url: str = ""
) -> set[str]:
    """Compute the set of allowed origins for the dashboard.

    When *dashboard_url* is provided, its origin (scheme + host + port)
    is added as-is so that reverse-proxy setups (e.g. Caddy with TLS on
    a custom domain) pass the CSRF check without code changes.
    """
    origins: set[str] = {
        f"http://127.0.0.1:{port}",
        f"http://localhost:{port}",
        f"http://gideon.localhost:{port}",
    }
    if os.environ.get("GIDEON_HOME"):
        origins.add("http://localhost:3000")
    if configured_host:
        origins.add(f"http://{configured_host}:{port}")
    if dashboard_url:
        origin = dashboard_origin(dashboard_url)
        if origin:
            origins.add(origin)
    if not local_only:
        mh = machine_hostname()
        if mh:
            origins.add(f"http://{mh}:{port}")
    proxy = devspaces_proxy_url(port)
    if proxy:
        origins.add(proxy)
    for _co in os.environ.get("GIDEON_CORS_ORIGINS", "").split(","):
        if _co.strip():
            origins.add(_co.strip())
    return origins


def allowed_cors_origin(request: web.Request) -> str:
    """Return an explicitly allowed browser ``Origin``, or ``""``.

    This is deliberately narrower than :func:`check_origin`.  CSRF and WebSocket
    admission accept loopback aliases on any port so SSH tunnels keep working, but
    reflecting an origin into ``Access-Control-Allow-Origin`` is a capability grant.
    Only origins declared in ``app["allowed_origins"]`` may receive that grant.
    """
    raw = (request.headers.get("Origin") or "").strip()
    if not raw or raw == "null":
        return ""
    try:
        parsed = urlparse(raw)
        port = parsed.port
    except ValueError:
        return ""
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        return ""
    host = parsed.hostname
    if ":" in host:
        host = f"[{host}]"
    default_port = 80 if parsed.scheme == "http" else 443
    origin = (
        f"{parsed.scheme}://{host}:{port}"
        if port is not None and port != default_port
        else f"{parsed.scheme}://{host}"
    )
    allowed: set[str] = request.app["allowed_origins"]
    return origin if origin in allowed else ""


def check_origin(
    request: web.Request,
    *,
    require: bool = True,
    fallback_header: str | None = None,
) -> bool:
    """Validate the request origin against ``app["allowed_origins"]``.

    Loopback requests (127.0.0.1, ::1) without an Origin header are
    always trusted — local processes like mcp-core and doctor don't
    send Origin headers but are not cross-origin attacks.  A browser
    on the same machine would always send an Origin header.
    """
    allowed: set[str] = request.app["allowed_origins"]
    origin = request.headers.get("Origin") or ""
    if not origin and fallback_header:
        origin = request.headers.get(fallback_header, "")
    if not origin:
        if is_loopback(request.remote or ""):
            return True
        return not require
    origin_base = "/".join(origin.split("/")[:3]) if "://" in origin else ""
    if origin_base in allowed:
        return True
    if origin_base:
        parsed = urlparse(origin_base)
        parsed_host = parsed.hostname or ""
        try:
            _ = parsed.port
        except ValueError:
            return False
        netloc = parsed.netloc.lower()
        if "@" in netloc:
            return False
        if netloc.startswith("["):
            after = netloc.rsplit("]", 1)[-1] if "]" in netloc else ":"
            if after.count(":") > 1:
                return False
        elif netloc.count(":") > 1:
            return False
        if is_loopback(parsed_host):
            return True
    return False
