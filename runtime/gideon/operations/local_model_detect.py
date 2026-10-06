"""Credentialless local discovery; a LAN scan runs only after explicit owner opt-in."""

from __future__ import annotations

import concurrent.futures as cf
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

from gideon.operations.seed_local_model import DEFAULT_ENDPOINT, DESCRIBE_BUDGET_SECS

SCAN_BUDGET_SECS = 3.0
SCAN_MAX_HOSTS = 256
SCAN_MAX_WORKERS = 64
_PRIVATE = tuple(
    ipaddress.ip_network(value)
    for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


def endpoint_identity(endpoint: str) -> str:
    try:
        url = urlsplit(endpoint)
        host = str(url.hostname or "").lower().rstrip(".")
        if host == "localhost":
            host = "127.0.0.1"
        else:
            try:
                host = ipaddress.ip_address(host).compressed
            except ValueError:
                pass
        return f'{url.scheme.lower()}://{host}:{url.port or (443 if url.scheme == "https" else 80)}{url.path.rstrip("/")}'
    except ValueError:
        return ""


def endpoint_is_local(endpoint: str) -> bool:
    try:
        url = urlsplit(endpoint)
        if (
            url.scheme not in ("http", "https")
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            return False
        if not url.hostname or url.port is not None and not 1 <= url.port <= 65535:
            return False
        host = url.hostname.lower().rstrip(".")
        if host == "localhost":
            return True
        address = ipaddress.ip_address(host)
        if (
            isinstance(address, ipaddress.IPv6Address)
            and address.ipv4_mapped is not None
        ):
            address = address.ipv4_mapped
        return address.is_loopback or any(
            address.version == net.version and address in net for net in _PRIVATE
        )
    except ValueError:
        return False


def _private_ipv4(host: str) -> bool:
    from gideon.security.net.guard import classify_host

    try:
        address = ipaddress.ip_address(host)
        return (
            address.version == 4
            and classify_host(host).category == "private"
            and any(address in net for net in _PRIVATE)
        )
    except ValueError:
        return False


@dataclass(frozen=True)
class DetectedEndpoint:
    endpoint: str
    model: str
    provider: str = ""

    def to_dict(self):
        return {
            "endpoint": self.endpoint,
            "model": self.model,
            "provider": self.provider,
        }


def _probe(endpoint: str, *, timeout=2.0, describe_budget=DESCRIBE_BUDGET_SECS):
    if not endpoint_is_local(endpoint):
        return None
    from gideon.operations.seed_local_model import (
        endpoint_models,
        instance_at,
        pick_model,
    )

    rows = endpoint_models(endpoint, timeout=timeout, describe_budget=describe_budget)
    selected = pick_model(rows, "chat") if rows else ""
    return (
        DetectedEndpoint(endpoint, selected, instance_at(endpoint))
        if selected
        else None
    )


def detect_localhost(*, endpoint: str = DEFAULT_ENDPOINT):
    # Automatic detection never consumes an environment-provided LAN/public endpoint.
    url = urlsplit(endpoint)
    if url.hostname != "localhost":
        try:
            if not ipaddress.ip_address(url.hostname or "").is_loopback:
                return None
        except ValueError:
            return None
    return _probe(endpoint)


def _candidate_hosts(max_hosts=SCAN_MAX_HOSTS):
    own = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 9))  # route selection only; no packet sent
            own.add(sock.getsockname()[0])
        own.update(
            str(row[4][0])
            for row in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
        )
    except OSError:
        pass
    candidates = []
    for host in sorted(own):
        if not _private_ipv4(host):
            continue
        for address in ipaddress.ip_network(f"{host}/24", strict=False).hosts():
            candidate = str(address)
            if (
                candidate not in own
                and candidate not in candidates
                and _private_ipv4(candidate)
            ):
                candidates.append(candidate)
                if len(candidates) >= max_hosts:
                    return candidates
    return candidates


def scan_local_network(*, budget_secs=SCAN_BUDGET_SECS, candidates=None, prober=None):
    hosts = [
        host
        for host in (candidates if candidates is not None else _candidate_hosts())
        if _private_ipv4(host)
    ][:SCAN_MAX_HOSTS]
    if not hosts:
        return []
    prober = prober or (
        lambda endpoint: _probe(endpoint, timeout=0.3, describe_budget=1.5)
    )
    pool = cf.ThreadPoolExecutor(max_workers=min(SCAN_MAX_WORKERS, len(hosts)))
    found = []
    try:
        futures = {pool.submit(prober, f"http://{host}:11434") for host in hosts}
        try:
            for future in cf.as_completed(futures, timeout=budget_secs):
                try:
                    if result := future.result():
                        found.append(result)
                except Exception:
                    pass
        except cf.TimeoutError:
            pass
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return sorted(found, key=lambda result: result.endpoint)
