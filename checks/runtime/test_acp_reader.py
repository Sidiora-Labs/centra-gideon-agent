"""Tests for the P9 FrameRouter (acp/reader.py) — the single-reader stdout demux.

Driven by a fake readline feeding scripted JSON-RPC frames; no real ACP process.
The load-bearing property is SESSION ISOLATION: frames for session A and B are
demuxed to separate queues and one session's fate doesn't stall the other."""

from __future__ import annotations

import asyncio
import json

import pytest

from gideon.integrations.acp.reader import FrameRouter


class _FakeStdout:
    """A scriptable async line source: feed() lines, then it blocks (like a live
    stream awaiting more) until close()."""

    def __init__(self):
        self._q: asyncio.Queue[bytes] = asyncio.Queue()

    def feed(self, obj):
        self._q.put_nowait((json.dumps(obj) + "\n").encode())

    def feed_raw(self, line: bytes):
        self._q.put_nowait(line)

    def eof(self):
        self._q.put_nowait(b"")

    async def readline(self) -> bytes:
        return await self._q.get()


def _f(**kw) -> dict:
    return kw


@pytest.mark.asyncio
async def test_response_resolves_pending_future():
    out = _FakeStdout()
    r = FrameRouter(out.readline)
    r.start()
    fut = r.expect(2)
    out.feed(_f(id=2, result={"sessionId": "s-abc"}))
    resp = await asyncio.wait_for(fut, timeout=2)
    assert resp.result["sessionId"] == "s-abc"
    await r.close()


@pytest.mark.asyncio
async def test_session_update_demuxed_to_its_queue():
    out = _FakeStdout()
    r = FrameRouter(out.readline)
    r.start()
    qa = r.register_session("A")
    qb = r.register_session("B")
    out.feed(_f(method="session/update", params={"sessionId": "A", "update": {"x": 1}}))
    out.feed(_f(method="session/update", params={"sessionId": "B", "update": {"y": 2}}))
    a = await asyncio.wait_for(qa.get(), timeout=2)
    b = await asyncio.wait_for(qb.get(), timeout=2)
    assert a.params["sessionId"] == "A" and b.params["sessionId"] == "B"
    assert qa.empty() and qb.empty()
    await r.close()


@pytest.mark.asyncio
async def test_session_isolation_interleaved():
    out = _FakeStdout()
    r = FrameRouter(out.readline)
    r.start()
    qa = r.register_session("A")
    qb = r.register_session("B")
    for i in range(5):
        out.feed(
            _f(method="session/update", params={"sessionId": "A", "update": {"n": i}})
        )
        out.feed(
            _f(method="session/update", params={"sessionId": "B", "update": {"n": i}})
        )
    a_ns = [
        (await asyncio.wait_for(qa.get(), timeout=2)).params["update"]["n"]
        for _ in range(5)
    ]
    b_ns = [
        (await asyncio.wait_for(qb.get(), timeout=2)).params["update"]["n"]
        for _ in range(5)
    ]
    assert a_ns == [0, 1, 2, 3, 4] and b_ns == [0, 1, 2, 3, 4]
    await r.close()


@pytest.mark.asyncio
async def test_one_session_unregister_does_not_stall_other():
    out = _FakeStdout()
    r = FrameRouter(out.readline)
    r.start()
    r.register_session("A")
    qb = r.register_session("B")
    out.feed(_f(method="session/update", params={"sessionId": "A", "update": {}}))
    r.unregister_session("A")
    out.feed(_f(method="session/update", params={"sessionId": "A", "update": {}}))
    out.feed(
        _f(method="session/update", params={"sessionId": "B", "update": {"ok": True}})
    )
    b = await asyncio.wait_for(qb.get(), timeout=2)
    assert b.params["update"]["ok"] is True
    await r.close()


@pytest.mark.asyncio
async def test_server_request_routed_to_session_then_handler():
    seen: list = []
    out = _FakeStdout()
    r = FrameRouter(out.readline, on_server_request=lambda m: seen.append(m))
    r.start()
    q = r.register_session("A")
    out.feed(_f(id=99, method="session/request_permission", params={"sessionId": "A"}))
    out.feed(
        _f(id=100, method="session/request_permission", params={"sessionId": "ZZZ"})
    )
    routed = await asyncio.wait_for(q.get(), timeout=2)
    assert routed.id == 99 and routed.method == "session/request_permission"
    await asyncio.sleep(0.05)
    assert len(seen) == 1 and seen[0].id == 100
    await r.close()


