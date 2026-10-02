"""Wave-one acceptance through the actual daemon and native model runtime."""

import asyncio

from .local_journey import authenticated_journey, conversation_journey


def test_local_daemon_preserves_history_across_restart(tmp_path):
    asyncio.run(authenticated_journey(tmp_path))


def test_native_conversation_and_unavailable_daemon(tmp_path):
    asyncio.run(conversation_journey(tmp_path))
