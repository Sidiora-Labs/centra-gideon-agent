"""Regression coverage for synchronous embedding calls from worker threads."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from gideon.cognition.knowledge.embed_batch import embed_texts
from gideon.integrations.embedding_providers.base import run_embed_sync
from gideon.integrations.embedding_providers.registry import _DirectBinding


def test_worker_calls_reuse_shared_loop_and_keep_batch_slots():
    resource_lock = asyncio.Lock()
    loop_ids: list[int] = []
    batch_sizes: list[int] = []

    async def pooled_batch(
        texts: list[str], *, model: str
    ) -> list[list[float] | None]:
        loop_ids.append(id(asyncio.get_running_loop()))
        batch_sizes.append(len(texts))

        # Contention binds this real asyncio resource to the serving loop, as a
        # pooled async client does when its first connection is checked out.
        await resource_lock.acquire()
        waiter = asyncio.create_task(resource_lock.acquire())
        await asyncio.sleep(0)
        resource_lock.release()
        await waiter
        resource_lock.release()

        if len(texts) > 1:
            raise RuntimeError("provider batch limit")
        return [
            None if text == "unavailable" else [float(len(text))]
            for text in texts
        ]

    binding = _DirectBinding(provider=None, model_id="test-model")
    embed_many = partial(binding.many, pooled_batch)

    with ThreadPoolExecutor(max_workers=1) as workers:
        # The same worker thread makes sequential sync calls. Before this
        # regression fix, each call created and closed a separate asyncio loop.
        first = workers.submit(
            run_embed_sync,
            partial(pooled_batch, ["first"], model="test-model"),
            5,
        ).result()
        vectors = workers.submit(
            embed_texts,
            ["one", "unavailable", "three"],
            embed_many=embed_many,
            batch_size=3,
            retry_budget=1,
            sleep=lambda _delay: None,
        ).result()

    assert first == [[5.0]]
    assert vectors == [[3.0], None, [5.0]]
    assert len(set(loop_ids)) == 1
    assert batch_sizes == [1, 3, 1, 2, 1, 1]
