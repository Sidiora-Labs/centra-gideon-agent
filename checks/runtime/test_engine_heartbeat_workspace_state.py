"""Heartbeat execution and session files exercised with real isolated storage."""

import asyncio

import pytest

from gideon import ShutdownLatch
from gideon.engine import heartbeat, session_search
from gideon.engine import session_workspace as workspace


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    session_search.reset_for_tests()
    yield
    session_search.reset_for_tests()


def test_task_document_comments_markers_and_delivery():
    source = """# Tasks
<!-- instructions
- hidden
-->
<!-- one-line comment -->
- [x] completed marker is still scheduled
- [ ] pending  <!-- deliver:prompt:dashboard:chat-1 --> trailing text
* ordinary
bare task
-
"""
    assert heartbeat._extract_tasks(source) == [
        ("completed marker is still scheduled", ""),
        ("pending", "prompt:dashboard:chat-1"),
        ("ordinary", ""),
        ("bare task", ""),
    ]


@pytest.mark.asyncio
async def test_concurrent_tasks_settle_in_source_order():
    all_started = asyncio.Event()

    async def record(text, target):
        workspace.append_history("heartbeat", {"task": text, "target": target})
        if len(workspace.load_history("heartbeat")) == 4:
            all_started.set()
        await all_started.wait()
        if text == "retry":
            return "HEARTBEAT_KEEP"
        if text == "failed":
            raise ValueError("retained failure")
        if text == "cancelled":
            raise asyncio.CancelledError()
        workspace.write_result("heartbeat", "done", text)
        return "done"

    path = heartbeat.heartbeat_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        heartbeat._HEADER
        + "- complete\n- retry  <!-- deliver:dashboard:chat-1 -->\n- failed\n- cancelled\n"
    )
    service = heartbeat.HeartbeatService(on_task=record)
    await asyncio.wait_for(service._process_heartbeat_file(), 3)
    assert len(workspace.load_history("heartbeat")) == 4
    assert workspace.read_result("heartbeat", "done") == "complete"
    assert heartbeat._extract_tasks(path.read_text()) == [
        ("retry", "dashboard:chat-1"),
        ("failed", ""),
        ("cancelled", ""),
    ]
    assert not service._processing


@pytest.mark.asyncio
async def test_cancelled_processing_preserves_file_and_releases_processing_flag():
    began, hold = asyncio.Event(), asyncio.Event()

    async def record(text, target):
        workspace.append_history("cancel", {"task": text})
        began.set()
        await hold.wait()

    path = heartbeat.heartbeat_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    original = heartbeat._HEADER + "- unfinished\n"
    path.write_text(original)
    service = heartbeat.HeartbeatService(on_task=record)
    processing = asyncio.create_task(service._process_heartbeat_file())
    await asyncio.wait_for(began.wait(), 3)
    processing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await processing
    assert path.read_text() == original
    assert not service._processing
    assert workspace.load_history("cancel") == [{"task": "unfinished"}]


@pytest.mark.asyncio
async def test_start_stop_and_shutdown_use_real_task_and_latch(monkeypatch):
    latch = ShutdownLatch()
    monkeypatch.setattr(heartbeat, "shutdown_event", latch)
    service = heartbeat.HeartbeatService(interval=3600)
    await service.start()
    assert heartbeat.heartbeat_path().read_text() == heartbeat._HEADER
    first = service._task
    await asyncio.sleep(0)
    service.stop()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert service._task is None and service._tick == 0
    heartbeat.heartbeat_path().write_text("- retained on restart\n")
    await service.start()
    second = service._task
    latch.set()
    await asyncio.wait_for(second, 3)
    assert heartbeat.heartbeat_path().read_text() == "- retained on restart\n"
    service.stop()
    monkeypatch.setattr(heartbeat, "shutdown_event", ShutdownLatch())
    pulse = heartbeat.HeartbeatService(interval=0.001)
    assert await pulse._next_tick()
    assert pulse._tick == 1
    heartbeat.shutdown_event.set()
    assert not await pulse._next_tick()


