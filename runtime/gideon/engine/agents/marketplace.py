"""Portable agent records and local marketplace storage."""

import json
import logging
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_MAX_DESCRIPTION = 1024
_MAX_SYSTEM_PROMPT = 32_000


def _path_home_gideon():
    try:
        from gideon.core.config.loader import config_dir

        directory = config_dir()
    except Exception:
        directory = Path.home() / ".gideon"
    return directory


@dataclass
class AgentDefinition:
    name: str
    description: str = ""
    model: str = ""
    system_prompt: str = ""
    voice: str = ""
    natural_voice: bool = False
    skills: list[str] = field(default_factory=list)
    provider_entry: str = ""
    mcp_servers: dict[str, Any] = field(default_factory=dict)
    source: str = "local"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    provider: str = ""
    specialty: str = ""
    route_hints: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AgentDefinition":
        return _DefinitionCodec.decode(d, cls)

    def validate(self) -> list[str]:
        return _DefinitionCodec.errors(self)


class AgentMarketplace(ABC):
    """Abstract agent marketplace.  Implementations provide CRUD + list."""

    @abstractmethod
    def list(self) -> list[AgentDefinition]:
        """Return all agent definitions in this marketplace."""

    @abstractmethod
    def get(self, name: str) -> AgentDefinition | None:
        """Return the named agent definition, or ``None`` if not found."""

    @abstractmethod
    def create(self, defn: AgentDefinition) -> AgentDefinition:
        """Persist *defn* and return it (with any server-set fields populated)."""

    @abstractmethod
    def update(self, name: str, patch: dict[str, Any]) -> AgentDefinition:
        """Apply *patch* fields to the named agent and return the updated definition."""

    @abstractmethod
    def delete(self, name: str) -> None:
        """Delete the named agent.  Raises ``KeyError`` if not found."""

    @property
    def marketplace_type(self) -> str:
        """Short identifier for this marketplace type (e.g. ``'local'``)."""
        return "unknown"


class _DefinitionCodec:
    text_fields = (
        "name",
        "description",
        "model",
        "system_prompt",
        "voice",
        "provider_entry",
        "provider",
        "specialty",
        "route_hints",
    )
    writable = frozenset(text_fields) - {"name"}
    conversions: dict[str, Callable[[Any], Any]] = {
        "skills": lambda value: [str(item) for item in (value or [])],
        "mcp_servers": lambda value: dict(value or {}),
        "natural_voice": bool,
    }

    @classmethod
    def decode(cls, document, constructor: Callable[..., Any]):
        values: dict = {key: str(document.get(key, "")) for key in cls.text_fields}
        values.update(
            natural_voice=bool(document.get("natural_voice", False)),
            skills=list(document.get("skills") or []),
            mcp_servers=dict(document.get("mcp_servers") or {}),
            source=str(document.get("source", "local")),
            created_at=float(document.get("created_at") or time.time()),
            updated_at=float(document.get("updated_at") or time.time()),
        )
        return constructor(**values)

    @classmethod
    def apply(cls, record, patch):
        for key, value in patch.items():
            convert = cls.conversions.get(key)
            if convert is not None:
                setattr(record, key, convert(value))
            elif key in cls.writable:
                setattr(record, key, "" if value is None else str(value))
        record.updated_at = time.time()
        cls.require_valid(record)
        return record

    @staticmethod
    def errors(record):
        failures = []
        if not _NAME_RE.match(record.name):
            failures.append(
                "name must match ^[a-z0-9][a-z0-9-]{0,62}$ " f"(got {record.name!r})"
            )
        limits = (
            ("description", _MAX_DESCRIPTION),
            ("system_prompt", _MAX_SYSTEM_PROMPT),
            ("specialty", _MAX_DESCRIPTION),
            ("route_hints", _MAX_DESCRIPTION),
        )
        failures.extend(
            f"{key} exceeds {limit} chars"
            for key, limit in limits
            if len(getattr(record, key)) > limit
        )
        failures.extend(
            f"invalid skill name: {skill!r}"
            for skill in record.skills
            if not isinstance(skill, str) or ".." in skill or "/" in skill
        )
        return failures

    @staticmethod
    def require_valid(record):
        failures = record.validate()
        if failures:
            raise ValueError(f"Invalid agent definition: {'; '.join(failures)}")


