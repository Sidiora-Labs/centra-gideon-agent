"""Ordered, expiring context delivery independent of the selected conversation."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass
class ContextEntry:
    content: str
    source: str | None = None
    ephemeral: bool = True
    max_age: float | None = None
    injected_at: float = field(default_factory=time.time)

    def payload(self) -> dict[str, Any]:
        return {"content": self.content, "source": self.source, "ephemeral": self.ephemeral, "maxAge": self.max_age}

    def expires_before(self, timestamp: float) -> bool:
        return self.max_age is not None and self.injected_at + self.max_age < timestamp


class ContextBuffer:
    def __init__(self, limit: int = 50):
        self.limit = limit
        self.entries: list[ContextEntry] = []
        self._delivery_lock = asyncio.Lock()

    def append(self, entry: ContextEntry) -> None:
        self.entries.append(entry)
        overflow = len(self.entries) - self.limit
        if overflow > 0:
            del self.entries[:overflow]

    async def flush(self, session_id: str, post: Callable[..., Awaitable[Any]]) -> None:
        async with self._delivery_lock:
            batch, self.entries = self.entries, []
            retained: list[ContextEntry] = []
            failure: Exception | None = None
            timestamp = time.time()
            for entry in batch:
                if entry.expires_before(timestamp):
                    continue
                try:
                    await post(f"/api/chat/sessions/{session_id}/context", entry.payload())
                except Exception as error:
                    retained.append(entry)
                    failure = error
            self.entries[:0] = retained
            if failure is not None:
                raise failure
