"""Observe editable files and publish changes after a quiet initial snapshot."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)
FS_WATCH_FEED = "fs:config"


def _signature(path: Path) -> tuple[float, int]:
    try:
        metadata = path.stat()
    except OSError:
        return 0.0, -1
    return metadata.st_mtime, metadata.st_size


@dataclass(frozen=True)
class WatchPlan:
    roots: tuple[Path, ...]
    suffixes: tuple[str, ...]

    def files(self):
        for location in self.roots:
            if location.is_file():
                yield location
                continue
            if not location.is_dir():
                continue
            yield from (
                candidate
                for candidate in location.rglob("*")
                if candidate.is_file() and candidate.suffix.lower() in self.suffixes
            )


class FileBaseline:
    def __init__(self):
        self.signatures: dict[str, tuple[float, int]] | None = None

    def replace(self, observations: list[tuple[str, tuple[float, int]]]) -> list[str]:
        before = self.signatures
        after = dict(observations)
        changes = []
        if before is not None:
            changes.extend(
                path
                for path, signature in observations
                if before.get(path) != signature
            )
            changes.extend(path for path in before if path not in after)
        self.signatures = after
        return changes


class ConfigFsWatcher:
    def __init__(
        self,
        roots,
        *,
        publish=None,
        interval: float = 3.0,
        suffixes=(".json", ".md", ".yaml", ".yml"),
    ):
        self._plan = WatchPlan(tuple(Path(root) for root in roots), tuple(suffixes))
        self._publisher = publish
        self._interval = interval
        self._baseline = FileBaseline()
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop())
        logger.info("Config FS watcher started (%d root(s))", len(self._plan.roots))

    def stop(self) -> None:
        self._stop.set()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()

    async def _loop(self) -> None:
        from gideon import shutdown_event

        while not (self._stop.is_set() or shutdown_event.is_set()):
            try:
                self.scan_once()
            except Exception:
                logger.debug("fs watch scan failed", exc_info=True)
            try:
                async with asyncio.timeout(self._interval):
                    await self._stop.wait()
                break
            except TimeoutError:
                continue

    def _iter_files(self):
        return self._plan.files()

    def scan_once(self) -> list[str]:
        observations = [(str(path), _signature(path)) for path in self._iter_files()]
        changes = self._baseline.replace(observations)
        for path in changes:
            self._emit(path)
        return changes

    def _emit(self, path: str) -> None:
        send = self._publisher
        if send is not None:
            try:
                send(FS_WATCH_FEED, "changed", dict(path=path))
            except Exception:
                logger.debug("fs watch publish failed", exc_info=True)


def default_config_roots():
    from gideon.core.config.loader import config_dir

    home = config_dir()
    return [home.joinpath(name) for name in ("config.json", "agents", "skills")]