class _AgentShelf:
    def __init__(self, root):
        self.root = root

    def files(self):
        for directory in sorted(self.root.iterdir()):
            path = directory / "agent.json"
            if directory.is_dir() and path.is_file():
                yield directory.name, path

    @staticmethod
    def read(path):
        return AgentDefinition.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def reserve(self, name):
        directory = self.root / name
        if directory.exists():
            raise FileExistsError(f"Agent '{name}' already exists")
        directory.mkdir(parents=True, exist_ok=False)
        return directory / "agent.json"

    def remove(self, name):
        directory = self.root / name
        if not _NAME_RE.match(name) or not directory.is_dir():
            raise KeyError(f"Agent '{name}' not found")
        import shutil

        shutil.rmtree(directory)


class LocalAgentMarketplace(AgentMarketplace):
    def __init__(self, base_dir: Path | None = None) -> None:
        self._base = base_dir or (_path_home_gideon() / "agents")

    @property
    def marketplace_type(self) -> str:
        return "local"

    def _agent_path(self, name: str) -> Path:
        return self._base.joinpath(name, "agent.json")

    def _ensure_base(self) -> None:
        self._base.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[AgentDefinition]:
        self._ensure_base()
        result = []
        for name, path in _AgentShelf(self._base).files():
            try:
                result.append(_AgentShelf.read(path))
            except Exception as exc:
                logger.warning("Skipping malformed agent %s: %s", name, exc)
        return result

    def get(self, name: str) -> AgentDefinition | None:
        if _NAME_RE.match(name):
            path = self._agent_path(name)
            if path.is_file():
                try:
                    return _AgentShelf.read(path)
                except Exception as exc:
                    logger.warning("Failed to read agent %s: %s", name, exc)
        return None

    def create(self, defn: AgentDefinition) -> AgentDefinition:
        _DefinitionCodec.require_valid(defn)
        self._ensure_base()
        path = _AgentShelf(self._base).reserve(defn.name)
        defn.created_at = defn.updated_at = time.time()
        self._write(path, defn)
        logger.info("Created local agent: %s", defn.name)
        return defn

    def update(self, name: str, patch: dict[str, Any]) -> AgentDefinition:
        existing = self.get(name)
        if existing is None:
            raise KeyError(f"Agent '{name}' not found")
        edited = _DefinitionCodec.apply(existing, patch)
        self._write(self._agent_path(name), edited)
        logger.info("Updated local agent: %s", name)
        return edited

    def delete(self, name: str) -> None:
        _AgentShelf(self._base).remove(name)
        logger.info("Deleted local agent: %s", name)

    @staticmethod
    def _write(path: Path, defn: AgentDefinition) -> None:
        from gideon.core.atomic_write import atomic_write

        atomic_write(path, json.dumps(defn.to_dict(), indent=2, ensure_ascii=False))


class AgentMarketplaceRegistry:
    def __init__(self) -> None:
        self._marketplaces: dict[str, AgentMarketplace] = {}

    def register(self, name: str, marketplace: AgentMarketplace) -> None:
        previous = name in self._marketplaces
        self._marketplaces.update({name: marketplace})
        if previous:
            logger.debug("AgentMarketplaceRegistry: overwriting %r", name)

    def get(self, name: str) -> AgentMarketplace:
        try:
            result = self._marketplaces[name]
        except KeyError:
            result = None
        if result is None:
            raise KeyError(f"No agent marketplace registered as {name!r}")
        return result

    def names(self) -> list[str]:
        return sorted(self._marketplaces.keys())

    def info(self) -> list[dict[str, str]]:
        return [
            dict(name=name, type=self._marketplaces[name].marketplace_type)
            for name in self.names()
        ]


_DEFAULT_REGISTRY: AgentMarketplaceRegistry | None = None


def get_default_agent_registry() -> AgentMarketplaceRegistry:
    global _DEFAULT_REGISTRY
    existing = _DEFAULT_REGISTRY
    if existing is not None:
        return existing
    _DEFAULT_REGISTRY = AgentMarketplaceRegistry()
    return _DEFAULT_REGISTRY


get_default_agent_registry().register("local", LocalAgentMarketplace())


def create_provider(config=None):
    return None
