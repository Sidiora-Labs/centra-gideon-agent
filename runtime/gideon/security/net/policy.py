"""Egress policy plane — declarative, per-surface network posture.

A caller picks a named :class:`EgressPolicy` profile (STRICT for agent fetch,
CONNECTOR for knowledge scrape, WEBHOOK for user-configured POSTs, LOOPBACK_INTERNAL
for gateway↔mcp self-calls) instead of re-implementing checks. The guard
(``net/guard.py``) reads the policy to decide; the client (``net/client.py``)
enforces the byte/timeout/redirect caps.
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EgressPolicy:
    """A network egress posture for one surface.

    ``allow_private`` flips the whole stance: STRICT/public profiles keep it False
    (public hosts only — block loopback/RFC-1918/link-local/etc.); LOOPBACK_INTERNAL
    sets it True AND ``loopback_only`` so it *expects* 127.0.0.1 and denies public.

    ``allow_hosts`` / ``deny_hosts`` are operator opt-in overrides matched by the
    Anthropic rule (bare domain covers its subdomains): a deny always wins; an allow
    permits an otherwise-private host (the homelab LAN-webhook case).
    """

    name: str = "strict"
    allow_schemes: tuple[str, ...] = ("http", "https")
    allow_private: bool = False
    loopback_only: bool = False
    allow_hosts: tuple[str, ...] = ()
    deny_hosts: tuple[str, ...] = ()
    allow_only: bool = False
    max_redirects: int = 5
    max_bytes: int = 5_000_000
    timeout_s: float = 30.0
    pin_resolved_ip: bool = True
    on_violation: str = "deny"

    def with_overrides(self, **kw) -> "EgressPolicy":
        """A copy with fields replaced (operator config layering)."""
        from dataclasses import replace

        return replace(self, **kw)


STRICT = EgressPolicy(name="strict")

CONNECTOR = EgressPolicy(name="connector", max_bytes=10_000_000, timeout_s=20.0)

SOURCE = EgressPolicy(name="source", max_bytes=10_000_000, timeout_s=20.0)

WEBHOOK = EgressPolicy(name="webhook", timeout_s=30.0)

LOOPBACK_INTERNAL = EgressPolicy(
    name="loopback_internal",
    allow_private=True,
    loopback_only=True,
    pin_resolved_ip=False,
    max_bytes=50_000_000,
    timeout_s=60.0,
)

REGISTRY_HOSTS: tuple[str, ...] = (
    "pypi.org",
    "files.pythonhosted.org",
    "registry.npmjs.org",
    "npmjs.com",
    "crates.io",
    "static.crates.io",
    "index.crates.io",
    "docker.io",
    "registry-1.docker.io",
    "ghcr.io",
    "github.com",
    "raw.githubusercontent.com",
    "codeload.github.com",
    "objects.githubusercontent.com",
    "repo1.maven.org",
    "repo.maven.apache.org",
    "rubygems.org",
    "proxy.golang.org",
    "sum.golang.org",
    "packagist.org",
    "nuget.org",
    "api.nuget.org",
)
REGISTRY = EgressPolicy(
    name="registry",
    allow_hosts=REGISTRY_HOSTS,
    allow_only=True,
    max_bytes=100_000_000,
    timeout_s=60.0,
)

LISTED = EgressPolicy(name="listed", allow_only=True)

METADATA_SERVICE_HOSTS: tuple[str, ...] = (
    "169.254.169.254",
    "metadata.google.internal",
    "metadata.goog",
    "100.100.100.200",
)
SYNC = EgressPolicy(
    name="sync",
    allow_only=True,
    allow_hosts=(),
    deny_hosts=METADATA_SERVICE_HOSTS,
    max_bytes=200_000_000,
    timeout_s=120.0,
)

BROWSE = EgressPolicy(
    name="browse",
    pin_resolved_ip=False,
    max_redirects=10,
    max_bytes=50_000_000,
    timeout_s=60.0,
)

FETCH_ACTION = EgressPolicy(
    name="fetch_action",
    allow_only=True,
    allow_hosts=(),
    deny_hosts=METADATA_SERVICE_HOSTS,
    max_bytes=2_000_000,
    timeout_s=20.0,
)

LOCAL_MODEL_PROBE = EgressPolicy(
    name="local_model_probe",
    allow_private=True,
    allow_only=True,
    allow_hosts=(),
    deny_hosts=METADATA_SERVICE_HOSTS,
    max_redirects=0,
    max_bytes=1_000_000,
    timeout_s=2.0,
)

_PROFILES: dict[str, EgressPolicy] = {
    p.name: p
    for p in (
        STRICT,
        CONNECTOR,
        SOURCE,
        WEBHOOK,
        LOOPBACK_INTERNAL,
        REGISTRY,
        LISTED,
        SYNC,
        BROWSE,
        FETCH_ACTION,
        LOCAL_MODEL_PROBE,
    )
}


def egress_policy_for_tier(tier: str) -> "EgressPolicy | None":
    """Resolve a safety-profile egress TIER to a base :class:`EgressPolicy` (§4.2).

    * ``off``      → ``None`` (the caller denies all egress — no policy applies).
    * ``listed``   → LISTED: exclusively the operator's ``security.egress.allow_hosts``
                     (unioned in by ``egress_policy_for`` at the call site).
    * ``registry`` → the curated REGISTRY preset, exclusively (dev registries only).
    * ``all``      → STRICT (public hosts, the normal agent posture).

    An unknown tier falls back to STRICT (the safe public-only default); the ceiling
    rejects an off-scale tier at boot, so an unknown value cannot arrive from there."""
    if tier == "off":
        return None
    if tier == "registry":
        return REGISTRY
    if tier == "listed":
        return LISTED
    return STRICT


def egress_policy_for_profile(base: EgressPolicy, tier: str) -> "EgressPolicy | None":
    """Narrow a surface's ``base`` policy by a run's egress TIER — tightest wins.

    The surface keeps its own caps and stance (a knowledge scrape stays CONNECTOR-shaped);
    the tier can only take reach away. ``None`` means the run may not egress at all and the
    caller must refuse — never fall through to the base.

    Composition, per field:

    * ``off`` → ``None``.
    * a tier with an exclusive host set (``listed``/``registry``) → the base becomes
      exclusive too, with the tier's hosts unioned onto the base's own (a surface that
      already allow-listed a host keeps it; the tier adds its preset).
    * ``all`` → the base is already at least this narrow, so it is returned unchanged.
    * caps (``max_bytes``/``timeout_s``) take the tighter of the two, so a tier can never
      raise a surface's ceiling — REGISTRY's 100 MB does not widen a 5 MB fetch.
    """
    tier_policy = egress_policy_for_tier(tier)
    if tier_policy is None:
        return None
    if not tier_policy.allow_only:
        return base
    return base.with_overrides(
        allow_only=True,
        allow_hosts=tuple(dict.fromkeys([*base.allow_hosts, *tier_policy.allow_hosts])),
        max_bytes=min(base.max_bytes, tier_policy.max_bytes),
        timeout_s=min(base.timeout_s, tier_policy.timeout_s),
    )


def get_policy(name: str) -> EgressPolicy:
    """Look up a named profile (defaults to STRICT for an unknown name)."""
    return _PROFILES.get(name, STRICT)


_LAST_DENY_HOSTS: tuple[str, ...] = ()


def egress_policy_for(base: EgressPolicy) -> EgressPolicy:
    """Layer the operator's ``security.egress`` config onto a base profile.

    A self-hoster can allow-list LAN hosts (homelab webhook), deny specific hosts, or
    opt the whole instance into private-network egress. The guard's built-in public-only
    default is unchanged when no config is set. Operator ``allow_hosts``/``deny_hosts``
    are UNIONed with the profile's own; ``allow_private`` ORs in. Config read is lazy +
    best-effort so ``net`` stays importable without a loaded config (tests, early boot).

    🔴 The best-effort catch is deliberate and stays. What it must NOT do is fail open in one
    direction. Dropping the operator's ``allow_hosts``/``allow_private`` on an error is safe —
    the result is narrower than they asked for. Dropping their ``deny_hosts`` is not: a host
    they explicitly denied becomes reachable, so a transient config-read error silently
    UN-DENIES it. The last successfully-observed deny list is therefore remembered and reused
    on a later failure, and the failure is logged at WARNING rather than swallowed — a control
    that stops applying should be visible, which is exactly what a bare ``return base`` was not.
    Deliberately NOT a hard fail: refusing all egress on a transient read would take the
    machine offline over a control that only ever ADDS denials."""
    global _LAST_DENY_HOSTS
    try:
        from gideon.core.config.loader import AppConfig

        eg = AppConfig.load().security.egress
    except Exception as exc:
        remembered = _LAST_DENY_HOSTS
        if not remembered:
            logger.warning("egress config unreadable (%s); using the base profile", exc)
            return base
        logger.warning(
            "egress config unreadable (%s); keeping the last known deny list (%d host(s)) "
            "so an explicitly denied host does not become reachable",
            exc,
            len(remembered),
        )
        return base.with_overrides(
            deny_hosts=tuple(dict.fromkeys([*base.deny_hosts, *remembered])),
        )
    _LAST_DENY_HOSTS = tuple(eg.deny_hosts or ())
    return base.with_overrides(
        allow_hosts=tuple(dict.fromkeys([*base.allow_hosts, *eg.allow_hosts])),
        deny_hosts=tuple(dict.fromkeys([*base.deny_hosts, *eg.deny_hosts])),
        allow_private=base.allow_private or bool(eg.allow_private),
    )


def fetch_action_egress_policy() -> EgressPolicy:
    """The posture ONE ``net-fetch`` action node runs under (AUTOMATION-SUBSTRATE / WF2KNO-9).

    Derived, never hand-written — :data:`FETCH_ACTION` supplies the exclusive stance, the tightened
    caps and the metadata denies, and :func:`egress_policy_for` layers the operator's
    ``security.egress`` config on top. That layering is what makes the reachable set the operator's
    own ``allow_hosts`` (see :data:`LISTED`'s contract) and what preserves their ``deny_hosts``.

    Three properties this composition has, in the order they are enforced by
    :func:`gideon.security.net.guard.evaluate`, and all three are the point:

    1. A ``deny_hosts`` match refuses BEFORE the allow-list and before DNS resolution, so
       :data:`METADATA_SERVICE_HOSTS` survives even an operator who allow-lists the metadata
       service by hand.
    2. ``allow_only`` refuses an off-list host BEFORE resolution — a DNS query is itself an egress
       signal, so an unlisted host is never even looked up.
    3. ``allow_private`` can be ORed in by operator config, and it still cannot widen reach past the
       allow-list: the exclusive check runs first and does not consult it. So "I trust my LAN"
       remains a statement about the listed hosts, not a bypass.

    A single named seam rather than an inline ``egress_policy_for(FETCH_ACTION)`` at the call site,
    for the reason ``sync_egress_policy`` is one: the policy decision is the security-relevant part
    of this provider, so it belongs somewhere greppable, reviewable and testable on its own — and a
    call site that composed its own could compose a permissive one.
    """
    return egress_policy_for(FETCH_ACTION)


class SyncEndpointRefused(ValueError):
    """A sync endpoint that cannot be pinned — so no policy is derived and nothing egresses.

    Raised instead of returning a wide-open policy: "I could not work out which host you
    meant" must never resolve to "reach any host". The caller surfaces it as a setup error.
    """


def sync_egress_policy(endpoint: str) -> EgressPolicy:
    """The SYNC policy pinned to one configured object-store ``endpoint`` (§4.3, Plug-in Map).

    Derived, never hand-written: :data:`SYNC` supplies the raised caps and the exclusive
    stance, :func:`egress_policy_for` layers the operator's ``security.egress`` posture
    (``allow_private`` for a LAN MinIO, ``deny_hosts`` for a host the operator has banned),
    and only then is the endpoint's host pinned as the sole reachable host.

    The pin is applied AFTER the operator layering on purpose. `egress_policy_for` UNIONs the
    operator's ``allow_hosts`` into whatever base it is given, which for an exclusive policy
    would widen the transport's reach to every host the operator listed for other surfaces —
    hosts that have no business being an S3 endpoint. A sync transport speaks to one endpoint,
    so ``allow_hosts`` ends as exactly that endpoint. ``deny_hosts`` is UNIONed rather than
    replaced, so both :data:`METADATA_SERVICE_HOSTS` and the operator's own denies survive — and a
    deny outranks the pin, including when the operator bans their own configured endpoint.

    Because the pinned host is its own allow-list entry, a private/loopback endpoint is
    reachable without further opt-in (see :data:`SYNC`'s note — that is the intended posture
    for user-owned storage, and the metadata service is denied separately).

    Raises :class:`SyncEndpointRefused` when no host can be parsed out of ``endpoint``, or when
    the endpoint names a denied host — refusing at derivation is more legible than handing back
    a policy whose only permitted host is one the guard will reject on every request.
    """
    from urllib.parse import urlparse

    raw = (endpoint or "").strip()
    if not raw:
        raise SyncEndpointRefused(
            "no sync endpoint configured — nothing to pin egress to"
        )
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise SyncEndpointRefused(f"cannot parse a host out of sync endpoint {raw!r}")
    if parsed.scheme not in SYNC.allow_schemes:
        raise SyncEndpointRefused(
            f"sync endpoint scheme {parsed.scheme!r} is not one of {SYNC.allow_schemes}"
        )
    layered = egress_policy_for(SYNC)
    denies = tuple(dict.fromkeys([*METADATA_SERVICE_HOSTS, *layered.deny_hosts]))
    from gideon.security.net.guard import host_matches

    if host_matches(host, denies):
        raise SyncEndpointRefused(
            f"sync endpoint host {host!r} is on the egress deny list and cannot be a sync target"
        )
    return layered.with_overrides(
        allow_only=True, allow_hosts=(host,), deny_hosts=denies
    )


class LocalModelProbeRefused(ValueError):
    """A local-model endpoint that cannot be pinned to one private address.

    Raised instead of returning a policy, for the reason
    :class:`SyncEndpointRefused` is: "I could not work out which private host you
    meant" must never resolve to "reach any host". The caller surfaces it as a
    refusal and nothing egresses.
    """


_LOCAL_PROBE_NAMED_HOSTS: frozenset[str] = frozenset({"localhost"})


def local_model_probe_policy(endpoint: str) -> EgressPolicy:
    """The posture ONE zero-key local-model probe runs under, pinned to its host.

    Derived, never hand-written — :data:`LOCAL_MODEL_PROBE` supplies the stance and the
    tightened caps, :func:`egress_policy_for` layers the operator's ``security.egress``
    config on top, and only then is the endpoint's host pinned as the sole reachable
    host (AFTER the layering, for the reason :func:`sync_egress_policy` documents: the
    operator's ``allow_hosts`` are for other surfaces and have no business widening a
    LAN probe's reach).

    Three properties make this safe to point at a user-named address:

    1. The host must be a loopback/RFC-1918-class address (or the literal name
       ``localhost``). A public IP, a link-local/metadata address, and any other
       hostname are all refused HERE, before a policy exists — so this seam can never
       become a general-purpose internet prober.
    2. ``allow_only`` with exactly one allow-list entry means one probe reaches one
       host. ``allow_private`` is what lets that host be an RFC-1918 one at all; it is
       the explicit, feature-scoped opt-in this surface is allowed to make, and it
       still cannot widen reach past the single pinned host.
    3. ``deny_hosts`` keeps :data:`METADATA_SERVICE_HOSTS` and the operator's own
       denies, and a deny outranks the pin — so an operator who has banned a host has
       banned it here too.

    ``max_redirects=0`` on the base is deliberate: a service that answers a model-catalog
    probe with a redirect is not the local service we are identifying, and following it
    would be a second host.
    """
    from urllib.parse import urlparse

    raw = (endpoint or "").strip()
    if not raw:
        raise LocalModelProbeRefused("no endpoint given — nothing to probe")
    parsed = urlparse(raw if "://" in raw else f"http://{raw}")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise LocalModelProbeRefused(f"cannot parse a host out of endpoint {raw!r}")
    if parsed.scheme not in LOCAL_MODEL_PROBE.allow_schemes:
        raise LocalModelProbeRefused(
            f"endpoint scheme {parsed.scheme!r} is not one of "
            f"{LOCAL_MODEL_PROBE.allow_schemes}"
        )
    if host not in _LOCAL_PROBE_NAMED_HOSTS:
        from gideon.security.net.guard import classify_host

        verdict = classify_host(host)
        if verdict.category == "invalid":
            raise LocalModelProbeRefused(
                f"{host!r} is not a literal address — a local-model probe only ever "
                "reaches an address the caller named, never a resolved name"
            )
        if verdict.category not in ("loopback", "private"):
            raise LocalModelProbeRefused(
                f"{host!r} is a {verdict.category} address; a local-model probe only "
                "reaches loopback and private-network addresses"
            )
    layered = egress_policy_for(LOCAL_MODEL_PROBE)
    denies = tuple(dict.fromkeys([*METADATA_SERVICE_HOSTS, *layered.deny_hosts]))
    from gideon.security.net.guard import host_matches

    if host_matches(host, denies):
        raise LocalModelProbeRefused(
            f"host {host!r} is on the egress deny list and cannot be probed"
        )
    return layered.with_overrides(
        allow_only=True,
        allow_hosts=(host,),
        deny_hosts=denies,
        max_redirects=LOCAL_MODEL_PROBE.max_redirects,
        max_bytes=LOCAL_MODEL_PROBE.max_bytes,
    )
