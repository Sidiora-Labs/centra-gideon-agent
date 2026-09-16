"""Shutdown signaling across event loops, threads, cancellation, and restarts."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

import gideon


def test_shutdown_event_importable_without_running_loop() -> None:
    assert isinstance(gideon.shutdown_event, gideon.ShutdownLatch)
    gideon.shutdown_event.clear()
    assert not gideon.shutdown_event.is_set()
    gideon.shutdown_event.set()
    assert gideon.shutdown_event.is_set()
    gideon.shutdown_event.clear()


def test_shutdown_event_survives_fresh_asyncio_run() -> None:
    signal = gideon.ShutdownLatch()

    async def wait_for_request() -> bool:
        asyncio.get_running_loop().call_soon(signal.set)
        return await signal.wait()

    assert asyncio.run(wait_for_request()) is True
    assert signal.is_set()


def test_shutdown_event_survives_multiple_asyncio_runs() -> None:
    signal = gideon.ShutdownLatch()

    async def wait_for_request() -> bool:
        asyncio.get_running_loop().call_soon(signal.set)
        return await signal.wait()

    for _ in range(3):
        signal.clear()
        assert asyncio.run(wait_for_request()) is True
        assert signal.is_set()


def test_shutdown_event_wait_for_timeout() -> None:
    signal = gideon.ShutdownLatch()

    async def wait_for_request() -> None:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(signal.wait(), timeout=0.02)
        assert not signal.is_set()
        signal.set()
        assert await signal.wait() is True

    asyncio.run(wait_for_request())


def test_shutdown_event_does_not_bind_to_default_loop_via_get_event_loop() -> None:
    dormant = asyncio.new_event_loop()
    asyncio.set_event_loop(dormant)
    signal = gideon.ShutdownLatch()

    async def wait_for_request() -> bool:
        asyncio.get_running_loop().call_soon(signal.set)
        return await signal.wait()

    try:
        assert asyncio.run(wait_for_request()) is True
    finally:
        dormant.close()
        asyncio.set_event_loop(None)


def test_shutdown_event_pending_set_preserved_across_loops() -> None:
    signal = gideon.ShutdownLatch()
    signal.set()
    assert signal.is_set()
    for _ in range(3):
        assert asyncio.run(signal.wait()) is True
    signal.clear()
    assert not signal.is_set()


def test_shutdown_wait_rejects_execution_without_a_running_loop() -> None:
    pending = gideon.ShutdownLatch().wait()
    try:
        with pytest.raises(RuntimeError, match="without a running event loop"):
            pending.send(None)
    finally:
        pending.close()


def test_cancelled_waiter_does_not_prevent_delivery_to_other_waiters() -> None:
    signal = gideon.ShutdownLatch()

    async def wait_for_request() -> None:
        cancelled = asyncio.create_task(signal.wait())
        survivor = asyncio.create_task(signal.wait())
        await asyncio.sleep(0)
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        signal.set()
        assert await asyncio.wait_for(survivor, timeout=1) is True

    asyncio.run(wait_for_request())


def test_request_wakes_waiters_on_two_real_threads() -> None:
    signal = gideon.ShutdownLatch()
    ready = threading.Barrier(3, timeout=3)

    def worker() -> bool:
        async def listen() -> bool:
            pending = asyncio.create_task(signal.wait())
            await asyncio.sleep(0)
            ready.wait()
            return await asyncio.wait_for(pending, timeout=3)

        return asyncio.run(listen())

    with ThreadPoolExecutor(max_workers=2) as pool:
        waiting = [pool.submit(worker) for _ in range(2)]
        ready.wait()
        signal.set()
        assert [future.result(timeout=5) for future in waiting] == [True, True]
