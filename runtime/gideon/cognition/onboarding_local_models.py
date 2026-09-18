"""Zero-key local-model onboarding — detection, explicit scanning, key-less binding.

An Ollama service is the one model provider that needs no credential at all: it
listens on ``127.0.0.1:11434`` and answers ``GET /api/tags`` with its pulled models.
So first-run setup can offer it as a one-click binding instead of asking for a key
that does not exist. This module is the engine behind that offer; the HTTP surface is
:mod:`gideon.interfaces.dashboard.handlers.onboarding_local_models`.

Three rules shape every function here, and they are the security content of the
feature rather than incidental caution:

**Loopback is detected; the private network is only ever SCANNED on request.**
:func:`detect_local_ollama` touches the loopback default and nothing else, so the
first-run screen can poll it freely. Reaching an RFC-1918 address happens exclusively
through :func:`scan_private_hosts`, which takes the caller's own target list and is
never invoked by detection, by a poll, or by any timer in this process.

**A scan is bounded in every dimension a scan can run away in.** Targets are
expanded, classified and counted before a socket opens (:func:`expand_targets`);
the wall clock is capped by a deadline the caller sets within
:data:`SCAN_MAX_BUDGET_S`; concurrency is capped; and only the Ollama port is ever
dialled, so this cannot be turned into a port scanner.

**A binding is offered only for a host that answered as Ollama.** "The port was
open" is not enough: :func:`probe_ollama` requires a 200 carrying an Ollama-shaped
``{"models": [...]}`` body, so a random LAN service listening on 11434 is reported
unreachable rather than offered as a provider.

Every probe goes through the ordinary egress chokepoint
(:func:`gideon.security.net.client.fetch`) under
:func:`~gideon.security.net.policy.local_model_probe_policy`, which pins one host per
probe, keeps the metadata denies, and refuses any address that is not loopback or
private. This module therefore adds no network reach of its own — the private-address
allowance is declared once, in the policy plane, where it is reviewable.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

#: The provider type a local Ollama binding is written as.
PROVIDER_TYPE = "ollama"

#: The only port a scan ever dials. A tuple so the shape admits a second Ollama
#: port later, but never a caller-supplied one — a scan that took a port list would
#: be a port scanner wearing an onboarding hat.
OLLAMA_PORTS: tuple[int, ...] = (11434,)

#: Ollama's model-catalog route. A 200 here carrying ``{"models": [...]}`` is what
#: "this really is an Ollama" means throughout this module.
TAGS_PATH = "/api/tags"

#: Where a local Ollama listens out of the box. Loopback only — this tuple is the
#: whole automatic reach of the feature.
DEFAULT_LOCAL_ENDPOINTS: tuple[str, ...] = ("http://127.0.0.1:11434",)

DETECT_TIMEOUT_S = 1.5
SCAN_PROBE_TIMEOUT_S = 1.0
SCAN_MAX_TARGETS = 256
SCAN_MAX_CONCURRENCY = 16
SCAN_DEFAULT_BUDGET_S = 5.0
SCAN_MAX_BUDGET_S = 30.0

#: The widest IPv4 prefix one target may name. /24 is a home LAN; anything wider
#: would blow past :data:`SCAN_MAX_TARGETS` anyway, but refusing at the prefix says
#: so in the caller's own terms instead of as an opaque count.
SCAN_MIN_IPV4_PREFIX = 24
SCAN_MIN_IPV6_PREFIX = 120

_SCANNABLE_CATEGORIES = ("private", "loopback")


class ScanRefused(ValueError):
    """A scan request that is refused before any socket opens.

    Every target is expanded and classified first, so a request that names a public
    address, an over-wide prefix, or more hosts than the cap allows never reaches the
    network at all — the refusal is the whole outcome, not a partial scan plus a
    warning.
    """


@dataclass(frozen=True)
class ProbeResult:
    """One host's answer to the Ollama catalog probe.

    ``ok`` is true only for a live Ollama: a 200 carrying an Ollama-shaped body.
    A refused connection, a non-200, a redirect, a body that is not Ollama's, and an
    egress denial all land here as ``ok=False`` with ``detail`` saying which — the
    caller reports them and offers nothing.
    """

    endpoint: str
    host: str
    port: int
    ok: bool
    models: tuple[str, ...] = ()
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "endpoint": self.endpoint,
            "host": self.host,
            "port": self.port,
            "ok": self.ok,
            "models": list(self.models),
            "requires_key": False,
            "provider_type": PROVIDER_TYPE,
        }
        if self.detail:
            payload["detail"] = self.detail
        return payload


@dataclass(frozen=True)
class ScanReport:
    """The outcome of one explicit, time-bounded private-network scan."""

    targets: int
    probed: int
    offers: tuple[ProbeResult, ...] = ()
    unreachable: int = 0
    budget_s: float = 0.0
    elapsed_s: float = 0.0
    exhausted_budget: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "targets": self.targets,
            "probed": self.probed,
            "offers": [o.to_dict() for o in self.offers],
            "unreachable": self.unreachable,
            "budget_s": round(self.budget_s, 3),
            "elapsed_s": round(self.elapsed_s, 3),
            "exhausted_budget": self.exhausted_budget,
            "notes": list(self.notes),
        }


def endpoint_for(host: str, port: int) -> str:
    """The catalog base URL for one host/port, IPv6 literals bracketed."""
    try:
        bracketed = isinstance(ipaddress.ip_address(host), ipaddress.IPv6Address)
    except ValueError:
        bracketed = False
    return f"http://[{host}]:{port}" if bracketed else f"http://{host}:{port}"


def _models_of(body: bytes) -> tuple[str, ...] | None:
    """The model names in an Ollama ``/api/tags`` body, or ``None`` if it is not one.

    ``None`` is not "no models" — an Ollama with nothing pulled answers
    ``{"models": []}`` and is a perfectly bindable Ollama. ``None`` means the thing
    that answered is something else, which is exactly the case a binding must not be
    offered for.
    """
    try:
        parsed = json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    rows = parsed.get("models")
    if not isinstance(rows, list):
        return None
    names: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = row.get("name") or row.get("model")
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
    return tuple(dict.fromkeys(names))


async def probe_ollama(
    endpoint: str, *, timeout_s: float = DETECT_TIMEOUT_S
) -> ProbeResult:
    """Ask one endpoint whether it is a live Ollama, under the pinned probe policy.

    The single network primitive of this module: detection and scanning both go
    through here, so there is one definition of "answered as Ollama" and one egress
    posture, rather than a strict one for the loopback default and a looser one for
    the address a user typed.
    """
    from gideon.security.net.client import EgressBlocked, fetch
    from gideon.security.net.policy import (
        LocalModelProbeRefused,
        local_model_probe_policy,
    )

    base = (endpoint or "").strip().rstrip("/")
    parsed = urlparse(base if "://" in base else f"http://{base}")
    host = (parsed.hostname or "").strip().lower()
    port = parsed.port or OLLAMA_PORTS[0]

    try:
        policy = local_model_probe_policy(base)
    except LocalModelProbeRefused as exc:
        return ProbeResult(base, host, port, False, detail=str(exc))

    try:
        resp = await fetch(
            f"{base}{TAGS_PATH}",
            policy=policy.with_overrides(timeout_s=max(0.05, float(timeout_s))),
        )
    except EgressBlocked as exc:
        return ProbeResult(base, host, port, False, detail=exc.decision.reason)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — an unreachable host is an outcome
        return ProbeResult(
            base, host, port, False, detail=f"{exc.__class__.__name__}: {exc}"[:200]
        )

    if resp.status != 200:
        return ProbeResult(
            base, host, port, False, detail=f"HTTP {resp.status} from {TAGS_PATH}"
        )
    models = _models_of(resp.body)
    if models is None:
        return ProbeResult(
            base,
            host,
            port,
            False,
            detail="answered, but not with an Ollama model catalog",
        )
    return ProbeResult(base, host, port, True, models=models)


async def detect_local_ollama(
    endpoints: Sequence[str] = DEFAULT_LOCAL_ENDPOINTS,
    *,
    timeout_s: float = DETECT_TIMEOUT_S,
) -> ProbeResult:
    """Probe the loopback default(s) and return the first live Ollama.

    Loopback ONLY: the default list is the feature's entire automatic reach, and it
    holds no private-network address. When nothing answers, the last failure is
    returned so the screen can say what it looked for and what it got.
    """
    last: ProbeResult | None = None
    for endpoint in endpoints:
        last = await probe_ollama(endpoint, timeout_s=timeout_s)
        if last.ok:
            return last
    if last is not None:
        return last
    return ProbeResult("", "", OLLAMA_PORTS[0], False, detail="no endpoint to probe")


def expand_targets(targets: Iterable[Any]) -> list[str]:
    """Expand caller-supplied addresses/CIDRs into the exact hosts a scan may dial.

    Refuses, before anything opens a socket:

    * an empty or non-string target list,
    * anything that is not an IP literal or CIDR (a hostname would mean a DNS lookup,
      which is itself an egress signal and would let a name resolve somewhere this
      feature has no business reaching),
    * a prefix wider than :data:`SCAN_MIN_IPV4_PREFIX` / :data:`SCAN_MIN_IPV6_PREFIX`,
    * any address that is not loopback or private (a public address, and the
      link-local/metadata range with it), classified by the one authoritative
      classifier in :mod:`gideon.security.net.guard`,
    * more than :data:`SCAN_MAX_TARGETS` hosts in total.

    Order is the caller's, duplicates collapse, and the result is what the scan
    dials — there is no second expansion later.
    """
    from gideon.security.net.guard import classify_host

    rows = list(targets)
    if not rows:
        raise ScanRefused("no addresses to scan — a scan is always explicit")

    out: list[str] = []
    seen: set[str] = set()
    for raw in rows:
        if not isinstance(raw, str) or not raw.strip():
            raise ScanRefused("every target must be a non-empty address or CIDR string")
        spec = raw.strip()
        try:
            network = ipaddress.ip_network(spec, strict=False)
        except ValueError as exc:
            raise ScanRefused(
                f"{spec!r} is not an IP address or CIDR ({exc}); a scan never "
                "resolves names"
            ) from exc
        floor = SCAN_MIN_IPV4_PREFIX if network.version == 4 else SCAN_MIN_IPV6_PREFIX
        if network.prefixlen < floor:
            raise ScanRefused(f"{spec!r} is wider than /{floor}; name a smaller range")
        for address in network:
            host = str(address)
            verdict = classify_host(host)
            if verdict.category not in _SCANNABLE_CATEGORIES:
                raise ScanRefused(
                    f"{host} is a {verdict.category} address; only loopback and "
                    "private-network addresses may be scanned"
                )
            if host in seen:
                continue
            seen.add(host)
            out.append(host)
            if len(out) > SCAN_MAX_TARGETS:
                raise ScanRefused(
                    f"more than {SCAN_MAX_TARGETS} addresses named; narrow the range"
                )
    return out


def clamp_budget(budget_s: Any) -> float:
    """The caller's time budget, clamped into ``(0, SCAN_MAX_BUDGET_S]``."""
    try:
        value = float(budget_s)
    except (TypeError, ValueError):
        return SCAN_DEFAULT_BUDGET_S
    if value != value or value <= 0:  # NaN or non-positive
        return SCAN_DEFAULT_BUDGET_S
    return min(value, SCAN_MAX_BUDGET_S)


