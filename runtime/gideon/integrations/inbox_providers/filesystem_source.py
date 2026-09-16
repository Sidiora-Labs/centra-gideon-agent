"""Consume sorted JSON message batches from the active home's incoming spool."""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, fields
from pathlib import Path
from threading import RLock
from typing import Any

from gideon.core.config import loader as config_loader
from gideon.integrations.inbox_providers.base import (
    IncomingMessage,
    MessageSourceProvider,
)

logger = logging.getLogger(__name__)
_spool_lock = RLock()
_MESSAGE_FIELDS = frozenset(member.name for member in fields(IncomingMessage))


def config_dir():
    return config_loader.config_dir()


@dataclass(frozen=True)
class _SpoolDrain:
    directory: Path

    def read(self) -> list[IncomingMessage]:
        result = []
        with _spool_lock:
            archive = self.directory / "processed"
            archive.mkdir(parents=True, exist_ok=True)
            for path in sorted(self.directory.glob("*.json")):
                try:
                    document = json.loads(path.read_text())
                    for record in document.get("messages", []):
                        result.append(self.decode(record, path.stem, len(result)))
                    path.rename(archive / path.name)
                except (json.JSONDecodeError, OSError):
                    logger.warning(
                        "Failed to read message file %s", path, exc_info=True
                    )
        return result

    @staticmethod
    def decode(record: dict[str, Any], stem: str, index: int) -> IncomingMessage:
        values = {
            "id": f"{stem}_{index}",
            "channel_id": "filesystem",
            "channel_name": "local",
            "timestamp": time.time(),
        }
        values.update(
            (name, record[name]) for name in _MESSAGE_FIELDS if name in record
        )
        values["kind"] = str(record.get("kind") or "")
        return IncomingMessage(**values)


class FilesystemSourceProvider(MessageSourceProvider):
    @property
    def source_name(self) -> str:
        return "filesystem"

    async def poll(
        self, watched_channels: list[str], checkpoints: dict[str, str], user_id: str
    ) -> tuple[list[IncomingMessage], dict[str, str]]:
        spool = _SpoolDrain(config_dir() / "inbox" / "incoming")
        messages = (
            await asyncio.to_thread(spool.read) if spool.directory.exists() else []
        )
        return messages, checkpoints

    async def send_reply(
        self, channel_id: str, text: str, thread_ts: str | None = None
    ) -> bool:
        return True

    async def add_reaction(self, channel_id: str, ts: str, emoji: str) -> bool:
        return True

    async def get_channel_history(
        self, channel_id: str, oldest: str, limit: int = 200
    ) -> list[dict[str, Any]]:
        return []

    async def resolve_user_name(self, user_id: str) -> str:
        return user_id


Provider = FilesystemSourceProvider


def create_provider(config=None):
    return FilesystemSourceProvider()