@pytest.mark.asyncio
async def test_broadcast_for_idless_and_unknown_session():
    seen: list = []
    out = _FakeStdout()
    r = FrameRouter(out.readline, on_broadcast=lambda m: seen.append(m))
    r.start()
    out.feed(_f(method="session/update", params={"sessionId": "unknown"}))
    out.feed(_f(method="_notify", params={}))
    await asyncio.sleep(0.05)
    assert len(seen) == 2
    await r.close()


@pytest.mark.asyncio
async def test_process_death_fails_pending_and_wakes_sessions():
    out = _FakeStdout()
    r = FrameRouter(out.readline)
    r.start()
    fut = r.expect(7)
    q = r.register_session("A")
    out.eof()
    with pytest.raises(Exception):
        await asyncio.wait_for(fut, timeout=2)
    poison = await asyncio.wait_for(q.get(), timeout=2)
    assert poison.method == "_router/closed"


@pytest.mark.asyncio
async def test_process_death_fans_out_to_ALL_concurrent_sessions():
    out = _FakeStdout()
    r = FrameRouter(out.readline)
    r.start()
    futs = [r.expect(i) for i in (10, 11, 12)]
    queues = [r.register_session(sid) for sid in ("A", "B", "C")]
    out.eof()
    for f in futs:
        with pytest.raises(Exception):
            await asyncio.wait_for(f, timeout=2)
    for q in queues:
        poison = await asyncio.wait_for(q.get(), timeout=2)
        assert poison.method == "_router/closed"


@pytest.mark.asyncio
async def test_non_json_line_skipped():
    out = _FakeStdout()
    r = FrameRouter(out.readline)
    r.start()
    q = r.register_session("A")
    out.feed_raw(b"this is a stray log line, not json\n")
    out.feed(
        _f(method="session/update", params={"sessionId": "A", "update": {"ok": 1}})
    )
    got = await asyncio.wait_for(q.get(), timeout=2)
    assert got.params["update"]["ok"] == 1
    await r.close()


@pytest.mark.asyncio
async def test_backpressure_drops_oldest_not_reader():
    out = _FakeStdout()
    r = FrameRouter(out.readline)
    r.start()
    q = r.register_session("A")
    q._maxsize = 3  # type: ignore[attr-defined]
    for i in range(10):
        out.feed(
            _f(method="session/update", params={"sessionId": "A", "update": {"n": i}})
        )
    await asyncio.sleep(0.1)
    drained = []
    while not q.empty():
        drained.append(q.get_nowait().params["update"]["n"])
    assert drained and max(drained) == 9 and len(drained) <= 3
    await r.close()


@pytest.mark.asyncio
async def test_real_stream_eof_is_terminal_for_late_consumers():
    stream = asyncio.StreamReader()
    router = FrameRouter(stream.readline)
    pending = router.expect(10)
    existing = router.register_session("existing")
    router.start()
    stream.feed_eof()
    with pytest.raises(ConnectionError, match="stdout EOF"):
        await asyncio.wait_for(pending, 1)
    assert (await existing.get()).method == "_router/closed"
    with pytest.raises(ConnectionError, match="stdout EOF"):
        await router.expect(11)
    late = router.register_session("late")
    assert (await late.get()).method == "_router/closed"
    await router.close()
    await router.close()
    assert existing.empty() and late.empty()


@pytest.mark.asyncio
async def test_real_stream_early_response_and_cancelled_waiter():
    stream = asyncio.StreamReader()
    router = FrameRouter(stream.readline)
    cancelled = router.expect(12)
    cancelled.cancel()
    router.start()
    stream.feed_data(b'{"id":12,"result":{}}\n{"id":13,"result":{"ok":true}}\n')
    await asyncio.sleep(0)
    response = await asyncio.wait_for(router.expect(13), 1)
    assert response.result == {"ok": True}
    assert cancelled.cancelled()
    await router.close()


@pytest.mark.asyncio
async def test_real_stream_external_close_fails_waiters_and_stops_reader():
    stream = asyncio.StreamReader()
    router = FrameRouter(stream.readline)
    pending = router.expect(1)
    mailbox = router.register_session("one")
    router.start()
    task = router._reader_task
    await router.close(RuntimeError("shutdown"))
    with pytest.raises(RuntimeError, match="shutdown"):
        await pending
    assert task.done()
    assert mailbox.get_nowait().method == "_router/closed"
