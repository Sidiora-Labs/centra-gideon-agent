"""Process-wide bounded cache of restart-safe tool-description vectors."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import threading
import time
from array import array
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from gideon.core.atomic_write import atomic_write
from gideon.cognition.knowledge.embed_batch import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_RETRY_BUDGET,
    embed_texts,
)

logger = logging.getLogger(__name__)
TOOL_VECTORS_FILE = "tool_embeddings.json"
MAX_VECTORS = 2048
RETRY_FAILED_SECS = 300.0


def tool_text(name: str, description: str) -> str:
    return f"{name}: {description or ''}".strip()


@dataclass(frozen=True, slots=True)
class Embedder:
    model: str
    one: Callable[[str], list[float] | None]
    many: Callable[[list[str]], list[list[float] | None]] | None


def bound_embedder() -> Embedder | None:
    try:
        from gideon.integrations.embedding_providers.registry import (
            _active_embedding_spec,
            get_active_embed_fn,
            get_active_embed_many_fn,
        )

        spec = _active_embedding_spec()
        if not spec or not spec[1]:
            return None
        one = get_active_embed_fn()
        if one is None:
            return None
        return Embedder(
            model=f"{spec[0]}:{spec[1]}",
            one=one,
            many=get_active_embed_many_fn(),
        )
    except Exception:
        logger.debug("tool vectors: active embedding model unavailable", exc_info=True)
        return None


def default_path() -> Path:
    from gideon.core.config.loader import config_dir

    return config_dir() / TOOL_VECTORS_FILE


def _digest(model: str, text: str) -> str:
    return hashlib.sha256(f"{model}\0{text}".encode("utf-8")).hexdigest()


def _pack(vector: list[float]) -> str:
    return base64.b64encode(array("f", vector).tobytes()).decode("ascii")


def _unpack(value: object) -> list[float] | None:
    if not isinstance(value, str):
        return None
    try:
        blob = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None
    if not blob or len(blob) % 4:
        return None
    out = array("f")
    out.frombytes(blob)
    return out.tolist()


def _read(path: Path) -> dict[str, str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    vectors = raw.get("vectors") if isinstance(raw, dict) else None
    return vectors if isinstance(vectors, dict) else {}


@dataclass
class _Job:
    path: Path
    embedder: Embedder
    texts: dict[str, str]


class ToolVectors:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._path: Path | None = None
        self._disk: dict[str, str] = {}
        self._vectors: dict[str, list[float]] = {}
        self._jobs: list[_Job] = []
        self._pending: set[tuple[str, str]] = set()
        self._failed: dict[tuple[str, str], float] = {}
        self._worker: threading.Thread | None = None

    def _load(self, path: Path) -> None:
        if self._path != path:
            self._path = path
            self._disk = _read(path)
            self._vectors = {
                digest: vector
                for digest, blob in self._disk.items()
                if (vector := _unpack(blob)) is not None
            }

    def vectors(self, path: Path, model: str, texts: Iterable[str]) -> dict[str, list[float]]:
        with self._lock:
            self._load(path)
            return {
                text: list(self._vectors[digest])
                for text in texts
                if (digest := _digest(model, text)) in self._vectors
            }

    def want(self, path: Path, embedder: Embedder, texts: Iterable[str]) -> None:
        now = time.monotonic()
        model = embedder.model
        with self._lock:
            self._load(path)
            todo: dict[str, str] = {}
            for text in texts:
                digest = _digest(model, text)
                key = (str(path), digest)
                if not text or digest in self._vectors or key in self._pending:
                    continue
                failed_at = self._failed.get(key)
                if failed_at is not None and now - failed_at < RETRY_FAILED_SECS:
                    continue
                todo[digest] = text
                self._pending.add(key)
            if not todo:
                return
            for job in self._jobs:
                if job.path == path and job.embedder.model == model:
                    job.texts.update(todo)
                    job.embedder = embedder
                    break
            else:
                self._jobs.append(_Job(path, embedder, todo))
            if self._worker is None:
                self._worker = threading.Thread(
                    target=self._fill, name="tool-vectors", daemon=True
                )
                self._worker.start()

    def drain(self, timeout: float | None = None) -> bool:
        with self._lock:
            worker = self._worker
        if worker is None:
            return True
        worker.join(timeout)
        return not worker.is_alive()

    def _fill(self) -> None:
        while True:
            with self._lock:
                if not self._jobs:
                    self._worker = None
                    return
                job = self._jobs.pop(0)
            try:
                vectors = embed_texts(
                    list(job.texts.values()),
                    embed_many=job.embedder.many,
                    embed_one=job.embedder.one,
                    batch_size=DEFAULT_BATCH_SIZE,
                    retry_budget=DEFAULT_RETRY_BUDGET,
                )
            except Exception:
                logger.warning("tool vector batch failed", exc_info=True)
                vectors = []
            self._store(job, vectors)

    def _store(self, job: _Job, vectors: list[list[float] | None]) -> None:
        now = time.monotonic()
        with self._lock:
            self._load(job.path)
            for index, (digest, _text) in enumerate(job.texts.items()):
                key = (str(job.path), digest)
                self._pending.discard(key)
                vector = vectors[index] if index < len(vectors) else None
                if vector:
                    packed = _pack(vector)
                    self._disk.pop(digest, None)
                    self._disk[digest] = packed
                    self._vectors[digest] = list(vector)
                    self._failed.pop(key, None)
                else:
                    self._failed[key] = now
            for digest in list(self._disk)[: max(0, len(self._disk) - MAX_VECTORS)]:
                self._disk.pop(digest, None)
                self._vectors.pop(digest, None)
            try:
                atomic_write(
                    job.path,
                    json.dumps(
                        {"version": 1, "vectors": self._disk}, separators=(",", ":")
                    ),
                )
            except OSError:
                logger.warning("tool vectors could not save %s", job.path, exc_info=True)


_INDEX = ToolVectors()


def tool_vectors() -> ToolVectors:
    return _INDEX
