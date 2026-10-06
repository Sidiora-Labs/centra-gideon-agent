"""Periodic user work, transcript upkeep, and heartbeat task-file processing."""

import asyncio
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Coroutine, Iterator

from gideon import shutdown_event
from gideon.cognition.memory import workspace_dir
from gideon.core.atomic_write import atomic_write, atomic_write_bytes
from gideon.engine.heartbeat_store import queue_lock
from gideon.security.approval_answer import OWNER, Principal
from gideon.security.owner_grants import GrantBook, seal

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
_HEARTBEAT_GRANTS = GrantBook("heartbeat_tasks")


def heartbeat_path() -> Path:
    return workspace_dir().joinpath(HEARTBEAT_FILE)


@dataclass(frozen=True)
class _TaskLine:
    text: str
    destination: str = ""

    def markdown(self) -> str:
        address = f"  <!-- deliver:{self.destination} -->" if self.destination else ""
        return f"- {self.text}{address}\n"


def _task_rows(content: str) -> Iterator[tuple[str, _TaskLine | None]]:
    """Parse queue tasks while retaining every original line and ending."""
    hidden = False
    for raw in content.splitlines(keepends=True):
        line = raw.strip()
        task = None
        if not line:
            pass
        elif "<!--" in line and "-->" not in line:
            hidden = True
        elif hidden:
            hidden = "-->" not in line
        elif line.startswith("#") or (line.startswith("<!--") and line.endswith("-->")):
            pass
        else:
            address = _DELIVER_RE.search(line)
            destination = address[1] if address else ""
            visible = line[: address.start()].rstrip() if address else line
            text = _LIST_MARKER.sub("", visible, count=1).strip()
            if text and text != "-":
                task = _TaskLine(text, destination)
        yield raw, task


def _task_lines(content: str) -> Iterator[_TaskLine]:
    for _raw, task in _task_rows(content):
        if task is not None:
            yield task


def _task_content(entry: _TaskLine) -> str:
    return json.dumps(
        [entry.text, entry.destination], ensure_ascii=False, separators=(",", ":")
    )


def _task_keys(entries: tuple[_TaskLine, ...] | list[_TaskLine]) -> list[str]:
    occurrences: Counter[str] = Counter()
    result = []
    for entry in entries:
        content = _task_content(entry)
        occurrences[content] += 1
        result.append(f"heartbeat:{seal(content)}:{occurrences[content]}")
    return result


def record_owner_file_added_tasks(
    before: str,
    after: str,
    *,
    principal: Principal,
) -> int:
    """Grant only exact task occurrences added by a committed owner Files edit.

    This is a post-commit hook. A grant failure leaves the task waiting and does not
    change the outcome of the already committed file write.
    """
    if not isinstance(principal, Principal) or principal.kind != OWNER:
        return 0
    old_counts = Counter(_task_content(entry) for entry in _task_lines(before))
    entries = tuple(_task_lines(after))
    seen: Counter[str] = Counter()
    granted = 0
    for entry, key in zip(entries, _task_keys(entries)):
        content = _task_content(entry)
        seen[content] += 1
        if seen[content] <= old_counts[content]:
            continue
        try:
            _HEARTBEAT_GRANTS.give(key, content, principal=principal.label)
            granted += 1
        except OSError:
            logger.warning(
                "owner heartbeat grant could not be recorded; task remains waiting"
            )
            try:
                from gideon.security.sel import sel

                sel().log_api_access(
                    caller=principal.label,
                    operation="heartbeat.owner_file_grant",
                    outcome="failed_closed",
                    source="grant_book",
                    resources="grant storage unavailable",
                )
            except Exception:
                logger.debug("heartbeat grant failure audit failed", exc_info=True)
            return granted
    return granted


def heartbeat_task_rows(content: str | None = None) -> list[dict[str, str | bool]]:
    """Return parsed current tasks with exact revisions and owner-grant state."""
    if content is None:
        try:
            content = heartbeat_path().read_text(encoding="utf-8")
        except OSError:
            return []
    entries = tuple(_task_lines(content))
    keys = _task_keys(entries)
    rows: list[dict[str, str | bool]] = []
    for entry, key in zip(entries, keys):
        task_content = _task_content(entry)
        rows.append(
            {
                "id": key,
                "text": entry.text,
                "destination": entry.destination,
                "revision": seal(task_content),
                "allowed": _HEARTBEAT_GRANTS.holds(key, task_content),
                "question": (
                    "Allow this exact heartbeat task to run unattended?\n\n"
                    f"Task: {entry.text}\nDestination: {entry.destination or 'dashboard'}"
                ),
            }
        )
    return rows


