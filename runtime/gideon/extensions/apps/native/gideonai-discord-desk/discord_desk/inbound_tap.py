"""The in-app inbound tap — how this bundle's providers share ONE inbound stream.

CHANNEL-EXPANSION CE-10. This bundle registers two inbound-facing providers built by
two separate manifest factories (``transport:create_provider`` and
``trigger_source:create_provider``), so the two instances never see each other. They
need to, because the alternative is a **second** Discord Gateway connection: Discord
counts identifies per bot token and would rate-limit us, the two sockets would resume
from different sequence numbers, and core would be told about every message twice.
The same reasoning made ``inbox_source`` in the slack bundle an adapter over the
existing client rather than a second client.

So the transport publishes here and the trigger source subscribes here. A module in
this package is the only thing the two instances demonstrably share: the app loader
pins the app dir on ``sys.path`` (see the header of ``transport.py``), and after that
``discord_desk.inbound_tap`` is one object in ``sys.modules`` for the life of the
process, exactly like ``discord_desk.settings``' cache.

**What may be published here, and what may not.** Only a message that has already
passed core's guarded door (``deliver_channel_inbound`` returned ``allowed``).
Publishing earlier would let anyone who shares a guild with the bot arm the owner's
automations by posting, which is a strictly worse failure than the one the trust gate
exists to prevent: a denied stranger gets no session AND must get no trigger fire.
The transport calls :func:`publish` on the allowed path only, and a test drives the
denied path to prove nothing is published.

**No event glue lives here.** This module knows nothing about events, the bus, or
triggers — it moves a :class:`~gideon.sdk.channel.ChannelMessage` between two objects
in one process. Turning one into an event is the trigger source's job, and it does it
by calling the ``emit`` callable core handed it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

#: An observer takes the normalized message plus the one structural fact the transport
#: already computed (``is_dm``). Kept to that, deliberately: an observer signature that
#: grew vendor payload fields would pull Discord's Gateway schema into every subscriber.
Observer = Callable[..., None]

_observers: list[Observer] = []


def subscribe(observer: Observer) -> None:
    """Register *observer* for allowed inbound messages. Idempotent.

    Idempotent because ``start`` is called on every app enable and a source that was
    enabled, disabled and re-enabled must not receive each message twice — a doubled event
    doubles the fires the user sees, and the debounce would hide it just often enough to
    make it hard to find.
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
    distinguishable facts; a source that silently failed to attach looks exactly like a
    quiet channel otherwise.
    """
    return len(_observers)


def publish(message: Any, *, is_dm: bool) -> int:
    """Hand one ALLOWED inbound message to every observer. Returns how many ran.

    Never raises. The caller is the transport's inbound path: an observer that throws must
    not cost the user the conversation turn that message was really for, so a fault is
    logged and the next observer still runs. Returning the count rather than nothing keeps
    a caller able to log honestly instead of assuming delivery.
    """
    delivered = 0
    for observer in list(_observers):
        try:
            observer(message, is_dm=is_dm)
            delivered += 1
        except Exception:  # noqa: BLE001 - see the docstring
            logger.debug("discord: inbound observer failed", exc_info=True)
    return delivered
