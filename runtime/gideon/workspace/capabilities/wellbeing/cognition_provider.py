"""Agent operations for recorded cognitive practice."""

import asyncio
import json

from .cognition import CognitiveStore
from .store import MeasurementError


async def invoke_cognition(home, arguments):
    store = CognitiveStore(home)
    operation = arguments.get("operation", "").removeprefix("cognition_")
    payload, identity = arguments.get("payload", {}), arguments.get("id")
    if operation == "start":
        result = await asyncio.to_thread(store.start, payload)
    elif operation in ("answer", "cancel"):
        result = await asyncio.to_thread(getattr(store, operation), identity, payload)
    elif operation == "get":
        result = await asyncio.to_thread(store.get, identity)
    elif operation == "list":
        result = await asyncio.to_thread(store.list_sessions, **payload)
    else:
        raise MeasurementError("Unknown cognitive operation")
    return json.dumps(result, allow_nan=False)
