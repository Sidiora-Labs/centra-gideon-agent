"""Embedding inference contracts and the synchronous coroutine boundary."""

from __future__ import annotations

import asyncio
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from concurrent.futures import Future
from dataclasses import dataclass
from functools import partial
from typing import Any, TypeVar

_T = TypeVar("_T")


class _BridgeWorker:
    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._worker: threading.Thread | None = None

    def _serve(self, ready: Future[asyncio.AbstractEventLoop]) -> None:
        loop = None
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            loop.call_soon(ready.set_result, loop)
            loop.run_forever()
        except BaseException as error:
            if not ready.done():
                ready.set_exception(error)
            else:
                raise
        finally:
            if loop is not None:
                loop.close()

    def acquire(self) -> asyncio.AbstractEventLoop:
        with self._guard:
            usable = (
                self._loop is not None
                and self._loop.is_running()
                and not self._loop.is_closed()
            )
            if (
                usable
                and self._loop is not None
                and self._worker is not None
                and self._worker.is_alive()
            ):
                return self._loop
            ready: Future[asyncio.AbstractEventLoop] = Future()
            self._worker = threading.Thread(
                target=self._serve,
                args=(ready,),
                name="gideon-embed-bridge",
                daemon=True,
            )
            self._worker.start()
            return ready.result()


_bridge = _BridgeWorker()


def sync_bridge_loop() -> asyncio.AbstractEventLoop:
    return _bridge.acquire()


def run_embed_sync(
    factory: Callable[[], Coroutine[Any, Any, _T]], timeout: float
) -> _T:
    try:
        caller_loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())
    worker_loop = sync_bridge_loop()
    if caller_loop is worker_loop:
        raise RuntimeError(
            "run_embed_sync called from inside the embedding bridge loop"
        )
    coroutine = factory()
    try:
        receipt = asyncio.run_coroutine_threadsafe(coroutine, worker_loop)
    except BaseException:
        coroutine.close()
        raise
    try:
        return receipt.result(timeout)
    except TimeoutError:
        receipt.cancel()
        raise


@dataclass
class EmbeddingModel:
    name: str
    dimension: int
    size_mb: float = 0
    description: str = ""
    downloaded: bool = False
    active: bool = False


class EmbeddingProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def display_name(self) -> str: ...

    @abstractmethod
    async def is_available(self) -> bool: ...

    @abstractmethod
    async def embed(self, text: str, model: str = "") -> list[float] | None: ...

    @abstractmethod
    async def embed_batch(
        self, texts: list[str], model: str = ""
    ) -> list[list[float]]: ...

    def _embed_sync(self, text: str, *, model: str) -> list[float] | None:
        return run_embed_sync(partial(self.embed, text, model), timeout=30)

    def get_embed_fn(self, model: str = "") -> Callable[[str], list[float] | None]:
        return partial(self._embed_sync, model=model)

    def info(self) -> dict[str, Any]:
        return dict(name=self.name, display_name=self.display_name)
