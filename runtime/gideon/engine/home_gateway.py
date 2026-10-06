"""Verify public home identity before sending local credentials."""
from __future__ import annotations
import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from gideon.engine import gateway_base

class HomeGatewayMismatch(RuntimeError):
    pass

class NoGatewayRunning(RuntimeError):
    pass

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HomeGatewayMismatch("local gateway redirects are forbidden")

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

def open_loopback(request, *, timeout=5):
    url = request.full_url if isinstance(request, urllib.request.Request) else request
    parsed = urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.username or parsed.password or not parsed.port or not 0 < parsed.port <= 65535:
        raise HomeGatewayMismatch("gateway callback requires an explicit loopback HTTP port")
    return _opener.open(request, timeout=timeout)

def require_home_gateway(port: int | None = None, *, timeout=5) -> int:
    if port is None:
        try:
            port = gateway_base.resolve_port()
        except gateway_base.GatewayBaseUnresolved as error:
            raise NoGatewayRunning(str(error)) from error
    if isinstance(port, bool) or not isinstance(port, int) or not 0 < port <= 65535:
        raise HomeGatewayMismatch("invalid gateway port")
    try:
        with open_loopback(f"http://127.0.0.1:{port}/api/healthz", timeout=timeout) as response:
            payload = json.loads(response.read(65537))
    except urllib.error.HTTPError as error:
        raise HomeGatewayMismatch("gateway did not provide home identity") from error
    except urllib.error.URLError as error:
        if isinstance(error.reason, ConnectionRefusedError):
            raise NoGatewayRunning("home gateway is not listening") from error
        raise HomeGatewayMismatch("gateway identity could not be verified") from error
    except (ValueError, OSError) as error:
        raise HomeGatewayMismatch("gateway identity could not be verified") from error
    pid = payload.get("pid") if isinstance(payload, dict) else None
    facts = gateway_base.process_facts(pid) if isinstance(pid, int) and not isinstance(pid, bool) else None
    if not isinstance(payload, dict) or payload.get("home_fingerprint") != gateway_base.home_fingerprint() or type(payload.get("port")) is not int or payload.get("port") != port or facts is None or facts.state == "Z" or payload.get("process_identity") != facts.identity:
        raise HomeGatewayMismatch("gateway belongs to another home or process")
    recorded = gateway_base.BoundGateway.read(gateway_base._runtime_path())
    if recorded and recorded.live_port() is not None and (recorded.port != port or recorded.pid != pid or recorded.identity != facts.identity):
        raise HomeGatewayMismatch("gateway disagrees with home runtime record")
    return port
