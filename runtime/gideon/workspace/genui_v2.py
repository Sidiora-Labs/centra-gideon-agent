"""Canonical schema, validation, and authoring guidance for native GenUI v2."""

from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import re
from typing import Any

from jsonschema import Draft202012Validator


_CATALOG_PATH = Path(__file__).with_name("genui_v2_catalog.json")
_SCHEMA: dict[str, Any] = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
Draft202012Validator.check_schema(_SCHEMA)
_VALIDATOR = Draft202012Validator(_SCHEMA)
_COMPONENTS = {entry["name"]: entry for entry in _SCHEMA["x-components"]}
_PROP_VALIDATORS = {
    name: Draft202012Validator(
        {
            "$schema": _SCHEMA["$schema"],
            "$defs": _SCHEMA["$defs"],
            "$ref": entry["propsSchema"],
        }
    )
    for name, entry in _COMPONENTS.items()
}
_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,127}\Z")
_DANGEROUS_KEYS = {"__proto__", "constructor", "prototype"}
_LAYOUT_COMPONENTS = {"Stack", "Card"}
_MAX_DEPTH = 16
_MAX_COLLECTION = 128
_MAX_STRING = 4096
_MAX_BYTES = 65536


def genui_v2_schema() -> dict[str, Any]:
    """Return the packaged Draft 2020-12 contract without exposing shared state."""

    return deepcopy(_SCHEMA)


def _bounded_json(value: Any, depth: int = 0) -> bool:
    if depth > _MAX_DEPTH:
        return False
    if value is None or isinstance(value, bool):
        return True
    if isinstance(value, str):
        return len(value) <= _MAX_STRING
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return len(value) <= _MAX_COLLECTION and all(
            _bounded_json(item, depth + 1) for item in value
        )
    if isinstance(value, dict):
        return len(value) <= _MAX_COLLECTION and all(
            isinstance(key, str)
            and len(key) <= _MAX_STRING
            and key not in _DANGEROUS_KEYS
            and _bounded_json(item, depth + 1)
            for key, item in value.items()
        )
    return False


def _bounded_encoding(value: Any) -> bool:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError):
        return False
    return len(encoded) <= _MAX_BYTES


def _component_semantics(kind: str, props: dict[str, Any]) -> bool:
    if kind == "Compare":
        item_ids = [item["id"] for item in props["items"]]
        return len(item_ids) == len(set(item_ids))
    return True


def validate_candidate(value: Any) -> dict[str, Any] | None:
    """Validate one complete grounded leaf candidate used by the chooser."""

    if not _bounded_json(value) or not _bounded_encoding(value):
        return None
    if not isinstance(value, dict) or set(value) != {"id", "type", "props"}:
        return None
    identity, kind, props = value["id"], value["type"], value["props"]
    if not isinstance(identity, str) or _IDENTIFIER.fullmatch(identity) is None:
        return None
    if not isinstance(kind, str) or kind not in _COMPONENTS or kind in _LAYOUT_COMPONENTS:
        return None
    if not isinstance(props, dict) or not _PROP_VALIDATORS[kind].is_valid(props):
        return None
    if not _component_semantics(kind, props):
        return None
    return value


def _valid_tree(root: str, elements: dict[str, dict[str, Any]]) -> bool:
    if root not in elements:
        return False
    seen: set[str] = set()
    active: set[str] = set()

    def visit(identity: str) -> bool:
        if identity not in elements or identity in active or identity in seen:
            return False
        active.add(identity)
        element = elements[identity]
        for child in element.get("children", []):
            if not visit(child):
                return False
        active.remove(identity)
        seen.add(identity)
        return True

    return visit(root) and len(seen) == len(elements)


def _valid_state(
    state: dict[str, dict[str, Any]], elements: dict[str, dict[str, Any]]
) -> bool:
    for identity, local in state.items():
        element = elements.get(identity)
        if element is None:
            return False
        kind = element["type"]
        if kind == "Compare":
            if not set(local) <= {"selected", "filter"}:
                return False
            selected = local.get("selected")
            if selected is not None and selected not in {
                item["id"] for item in element["props"]["items"]
            }:
                return False
        elif kind in {"Form", "ActionPreview"}:
            if not set(local) <= {"fields"}:
                return False
            fields = local.get("fields", {})
            if not set(fields) <= set(element["props"].get("fields", [])):
                return False
        else:
            return False
    return True


def _valid_references(elements: dict[str, dict[str, Any]]) -> bool:
    for element in elements.values():
        kind = element["type"]
        if not _component_semantics(kind, element["props"]):
            return False
        if kind == "ActionPreview":
            source = element["props"].get("selectionFrom")
            if source is not None and (
                source not in elements or elements[source]["type"] != "Compare"
            ):
                return False
    return True


def validate_genui_v2(value: Any) -> dict[str, Any] | None:
    """Validate the exact bounded envelope plus tree, state, and cross references."""

    if not _bounded_json(value) or not _bounded_encoding(value):
        return None
    if not isinstance(value, dict) or not _VALIDATOR.is_valid(value):
        return None
    elements = value["elements"]
    if not _valid_references(elements):
        return None
    if not _valid_tree(value["root"], elements):
        return None
    if not _valid_state(value["state"], elements):
        return None
    return value


def _type_hint(spec: dict[str, Any]) -> str:
    if "enum" in spec:
        return "|".join(str(value) for value in spec["enum"])
    if "$ref" in spec:
        name = spec["$ref"].rsplit("/", 1)[-1]
        if name in {"Text", "SafeKey"}:
            return "string"
        if name == "Identifier":
            return "id"
        if name == "JsonObject":
            return "object"
        return _type_hint(_SCHEMA["$defs"][name])
    if "oneOf" in spec:
        return "|".join(_type_hint(choice) for choice in spec["oneOf"])
    kind = spec.get("type", "value")
    if kind == "array":
        return f"[{_type_hint(spec['items'])}]"
    if kind == "object":
        required = set(spec.get("required", []))
        fields = ",".join(
            f"{key}{'' if key in required else '?'}:{_type_hint(field)}"
            for key, field in spec.get("properties", {}).items()
        )
        return "{" + fields + "}"
    if kind == "string" and str(spec.get("pattern", "")).startswith("^https?"):
        return "http(s)-url"
    return str(kind)


def genui_v2_prompt() -> str:
    """Derive compact model guidance from the packaged component catalog."""

    lines = [
        "Native GenUI v2: emit one JSON object with schemaVersion=2, id, revision>=1, root, elements, and state.",
        "Each element is {type, props, children?}. Only Stack and Card own children; every other component is a leaf.",
        "State keys are element IDs: Compare uses selected/filter; Form and ActionPreview use fields.",
        "Components:",
    ]
    for entry in _SCHEMA["x-components"]:
        name = entry["name"]
        props = _SCHEMA["$defs"][f"{name}Props"]
        required = set(props.get("required", []))
        signature = ", ".join(
            f"{key}{'' if key in required else '?'}:{_type_hint(spec)}"
            for key, spec in props.get("properties", {}).items()
        )
        lines.append(f"  {name}({signature}) — {entry['description']}")
    return "\n".join(lines)
