"""Deterministic ownership for forwarded asynchronous turn streams."""

from contextlib import asynccontextmanager


@asynccontextmanager
async def closing_stream(stream):
    try:
        yield stream
    finally:
        close = getattr(stream, "aclose", None)
        if close is not None:
            await close()
