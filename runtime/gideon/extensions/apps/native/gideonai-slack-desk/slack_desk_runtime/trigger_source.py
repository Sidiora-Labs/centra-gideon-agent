"""Slack's ``trigger_source`` provider — real inbound traffic as automation events.

CHANNEL-EXPANSION CE-10, closing the vendor-completeness checklist's third row for this
bundle. ``CE-8`` brought this app to ``channel`` + ``inbox``, which was the whole bar
available at the time; ``WF2AUT-8`` then shipped the app-registered trigger-source seam, and
this is the app half of it. The app observes something core knows nothing about (a Slack
workspace) and hands core typed
:class:`~gideon.sdk.trigger_source.SourceEvent`\\ s. Core does everything else: it
namespaces them ``app:gideonai-slack-desk:<event>``, fences the text at origin with full provenance,
and matches them against the user's ``kind: event`` triggers through the existing
``{source, pattern}`` spec.

**Nothing here reaches the bus.** The only outward call is the ``emit`` callable core hands
:meth:`SlackDeskTriggerSource.start`. There is no ``emit_event``, no ``event_bus``, no
``register_source`` and no ``triggers.json`` in this bundle — the registered
``TriggerSourceTypeHandler`` owns every one of those, which is the whole point of adopting the
seam instead of hand-rolling glue.

**A posted message must not be able to become an instruction.** Three separate disciplines,
because they fail separately:

1. **Only admitted traffic is observed.** ``events.py`` publishes to ``inbound_tap`` at the
   ONE point where a message has cleared this app's allowlist / open-channel /
   tracked-channel gate, its channel activation mode and its dedup cache. So a workspace
   member who is not on the allowlist gets no session AND arms no automation. See
   ``inbound_tap``'s docstring for why this app's own gate is the one available here
   (admission still runs on this app's allowlist, not core's ``guard_inbound``) and what
   changes when it moves onto the door. A test drives the denied path.
2. **The event NAME comes from a closed vocabulary in this file** (:data:`EVENTS`), chosen by
   a STRUCTURAL fact (the channel id's ``D`` prefix, which is Slack's DM signal) and never
   from anything in the message. A source that let inbound data pick its own event name would
   let a poster choose which of the owner's triggers to match, which is choosing the action —
   the same hazard the ``companion`` app avoids by synthesising its trigger rows in code so
   no field in its file can name an action. There is no field in a Slack message that names an
   event here either.
3. **Prose goes in ``text`` and nowhere else.** ``text`` is the one field core fences at
   ingestion. ``meta`` is matched, not narrated, and is built literally below from three
   identifiers and a boolean — no display name, no message body. A Slack profile name is
   chosen by whoever owns the account, and prose that skipped the fence is prose that arrives
   as instructions.

This provider persists nothing. There is no file it writes, so there is no durable row in
which an action could be stored and later fired.
"""

from __future__ import annotations

import logging
import sys as _sys
from pathlib import Path as _Path
from typing import Any

# The app loader only keeps this app's dir on sys.path while it execs THIS module, but the
# sibling ``slack_desk_runtime.*`` import below must keep resolving for the life of the process
# (the source lives as long as the app is enabled). Pin it, exactly as transport.py and
# inbox_source.py do for the other two halves.
_APP_DIR = str(_Path(__file__).resolve().parents[1])
if _APP_DIR not in _sys.path:
    _sys.path.insert(0, _APP_DIR)

from gideon.sdk.trigger_source import SourceEvent, TriggerSourceProvider

from slack_desk_runtime.inbound_tap import subscribe, unsubscribe

logger = logging.getLogger(__name__)

#: The registered source name, and therefore the middle segment of every namespaced event
#: (``app:gideonai-slack-desk:<event>``). The APP name rather than the vendor word: the SDK contract
#: says the identifier is "matched to the app name", core derives the namespace from the
#: REGISTERED name, and a trigger bound to it must keep matching across app updates. Note this
#: is deliberately NOT the ``"slack"`` string the transport's ``name`` and the inbox source's
#: ``source_name`` use — those key vendor registries, this keys an event namespace.
APP_NAME = "gideonai-slack-desk"

#: A message in a DM with the bot (Slack channel ids for IMs start with ``D``).
EVENT_DIRECT_MESSAGE = "direct_message"
#: A message in a channel this app is tracking, or an @mention in one.
EVENT_CHANNEL_MESSAGE = "channel_message"

