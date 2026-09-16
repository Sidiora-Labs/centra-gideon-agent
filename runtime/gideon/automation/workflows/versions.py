"""Immutable definition snapshots, movable pins and structural comparisons."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from gideon.automation.workflows import store
from gideon.automation.workflows.models import valid_name
from gideon.core.atomic_write import atomic_write

logger = logging.getLogger(__name__)
SOURCE_USER = "user"
SOURCE_REFINER = "refiner"


def _versions_root():
    return store.workflows_dir() / "versions"


def _template_dir(name: str):
    return _versions_root() / name


def _pin_path(name: str):
    return _template_dir(name) / "pinned.json"


def _version_path(name: str, version: int):
    return _template_dir(name) / f"v{version:03d}.json"


@dataclass
class VersionRecord:
    version: int
    spec: dict[str, Any]
    source: str = SOURCE_USER
    created_at: str = ""
    ops: list[dict[str, Any]] = field(default_factory=list)
    run_ids: list[str] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            name: getattr(self, name)
            for name in (
                "version",
                "spec",
                "source",
                "created_at",
                "ops",
                "run_ids",
                "note",
            )
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "VersionRecord":
        fields = {
            "version": int(d.get("version", 0) or 0),
            "spec": dict(d.get("spec") or {}),
        }
        for name, fallback in (
            ("source", SOURCE_USER),
            ("created_at", ""),
            ("note", ""),
        ):
            fields[name] = str(d.get(name, fallback) or fallback)
        fields["ops"] = [
            operation
            for operation in (d.get("ops") or [])
            if isinstance(operation, dict)
        ]
        fields["run_ids"] = list(map(str, d.get("run_ids") or []))
        return cls(**fields)


class VersionJournal:
    def __init__(self, name: str):
        self.name = name

    def numbers(self) -> list[int]:
        directory = _template_dir(self.name)
        if not directory.is_dir():
            return []

        def number(filename: str):
            if filename.startswith("v") and filename.endswith(".json"):
                try:
                    return int(filename[1:-5])
                except ValueError:
                    return None
            return None

        parsed = (number(entry.name) for entry in directory.iterdir())
        return sorted(value for value in parsed if value is not None)

    def read(self, version: int) -> VersionRecord | None:
        target = _version_path(self.name, version)
        if target.is_file():
            try:
                document = json.loads(target.read_text(encoding="utf-8"))
                return VersionRecord.from_dict(document)
            except (OSError, ValueError):
                logger.warning(
                    "versions: unreadable %s v%s", self.name, version, exc_info=True
                )
        return None

    @staticmethod
    def write(path, document: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, json.dumps(document, indent=2, ensure_ascii=False))

    def append(self, spec, source, ops, run_ids, note) -> int:
        if not valid_name(self.name):
            raise ValueError(f"{self.name!r} is not a valid definition name")
        requested = int(spec.get("version", 0) or 0)
        number = requested if requested > 0 else latest_version(self.name) + 1
        target = _version_path(self.name, number)
        if not target.exists():
            record = VersionRecord(
                version=number,
                spec=dict(spec),
                source=source,
                created_at=str(spec.get("updated_at") or spec.get("created_at") or ""),
                ops=[
                    operation
                    for operation in (ops or [])
                    if isinstance(operation, dict)
                ],
                run_ids=list(map(str, run_ids or [])),
                note=note,
            )
            self.write(target, record.to_dict())
        _write_pin(self.name, number)
        return number

    def pin(self) -> int | None:
        marker = _pin_path(self.name)
        if marker.is_file():
            try:
                requested = int(
                    json.loads(marker.read_text(encoding="utf-8")).get("pinned", 0) or 0
                )
            except (OSError, ValueError):
                requested = 0
            if requested and _version_path(self.name, requested).is_file():
                return requested
        return latest_version(self.name) or None


def _existing_versions(name: str) -> list[int]:
    return VersionJournal(name).numbers()


def latest_version(name: str) -> int:
    return max(_existing_versions(name), default=0)


def record_version(
    name: str,
    spec: dict[str, Any],
    *,
    source: str = SOURCE_USER,
    ops: list[dict[str, Any]] | None = None,
    run_ids: list[str] | None = None,
    note: str = "",
) -> int:
    return VersionJournal(name).append(spec, source, ops, run_ids, note)


def get_version(name: str, version: int) -> VersionRecord | None:
    return VersionJournal(name).read(version)


def list_versions(name: str) -> list[VersionRecord]:
    entries = (get_version(name, version) for version in _existing_versions(name))
    return [entry for entry in entries if entry is not None]


def _write_pin(name: str, version: int) -> None:
    VersionJournal.write(_pin_path(name), {"pinned": int(version)})


def pinned_version(name: str) -> int | None:
    return VersionJournal(name).pin()


def repin(name: str, version: int) -> bool:
    available = get_version(name, version) is not None
    if available:
        _write_pin(name, version)
    return available


rollback = repin


class DefinitionShape:
    def __init__(self, root: dict[str, Any]):
        self.order: list[str] = []
        self.nodes: dict[str, dict[str, Any]] = {}
        pending = [root]
        while pending:
            node = pending.pop()
            if not isinstance(node, dict):
                continue
            identifier = str(node.get("id", "") or "")
            if identifier:
                self.order.append(identifier)
                config = node.get("config")
                self.nodes[identifier] = {
                    "kind": str(node.get("kind", node.get("macro", "")) or ""),
                    "config": config if isinstance(config, dict) else {},
                }
            children = []
            for field_name in ("children", "body"):
                child = node.get(field_name)
                if isinstance(child, list):
                    children.extend(child)
                elif isinstance(child, dict):
                    children.append(child)
            pending.extend(reversed(children))

    def edits_to(self, after: "DefinitionShape") -> list[dict[str, Any]]:
        edits = []
        for identifier in after.order:
            if identifier in self.nodes:
                changed = [
                    name
                    for name in ("kind", "config")
                    if self.nodes[identifier].get(name)
                    != after.nodes[identifier].get(name)
                ]
                if changed:
                    edits.append(
                        {"op": "update_node", "node_id": identifier, "fields": changed}
                    )
            else:
                edits.append(
                    {
                        "op": "insert",
                        "node_id": identifier,
                        "kind": after.nodes[identifier]["kind"],
                    }
                )
        edits.extend(
            {"op": "delete", "node_id": identifier}
            for identifier in self.order
            if identifier not in after.nodes
        )
        before_shared = [
            identifier for identifier in self.order if identifier in after.nodes
        ]
        after_shared = [
            identifier for identifier in after.order if identifier in self.nodes
        ]
        edits.extend(
            {"op": "move", "node_id": left}
            for left, right in zip(before_shared, after_shared)
            if left != right
        )
        return edits


def _flatten(root: dict[str, Any]) -> tuple[list[str], dict[str, dict[str, Any]]]:
    shape = DefinitionShape(root)
    return shape.order, shape.nodes


def diff(name: str, a: int, b: int) -> list[dict[str, Any]]:
    before, after = get_version(name, a), get_version(name, b)
    if before is None or after is None:
        return []
    shapes = [
        DefinitionShape(dict(record.spec.get("root") or {}))
        for record in (before, after)
    ]
    edits = shapes[0].edits_to(shapes[1])
    if (before.spec.get("inputs") or {}) != (after.spec.get("inputs") or {}):
        edits.append({"op": "set_input"})
    return edits


def _static_signals(spec: dict[str, Any]) -> dict[str, bool]:
    _, nodes = _flatten(dict(spec.get("root") or {}))

    def branch(parent, name):
        value = parent.get(name)
        return value if isinstance(value, dict) else {}

    hints = branch(spec, "runtime_hints")
    signals = {
        "has_gate_or_judge": any(
            node["kind"] in ("gate", "judge")
            or bool((node["config"] or {}).get("judge_contract"))
            for node in nodes.values()
        )
    }
    for label, section, key in (
        ("has_escalation", "execution", "escalation"),
        ("has_breaker", "execution", "breaker"),
        ("has_stop_condition", "judge", "stop_condition"),
    ):
        signals[label] = bool(branch(hints, section).get(key))
    return signals


def template_maturity(
    spec: dict[str, Any], *, clean_runs: int = 0, evaluator_rejected: bool = False
) -> dict[str, Any]:
    signals = _static_signals(spec)
    if not any(signals.values()):
        level = 0
    else:
        level = 1 + int(clean_runs >= 3)
        if level == 2 and evaluator_rejected and signals["has_gate_or_judge"]:
            level += 1
    return {
        "level": level,
        "label": ("draft", "shaping", "proven", "mature")[level],
        "signals": signals,
        "clean_runs": int(clean_runs),
        "evaluator_rejected": bool(evaluator_rejected),
    }
