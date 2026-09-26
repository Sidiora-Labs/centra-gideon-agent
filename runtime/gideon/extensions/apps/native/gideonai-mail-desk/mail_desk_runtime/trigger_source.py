"""The ``trigger_source`` provider for this bundle — real inbound mail as automation events.

CHANNEL-EXPANSION CE-10, closing the vendor-completeness checklist's third row for this
bundle. ``WF2AUT-8`` shipped the app-registered trigger-source seam; this is the app half.
The app observes something core knows nothing about (an IMAP mailbox) and hands core typed
:class:`~gideon.sdk.trigger_source.SourceEvent`\\ s. Core does everything else: it
namespaces them ``app:gideonai-mail-desk:<event>``, fences the text at origin with full
provenance, and matches them against the user's ``kind: event`` triggers through the
existing ``{source, pattern}`` spec.

**Nothing here reaches the bus.** The only outward call is the ``emit`` callable core hands
:meth:`MailDeskTriggerSource.start`. There is no ``emit_event``, no ``event_bus``, no
``register_source`` and no ``triggers.json`` in this bundle — the registered
``TriggerSourceTypeHandler`` owns every one of those, which is the whole point of adopting
the seam instead of hand-rolling glue.

**One declared event, and that is the honest count.** Every mail to the mailbox is a direct
message: there is no room concept, so the transport passes ``is_dm=True`` unconditionally
and there is no second structural fact to name a second event with. Declaring
``mail_received`` plus something aspirational would put a name in the trigger-create
vocabulary that can never fire.

**An arriving mail must not be able to become an instruction.** Three separate disciplines,
because they fail separately:

1. **Only trust-gated mail is observed.** The transport publishes to ``inbound_tap`` on the
   path where core's guarded door already returned ``allowed``. A ``From`` address is
   trivially forged, which is precisely why the allowlist decision is core's; a denied
   sender gets no session AND arms no automation. A test drives the denied path.
2. **The event NAME is a constant in this file** (:data:`EVENTS`), never read from the
   mail. A source that took its event name from a header would let a sender choose which of
   the owner's triggers to match, which is choosing the action — the same hazard the
   ``companion`` app avoids by synthesising its trigger rows in code so no field in its
   file can name an action. There is no header that names an event here either.
3. **Prose goes in ``text`` and nowhere else.** ``text`` is the one field core fences at
   ingestion, and it carries the quote-STRIPPED new prose the door itself received — not
   the raw body, whose quoted history is mostly our own previous words. ``meta`` is
   matched, not narrated, and is built literally below from two identifiers and a boolean.

   **The ``Subject`` is therefore ABSENT from ``meta``, deliberately.** A subject is
   attacker-chosen prose and ``meta`` is the one field core does not fence, so putting it
   there would hand unfenced prose to every reader of the fire record. That does mean a
   subject-matching trigger is not expressible today; the limit is recorded here rather
   than traded for the fence.

This provider persists nothing. There is no file it writes, so there is no durable row in
which an action could be stored and later fired.
"""

from __future__ import annotations

import logging
import sys as _sys
from pathlib import Path as _Path
from typing import Any

# The app loader only keeps this app's dir on sys.path while it execs THIS module, but the
# sibling ``mail_desk_runtime.*`` import below must keep resolving for the life of the
# process (the source lives as long as the app is enabled). Pin it, exactly as
# transport.py does for the channel half.
_APP_DIR = str(_Path(__file__).resolve().parents[1])
if _APP_DIR not in _sys.path:
    _sys.path.insert(0, _APP_DIR)

from gideon.sdk.trigger_source import SourceEvent, TriggerSourceProvider

from mail_desk_runtime.inbound_tap import subscribe, unsubscribe

logger = logging.getLogger(__name__)

#: The registered source name, and therefore the middle segment of every namespaced event
#: (``app:gideonai-mail-desk:<event>``). The APP name rather than the transport word: the
#: SDK contract says the identifier is "matched to the app name", core derives the
#: namespace from the REGISTERED name, and a trigger bound to it must keep matching across
#: app updates.
APP_NAME = "gideonai-mail-desk"

