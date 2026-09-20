"""Configuration document reads and writes with opaque-section preservation."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import asdict, fields, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CREDENTIAL_MASK = "[REDACTED: credential]"
_CREDENTIAL_FIELD = re.compile(
    r"(?:^|_)(?:api_?key|secret(?:_key)?|access_token|refresh_token|session_token|"
    r"bot_token|app_token|password|passwd|credential|private_key|bearer_token|"
    r"signing_key|webhook_secret)(?:$|_)",
    re.IGNORECASE,
)


def _credential_field(name: object) -> bool:
    return bool(_CREDENTIAL_FIELD.search(str(name)))


def redact_configuration(values: dict[str, Any]) -> dict[str, Any]:
    """Return the config's display form without credential values."""

    def redact(value: Any, name: object = "") -> Any:
        if isinstance(value, dict):
            return {key: redact(item, key) for key, item in value.items()}
        if isinstance(value, list):
            return [redact(item) for item in value]
        return CREDENTIAL_MASK if value and _credential_field(name) else deepcopy(value)

    return redact(values)


def preserve_configuration_credentials(
    incoming: dict[str, Any], existing: dict[str, Any]
) -> dict[str, Any]:
    """Restore omitted or display-masked credential leaves from the stored config."""

    def preserve(new: Any, old: Any) -> Any:
        if isinstance(new, dict) and isinstance(old, dict):
            result = deepcopy(new)
            for key, old_value in old.items():
                if isinstance(old_value, (dict, list)):
                    if key in result:
                        result[key] = preserve(result[key], old_value)
                elif _credential_field(key) and old_value:
                    if key not in result or result[key] == CREDENTIAL_MASK:
                        result[key] = deepcopy(old_value)
            return result
        if isinstance(new, list) and isinstance(old, list):
            return [
                preserve(item, old[index]) if index < len(old) else deepcopy(item)
                for index, item in enumerate(new)
            ]
        return deepcopy(new)

    return preserve(incoming, existing)


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


def merge_configuration(
    path: Path, values: dict[str, Any], error_type: type[Exception]
) -> dict[str, Any]:
    """Merge a serialized config over every top-level value already on disk."""
    if not path.exists():
        return values
    try:
        text = path.read_text(encoding="utf-8")
        previous = json.loads(text) if text.strip() else {}
    except (OSError, json.JSONDecodeError) as failure:
        raise error_type(
            f"refusing to save config: {path} exists but could not be read, so the "
            f"values it holds cannot be preserved ({failure})"
        ) from failure
    if not isinstance(previous, dict):
        raise error_type(
            f"refusing to save config: {path} holds {type(previous).__name__}, not an object"
        )
    return {**previous, **values}


def write_configuration(
    path: Path, values: dict[str, Any], error_type: type[Exception]
) -> None:
    from gideon import __version__
    from gideon.core.atomic_write import atomic_write

    document = merge_configuration(path, values, error_type)
    document.setdefault(
        "meta",
        {
            "lastTouchedVersion": __version__,
            "lastTouchedAt": datetime.now(timezone.utc).isoformat(),
        },
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(document, indent=2) + "\n")