def allow_heartbeat_task(task_id: str, *, seen: str, principal: str) -> bool:
    """Allow one current task occurrence only if it still matches the reviewed revision."""
    with queue_lock(heartbeat_path()):
        try:
            content = heartbeat_path().read_text(encoding="utf-8")
        except OSError:
            return False
        entries = tuple(_task_lines(content))
        for entry, key in zip(entries, _task_keys(entries)):
            task_content = _task_content(entry)
            if key == task_id and seal(task_content) == seen:
                try:
                    _HEARTBEAT_GRANTS.give(key, task_content, principal=principal)
                    return True
                except OSError:
                    logger.warning(
                        "heartbeat grant could not be recorded; task remains waiting"
                    )
                return False
        return False


@dataclass
class _TaskDocument:
    path: Path
    entries: tuple[_TaskLine, ...]

    @classmethod
    def open(cls, path: Path) -> "_TaskDocument":
        with queue_lock(path):
            content = path.read_bytes().decode("utf-8") if path.exists() else ""
        return cls(path, tuple(_task_lines(content)))

    def settle(self, completed_keys: set[str]) -> None:
        """Remove only completed occurrences from the current file, preserving other bytes."""
        if not completed_keys:
            return
        with queue_lock(self.path):
            try:
                current = self.path.read_bytes().decode("utf-8")
            except FileNotFoundError:
                return
            except (OSError, UnicodeDecodeError):
                logger.warning(
                    "heartbeat queue unreadable during settlement", exc_info=True
                )
                return
            occurrences: Counter[str] = Counter()
            kept = []
            removed = set()
            for raw, task in _task_rows(current):
                if task is not None:
                    content = _task_content(task)
                    occurrences[content] += 1
                    key = f"heartbeat:{seal(content)}:{occurrences[content]}"
                    if key in completed_keys:
                        removed.add(key)
                        continue
                kept.append(raw)
            if removed:
                atomic_write_bytes(self.path, "".join(kept).encode("utf-8"))
        if completed_keys - removed:
            logger.info(
                "heartbeat completed occurrences edited or removed before settlement: %d",
                len(completed_keys - removed),
            )


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
        with queue_lock(location):
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

    async def run_tasks(self) -> dict[str, int]:
        """Run the owner-authorized HEARTBEAT.md task pass from its visible trigger."""
        from gideon.workspace.capabilities.identity.continuity import (
            heartbeat_turns_paused,
        )

        if heartbeat_turns_paused():
            return {"processed": 0, "completed": 0, "retained": 0}
        document = _TaskDocument.open(heartbeat_path())
        if not document.entries or not self._on_task:
            return {"processed": 0, "completed": 0, "retained": 0}
        keys = _task_keys(document.entries)
        authorized = [
            _HEARTBEAT_GRANTS.holds(key, _task_content(entry))
            for entry, key in zip(document.entries, keys)
        ]
        processed = sum(authorized)
        if not processed:
            return {"processed": 0, "completed": 0, "retained": len(document.entries)}
        self._processing = True
        try:
            logger.info("Heartbeat tasks: %d owner-approved task(s) found", processed)
            outcomes = await asyncio.gather(
                *(
                    (
                        self._run_one_task(entry.text, entry.destination)
                        if allowed
                        else asyncio.sleep(0, result=_KEEP_SENTINEL)
                    )
                    for entry, key, allowed in zip(document.entries, keys, authorized)
                ),
                return_exceptions=True,
            )
            completed = 0
            completed_keys: set[str] = set()
            for entry, key, outcome in zip(document.entries, keys, outcomes):
                if (
                    not isinstance(outcome, BaseException)
                    and not _should_keep(outcome)
                    and _HEARTBEAT_GRANTS.holds(key, _task_content(entry))
                ):
                    try:
                        _HEARTBEAT_GRANTS.revoke(key)
                        completed += 1
                        completed_keys.add(key)
                    except OSError:
                        logger.warning("completed heartbeat grant could not be cleared")
            document.settle(completed_keys)
            return {
                "processed": processed,
                "completed": completed,
                "retained": max(0, len(document.entries) - completed),
            }
        finally:
            self._processing = False

    async def _process_heartbeat_file(self) -> None:
        """Compatibility entrypoint; ordinary timer beats no longer execute user tasks."""
        await self.run_tasks()


def _should_keep(result: str | None) -> bool:
    return result is not None and _KEEP_RE.search(result) is not None


def strip_keep_sentinel(text: str) -> str:
    return _KEEP_RE.sub("", text).strip()


def is_keep_response(text: str | None) -> bool:
    return text is not None and _KEEP_SENTINEL in text.upper()


def _extract_tasks(content: str) -> list[tuple[str, str]]:
    return [(entry.text, entry.destination) for entry in _task_lines(content)]
