"""Contract for external knowledge chunk vector stores."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class VectorStoreProvider(ABC):
    """Persistence and cosine candidate generation for knowledge chunks.

    ``search`` returns dictionaries containing ``chunk_id``, ``item_id`` and
    ``similarity``. Similarities are cosine values in [-1, 1], descending. Core owns
    filtering, item liveness, MAX roll-up, and fusion.
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def search(self, embedding: list[float], *, limit: int) -> list[dict[str, Any]]: ...

    @abstractmethod
    def replace_item(self, item_id: str, chunks: list[dict[str, Any]]) -> None: ...

    @abstractmethod
    def delete_item(self, item_id: str) -> None: ...