async def scan_private_hosts(
    targets: Iterable[Any],
    *,
    budget_s: float = SCAN_DEFAULT_BUDGET_S,
    concurrency: int = SCAN_MAX_CONCURRENCY,
) -> ScanReport:
    """Probe the caller's named private addresses once, under a hard time budget.

    Never called by detection, by a poll, or by a timer — the only way in is a
    caller handing over the addresses they want probed. The budget is a wall clock,
    not a per-probe timeout: probes still in flight when it expires are cancelled and
    counted, and probes not yet started are dropped, so a wide range answers with a
    partial report rather than running long.
    """
    hosts = expand_targets(targets)
    budget = clamp_budget(budget_s)
    limit = max(1, min(int(concurrency or 1), SCAN_MAX_CONCURRENCY))
    per_probe = min(SCAN_PROBE_TIMEOUT_S, budget)

    loop = asyncio.get_running_loop()
    deadline = loop.time() + budget
    gate = asyncio.Semaphore(limit)
    results: list[ProbeResult] = []

    async def one(host: str, port: int) -> None:
        async with gate:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return
            results.append(
                await probe_ollama(
                    endpoint_for(host, port), timeout_s=min(per_probe, remaining)
                )
            )

    began = time.monotonic()
    tasks = [
        asyncio.ensure_future(one(host, port))
        for host in hosts
        for port in OLLAMA_PORTS
    ]
    _done, pending = await asyncio.wait(tasks, timeout=budget)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    elapsed = time.monotonic() - began

    offers = tuple(r for r in results if r.ok)
    exhausted = len(results) < len(tasks)
    notes: list[str] = []
    if exhausted:
        notes.append(
            f"the {budget:g}s budget expired with {len(tasks) - len(results)} "
            "address(es) unprobed — scan a narrower range to finish"
        )
    return ScanReport(
        targets=len(hosts),
        probed=len(results),
        offers=offers,
        unreachable=len(results) - len(offers),
        budget_s=budget,
        elapsed_s=elapsed,
        exhausted_budget=exhausted,
        notes=tuple(notes),
    )


