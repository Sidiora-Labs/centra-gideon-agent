"""Configuration document reads and writes with opaque-section preservation."""

from __future__ import annotations

import json
from dataclasses import asdict, fields, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OPAQUE_SECTIONS = ("providers", "use_cases", "slack")


def read_configuration(path: Path, logger: Any) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as failure:
        logger.warning("Failed to load config from %s: %s", path, failure)
        return None
    if isinstance(values, dict):
        return values
    logger.warning("Config is not a JSON object, using defaults")
    return None


def configuration_values(configuration: Any) -> dict[str, Any]:
    result = {}
    for item in fields(configuration):
        value = getattr(configuration, item.name)
        if is_dataclass(value) and not isinstance(value, type):
            result[item.name] = asdict(value)
        elif item.name in ("agents", "memory_stores"):
            result[item.name] = {key: asdict(record) for key, record in value.items()}
        else:
            result[item.name] = value
    return result


def _opaque_values(path: Path, error_type: type[Exception]) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
        previous = json.loads(text) if text.strip() else {}
    except (OSError, json.JSONDecodeError) as failure:
        raise error_type(
            f"refusing to save config: {path} exists but could not be read, so the "
            f"providers/use_cases/slack blocks it holds cannot be preserved ({failure})"
        ) from failure
    if not isinstance(previous, dict):
        raise error_type(
            f"refusing to save config: {path} holds {type(previous).__name__}, not an object"
        )
    return {key: previous[key] for key in OPAQUE_SECTIONS if key in previous}


def write_configuration(
    path: Path, values: dict[str, Any], error_type: type[Exception]
) -> None:
    from gideon import __version__
    from gideon.core.atomic_write import atomic_write

    protected = _opaque_values(path, error_type)
    document = {
        "meta": {
            "lastTouchedVersion": __version__,
            "lastTouchedAt": datetime.now(timezone.utc).isoformat(),
        },
        **values,
        **protected,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(document, indent=2) + "\n")
