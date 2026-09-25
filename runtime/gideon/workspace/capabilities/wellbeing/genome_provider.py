"""Agent operations for genome sources and authored variant notes."""

import asyncio
import base64
import json

from .genome import GenomeStore
from .store import MeasurementError


async def invoke_genome(home, arguments):
    store = GenomeStore(home)
    operation = arguments.get("operation", "").removeprefix("genome_")
    payload, identity = arguments.get("payload", {}), arguments.get("id")
    methods = {
        "preview": store.preview,
        "commit": store.commit,
        "sources": store.list_sources,
        "source": store.get_source,
        "original": store.original,
        "variants": store.list_variants,
        "variant": store.get_variant,
        "annotate": store.annotate,
        "history": store.history,
    }
    if operation not in methods:
        raise MeasurementError("Unknown genome operation")
    method = methods[operation]
    if operation in ("preview", "commit"):
        result = await asyncio.to_thread(method, payload)
    elif operation == "sources":
        result = await asyncio.to_thread(method)
    elif operation == "variants":
        result = await asyncio.to_thread(method, identity, **payload)
    elif operation == "annotate":
        result = await asyncio.to_thread(method, identity, payload)
    else:
        result = await asyncio.to_thread(method, identity)
    if operation == "original":
        result = {"content_base64": base64.b64encode(result).decode()}
    return json.dumps(result, allow_nan=False)