#: A mail arrived from an allowed correspondent.
EVENT_MAIL_RECEIVED = "mail_received"

#: The CLOSED event vocabulary — what a user can bind a trigger to, and the only name this
#: source will ever emit. See the module docstring for why there is exactly one.
EVENTS: tuple[str, ...] = (EVENT_MAIL_RECEIVED,)

#: The exact ``meta`` keys this source emits. Named as a constant so a test can assert the
#: shape is closed rather than trusting the construction below to stay closed. Note the
#: absent ``subject`` — see the module docstring.
META_KEYS: tuple[str, ...] = ("channel_id", "sender", "is_dm")


class MailDeskTriggerSource(TriggerSourceProvider):
    """Inbound mail, as trigger events.

    A PUSH source: it subscribes to the bundle's inbound tap and calls the ``emit``
    callable core gave it. Core never polls it — the IMAP poll cadence, the UID cursor and
    the reconnect backoff are the transport's, and they stay there.
    """

    name = APP_NAME
    display_name = "Mail Desk"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        # Config is accepted (the manifest factory contract) and unused: there is nothing
        # about this source a user could configure that is not already the transport's
        # setting. A settings block here would be a second place to configure the mailbox.
        self._config = dict(config or {})
        self._emit: Any = None

    @property
    def events(self) -> tuple[str, ...]:
        return EVENTS

    async def start(self, emit: Any) -> None:
        """Attach to the bundle's inbound tap. Returns immediately.

        No poll loop of its own: the transport already owns the IMAP connection, and a
        second one would be a second cursor over the same mailbox. ``start`` runs on the
        app-enable path, so it must not block.
        """
        self._emit = emit
        subscribe(self._on_inbound)
        logger.info("mail-desk trigger source attached (%d event names)", len(EVENTS))

    async def stop(self) -> None:
        """Detach. Idempotent, and it drops ``emit`` LAST.

        Order matters: unsubscribing first means a message already in flight finds no
        observer, while clearing ``emit`` first would leave a subscribed observer that
        silently discards. Either way nothing is emitted after ``stop``, and core parks the
        triggers bound to this source once this returns.
        """
        unsubscribe(self._on_inbound)
        self._emit = None

    def _on_inbound(self, message: Any, *, text: str) -> None:
        """Turn one ALLOWED inbound mail into one event. Never raises.

        Called from the transport's inbound path, so a fault here must not cost the user
        the conversation turn the mail was really for. This method additionally refuses to
        emit when it has no ``emit`` callable rather than raising, because "the source is
        not started" is a normal state (mail can arrive between ``stop`` and the app's
        actual shutdown).
        """
        emit = self._emit
        if emit is None:
            return
        body = str(text or "")
        if not body.strip():
            # The transport already drops these, but a caller that stopped doing so must
            # not start firing every content-matching trigger's catch-all with an empty
            # payload.
            return
        emit(
            SourceEvent(
                event=EVENT_MAIL_RECEIVED,
                # The RFC 5322 Message-ID, so two fires of the same event are
                # distinguishable in the fire record and the fence's `source_id`.
                key=str(getattr(message, "message_id", "") or ""),
                # The ONE field core fences at ingestion. All prose goes here.
                text=body,
                meta={
                    # The correspondent's address — how core addresses a reply back —
                    # which is an identifier, not prose. The DISPLAY name is not here.
                    "channel_id": str(getattr(message, "channel_id", "") or ""),
                    "sender": str(getattr(message, "sender", "") or ""),
                    # Always true for mail: there is no room concept. Emitted anyway so a
                    # trigger authored against one channel app reads the same as against
                    # another, rather than needing to know which apps omit the key.
                    "is_dm": "true",
                },
            )
        )


def create_provider(config: dict[str, Any] | None = None) -> MailDeskTriggerSource:
    """Manifest factory — mirrors ``transport.create_provider``'s contract."""
    return MailDeskTriggerSource(config)
