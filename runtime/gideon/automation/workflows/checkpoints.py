"""Durable workflow snapshots, fork transfer and dependency-aware state reversal."""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from typing import Any

from gideon.automation.workflows import store
from gideon.automation.workflows.models import (
    SUCCESS_STATES,
    InstanceState,
    Node,
    NodeInstance,
    RunStatus,
    WorkflowRun,
    walk,
)
from gideon.core.atomic_write import atomic_write

logger = logging.getLogger(__name__)

CHECKPOINT_DIR = "checkpoints"


@dataclass
class Checkpoint:
    """A per-super-step state snapshot — a fork point.

    Stores the instance map and the spec version, NOT the outputs: outputs live in
    `outputs/` and are content-addressed by node path, so a checkpoint that copied them
    would double every run's disk cost for no gain. The instance map is what pins "which
    nodes were done, at which epoch".
    """

    id: str
    run_id: str
    spec_version: int
    created_at: str = ""
    note: str = ""
    instances: dict[str, dict[str, Any]] = field(default_factory=dict)
    workspace_snapshot: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in _CHECKPOINT_FIELDS}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Checkpoint:
        return cls(**_checkpoint_values(d or {}))

    def instance_map(self) -> dict[str, NodeInstance]:
        return dict(
            _restore_instance(path, raw) for path, raw in self.instances.items()
        )


def _checkpoint_dir(run_id: str):
    return store.run_dir(run_id) / CHECKPOINT_DIR


def next_checkpoint_id(run_id: str) -> str:
    return _CheckpointFiles(run_id).next_id()


def save_checkpoint(
    run: WorkflowRun,
    instances: dict[str, NodeInstance],
    *,
    note: str = "",
    now: str = "",
    workspace_snapshot: str = "",
) -> Checkpoint:
    checkpoint = Checkpoint(
        id=next_checkpoint_id(run.id),
        run_id=run.id,
        spec_version=run.spec_version,
        created_at=now,
        note=note,
        instances=dict(
            (path, instance.to_dict()) for path, instance in instances.items()
        ),
        workspace_snapshot=workspace_snapshot,
    )
    _CheckpointFiles(run.id).write(checkpoint)
    return checkpoint


def load_checkpoint(run_id: str, checkpoint_id: str) -> Checkpoint | None:
    return _CheckpointFiles(run_id).load(checkpoint_id)


def list_checkpoints(run_id: str) -> list[Checkpoint]:
    return list(_CheckpointFiles(run_id).readable())


SHARED_AXES = (
    "filesystem workspace (both runs write the same paths unless the spec differs)",
    "external resources the parent already created (its committed effects still exist)",
    "wall-clock and randomness (unique-name generators must take fork_axis as a seed)",
)


@dataclass
class ForkResult:
    child: WorkflowRun
    checkpoint_id: str = ""
    fork_axis: str = ""
    isolation_notes: list[str] = field(default_factory=list)
    cached_prefix: int = 0

    def to_dict(self) -> dict[str, Any]:
        details: dict = dict(
            child_run_id=self.child.id,
            checkpoint_id=self.checkpoint_id,
            fork_axis=self.fork_axis,
        )
        details.update(
            isolation_notes=list(self.isolation_notes),
            cached_prefix=self.cached_prefix,
            shared_axes=list(SHARED_AXES),
        )
        return details


def fork_run(
    parent: WorkflowRun,
    spec: dict[str, Any],
    instances: dict[str, NodeInstance],
    *,
    checkpoint_id: str = "",
    note: str = "",
    now: str = "",
) -> ForkResult:
    transfer = _ForkTransfer(parent, dict(instances))
    transfer.select(checkpoint_id)
    child, axis = transfer.create(checkpoint_id, note)
    store.write_spec(child.id, spec)
    store.write_state(child.id, transfer.instances)
    carried = _copy_journal_prefix(parent.id, child.id)
    _copy_outputs(parent.id, child.id, transfer.instances)
    return ForkResult(
        child=child,
        checkpoint_id=checkpoint_id,
        fork_axis=axis,
        isolation_notes=list(map(lambda axis: f"NOT isolated: {axis}", SHARED_AXES)),
        cached_prefix=carried,
    )


def _copy_journal_prefix(parent_id: str, child_id: str) -> int:
    from gideon.automation.workflows.journal import (
        EVENTS_FILE,
        JOURNAL_FILE,
        STEP_CACHED,
        STEP_COMPLETED,
    )

    cached = 0
    for filename in (JOURNAL_FILE, EVENTS_FILE):
        for record in store.read_jsonl(parent_id, filename):
            store.append_jsonl(child_id, filename, record)
            if filename != JOURNAL_FILE:
                continue
            cached += int(record.get("kind") in (STEP_COMPLETED, STEP_CACHED))
    return cached


def _copy_outputs(
    parent_id: str, child_id: str, instances: dict[str, NodeInstance]
) -> None:
    for path, instance in instances.items():
        if instance.state in SUCCESS_STATES:
            for address in (path, f"{path}::prompt"):
                payload = store.read_output(parent_id, address)
                if payload is not None:
                    store.write_output(child_id, address, payload)


def prune_fork(child_id: str) -> bool:
    target = store.run_dir(child_id).resolve()
    root = store.runs_root().resolve()
    if root in target.parents:
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
            store.delete(child_id)
            return True
    else:
        logger.error("refusing to prune %s: outside the runs root", target)
    return False


