"""Composable readers for the configuration wire format."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any


class ConfigInput:
    def __init__(self, values: dict[str, Any]):
        self.values = values
        self._sections: dict[tuple[str, ...], dict[str, Any]] = {(): values}

    def section(self, *path: str) -> dict[str, Any]:
        if path not in self._sections:
            parent = self.section(*path[:-1])
            candidate = parent.get(path[-1])
            self._sections[path] = candidate if isinstance(candidate, dict) else {}
        return self._sections[path]

    def get(self, path: tuple[str, ...], fallback: Any) -> Any:
        parent = self.section(*path[:-1])
        key = path[-1]
        return parent[key] if key in parent else deepcopy(fallback)


@dataclass(frozen=True, slots=True)
class Value:
    path: tuple[str, ...]
    fallback: Any = None
    convert: Callable[[Any], Any] | None = None

    def read(self, document: ConfigInput) -> Any:
        value = document.get(self.path, self.fallback)
        return value if self.convert is None else self.convert(value)


@dataclass(frozen=True, slots=True)
class Derived:
    evaluate: Callable[[ConfigInput], Any]

    def read(self, document: ConfigInput) -> Any:
        return self.evaluate(document)


@dataclass(frozen=True, slots=True)
class Record:
    model: str
    fields: Mapping[str, Value | Derived]

    def read(self, document: ConfigInput, constructor: Callable[..., Any]) -> Any:
        values = {name: reader.read(document) for name, reader in self.fields.items()}
        return constructor(**values)


def mapped_fields(tree: Any) -> set[str]:
    """Read the literal field declarations without importing the inspected project."""
    import ast

    names: set = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "Record" or len(node.args) < 2:
            continue
        if not isinstance(node.args[1], ast.Dict):
            continue
        names.update(
            key.value
            for key in node.args[1].keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        )
    return names
