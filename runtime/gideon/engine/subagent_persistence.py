"""Per-agent state, result streams and abnormal-exit records in the active home."""

from __future__ import annotations

import json
import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from gideon.core.atomic_write import atomic_write
from gideon.core.config import loader as config_loader
from gideon.integrations.llm.cleanup import _is_safe_path

logger = logging.getLogger(__name__)


def config_dir() -> Path:
    return config_loader.config_dir()


def _path_home_gideon():
    try:
        from gideon.core.config.loader import config_dir as active_home

        return active_home()
    except Exception:
        return Path.home().joinpath(".gideon")


def _subagents_dir() -> Path:
    return config_dir().joinpath("subagents")


def _agent_dir(agent_id: str) -> Path:
    if (
        not agent_id
        or agent_id == "."
        or any(marker in agent_id for marker in ("..", "/", "\\", "\0"))
    ):
        raise ValueError(f"Invalid agent_id: {agent_id!r}")
    root = _subagents_dir()
    candidate, boundary = (root / agent_id).resolve(), root.resolve()
    if candidate == boundary or not candidate.is_relative_to(boundary):
        raise ValueError(f"Path traversal blocked for agent_id: {agent_id!r}")
    return candidate


@dataclass(frozen=True)
class AgentFiles:
    directory: Path

    def path(self, filename: str) -> Path:
        return self.directory / filename

    def read(self, filename: str):
        return json.loads(self.path(filename).read_text(encoding="utf-8"))

    def write(self, filename: str, value: dict) -> None:
        _atomic_write(self.path(filename), value)

    def start(
        self, agent_id: str, task: str, agent: str, parent_session: str, max_turns: int
    ) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        state = dict(
            id=agent_id,
            task=task,
            agent=agent,
            parent_session=parent_session,
            started=time.time(),
            max_turns=max_turns,
            status="running",
            pid=None,
            turns=0,
            last_tool="",
            updated_at=time.time(),
        )
        self.write("state.json", state)
        return self.directory

    def merge(self, fields: dict) -> None:
        try:
            state = self.read("state.json")
        except (OSError, json.JSONDecodeError):
            logger.debug("agent state unavailable: %s", self.directory.name)
            return
        state.update(fields)
        state["updated_at"] = time.time()
        self.write("state.json", state)


def create_agent_folder(
    agent_id: str,
    *,
    task: str = "",
    agent: str = "",
    parent_session: str = "",
    max_turns: int = 0,
) -> Path:
    return AgentFiles(_agent_dir(agent_id)).start(
        agent_id, task, agent, parent_session, max_turns
    )


def read_state(agent_id: str) -> dict | None:
    try:
        directory = _agent_dir(agent_id)
    except ValueError:
        return None
    try:
        return AgentFiles(directory).read("state.json")
    except (OSError, json.JSONDecodeError):
        return None


def update_state(agent_id: str, **fields: object) -> None:
    AgentFiles(_agent_dir(agent_id)).merge(fields)


def write_result_chunk(agent_id: str, text: str) -> None:
    path = _agent_dir(agent_id) / "result.txt"
    try:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(text)
    except OSError:
        logger.debug("agent result append failed: %s", agent_id, exc_info=True)


def _check_result_available(path: Path) -> bool:
    try:
        size = path.stat().st_size
    except OSError:
        return False
    return size > 0


def write_tombstone(
    agent_id: str, *, cause: str, recovery_action: str, **extra: object
) -> None:
    files = AgentFiles(_agent_dir(agent_id))
    state = read_state(agent_id) or {}
    tombstone = {
        "id": agent_id,
        **{key: state.get(key, "") for key in ("task", "agent", "parent_session")},
        "started": state.get("started"),
        "died": time.time(),
        "cause": cause,
        "recovery_action": recovery_action,
        "result_available": _check_result_available(files.path("result.txt")),
        "result_path": str(files.path("result.txt")),
        **extra,
    }
    try:
        files.write("tombstone.json", tombstone)
    except OSError:
        logger.warning("agent tombstone write failed: %s", agent_id, exc_info=True)


def delete_agent_folder(agent_id: str) -> None:
    shutil.rmtree(_agent_dir(agent_id), ignore_errors=True)


def _agent_folders() -> list[Path]:
    try:
        return [path for path in sorted(_subagents_dir().iterdir()) if path.is_dir()]
    except OSError:
        return []


def list_orphans() -> list[dict]:
    states = []
    for directory in _agent_folders():
        if (directory / "tombstone.json").exists():
            continue
        state = read_state(directory.name)
        if state is not None:
            states.append(state)
        else:
            logger.debug("agent orphan state is corrupt: %s", directory.name)
    return states


@dataclass(frozen=True)
class TombstonePruner:
    cutoff: float

    def cleanup_transcript(self, files: AgentFiles, tombstone: dict) -> None:
        try:
            state = read_state(files.directory.name)
            session = tombstone.get("session_id") or (
                state.get("session_id", "") if state else ""
            )
            if session:
                _cleanup_session_files_sync(session)
        except Exception:
            logger.debug(
                "agent transcript cleanup failed: %s",
                files.directory.name,
                exc_info=True,
            )

    def prune(self, directory: Path) -> bool:
        files = AgentFiles(directory)
        if not files.path("tombstone.json").exists():
            return False
        try:
            record = files.read("tombstone.json")
            if record.get("died", 0) < self.cutoff:
                self.cleanup_transcript(files, record)
                shutil.rmtree(directory, ignore_errors=True)
                return True
        except (json.JSONDecodeError, OSError):
            logger.debug("agent tombstone is corrupt: %s", directory.name)
        return False


def prune_stale_tombstones(max_age_days: int = 7) -> int:
    pruning = TombstonePruner(time.time() - max_age_days * 86400)
    return sum(pruning.prune(directory) for directory in _agent_folders())


def _cleanup_session_files_sync(session_id: str) -> None:
    if not session_id or session_id in (".", ".."):
        return
    try:
        root = _path_home_gideon() / "sessions"
        for suffix in (".json", ".jsonl"):
            path = root / f"{session_id}{suffix}"
            if not _is_safe_path(path, root):
                logger.error(
                    "agent transcript path escapes session directory: %s", path
                )
                return
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "agent transcript removal failed: %s", path, exc_info=True
                )
    except Exception:
        logger.warning("agent transcript cleanup failed: %s", session_id, exc_info=True)


def _atomic_write(path: Path, data: dict) -> None:
    encoded = json.dumps(data, ensure_ascii=False)
    atomic_write(path, encoded, fsync=True)
