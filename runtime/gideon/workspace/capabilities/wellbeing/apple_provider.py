"""Apple import tools delegate to the same domain importer."""

import asyncio
import json

from .apple_health import AppleHealthStore
from .store import MeasurementError


async def invoke_apple(home, arguments):
    store = AppleHealthStore(home)
    operation, payload = arguments.get("operation"), arguments.get("payload", {})
    if operation in ("apple_preview", "apple_commit"):
        result = await asyncio.to_thread(
            store.preview if operation == "apple_preview" else store.commit, payload
        )
    elif operation == "apple_metrics":
        result = await asyncio.to_thread(store.list_metrics, **payload)
    else:
        raise MeasurementError("Unknown Apple import operation")
    return json.dumps(result, allow_nan=False)
