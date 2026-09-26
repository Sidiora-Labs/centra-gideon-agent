"""The in-app inbound tap — how this bundle's providers share ONE inbound stream.

CHANNEL-EXPANSION CE-10. This app registers two inbound-facing providers built by two
separate manifest factories (``transport:create_provider`` and
``trigger_source:create_provider``), so the two instances never see each other. They need
to, because the alternative is a **second** IMAP poll: two connections against the same
mailbox, two UID cursors that drift apart, and a provider-side connection limit that many
mail hosts enforce at two or three. The same reasoning made ``inbox_source`` in the slack
bundle an adapter over the existing client rather than a second client.

So the transport publishes here and the trigger source subscribes here. A module in this
package is the only thing the two instances demonstrably share: the app loader pins the app
dir on ``sys.path`` (see the header of ``transport.py``), and after that
``mail_desk_runtime.inbound_tap`` is one object in ``sys.modules`` for the life of the
process, exactly like ``mail_desk_runtime.settings``' cache.

**What may be published here, and what may not.** Only a message that has already passed
core's guarded door (``deliver_channel_inbound`` returned ``allowed``). Publishing earlier
would let anyone who knows the mailbox address arm the owner's automations by sending
mail — and a ``From`` address is trivially forged, which is exactly why the address
allowlist is core's decision and not this module's. A denied sender gets no session AND
must get no trigger fire. The transport calls :func:`publish` on the allowed path only,
and a test drives the denied path to prove nothing is published.

**No event glue lives here.** This module knows nothing about events, the bus, or triggers
— it moves a :class:`~gideon.sdk.channel.ChannelMessage` between two objects in one
process. Turning one into an event is the trigger source's job, and it does it by calling
the ``emit`` callable core handed it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

#: An observer takes the normalized message plus the text the transport actually handed the
#: door — the quote-STRIPPED new prose, not the raw body. A source that re-derived it would
#: emit the quoted history of every reply, which is mostly our own previous words.
Observer = Callable[..., None]

_observers: list[Observer] = []


def subscribe(observer: Observer) -> None:
    """Register *observer* for allowed inbound mail. Idempotent.

    Idempotent because ``start`` runs on every app enable and a source that was enabled,
    disabled and re-enabled must not receive each message twice — a doubled event doubles
    the fires the user sees, and the debounce would hide it just often enough to make it
    hard to find.
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
    quiet mailbox otherwise.
    """
    return len(_observers)


def publish(message: Any, *, text: str) -> int:
    """Hand one ALLOWED inbound message to every observer. Returns how many ran.

    Never raises. The caller is the transport's inbound path: an observer that throws must
    not cost the user the conversation turn that mail was really for, so a fault is logged
    and the next observer still runs. Returning the count rather than nothing keeps a
    caller able to log honestly instead of assuming delivery.
    """
    delivered = 0
    for observer in list(_observers):
        try:
            observer(message, text=text)
            delivered += 1
        except Exception:  # noqa: BLE001 - see the docstring
            logger.debug("mail-desk: inbound observer failed", exc_info=True)
    return delivered
