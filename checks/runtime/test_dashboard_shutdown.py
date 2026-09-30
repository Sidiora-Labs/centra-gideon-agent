import asyncio

from gideon.core.cancellation import cancel_task_bounded


def test_cancel_resistant_task_does_not_hold_shutdown(caplog):
    async def exercise():
        release = asyncio.Event()
        cancellation_seen = asyncio.Event()

        async def cancellation_resistant():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancellation_seen.set()
                await release.wait()

        task = asyncio.create_task(cancellation_resistant(), name="resistant")
        await asyncio.sleep(0)
        started = asyncio.get_running_loop().time()
        await cancel_task_bounded(task, timeout=0.02, label="maintenance")
        elapsed = asyncio.get_running_loop().time() - started

        assert cancellation_seen.is_set()
        assert not task.done()
        assert elapsed < 0.5
        assert "maintenance task resistant did not stop" in caplog.text

        release.set()
        await task

        async def fail():
            raise RuntimeError("shutdown task failure")

        failed = asyncio.create_task(fail())
        await asyncio.sleep(0)
        await cancel_task_bounded(failed, timeout=0.02, label="failed task")
        assert "failed task stopped with an error" in caplog.text

        second_release = asyncio.Event()
        second_cancel_seen = asyncio.Event()

        async def cancellation_resistant_again():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                second_cancel_seen.set()
                await second_release.wait()

        second = asyncio.create_task(cancellation_resistant_again())
        caller = asyncio.create_task(
            cancel_task_bounded(second, timeout=1.0, label="caller cancellation")
        )
        await second_cancel_seen.wait()
        caller.cancel()
        try:
            await caller
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("caller cancellation was swallowed")
        second_release.set()
        await second

    asyncio.run(exercise())
