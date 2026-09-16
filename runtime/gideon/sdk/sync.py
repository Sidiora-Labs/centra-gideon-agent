"""SDK: the sync-transport contract — ``SyncTransportProvider`` + its data types.

A sync-transport app imports these from ``gideon.sdk.sync`` (never from the core
module directly) to implement a back-end that moves durability shards between machines.

Also re-exported here (DAS-8) is the **egress derivation** an HTTP transport must use and
the small part of the **encryption codec** a transport legitimately needs:

* :func:`sync_egress_policy` — the ONE way an object-store transport (``s3-sync``) gets its
  network policy: the ``SYNC`` profile, host-pinned to the configured endpoint, with the
  operator's ``security.egress`` posture layered in. A transport calls this and hands the
  result to ``sdk.net.fetch``; it never builds an ``EgressPolicy`` by hand and never opens
  its own HTTP client.
* :data:`ROUTING_KEYS` / :func:`is_routing_key` / :data:`SALT_KEY` — which object keys stay
  plaintext, so a transport that special-cases the registry or the salt (rename-lock CAS,
  a bucket-level ACL) asks core rather than hard-coding a string.
* :data:`PASSPHRASE_CREDENTIAL` — the credential NAME the sync passphrase lives under, so a
  transport's setup UI can point a user at the right place without ever holding the value.

Deliberately NOT exported: the key-derivation and encrypt/decrypt primitives. Encryption is
applied by the sync cycle at the transport boundary, *above* every transport — a transport
that could encrypt for itself is a transport that could forget to.
"""

from gideon.integrations.sync_transports.base import (
    ConnectionResult,
    PushResult,
    RemoteRef,
    SyncObject,
    SyncTransportProvider,
)
from gideon.operations.durability.crypto import (
    PASSPHRASE_CREDENTIAL,
    ROUTING_KEYS,
    SALT_KEY,
    is_routing_key,
)
from gideon.security.net.policy import SYNC, SyncEndpointRefused, sync_egress_policy

__all__ = [
    "SyncTransportProvider",
    "SyncObject",
    "RemoteRef",
    "PushResult",
    "ConnectionResult",
    "SYNC",
    "sync_egress_policy",
    "SyncEndpointRefused",
    "SALT_KEY",
    "ROUTING_KEYS",
    "is_routing_key",
    "PASSPHRASE_CREDENTIAL",
]
