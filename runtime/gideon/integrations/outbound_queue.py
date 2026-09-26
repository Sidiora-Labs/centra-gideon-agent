"""Bounded, independent outbound workers for registered channel deliveries."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)
_QUEUED_METHODS = frozenset(
    {
        "deliver_text",
        "deliver_rich",
        "deliver_cron_result",
        "deliver_notification",
        "deliver_chat_mirror",
        "deliver_subagent_reply",
        "upload_attachment",
        "open_dm",
        "start_stream",
        "append_stream_text",
        "append_stream_task",
        "stop_stream",
        "send",
    }
)


@dataclass
class _Send:
    method: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    result: asyncio.Future[Any]


class QueuedDelivery:
    def __init__(self, provider: str, delivery: Any, *, capacity: int = 100) -> None:
        self.provider = provider
        self.delivery = delivery
        self._queue: asyncio.Queue[_Send] = asyncio.Queue(maxsize=capacity)
        self._worker: asyncio.Task[None] | None = None
        self._active: asyncio.Future[Any] | None = None
        self._retired = False
        self.exhausted = 0

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self.delivery, name)
        if name not in _QUEUED_METHODS:
            return attribute

        async def queued(*args: Any, **kwargs: Any) -> Any:
            if self._retired:
                raise RuntimeError(f"{self.provider} delivery retired")
            loop = asyncio.get_running_loop()
            result: asyncio.Future[Any] = loop.create_future()
            item = _Send(name, args, kwargs, result)
            while not self._retired:
                try:
                    self._queue.put_nowait(item)
                    break
                except asyncio.QueueFull:
                    await asyncio.sleep(0.05)
            if self._retired:
                if not result.done():
                    result.set_exception(RuntimeError(f"{self.provider} delivery retired"))
                return await result
            if self._worker is None or self._worker.done():
                self._worker = loop.create_task(self._run())
            return await asyncio.shield(result)

        return queued

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            self._active = item.result
            try:
                if item.result.cancelled():
                    continue
                for attempt in range(4):
                    try:
                        value = await getattr(self.delivery, item.method)(
                            *item.args, **item.kwargs
                        )
                        if value is False or (
                            item.method in {"send", "deliver_text"} and value is None
                        ) or (
                            item.method == "deliver_text"
                            and value == ""
                            and len(item.args) > 1
                            and bool(item.args[1])
                            and not getattr(self.delivery, "allows_empty_receipt", False)
                        ):
                            raise ConnectionError(
                                f"{self.provider} {item.method} returned no delivery receipt"
                            )
                    except Exception as error:
                        if attempt == 3:
                            self.exhausted += 1
                            logger.error(
                                "channel delivery exhausted: provider=%s method=%s attempts=4",
                                self.provider,
                                item.method,
                                exc_info=True,
                            )
                            if not item.result.done():
                                item.result.set_exception(error)
                        else:
                            await asyncio.sleep(0.25 * (2**attempt))
                    else:
                        if not item.result.done():
                            item.result.set_result(value)
                        break
            finally:
                self._active = None
                self._queue.task_done()

    def retire(self) -> None:
        self._retired = True
        if self._worker is not None:
            self._worker.cancel()
        pending = self._active
        if pending is not None and not pending.done():
            pending.set_exception(RuntimeError(f"{self.provider} delivery retired"))
        while not self._queue.empty():
            item = self._queue.get_nowait()
            if not item.result.done():
                item.result.set_exception(RuntimeError(f"{self.provider} delivery retired"))
            self._queue.task_done()
