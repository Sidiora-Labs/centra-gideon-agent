"""Periodic user work, transcript upkeep, and heartbeat task-file processing."""

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Coroutine, Iterator

from gideon import shutdown_event
from gideon.cognition.memory import workspace_dir
from gideon.core.atomic_write import atomic_write

if TYPE_CHECKING:
    from gideon.cognition.history import HistoryConsolidator

logger = logging.getLogger(__name__)
_DELIVER_RE = re.compile(r"<!--\s*deliver:(\S+)\s*-->")
_KEEP_SENTINEL = "HEARTBEAT_KEEP"
_KEEP_RE = re.compile(_KEEP_SENTINEL, re.IGNORECASE)
_LIST_MARKER = re.compile(r"^(?:- \[x\] |- \[ \] |- |\* )")
_DEFAULT_INTERVAL = 60
_BG_COMPRESS_TICKS = 60
_SESSION_INDEX_TICKS = 5
_SESSION_ARCHIVE_TICKS = 60
_SESSION_INDEX_MAX_PER_PASS = 200
HEARTBEAT_FILE = "HEARTBEAT.md"
_HEADER = (
    "# Heartbeat Tasks\n\n<!-- Add tasks below (one per line). "
    "Gideon picks them up on next heartbeat. -->\n"
)


def heartbeat_path() -> Path:
    return workspace_dir().joinpath(HEARTBEAT_FILE)


@dataclass(frozen=True)
class _TaskLine:
    text: str
    destination: str = ""

    def markdown(self) -> str:
        address = f"  <!-- deliver:{self.destination} -->" if self.destination else ""
        return f"- {self.text}{address}\n"


def _task_lines(content: str) -> Iterator[_TaskLine]:
    hidden = False
    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            continue
        opens = "<!--" in line
        closes = "-->" in line
        if opens and not closes:
            hidden = True
            continue
        if hidden:
            hidden = not closes
            continue
        if line.startswith("#") or (line.startswith("<!--") and line.endswith("-->")):
            continue
        address = _DELIVER_RE.search(line)
        destination = address[1] if address else ""
        visible = line[: address.start()].rstrip() if address else line
        text = _LIST_MARKER.sub("", visible, count=1).strip()
        if text and text != "-":
            yield _TaskLine(text, destination)


@dataclass
class _TaskDocument:
    path: Path
    entries: tuple[_TaskLine, ...]

    @classmethod
    def open(cls, path: Path) -> "_TaskDocument":
        content = path.read_text(encoding="utf-8").strip() if path.exists() else ""
        return cls(path, tuple(_task_lines(content)))

    def settle(self, outcomes) -> None:
        retained = []
        for entry, outcome in zip(self.entries, outcomes):
            if isinstance(outcome, BaseException):
                logger.warning(
                    "Heartbeat task failed: %s", entry.text[:80], exc_info=outcome
                )
            elif _should_keep(outcome):
                logger.info("Heartbeat task incomplete, keeping: %s", entry.text[:80])
            else:
                continue
            retained.append(entry.markdown())
        atomic_write(self.path, _HEADER + "".join(retained))


@dataclass(frozen=True)
class _BeatAction:
    callback: Callable[[], Coroutine] | None
    every: int
    failure: str
    level: int = logging.DEBUG
    first: bool = False

    async def run(self, tick: int) -> None:
        if self.callback is None or not (
            tick % self.every == 0 or (self.first and tick == 1)
        ):
            return
        try:
            await self.callback()
        except Exception:
            logger.log(self.level, self.failure, exc_info=True)


