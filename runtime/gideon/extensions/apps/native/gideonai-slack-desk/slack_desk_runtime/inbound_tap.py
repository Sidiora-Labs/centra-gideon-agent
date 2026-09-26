"""The in-app inbound tap — how this bundle's providers share ONE inbound stream.

CHANNEL-EXPANSION CE-10. This bundle now registers three providers built by three separate
manifest factories (``transport:create_provider``, ``inbox_source:create_provider``,
``trigger_source:create_provider``), so no two instances see each other. The trigger source
needs the inbound stream the transport's Socket-Mode receiver already owns, and the
alternative is a **second** Socket-Mode connection: Slack counts connections per app-level
token, the two sockets would each receive every event (so core would be told twice), and a
second receiver would need its own dedup cache to stay consistent with the first. The same
reasoning made ``inbox_source`` an adapter over the existing ``RealSlackDeskClient`` rather than
a second client.

So ``events.py`` publishes here and the trigger source subscribes here. A module in this
package is the only thing the instances demonstrably share: the app loader pins the app dir
on ``sys.path`` (see the header of ``transport.py``), and after that
``slack_desk_runtime.inbound_tap`` is one object in ``sys.modules`` for the life of the process.

**What may be published here, and what may not.** Only a message this app has already
admitted: past the allowlist / open-channel / tracked-channel gate, past the channel
activation mode, and past the dedup cache. Publishing earlier would let anyone in a shared
workspace arm the owner's automations by posting, which is strictly worse than the session
the gate already refuses them.

Note the difference from the telegram/discord/email bundles, and why it is not a weakening:
those three ADMIT on ``verdict.allowed`` from core's guarded door
(``deliver_channel_inbound``). Slack still ADMITS through its own
``slack_desk_runtime/allowlist.py`` allow/deny UX — ``grep -rn guard_inbound gideonai-slack-desk/`` is
still empty — so the gate this tap sits behind is the app's own, at the ONE point where an
admitted message is handed to ``handle_message``. That admission gate is unchanged. What DID
land (CHANNEL-EXPANSION T1.4, the CE-6 ``[fencing]`` clause) is downstream of here:
``handle_message`` now fences a non-owner's text before it becomes the agent's prompt
(``transport.fence_untrusted_inbound``), so ``tests/test_conformance.py`` passes the kit with
no xfail. If Slack's admission later moves onto the door too, this call site moves behind
``verdict.allowed`` and nothing else here changes.

**No event glue lives here.** This module knows nothing about events, the bus, or triggers —
it moves a :class:`~gideon.sdk.channel.ChannelMessage` between two objects in one
process. Turning one into an event is the trigger source's job, and it does it by calling the
``emit`` callable core handed it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

#: An observer takes the normalized message plus the one structural fact the caller already
#: computed (``is_dm``). Kept to that, deliberately: an observer signature that grew Slack
#: event fields would pull the Events API schema into every subscriber.
Observer = Callable[..., None]

_observers: list[Observer] = []


def subscribe(observer: Observer) -> None:
    """Register *observer* for admitted inbound messages. Idempotent.

    Idempotent because ``start`` is called on every app enable and a source that was enabled,
    disabled and re-enabled must not receive each message twice — a doubled event doubles the
    fires the user sees, and the debounce would hide it just often enough to make it hard to
    find.
    """
    if observer not in _observers:
        _observers.append(observer)


def unsubscribe(observer: Observer) -> None:
    """Remove *observer*. Safe when it was never subscribed (``stop`` is idempotent)."""
    try:
        _observers.remove(observer)
    except ValueError:
        pass


def observer_count() -> int:
    """How many observers are attached — the doctor/test surface.

    Exists so "nothing subscribed" and "subscribed but nothing arrived" stay two
    distinguishable facts; a source that silently failed to attach looks exactly like a quiet
    workspace otherwise.
    """
    return len(_observers)


def publish(message: Any, *, is_dm: bool) -> int:
    """Hand one ADMITTED inbound message to every observer. Returns how many ran.

    Never raises. The caller is on the path that is about to start a turn: an observer that
    throws must not cost the user the conversation that message was really for, so a fault is
    logged and the next observer still runs. Returning the count rather than nothing keeps a
    caller able to log honestly instead of assuming delivery.
    """
    delivered = 0
    for observer in list(_observers):
        try:
            observer(message, is_dm=is_dm)
            delivered += 1
        except Exception:  # noqa: BLE001 - see the docstring
            logger.debug("slack: inbound observer failed", exc_info=True)
    return delivered
