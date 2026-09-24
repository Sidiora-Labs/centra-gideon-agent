"""SDK: the guarded network-egress chokepoint + the high-level web fetch/extract.

Stable re-export of ``gideon.security.net`` (the egress policy layer: ``fetch`` + the
``CONNECTOR`` policy — an app's outbound traffic is subject to the same guard core
uses) and ``gideon.integrations.web.fetch`` (the SSRF-guarded page fetch + content
extraction pipeline: ``web_fetch``/``web_extract`` + ``record_seen_urls`` provenance).
Generic, provider-agnostic infrastructure a web-capable app/tool builds on.
"""

from gideon.integrations.inbound.a2a import outbound_policy as a2a_outbound_policy  # noqa: F401  # fmt: skip
from gideon.integrations.web.fetch import record_seen_urls, web_extract, web_fetch
from gideon.security.net import (
    CONNECTOR,
    SYNC,
    WEBHOOK,
    EgressBlocked,
    EgressPolicy,
    GuardDecision,
    SyncEndpointRefused,
    egress_policy_for,
    evaluate,
    fetch,
    sync_egress_policy,
)

__all__ = [
    "fetch",
    "CONNECTOR",
    "EgressPolicy",
    "WEBHOOK",
    "EgressBlocked",
    "egress_policy_for",
    "evaluate",
    "GuardDecision",
    "SYNC",
    "sync_egress_policy",
    "SyncEndpointRefused",
    "web_fetch",
    "web_extract",
    "record_seen_urls",
    "a2a_outbound_policy",
]