@dataclass
class RevertConflict:
    """Why a revert was refused, naming WHICH later nodes depend on the value.

    Named rather than counted: "3 nodes depend on this" leaves the user guessing, and the
    whole reason to refuse instead of cascading is that they can then decide.
    """

    node_id: str
    dependents: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"node_id": self.node_id, "dependents": list(self.dependents)}


def revert_node(
    root: Node,
    instances: dict[str, NodeInstance],
    node_id: str,
) -> tuple[list[str], RevertConflict | None]:
    from gideon.automation.workflows.mutations import dependents_graph

    consumers = dependents_graph(root).get(node_id, set())
    locations: dict[str, list[str]] = {}
    for path, node in walk(root):
        if node.id:
            locations.setdefault(node.id, []).append(path)
    blocked = []
    for consumer in sorted(consumers):
        for path in locations.get(consumer, ()):
            if path in instances and instances[path].state in SUCCESS_STATES:
                blocked.append(consumer)
                break
    if blocked:
        return [], RevertConflict(node_id=node_id, dependents=blocked)
    return list(filter(instances.__contains__, locations.get(node_id, ()))), None


def revert_paths(
    run_id: str,
    instances: dict[str, NodeInstance],
    paths: list[str],
    *,
    version: int,
) -> int:
    reset = 0
    for path in paths:
        instance = instances.get(path)
        if instance is not None:
            if instance.output_ref:
                store.archive_output(run_id, path, version)
            for field_name, value in _RESET_FIELDS:
                setattr(instance, field_name, value)
            reset += 1
    return reset


_CHECKPOINT_FIELDS = (
    "id",
    "run_id",
    "spec_version",
    "created_at",
    "note",
    "instances",
    "workspace_snapshot",
)
_RESET_FIELDS = (
    ("state", InstanceState.PENDING),
    ("output_ref", ""),
    ("failure", None),
    ("completed_at", None),
    ("attempt", 0),
)


def _checkpoint_values(record: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in _CHECKPOINT_FIELDS:
        value: Any
        if name == "spec_version":
            value = int(record.get(name, 1) or 1)
        elif name == "instances":
            value = dict(record.get(name) or {})
        else:
            value = str(record.get(name, "") or "")
        result[name] = value
    return result


def _restore_instance(path: str, raw: dict[str, Any]):
    instance = NodeInstance.from_dict(raw)
    if not instance.path:
        instance.path = str(path)
    return str(path), instance


class _CheckpointFiles:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.directory = _checkpoint_dir(run_id)

    def next_id(self) -> str:
        count = (
            len(list(self.directory.glob("*.json"))) if self.directory.is_dir() else 0
        )
        return format(count + 1, "03d")

    def write(self, checkpoint: Checkpoint) -> None:
        destination = self.directory / (checkpoint.id + ".json")
        destination.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(
            destination, json.dumps(checkpoint.to_dict(), indent=2, ensure_ascii=False)
        )

    def load(self, key: str) -> Checkpoint | None:
        target = self.directory / f"{key}.json"
        if target.is_file():
            try:
                document = json.loads(target.read_text(encoding="utf-8"))
                return Checkpoint.from_dict(document)
            except (OSError, ValueError):
                logger.warning("run %s: unreadable checkpoint %s", self.run_id, key)
        return None

    def readable(self):
        if not self.directory.is_dir():
            return
        for target in sorted(self.directory.glob("*.json")):
            try:
                document = json.loads(target.read_text(encoding="utf-8"))
                checkpoint = Checkpoint.from_dict(document)
            except (OSError, ValueError):
                logger.debug(
                    "run %s: skipping unreadable checkpoint %s",
                    self.run_id,
                    target.name,
                )
            else:
                yield checkpoint


class _ForkTransfer:
    def __init__(self, parent: WorkflowRun, instances: dict[str, NodeInstance]):
        self.parent = parent
        self.instances = instances
        self.version = parent.spec_version
        self.snapshot = ""

    def select(self, checkpoint_id: str) -> None:
        if not checkpoint_id:
            return
        checkpoint = load_checkpoint(self.parent.id, checkpoint_id)
        if checkpoint is None:
            raise ValueError(
                f"unknown checkpoint {checkpoint_id!r} on run {self.parent.id}"
            )
        self.instances = checkpoint.instance_map()
        self.version, self.snapshot = (
            checkpoint.spec_version,
            checkpoint.workspace_snapshot,
        )

    def create(self, checkpoint_id: str, note: str):
        source = self.parent
        axis = f"{source.id}:{checkpoint_id or 'head'}"
        inputs = dict(source.inputs)
        inputs["__fork_axis"] = axis
        record = WorkflowRun(
            id="",
            workflow_name=source.workflow_name,
            status=RunStatus.DRAFT,
            spec_version=self.version,
            inputs=inputs,
            intent=source.intent,
            origin=source.origin,
            parent_run_id=source.id,
            root_run_id=source.root_run_id or source.id,
            branch_key=axis,
            forked_from=dict(
                run_id=source.id,
                checkpoint_id=checkpoint_id,
                note=note,
                workspace_snapshot=self.snapshot,
            ),
            project_id=source.project_id,
            mode=source.mode,
            budget=source.budget,
        )
        return store.create(record), axis
