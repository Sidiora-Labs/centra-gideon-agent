"""The DISABLE_LIVE_WRITES kill switch, as a channel transport sees it.

``GIDEON_DISABLE_LIVE_WRITES`` is the platform's process-wide "make no hard-to-reverse
outward write" toggle (AUTONOMY-GUARDRAILS §1.4). Core honors it at every live write IT
owns (``net.fetch`` non-GET to a non-loopback host, local-model ``delete_model``).
Handing a message to an SMTP server is the most irreversible write this repo makes:
once the server accepts it there is no recall, no edit and no delete — so this transport
honors the same switch.

**Why the flag is re-read here rather than imported.** Core's implementation lives in
``gideon.guardrails.writes``, which is NOT re-exported through ``gideon.sdk.*``; an app
may only import core via the SDK facade (lint-enforced), so reaching for it directly is
not an option. The env var itself is a stable, documented platform contract, so this
module reads it and mirrors core's parse EXACTLY — including the fail-safe half. Any
drift here would be a guard that disagrees with the rest of the platform about whether
writes are on, which is worse than no guard at all, so the semantics are pinned by test.

**Why every channel app ships its own copy of this file rather than sharing one.** Each
bundle installs on its own, so there is no sibling module to import and no cross-app
package to put one in. The four copies are held together by test, not by import: each
app's suite cross-checks THIS parse against core's own symbol, so all four agree with
core and therefore with each other. A repo rail additionally asserts every
``provider.type == "channel"`` app carries the honor point, so a fifth channel app
cannot ship without one.

**The semantics being mirrored** (core ``guardrails/writes.live_writes_disabled`` over
``guardrails/flags.guard_flag``):

* var ABSENT → writes ALLOWED. The switch is opt-IN; a normal gateway is untouched.
* var present and an explicit falsy token (``0 false no off disable disabled n f``,
  case- and space-insensitive) → guard OFF.
* var present with ANY other value — including ``""`` and a typo → guard ON. A
  mistyped guard flag must keep the guard on, never silently off.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

#: The platform-wide env var. Same spelling core uses; not this app's to rename.
ENV_DISABLE_LIVE_WRITES = "GIDEON_DISABLE_LIVE_WRITES"

#: The ONLY spellings that disable a guard. Mirrors core ``guardrails.flags``: every
#: other value (unknown token, empty string, typo) parses as ENABLED, because for a
#: guard, ambiguity must fail safe.
_EXPLICIT_FALSE = frozenset({"0", "false", "no", "off", "disable", "disabled", "n", "f"})


def guard_flag(value: object) -> bool:
    """Whether a guard is ENABLED, parsed fail-safe. Mirrors core's ``guard_flag``.

    ``None`` → ``True``; a real ``bool`` → itself; an ``int`` → C-style; a ``str`` →
    ``False`` only for an explicit falsy token; anything else → ``True``.
    """
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() not in _EXPLICIT_FALSE
    return True


def live_writes_disabled() -> bool:
    """True when this process must refuse live, hard-to-reverse outward writes.

    Read live from the environment on every call (never cached at import): the switch is
    an ops/test toggle an operator may flip on a running process, and a cached read would
    make it a boot-time-only setting.
    """
    raw = os.environ.get(ENV_DISABLE_LIVE_WRITES)
    if raw is None:
        # ABSENT means allowed. This is NOT a guard-class default-on flag — it is an
        # explicit opt-in, so the fail-safe parse only applies once the var exists.
        return False
    return guard_flag(raw)


@dataclass(frozen=True)
class SendRefused:
    """A typed, FALSY refusal returned by ``send()`` instead of transmitting.

    Three properties, each load-bearing:

    1. **Typed** — a caller can tell "the platform refused to write" from "the send was
       attempted and failed" with ``isinstance(result, SendRefused)``. A bare ``False``
       conflates the two, and the two demand opposite responses: a failure is worth
       retrying and alerting on, a refusal is the operator's own choice.
    2. **Falsy** — ``bool(SendRefused(...)) is False``, so every existing
       ``if await transport.send(msg):`` call site (core's ``ChannelManager.send`` and
       everything above it) keeps reading "not delivered" with no change.
    3. **Not an exception** — ``send()`` is contractually forbidden from raising for a
       well-formed message (core's channel conformance kit asserts it), so the refusal
       has to ride the return value.
    """

    #: The channel that refused (``"mail-desk"``) — set so a log line or an aggregated
    #: refusal is attributable when several transports are live.
    channel: str
    #: The recipient address the message WOULD have gone to.
    target: str
    #: Why, in operator terms. Names the env var so the reader knows which switch.
    reason: str = f"{ENV_DISABLE_LIVE_WRITES} is set"

    def __bool__(self) -> bool:
        return False

    def __str__(self) -> str:
        return f"live write refused ({self.channel} → {self.target}): {self.reason}"
