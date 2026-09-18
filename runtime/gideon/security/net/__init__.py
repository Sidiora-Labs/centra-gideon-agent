"""Network egress/ingress security layer — the single outbound chokepoint.

One guard, one classifier, policy-per-surface. All outbound HTTP that could reach an
attacker-influenced host goes through :func:`gideon.security.net.client.fetch`, which
evaluates the URL against an :class:`~gideon.security.net.policy.EgressPolicy`, pins the
validated IP (closing the DNS-rebind TOCTOU window), re-checks every redirect hop, and
caps bytes/timeout. :func:`gideon.security.net.guard.classify_host` is the authoritative
"is this IP safe to reach" answer consulted by both outbound and inbound checks.
"""

from gideon.security.net.client import EgressBlocked, FetchResponse, fetch
from gideon.security.net.guard import GuardDecision, IpVerdict, classify_host, evaluate
from gideon.security.net.policy import (
    CONNECTOR,
    LOCAL_MODEL_PROBE,
    LOOPBACK_INTERNAL,
    STRICT,
    SYNC,
    WEBHOOK,
    EgressPolicy,
    LocalModelProbeRefused,
    SyncEndpointRefused,
    egress_policy_for,
    get_policy,
    local_model_probe_policy,
    sync_egress_policy,
)

__all__ = [
    "EgressBlocked",
    "FetchResponse",
    "fetch",
    "GuardDecision",
    "IpVerdict",
    "classify_host",
    "evaluate",
    "EgressPolicy",
    "STRICT",
    "CONNECTOR",
    "WEBHOOK",
    "LOOPBACK_INTERNAL",
    "LOCAL_MODEL_PROBE",
    "SYNC",
    "get_policy",
    "egress_policy_for",
    "local_model_probe_policy",
    "sync_egress_policy",
    "LocalModelProbeRefused",
    "SyncEndpointRefused",
]
