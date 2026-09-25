"""Agent access to recorded consumption and product presets."""

import asyncio
import json

from .store import MeasurementError
from .substances import ConsumptionStore


async def invoke_substances(home, arguments):
    store = ConsumptionStore(home)
    name, payload = arguments.get("operation", ""), arguments.get("payload", {})
    operation = name.removeprefix("substances_")
    methods = {
        "entry_create": store.create_entry,
        "entry_correct": store.correct_entry,
        "entry_delete": store.delete_entry,
        "entry_get": store.get_entry,
        "entry_history": store.history_entry,
        "entry_list": store.list_entries,
        "preset_create": store.create_preset,
        "preset_update": store.update_preset,
        "preset_delete": store.delete_preset,
        "preset_list": store.list_presets,
        "summary": store.summary,
    }
    if operation not in methods:
        raise MeasurementError("Unknown consumption operation")
    method = methods[operation]
    if operation in ("entry_get", "entry_history"):
        result = await asyncio.to_thread(method, arguments.get("id"))
    elif operation in (
        "entry_correct",
        "entry_delete",
        "preset_update",
        "preset_delete",
    ):
        result = await asyncio.to_thread(method, arguments.get("id"), payload)
    elif operation in ("entry_create", "preset_create"):
        result = await asyncio.to_thread(method, payload)
    else:
        result = await asyncio.to_thread(method, **payload)
    return json.dumps(result, allow_nan=False)
