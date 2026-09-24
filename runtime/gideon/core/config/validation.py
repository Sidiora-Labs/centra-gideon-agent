"""Normalize a stored configuration and report rejected values without writing it."""

import hashlib
import json
import logging
from copy import deepcopy
from threading import RLock

import jsonschema

logger = logging.getLogger(__name__)
_DIRECT_READ_TOP_KEYS = {"providers", "meta", "slack"}
_RETIRED_FIELDS = {
    "": ("default_memory_store",),
    "agent": ("streaming", "model"),
    "inbox": ("quick_reactions", "message_provider"),
}
_JSON_TYPES = (
    (bool, "boolean"),
    (int, "integer"),
    (float, "number"),
    (str, "string"),
    (list, "array"),
    (dict, "object"),
    (type(None), "null"),
)


def _lookup_schema_node(schema: dict, dot_path: str) -> dict | None:
    node = schema
    for part in dot_path.split("."):
        node = node.get("properties", {}).get(part)
        if node is None:
            break
    return node


def _is_sensitive_path(schema: dict, dot_path: str) -> bool:
    return (
        (_lookup_schema_node(schema, dot_path) or {})
        .get("x-meta", {})
        .get("sensitive", False)
    )


def _mask_value(value: object, sensitive: bool) -> str:
    return '"***"' if sensitive else repr(value)


def _dot_path_from_json_path(path: list) -> str:
    return ".".join(map(str, path))


def _actual_type_name(value: object) -> str:
    return next(
        (name for kind, name in _JSON_TYPES if isinstance(value, kind)),
        type(value).__name__,
    )


def _apply_field_default(data: dict, dot_path: str) -> None:
    parts = dot_path.split(".")
    if len(parts) > 2:
        return
    owner = data if len(parts) == 1 else data.get(parts[0])
    if isinstance(owner, dict):
        owner.pop(parts[-1], None)


def _present(data: dict, path: str) -> bool:
    node = data
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return True


def consume_retired_keys(values: dict) -> None:
    for section, retired in _RETIRED_FIELDS.items():
        owner = values.get(section) if section else values
        if isinstance(owner, dict):
            for field in retired:
                owner.pop(field, None)


_VALIDATED_CONTENT: dict[str, dict] = {}
_VALIDATION_LOCK = RLock()


class ConfigurationSanitizer:
    def __init__(self, schema: dict, entries: list):
        self.schema = schema
        self.entries = entries

    def prepare(self, values: dict) -> None:
        consume_retired_keys(values)
        known = _DIRECT_READ_TOP_KEYS | {
            entry.path
            for entry in self.entries
            if "." not in entry.path and entry.path != "*"
        }
        unknown = sorted(set(values) - known)
        if unknown:
            logger.warning(
                "Config: unrecognized top-level keys: %s", ", ".join(unknown)
            )
        for entry in self.entries:
            if entry.deprecated and _present(values, entry.path):
                logger.warning(
                    "Config: deprecated field '%s': %s", entry.path, entry.help
                )
        agent = values.get("agent")
        if isinstance(agent, dict) and isinstance(agent.get("log_level"), str):
            agent["log_level"] = agent["log_level"].upper()

    def report(self, issue: jsonschema.ValidationError, path: str) -> None:
        display = _mask_value(issue.instance, _is_sensitive_path(self.schema, path))
        if issue.validator == "enum":
            logger.warning(
                "Config: enum violation at '%s': allowed values %s, got %s; using default",
                path,
                issue.schema.get("enum", []),
                display,
            )
        elif issue.validator == "type":
            logger.warning(
                "Config: type mismatch at '%s': expected %s, got %s (value: %s); using default",
                path,
                issue.schema.get("type", "unknown"),
                _actual_type_name(issue.instance),
                display,
            )
        else:
            logger.warning(
                "Config: validation error at '%s': %s; using default",
                path,
                issue.message,
            )

    def clean(self, values: dict) -> dict:
        self.prepare(values)
        validator = jsonschema.validators.validator_for(self.schema)(self.schema)
        for issue in validator.iter_errors(values):
            path = _dot_path_from_json_path(issue.absolute_path)
            if path:
                self.report(issue, path)
                _apply_field_default(values, path)
        return values


def _validate_config_data(data: dict) -> dict:
    from gideon.core.config.schema import JSON_SCHEMA, SCHEMA_REGISTRY

    content = json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
    key = hashlib.sha256(content).hexdigest()
    with _VALIDATION_LOCK:
        if key not in _VALIDATED_CONTENT:
            cleaned = ConfigurationSanitizer(JSON_SCHEMA, SCHEMA_REGISTRY).clean(
                deepcopy(data)
            )
            _VALIDATED_CONTENT[key] = cleaned
        data.clear()
        data.update(deepcopy(_VALIDATED_CONTENT[key]))
    return data