#: The CLOSED event vocabulary — what a user can bind a trigger to, and the only names this
#: source will ever emit. Declared (not discovered) so the trigger-create surface offers names
#: that can actually fire; frozen in code so no inbound field can add one.
EVENTS: tuple[str, ...] = (EVENT_DIRECT_MESSAGE, EVENT_CHANNEL_MESSAGE)

#: The exact ``meta`` keys this source emits. Named as a constant so a test can assert the
#: shape is closed rather than trusting the construction below to stay closed.
META_KEYS: tuple[str, ...] = ("channel_id", "sender", "thread_id", "is_dm")


class SlackDeskTriggerSource(TriggerSourceProvider):
    """Slack's inbound messages, as trigger events.

    A PUSH source: it subscribes to the bundle's inbound tap and calls the ``emit`` callable
    core gave it. Core never polls it — the Socket-Mode connection, the reconnect and the
    seen-cache are the transport's and ``events.py``'s, and they stay there.
    """

    name = APP_NAME
    display_name = "Slack"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        # Config is accepted (the manifest factory contract) and unused: there is nothing
        # about this source a user could configure that is not already the transport's
        # setting. A settings block here would be a second place to turn Slack on.
        self._config = dict(config or {})
        self._emit: Any = None

    @property
    def events(self) -> tuple[str, ...]:
        return EVENTS

    async def start(self, emit: Any) -> None:
        """Attach to the bundle's inbound tap. Returns immediately.

        No receiver of its own: the transport already owns the Socket-Mode connection, and a
        second one would be a second connection against the same app-level token. ``start``
        runs on the app-enable path, so it must not block.
        """
        self._emit = emit
        subscribe(self._on_inbound)
        logger.info("slack trigger source attached (%d event names)", len(EVENTS))

    async def stop(self) -> None:
        """Detach. Idempotent, and it drops ``emit`` LAST.

        Order matters: unsubscribing first means a message already in flight finds no
        observer, while clearing ``emit`` first would leave a subscribed observer that
        silently discards. Either way nothing is emitted after ``stop``, and core parks the
        triggers bound to this source once this returns.
        """
        unsubscribe(self._on_inbound)
        self._emit = None

    def _on_inbound(self, message: Any, *, is_dm: bool) -> None:
        """Turn one ADMITTED inbound message into one event. Never raises.

        Called on the path that is about to start a turn, so a fault here must not cost the
        user the conversation the message was really for. This method additionally refuses to
        emit when it has no ``emit`` callable rather than raising, because "the source is not
        started" is a normal state (a message can arrive between ``stop`` and the app's actual
        shutdown).
        """
        emit = self._emit
        if emit is None:
            return
        text = str(getattr(message, "text", "") or "")
        if not text.strip():
            # Nothing to match on. A file-only or reaction-only event with an empty payload
            # would fire every content-matching trigger's catch-all and tell the user nothing.
            return
        # STRUCTURAL, not textual: the caller derives `is_dm` from the channel id's `D`
        # prefix, so no message field selects the event name. See rule 2 in the module
        # docstring.
        event = EVENT_DIRECT_MESSAGE if is_dm else EVENT_CHANNEL_MESSAGE
        emit(
            SourceEvent(
                event=event,
                # The Slack `ts`, so two fires of the same event are distinguishable in the
                # fire record and the fence's `source_id`.
                key=str(getattr(message, "message_id", "") or ""),
                # The ONE field core fences at ingestion. All prose goes here.
                text=text,
                meta={
                    "channel_id": str(getattr(message, "channel_id", "") or ""),
                    "sender": str(getattr(message, "sender", "") or ""),
                    # The thread root ts — an identifier, and the thing that tells a
                    # thread reply apart from a top-level post.
                    "thread_id": str(getattr(message, "thread_id", "") or ""),
                    # A string, not a bool: core coerces `meta` values for the pattern
                    # matchers to glob, and "true"/"false" is what a glob can express.
                    "is_dm": "true" if is_dm else "false",
                },
            )
        )


def create_provider(config: dict[str, Any] | None = None) -> SlackDeskTriggerSource:
    """Manifest factory — mirrors ``transport.create_provider``'s contract."""
    return SlackDeskTriggerSource(config)
