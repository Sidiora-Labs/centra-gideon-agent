"""Tests for the process-wide ``shutdown_event`` lazy proxy.

``shutdown_event`` must not be a plain ``asyncio.Event()`` created at import
time: an Event bound to the import-time loop, awaited later from a different
loop (e.g. the one ``asyncio.run()`` creates for the gateway), raises
``RuntimeError: got Future attached to a different loop``. The lazy proxy binds
to the running loop on first use instead, and re-binds across fresh
``asyncio.run()`` loops while preserving a pending set() across them.
"""

import asyncio

import gideon


def test_shutdown_event_importable_without_running_loop() -> None:
    """Importing the module must not require a running event loop."""
    assert hasattr(gideon, "shutdown_event")
    assert callable(gideon.shutdown_event.set)
    assert callable(gideon.shutdown_event.clear)
    assert callable(gideon.shutdown_event.is_set)


def test_shutdown_event_survives_fresh_asyncio_run() -> None:
    """``await shutdown_event.wait()`` must work inside a fresh ``asyncio.run()``.

    This mirrors the exact pattern that crashed the gateway: the module is
    imported at top level, then ``asyncio.run()`` creates a new loop and the
    gateway coroutine awaits ``shutdown_event.wait()``.
    """
    gideon.shutdown_event.clear()

    async def main() -> None:
        async def setter() -> None:
            await asyncio.sleep(0.01)
            gideon.shutdown_event.set()

        asyncio.create_task(setter())
        await gideon.shutdown_event.wait()

    asyncio.run(main())
    assert gideon.shutdown_event.is_set()
    gideon.shutdown_event.clear()


def test_shutdown_event_survives_multiple_asyncio_runs() -> None:
    """The proxy must rebind cleanly across successive loops."""
    for _ in range(3):

        async def main() -> None:
            async def setter() -> None:
                await asyncio.sleep(0.01)
                gideon.shutdown_event.set()

            asyncio.create_task(setter())
            await gideon.shutdown_event.wait()

        gideon.shutdown_event.clear()
        asyncio.run(main())
        assert gideon.shutdown_event.is_set()

    gideon.shutdown_event.clear()


def test_shutdown_event_wait_for_timeout() -> None:
    """``asyncio.wait_for(shutdown_event.wait(), timeout=...)`` must work."""
    gideon.shutdown_event.clear()

    async def main() -> str:
        try:
            await asyncio.wait_for(gideon.shutdown_event.wait(), timeout=0.05)
        except asyncio.TimeoutError:
            return "timed_out"
        return "set"

    result = asyncio.run(main())
    assert result == "timed_out"


def test_shutdown_event_does_not_bind_to_default_loop_via_get_event_loop() -> None:
    """The proxy must NOT bind to the default loop on first access."""
    # Step 1: make sure there's a default loop on the main thread
    try:
        default_loop = asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        default_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(default_loop)
    assert default_loop is not None

    # Step 2: reset the proxy's cached Event so it has to rebuild
    gideon.shutdown_event.clear()
    gideon.shutdown_event._event = None  # type: ignore[attr-defined]
    gideon.shutdown_event._loop = None  # type: ignore[attr-defined]

    # Step 3 + 4: fresh loop via asyncio.run, must not cross-loop
    async def main() -> None:
        async def setter() -> None:
            await asyncio.sleep(0.01)
            gideon.shutdown_event.set()

        asyncio.create_task(setter())
        await gideon.shutdown_event.wait()

    asyncio.run(main())
    assert gideon.shutdown_event.is_set()
    gideon.shutdown_event.clear()


def test_shutdown_event_pending_set_preserved_across_loops() -> None:
    """A ``set()`` call without a running loop must survive until one starts."""
    # Reset
    gideon.shutdown_event.clear()
    gideon.shutdown_event._event = None  # type: ignore[attr-defined]
    gideon.shutdown_event._loop = None  # type: ignore[attr-defined]

    # Sync set() before any loop runs
    gideon.shutdown_event.set()
    assert gideon.shutdown_event.is_set()

    async def main() -> bool:
        return gideon.shutdown_event.is_set()

    assert asyncio.run(main()) is True
    gideon.shutdown_event.clear()


def test_shutdown_event_get_raises_without_loop() -> None:
    """``_get()`` must raise RuntimeError when no loop is running."""
    gideon.shutdown_event._event = None  # type: ignore[attr-defined]
    gideon.shutdown_event._loop = None  # type: ignore[attr-defined]
    try:
        gideon.shutdown_event._get()  # type: ignore[attr-defined]
        assert False, "Expected RuntimeError"
    except RuntimeError as e:
        assert "without a running event loop" in str(e)
    finally:
        gideon.shutdown_event.clear()
