"""Storage and retention policies for delegated work and its visible output."""

import heapq
import logging
import shutil
import time
from pathlib import Path

from gideon.core.atomic_write import atomic_write
from gideon.core.config import loader as config_loader

logger = logging.getLogger(__name__)
RESULT_FILE_MAX_BYTES = 512_000
STREAMING_TEXT_MAX_CHARS = 50_000
RESULT_SUMMARY_WORDS = 200
SESSION_MAX_BYTES = 5_000_000
HISTORY_MAX_ENTRIES = 500
SESSION_MAX_AGE_SECS = 86400 * 7
MAX_RETAINED_AGENTS = 50


def config_dir() -> Path:
    return config_loader.config_dir()


class _ResultEnvelope:
    def __init__(self, path, size):
        self.path, self.size = path, size

    def persist(self):
        body = self.path.read_text(encoding="utf-8", errors="replace")
        start = RESULT_FILE_MAX_BYTES // 5
        finish = RESULT_FILE_MAX_BYTES - start - 100
        pieces = (
            body[:start],
            f"\n\n[...truncated {self.size - RESULT_FILE_MAX_BYTES:,} bytes...]\n\n",
            body[-finish:],
        )
        atomic_write(self.path, "".join(pieces))
        logger.info(
            "Truncated %s from %d to %d bytes",
            self.path.name,
            self.size,
            RESULT_FILE_MAX_BYTES,
        )


def cap_result_file(path: Path) -> bool:
    try:
        size = path.stat().st_size
    except OSError:
        return False
    over = size > RESULT_FILE_MAX_BYTES
    if over:
        _ResultEnvelope(path, size).persist()
    return over


def cap_streaming_text(text: str) -> str:
    if len(text) > STREAMING_TEXT_MAX_CHARS:
        retained = slice(-STREAMING_TEXT_MAX_CHARS + 20, None)
        return "…(truncated)\n" + text[retained]
    return text


def cap_history(entries: list[dict]) -> list[dict]:
    return (
        entries[-HISTORY_MAX_ENTRIES:]
        if len(entries) > HISTORY_MAX_ENTRIES
        else entries
    )


def check_session_budget(session_dir: Path) -> bool:
    occupied = 0
    for item in session_dir.glob("agent-*.md"):
        if item.is_file():
            occupied += item.stat().st_size
    return occupied > SESSION_MAX_BYTES


def evict_completed_agents(
    agents: dict, max_retained: int = MAX_RETAINED_AGENTS
) -> int:
    eligible = {key: agent for key, agent in agents.items() if agent.done}
    remove = len(eligible) - max_retained
    if remove <= 0:
        return 0
    oldest = heapq.nsmallest(remove, eligible, key=lambda key: eligible[key].started)
    for key in oldest:
        agents.pop(key)
    logger.info("Evicted %d completed sub-agents (kept %d)", remove, max_retained)
    return remove


class _WorkspaceRetention:
    def __init__(self, root, now):
        self.root, self.now = root, now

    def prune(self):
        removed = 0
        for candidate in self.root.iterdir():
            if not candidate.is_dir():
                continue
            try:
                children = list(candidate.iterdir())
                modified = max(
                    (child.stat().st_mtime for child in children),
                    default=candidate.stat().st_mtime,
                )
                if self.now - modified > SESSION_MAX_AGE_SECS:
                    shutil.rmtree(candidate, ignore_errors=True)
                    removed += 1
            except OSError:
                continue
        return removed


def cleanup_stale_sessions() -> int:
    root = config_dir().joinpath("sessions")
    if not root.exists():
        return 0
    removed = _WorkspaceRetention(root, time.time()).prune()
    if removed:
        logger.info("Cleaned up %d stale session workspaces", removed)
    return removed
