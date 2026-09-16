"Run snapshot projection with nonblocking schema diagnostics."

from __future__ import annotations

import logging
from typing import Any

from gideon.automation.workflows.models import InstanceState, RunStatus

logger = logging.getLogger(__name__)

RUN_FIELDS: tuple[tuple[str, tuple[type, ...], bool], ...] = (
    ("run_id", (str,), True),
    ("workflow", (str,), True),
    ("status", (str,), True),
    ("spec_version", (int,), True),
    ("error", (str,), False),
    ("attention", (dict, type(None)), False),
    ("tokens", (int,), False),
    ("elapsed_secs", (int, float), False),
    ("nodes", (list,), True),
)

NODE_FIELDS: tuple[tuple[str, tuple[type, ...], bool], ...] = (
    ("instance_path", (str,), True),
    ("node_id", (str,), True),
    ("state", (str,), True),
    ("attempt", (int, type(None)), False),
    ("degraded_reason", (str,), False),
    ("failure", (dict, type(None)), False),
    ("item_index", (int,), False),
    ("item_total", (int,), False),
    ("item_label", (str,), False),
)

_RUN_STATUSES = frozenset(s.value for s in RunStatus)
_NODE_STATES = frozenset(s.value for s in InstanceState)


def validate_snapshot(snap: Any) -> list[str]:
    if not isinstance(snap, dict):
        return [f"snapshot must be an object, got {type(snap).__name__}"]
    issues = SnapshotFields(RUN_FIELDS, "status", _RUN_STATUSES, "run status").issues(
        snap
    )
    nodes = snap.get("nodes")
    if isinstance(nodes, list):
        issues.extend(
            f"nodes[{index}]: {issue}"
            for index, node in enumerate(nodes)
            for issue in _validate_node(node)
        )
    return issues


def _validate_node(node: Any) -> list[str]:
    if not isinstance(node, dict):
        return [f"must be an object, got {type(node).__name__}"]
    return SnapshotFields(NODE_FIELDS, "state", _NODE_STATES, "node state").issues(node)


def project(run_id: str) -> tuple[dict[str, Any], list[str]]:
    from gideon.automation.workflows import service

    response = service.status(run_id)
    if not response.get("ok"):
        return {}, [str(response.get("code") or "unavailable")]
    snapshot = dict(response)
    snapshot.pop("ok", None)
    issues = validate_snapshot(snapshot)
    if issues:
        logger.warning(
            "workflow %s snapshot projection invalid: %s", run_id, "; ".join(issues)
        )
    return snapshot, issues


class SnapshotFields:
    def __init__(self, fields, enum_field, values, label):
        self.fields, self.enum_field, self.values, self.label = (
            fields,
            enum_field,
            values,
            label,
        )

    def issues(self, record: dict[str, Any]) -> list[str]:
        issues = []
        for name, accepted, required in self.fields:
            if name in record:
                value = record[name]
                if isinstance(value, bool) or not isinstance(value, accepted):
                    expected = "|".join(kind.__name__ for kind in accepted)
                    issues.append(
                        f"{name!r} must be {expected}, got {type(value).__name__}"
                    )
            elif required:
                issues.append(f"missing required field {name!r}")
        value = record.get(self.enum_field)
        if isinstance(value, str) and value not in self.values:
            issues.append(f"unknown {self.label} {value!r}")
        return issues