@pytest.mark.asyncio
async def test_first_and_periodic_ticks_build_real_transcript_index():
    from gideon.cognition.history import ConversationLog, HistoryConsolidator
    from gideon.cognition.memory import MemoryJournal

    log = ConversationLog()
    log.append("pulse-one", "user", "heliotrope launch checklist")
    service = heartbeat.HeartbeatService(
        consolidator=HistoryConsolidator(log, MemoryJournal())
    )
    service._tick = 1
    await service._beat()
    assert [row["key"] for row in session_search.search_sessions("heliotrope")] == [
        "pulse-one"
    ]
    log.append("pulse-two", "user", "tamarind maintenance schedule")
    service._tick = 2
    await service._beat()
    assert session_search.search_sessions("tamarind") == []
    service._tick = heartbeat._SESSION_INDEX_TICKS
    await service._beat()
    assert [row["key"] for row in session_search.search_sessions("tamarind")] == [
        "pulse-two"
    ]
    before = log.read_messages("pulse-one")
    service._tick = heartbeat._BG_COMPRESS_TICKS
    await service._beat()
    assert log.read_messages("pulse-one") == before


@pytest.mark.asyncio
async def test_archive_cadence_and_guard_preserve_commitment_work():
    async def archive():
        workspace.append_history("passes", {"pass": "archive"})
        workspace.cleanup("expired")
        raise OSError("archive cleanup notification failed")

    async def deliver():
        workspace.append_history("passes", {"pass": "commitments"})

    expired = workspace.write_result("expired", "result", "old")
    service = heartbeat.HeartbeatService(
        on_auto_archive=archive, on_due_commitments=deliver
    )
    service._tick = heartbeat._SESSION_ARCHIVE_TICKS - 1
    await service._beat()
    assert expired.exists()
    service._tick += 1
    await service._beat()
    assert not expired.exists()
    assert workspace.load_history("passes") == [
        {"pass": "commitments"},
        {"pass": "archive"},
        {"pass": "commitments"},
    ]


def test_history_recovery_and_result_catalog_follow_live_home(tmp_path, monkeypatch):
    first = workspace.workspace_dir("channel:one.1")
    history = workspace.history_path("channel:one.1")
    history.write_text(
        '{"text":"café"}\n\ncorrupt\n{"text":"next"}\u2028{"text":"last"}\n'
    )
    assert workspace.load_history("channel:one.1") == [
        {"text": "café"},
        {"text": "next"},
        {"text": "last"},
    ]
    workspace.write_result("channel:one.1", "z", "é")
    workspace.append_result("channel:one.1", "z", "終")
    workspace.write_result("channel:one.1", "a", "first")
    assert [
        (row["agent_id"], row["size"])
        for row in workspace.list_results("channel:one.1")
    ] == [("a", 5), ("z", 5)]
    assert workspace.list_results("missing") == []
    assert not (first.parent / "missing").exists()
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "second-home"))
    assert workspace.load_history("channel:one.1") == []
    assert workspace.read_result("channel:one.1", "z") == ""
    assert history.is_file()
    workspace.append_history("channel:one.1", {"text": "new home"})
    assert workspace.load_history("channel:one.1") == [{"text": "new home"}]
    workspace.cleanup("channel:one.1")
    assert first.is_dir()


@pytest.mark.parametrize(
    "identifier", ["", "..", "name..part", "a/b", "a\\b", "with space", "é", "line\n"]
)
def test_workspace_identifiers_cannot_escape_storage(identifier):
    with pytest.raises(ValueError, match="Invalid session_id"):
        workspace.workspace_dir(identifier)
    with pytest.raises(ValueError, match="Invalid agent_id"):
        workspace.write_result("valid", identifier, "rejected")
