"""Agent access to canonical memory cards and self-graded practice."""

import asyncio
import json

from .memory_practice import MemoryPracticeStore
from .store import MeasurementError


async def invoke_memory(home, arguments):
    store = MemoryPracticeStore(home)
    operation = arguments.get("operation", "").removeprefix("memory_")
    payload, identity = arguments.get("payload", {}), arguments.get("id")
    if operation == "create":
        result = await asyncio.to_thread(store.create, payload)
    elif operation in ("update", "practice"):
        result = await asyncio.to_thread(getattr(store, operation), identity, payload)
    elif operation in ("get", "history"):
        result = await asyncio.to_thread(getattr(store, operation), identity)
    elif operation == "list":
        result = await asyncio.to_thread(store.list_cards, **payload)
    else:
        raise MeasurementError("Unknown memory practice operation")
    return json.dumps(result, allow_nan=False)
