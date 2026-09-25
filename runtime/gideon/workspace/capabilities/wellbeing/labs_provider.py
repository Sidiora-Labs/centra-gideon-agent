"""Laboratory tool operations share the canonical domain service."""

import asyncio
import json

from .labs import LabStore
from .store import MeasurementError


async def invoke_labs(home, arguments):
    store = LabStore(home)
    operation = arguments.get("operation")
    payload = arguments.get("payload", {})
    if operation in ("labs_preview", "labs_commit"):
        result = await asyncio.to_thread(
            store.preview if operation == "labs_preview" else store.commit, payload
        )
    elif operation == "labs_correct":
        result = await asyncio.to_thread(store.correct, arguments.get("id"), payload)
    elif operation in ("labs_get", "labs_history"):
        result = await asyncio.to_thread(
            store.get if operation == "labs_get" else store.history, arguments.get("id")
        )
    elif operation == "labs_list":
        result = await asyncio.to_thread(store.list, **payload)
    elif operation == "labs_trends":
        result = await asyncio.to_thread(store.trends, **payload)
    else:
        raise MeasurementError("Unknown laboratory operation")
    return json.dumps(result, allow_nan=False)
