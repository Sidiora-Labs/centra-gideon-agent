"""Provider-scoped durable chat destinations."""

import asyncio
import logging

from gideon.interfaces.dashboard.chat_utils import _history_key_for

logger = logging.getLogger(__name__)
_NOTICE_TASKS: set[asyncio.Task] = set()
RELOCATION_NOTICE_TIMEOUT_SECS = 10


def linked_chat(state, thread: str, provider: str = ""):
    if not thread or state.sessions is None:
        return None
    key = state.sessions.get_session_for_thread(thread, provider if provider else None)
    if not isinstance(key, str) or not key.startswith("dashboard:"):
        return None
    destination_provider = state.sessions.get_channel_provider(key)
    if not destination_provider or (provider and provider != destination_provider):
        return None
    name = key.removeprefix("dashboard:")
    session = state._sessions.get(name)
    if session is None:
        from gideon.interfaces.dashboard.chat_persistence import (
            _rehydrate_session_from_history,
        )
        from gideon.interfaces.dashboard.chat_utils import resolve_history_key

        history_key = (
            resolve_history_key(state.conversation_log, name)
            if state.conversation_log
            else None
        )
        metadata = (
            state.conversation_log.get_metadata(history_key) if history_key else {}
        )
        if (
            not metadata
            or metadata.get("memory_mode", "persistent") != "persistent"
            or metadata.get("lifecycle") == "archived"
        ):
            return None
        session = _rehydrate_session_from_history(state, name)
    if session is None or session.lifecycle == "archived":
        return None
    current_thread, channel = state.sessions.get_channel_link(key)
    if not channel or current_thread != thread:
        return None
    session._channel_linked = True
    session._channel_thread_ts = thread
    session._channel_id = channel
    session._channel_provider = destination_provider
    return session


def link(state, name: str, thread: str, channel: str, provider: str):
    if state.sessions is None:
        return
    session = state._sessions.get(name)
    if session is None:
        from gideon.interfaces.dashboard.chat_persistence import (
            _rehydrate_session_from_history,
        )

        session = _rehydrate_session_from_history(state, name)
    if session is None or session.lifecycle == "archived":
        return
    key = _history_key_for(name)
    old_thread, old_channel = state.sessions.get_channel_link(key)
    old_provider = state.sessions.get_channel_provider(key)
    previous_key = state.sessions.get_session_for_thread(thread, provider)
    state.sessions.set_channel_link(key, thread, channel, channel_provider=provider)
    if previous_key and previous_key != key and previous_key.startswith("dashboard:"):
        previous = state._sessions.get(previous_key.removeprefix("dashboard:"))
        if previous is not None:
            previous._channel_linked = False
            previous._channel_thread_ts = previous._channel_id = (
                previous._channel_provider
            ) = ""
    if old_thread:
        state._channel_to_session.pop((old_provider, old_thread), None)
        if state._channel_to_session.get(old_thread) == name:
            state._channel_to_session.pop(old_thread, None)
    session._channel_linked = bool(thread and channel)
    session._channel_thread_ts = thread
    session._channel_id = channel
    session._channel_provider = provider if session._channel_linked else ""
    if session._channel_linked:
        state._channel_to_session[(provider, thread)] = name
    state.push_sessions_update()
    previous_destination = (old_thread, old_channel, old_provider)
    if (
        old_thread
        and old_channel
        and old_provider
        and provider
        and previous_destination != (thread, channel, provider)
    ):
        _notify_former_owner_dm(state, previous_destination, provider)


def _notify_former_owner_dm(
    state, previous: tuple[str, str, str], provider: str
) -> None:
    from gideon.integrations.channel_transports import get_transport

    thread, channel, was_on = previous
    old_transport = get_transport(was_on)
    if old_transport is None or not old_transport.connected:
        return
    try:
        if old_transport.capabilities().speaks_as_owner:
            return
    except Exception:
        return
    delivery = state.delivery_for(was_on)
    if delivery is None:
        return
    target = get_transport(provider)
    shown = str(getattr(target, "display_name", "") or provider)
    if was_on == provider:
        message = f"This chat continues in another {shown} conversation now. Messages here no longer reach it."
    else:
        message = (
            f"This chat continues on {shown} now. Messages here no longer reach it."
        )

    async def send() -> None:
        from gideon.core.config.credentials import owner_id_for

        try:
            async with asyncio.timeout(RELOCATION_NOTICE_TIMEOUT_SECS):
                owner = owner_id_for(was_on)
                direct = str(await delivery.open_dm(owner) or "") if owner else ""
                if direct and direct == channel:
                    await delivery.deliver_text(channel, message, thread)
        except Exception:
            logger.info(
                "Former channel conversation notice could not be delivered",
                exc_info=True,
            )

    try:
        task = asyncio.get_running_loop().create_task(send())
    except RuntimeError:
        return
    _NOTICE_TASKS.add(task)
    task.add_done_callback(_NOTICE_TASKS.discard)