class HeartbeatService:
    """Own the wake-up task and execute ordered, independently guarded passes."""

    _on_auto_archive: Callable[[], Coroutine] | None = None

    def __init__(
        self,
        on_task: Callable[[str, str], Coroutine] | None = None,
        interval: int = _DEFAULT_INTERVAL,
        consolidator: "HistoryConsolidator | None" = None,
        on_due_commitments: Callable[[], Coroutine] | None = None,
        on_auto_archive: Callable[[], Coroutine] | None = None,
    ) -> None:
        self._on_task, self._interval = on_task, interval
        self._consolidator = consolidator
        self._on_due_commitments = on_due_commitments
        self._on_auto_archive = on_auto_archive
        self._tick = 0
        self._processing = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        location = heartbeat_path()
        if not location.exists():
            atomic_write(location, _HEADER)
        self._task = asyncio.create_task(self._loop())
        logger.info("Heartbeat started (interval=%ds)", self._interval)

    def stop(self) -> None:
        running = self._task
        if running:
            running.cancel()
            self._task = None

    async def _next_tick(self) -> bool:
        if shutdown_event.is_set():
            return False
        try:
            await asyncio.wait_for(shutdown_event.wait(), self._interval)
        except asyncio.TimeoutError:
            self._tick += 1
            return True
        return False

    async def _loop(self) -> None:
        while await self._next_tick():
            try:
                await self._beat()
            except Exception:
                logger.warning("Heartbeat tick failed", exc_info=True)

    def _actions(self) -> Iterator[_BeatAction]:
        if self._consolidator:
            yield _BeatAction(
                self._run_bg_compression,
                _BG_COMPRESS_TICKS,
                "background compression pass failed",
            )
        yield _BeatAction(
            self._reindex_sessions,
            _SESSION_INDEX_TICKS,
            "session search reindex failed",
            first=True,
        )
        yield _BeatAction(
            self._on_auto_archive,
            _SESSION_ARCHIVE_TICKS,
            "session auto-archive pass failed",
        )
        yield _BeatAction(
            self._on_due_commitments, 1, "Commitment delivery failed", logging.WARNING
        )

    async def _beat(self) -> None:
        if not self._processing:
            await self._process_heartbeat_file()
        if self._consolidator:
            self._consolidator.check_idle_sessions()
        for action in self._actions():
            await action.run(self._tick)

    async def _reindex_sessions(self) -> None:
        from functools import partial

        from gideon.engine import session_search

        operation = partial(
            session_search.reindex_all, limit=_SESSION_INDEX_MAX_PER_PASS
        )
        count = await asyncio.get_running_loop().run_in_executor(None, operation)
        if count:
            logger.debug("session search: indexed %d session(s)", count)

    async def _run_bg_compression(self) -> None:
        history = getattr(self._consolidator, "_log", None)
        if history is None:
            return
        from gideon.cognition.bg_compress import run_bg_compression_pass

        try:
            from gideon.integrations.embedding_providers.registry import (
                get_active_embed_fn,
            )

            embedding = get_active_embed_fn()
        except Exception:
            embedding = None
        outcomes = await run_bg_compression_pass(history, embed_fn=embedding)
        if outcomes:
            reclaimed = sum(item["chars_in"] - item["chars_out"] for item in outcomes)
            logger.info(
                "Background compression: %d session(s), ~%d chars reclaimed",
                len(outcomes),
                reclaimed,
            )

    async def _run_one_task(self, task_text: str, deliver: str) -> str | None:
        from gideon.workspace.capabilities.identity.continuity import (
            heartbeat_turns_paused,
        )

        if heartbeat_turns_paused():
            return _KEEP_SENTINEL
        assert self._on_task is not None
        return await self._on_task(task_text, deliver)

    async def _process_heartbeat_file(self) -> None:
        from gideon.workspace.capabilities.identity.continuity import (
            heartbeat_turns_paused,
        )

        if heartbeat_turns_paused():
            return
        document = _TaskDocument.open(heartbeat_path())
        if not document.entries or not self._on_task:
            return
        self._processing = True
        try:
            logger.info("Heartbeat: %d task(s) found", len(document.entries))
            outcomes = await asyncio.gather(
                *(
                    self._run_one_task(entry.text, entry.destination)
                    for entry in document.entries
                ),
                return_exceptions=True,
            )
            document.settle(outcomes)
        finally:
            self._processing = False


def _should_keep(result: str | None) -> bool:
    return result is not None and _KEEP_RE.search(result) is not None


def strip_keep_sentinel(text: str) -> str:
    return _KEEP_RE.sub("", text).strip()


def is_keep_response(text: str | None) -> bool:
    return text is not None and _KEEP_SENTINEL in text.upper()


def _extract_tasks(content: str) -> list[tuple[str, str]]:
    return [(entry.text, entry.destination) for entry in _task_lines(content)]
