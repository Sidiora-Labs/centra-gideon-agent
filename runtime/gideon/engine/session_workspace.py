"""On-disk session journals and delegated-agent result documents."""

import json
import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from gideon.core.atomic_write import atomic_write
from gideon.core.config import loader as config_loader

logger = logging.getLogger(__name__)
_SESSIONS_DIR = "sessions"
_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9_.:-]+$")


def config_dir() -> Path:
    return config_loader.config_dir()


def _validate_id(value: str, label: str = "id") -> str:
    valid = value and ".." not in value and _SAFE_ID_RE.fullmatch(value)
    if not valid:
        raise ValueError(f"Invalid {label}: {value!r}")
    return value


@dataclass(frozen=True)
class _SessionFiles:
    root: Path

    @classmethod
    def locate(cls, identifier: str) -> "_SessionFiles":
        base = config_dir()
        name = _validate_id(identifier, "session_id")
        return cls(base.joinpath(_SESSIONS_DIR, name))

    def ensure(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root

    def journal(self) -> Path:
        return self.ensure().joinpath("history.jsonl")

    def result(self, identifier: str) -> Path:
        base = self.ensure()
        name = _validate_id(identifier, "agent_id")
        return base.joinpath(f"agent-{name}.md")

    def replay(self) -> list[dict]:
        location = self.journal()
        if not location.exists():
            return []
        recovered = []
        with location.open(encoding="utf-8") as source:
            for physical in source:
                for line in physical.splitlines():
                    value = line.strip()
                    if not value:
                        continue
                    try:
                        recovered.append(json.loads(value))
                    except json.JSONDecodeError:
                        logger.warning(
                            "Skipping malformed history line in %s", location
                        )
        return recovered

    def catalog(self) -> list[dict]:
        if not self.root.exists():
            return []
        return [
            {
                "agent_id": entry.stem[len("agent-") :],
                "path": str(entry),
                "size": entry.stat().st_size,
            }
            for entry in sorted(self.root.glob("agent-*.md"))
        ]

    def remove(self) -> bool:
        if not self.root.exists():
            return False
        shutil.rmtree(self.root, ignore_errors=True)
        return True


def _append(path: Path, text: str) -> Path:
    with path.open("a", encoding="utf-8") as destination:
        destination.write(text)
    return path


def workspace_dir(session_id: str) -> Path:
    return _SessionFiles.locate(session_id).ensure()


def history_path(session_id: str) -> Path:
    return _SessionFiles.locate(session_id).journal()


def append_history(session_id: str, entry: dict) -> None:
    destination = history_path(session_id)
    encoded = json.dumps(entry, ensure_ascii=False)
    _append(destination, encoded + "\n")


def load_history(session_id: str) -> list[dict]:
    return _SessionFiles.locate(session_id).replay()


def result_path(session_id: str, agent_id: str) -> Path:
    return _SessionFiles.locate(session_id).result(agent_id)


def write_result(session_id: str, agent_id: str, content: str) -> Path:
    destination = result_path(session_id, agent_id)
    atomic_write(destination, content)
    return destination


def append_result(session_id: str, agent_id: str, chunk: str) -> Path:
    return _append(result_path(session_id, agent_id), chunk)


def read_result(session_id: str, agent_id: str) -> str:
    location = result_path(session_id, agent_id)
    try:
        return location.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def list_results(session_id: str) -> list[dict]:
    return _SessionFiles.locate(session_id).catalog()


def cleanup(session_id: str) -> None:
    if _SessionFiles.locate(session_id).remove():
        logger.info("Cleaned up session workspace %s", session_id)
