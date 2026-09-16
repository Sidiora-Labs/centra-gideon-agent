import asyncio
import time
from datetime import datetime, timedelta, timezone

from gideon.cognition.history import ConversationLog, HistoryConsolidator
from gideon.cognition.memory import MemoryJournal
from gideon.cognition.vector_memory import SemanticArchive
from gideon.core.config import AppConfig
from gideon.engine.gateway import RuntimeCoordinator
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.chat_persistence import save_session_to_history
from gideon.interfaces.dashboard.chat_utils import _history_key_for
from gideon.interfaces.dashboard.state import (
    ConsoleState,
    _ChatSession,
    _load_notifications,
)


def coordinator():
    config = AppConfig()
    config.session.auto_archive_days = 1
    config.memory.proactive_commitments = True
    config.save()
    runtime = RuntimeCoordinator(config)
    runtime._channel_delivery = None
    runtime.sessions = ConversationDirectory(config)
    log = ConversationLog()
    runtime.dashboard_state = ConsoleState(
        runtime.sessions, time.time(), conversation_log=log
    )
    return runtime, log


async def stop(runtime):
    task = runtime.heartbeat_svc._task
    runtime.heartbeat_svc.stop()
    await asyncio.gather(task, return_exceptions=True)


def test_heartbeat_archive_callback_persists_real_session_metadata():
    async def exercise():
        runtime, log = coordinator()
        state = runtime.dashboard_state
        session = _ChatSession("chat-1-123")
        state._sessions[session.key] = session
        session.append("user", "Keep this transcript", "msg msg-u", broadcast=False)
        session.last_activity_at = time.time() - 3 * 86400
        save_session_to_history(state, session, force=True)
        await runtime._init_heartbeat()
        try:
            await runtime.heartbeat_svc._on_auto_archive()
            assert session.lifecycle == "archived"
            meta = log.get_metadata(_history_key_for(session.key))
            assert meta["lifecycle"] == "archived"
            assert (
                log.read_messages(_history_key_for(session.key))[0]["content"]
                == "Keep this transcript"
            )
        finally:
            await stop(runtime)

    asyncio.run(exercise())


def test_due_commitments_deliver_once_and_preserve_future_windows(tmp_path):
    async def exercise():
        runtime, log = coordinator()
        records = SemanticArchive(db_path=tmp_path / "records.sqlite", embedding_dim=3)
        records.init()
        runtime.consolidator = HistoryConsolidator(
            log, MemoryJournal(), vector_store=records
        )
        service = runtime.consolidator._svc
        now = datetime.now(timezone.utc)
        old = service.record_commitment(
            agent="default",
            channel="dashboard",
            text="Review the report",
            due_window=(now - timedelta(hours=1)).isoformat(),
            confidence=0.9,
            enabled=True,
        )
        future = service.record_commitment(
            agent="default",
            channel="dashboard",
            text="Review tomorrow",
            due_window=(now + timedelta(days=1)).isoformat(),
            confidence=0.9,
            enabled=True,
        )
        assert old and future
        await runtime._init_heartbeat()
        try:
            await runtime.heartbeat_svc._on_due_commitments()
            notes = _load_notifications()
            assert len(notes) == 1 and "Review the report" in notes[0]["body"]
            assert not service.due_commitments_all(now_iso=now.isoformat())
            tomorrow = service.due_commitments_all(
                now_iso=(now + timedelta(days=2)).isoformat()
            )
            assert [item["key"] for item in tomorrow] == [future]
            await runtime.heartbeat_svc._on_due_commitments()
            assert _load_notifications() == notes
        finally:
            await stop(runtime)
            records.close()

    asyncio.run(exercise())
