"""Compile configuration records into schema documents and indexed field descriptions."""

import dataclasses
import typing
from dataclasses import dataclass, fields

from gideon.core.config.loader import AppConfig

_TYPE_MAP = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    set: "array",
    dict: "object",
}


@dataclass
class ConfigEntry:
    path: str
    kind: str
    type: str
    required: bool
    deprecated: bool
    sensitive: bool
    tags: list[str]
    label: str
    help: str
    has_children: bool
    enum_values: list | None
    default_value: object
    nullable: bool = False


def _python_type_to_json(tp: type) -> str:
    return _TYPE_MAP.get(typing.get_origin(tp) or tp, "string")


def _is_dataclass_type(tp: type) -> bool:
    return (
        isinstance(tp, type)
        and dataclasses.is_dataclass(tp)
        and typing.get_origin(tp) is None
    )


def _container_argument(
    annotation: type, container: type, position: int
) -> type | None:
    arguments = (
        typing.get_args(annotation)
        if typing.get_origin(annotation) is container
        else ()
    )
    return arguments[position] if len(arguments) > position else None


def _extract_item_type(tp: type) -> type | None:
    return _container_argument(tp, list, 0)


def _extract_value_type(tp: type) -> type | None:
    return _container_argument(tp, dict, 1)


def _optional_inner(tp: type) -> tuple[type, bool]:
    origin = typing.get_origin(tp)
    union = origin is typing.Union or getattr(origin, "__name__", "") == "UnionType"
    arguments = typing.get_args(tp) if union else ()
    candidates = tuple(item for item in arguments if item is not type(None))
    return (
        (candidates[0], True)
        if len(arguments) == 2 and len(candidates) == 1
        else (tp, False)
    )


def _json_type_for_value(tp: type) -> str | list[str]:
    base, nullable = _optional_inner(tp)
    name = _python_type_to_json(base)
    return [name, "null"] if nullable else name


def _default_for_field(f: dataclasses.Field) -> object:
    if f.default is not dataclasses.MISSING:
        return f.default
    factory = f.default_factory
    if factory is dataclasses.MISSING:
        return None
    result = factory()
    return sorted(result) if isinstance(result, set) else result


def _resolve_field_type(f: dataclasses.Field) -> type:
    from gideon.core.config import loader

    if not isinstance(f.type, str):
        return f.type
    try:
        return eval(f.type, vars(loader))
    except Exception:
        return str


class SchemaCompiler:
    def object(self, record: type) -> dict:
        return {
            "type": "object",
            "properties": {item.name: self.field(item) for item in fields(record)},
        }

    def value(self, annotation: type) -> dict:
        if _is_dataclass_type(annotation):
            return self.object(annotation)
        shape = _python_type_to_json(annotation)
        node = {"type": shape}
        containers = {
            "array": ("items", _extract_item_type, {}),
            "object": ("additionalProperties", _extract_value_type, True),
        }
        if shape in containers:
            key, argument, unspecified = containers[shape]
            child = argument(annotation)
            node[key] = (
                self.object(child)
                if _is_dataclass_type(child)
                else {"type": _json_type_for_value(child)} if child else unspecified
            )
        return node

    def field(self, item: dataclasses.Field) -> dict:
        node = self.value(_resolve_field_type(item))
        default = _default_for_field(item)
        if default is not None:
            node["default"] = default
        if item.metadata.get("enum") is not None:
            node["enum"] = item.metadata["enum"]
        fallbacks = {
            "label": item.name,
            "help": "",
            "tags": [],
            "sensitive": False,
            "deprecated": False,
        }
        node["x-meta"] = {
            key: item.metadata.get(key, fallback) for key, fallback in fallbacks.items()
        }
        return node


def _build_field_schema(f: dataclasses.Field) -> dict:
    return SchemaCompiler().field(f)


def _build_object_schema(cls: type) -> dict:
    return SchemaCompiler().object(cls)


def build_json_schema(root_cls: type) -> dict:
    return {
        **SchemaCompiler().object(root_cls),
        "$schema": "http://json-schema.org/draft-07/schema#",
    }


def _entry(node: dict, path: str) -> ConfigEntry:
    raw = node.get("type", "object")
    variants = raw if isinstance(raw, list) else [raw]
    base = next((kind for kind in variants if kind != "null"), "null")
    meta = node.get("x-meta", {})
    choices = node.get("enum")
    return ConfigEntry(
        path=path,
        kind="core",
        type=base,
        required=False,
        deprecated=meta.get("deprecated", False),
        sensitive=meta.get("sensitive", False),
        tags=list(meta.get("tags", [])),
        label=meta.get("label", path.rsplit(".", 1)[-1]),
        help=meta.get("help", ""),
        has_children=base in ("object", "array"),
        enum_values=list(choices) if choices is not None else None,
        default_value=node.get("default"),
        nullable=isinstance(raw, list) and "null" in raw,
    )


def _schema_walk(document: dict, prefix: str):
    pending = [(prefix, document)]
    while pending:
        path, node = pending.pop()
        if path:
            yield _entry(node, path)
        children = list(node.get("properties", {}).items())
        for key in ("additionalProperties", "items"):
            child = node.get(key)
            if isinstance(child, dict) and child.get("type"):
                children.append(("*", child))
        pending.extend(
            (f"{path}.{name}" if path else name, child)
            for name, child in reversed(children)
        )


def flatten_to_entries(json_schema: dict, prefix: str = "") -> list[ConfigEntry]:
    return list(_schema_walk(json_schema, prefix))


def _flatten_recurse(node: dict, path: str, out: list[ConfigEntry]) -> None:
    out.extend(_schema_walk(node, path))


_ENTRY_WIRE_NAMES = {
    "has_children": "hasChildren",
    "enum_values": "enumValues",
    "default_value": "defaultValue",
}


def config_entry_to_dict(entry: ConfigEntry) -> dict:
    return {
        _ENTRY_WIRE_NAMES.get(item.name, item.name): getattr(entry, item.name)
        for item in fields(entry)
        if item.name != "nullable" or entry.nullable
    }


JSON_SCHEMA = build_json_schema(AppConfig)
SCHEMA_REGISTRY = flatten_to_entries(JSON_SCHEMA)