def binding_entry(endpoint: str, *, name: str, model: str = "") -> dict[str, Any]:
    """The ``config.json`` provider row for a key-less local-model binding.

    "No key required" is spelled the way the existing provider config already spells
    it: the row simply carries no ``credential`` and no key field in ``options``.
    There is no empty-string key, no placeholder, and no credentials-store entry —
    nothing to leak, because nothing was ever asked for. The SDK's own
    ``_anon_credential`` covers the client-constructor side for an unauth'd endpoint,
    so the config side has nothing left to represent.
    """
    entry: dict[str, Any] = {
        "name": name,
        "type": PROVIDER_TYPE,
        "model": model,
        "options": {"endpoint": endpoint},
    }
    return entry


__all__ = [
    "DEFAULT_LOCAL_ENDPOINTS",
    "DETECT_TIMEOUT_S",
    "OLLAMA_PORTS",
    "PROVIDER_TYPE",
    "SCAN_DEFAULT_BUDGET_S",
    "SCAN_MAX_BUDGET_S",
    "SCAN_MAX_CONCURRENCY",
    "SCAN_MAX_TARGETS",
    "TAGS_PATH",
    "ProbeResult",
    "ScanRefused",
    "ScanReport",
    "binding_entry",
    "clamp_budget",
    "detect_local_ollama",
    "endpoint_for",
    "expand_targets",
    "probe_ollama",
    "scan_private_hosts",
]
