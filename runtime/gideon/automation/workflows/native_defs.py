"""Writable workflow definitions with monotonic version snapshots."""

from __future__ import annotations

import json
import logging
import fcntl
import asyncio
from pathlib import Path
from typing import Any

from gideon.automation.workflows import provisioning, store
from gideon.automation.workflows.definition_catalog import (
    definition_names,
    definition_window,
    read_definition,
)
from gideon.automation.workflows.defs import WorkflowDefProvider
from gideon.automation.workflows.models import (
    SPEC_SEMVER,
    Node,
    WorkflowDef,
    valid_name,
)
from gideon.core.atomic_write import atomic_write

logger = logging.getLogger(__name__)
DEF_FILE = "workflow.json"


def defs_root() -> Path:
    return store.workflows_dir() / "defs"


def _def_path(name: str) -> Path:
    return defs_root() / name / DEF_FILE


class DefinitionDraft:
    def __init__(self, fields: dict[str, Any]):
        self.fields = fields
        self.name = str(fields.get("name", "") or "")
        if not valid_name(self.name):
            raise ValueError(f"{self.name!r} is not a valid definition name")
        self.root = fields.get("root")
        if not isinstance(self.root, dict):
            raise ValueError("a definition needs a `root` node object")
        Node.from_dict(self.root)

    def document(self, prior: WorkflowDef | None) -> dict[str, Any]:
        fields = self.fields
        result = {
            "name": self.name,
            "root": self.root,
            "version": 1 if prior is None else prior.version + 1,
        }
        for key, default in (
            ("spec_semver", SPEC_SEMVER),
            ("description", ""),
            ("provenance", "user"),
        ):
            result[key] = str(fields.get(key, default) or default)
        result.update(
            source="user",
            inputs=dict(fields.get("inputs") or {}),
            tags=list(map(str, fields.get("tags") or [])),
            metadata=dict(fields.get("metadata") or {}),
            created_at=_now() if prior is None else prior.created_at,
            updated_at=_now(),
        )
        if isinstance(fields.get("defaults"), dict):
            result["defaults"] = fields["defaults"]
        if "on_overlap" in fields:
            result["on_overlap"] = str(fields["on_overlap"])
        workspace = fields.get(provisioning.WORKSPACE_KEY)
        if isinstance(workspace, dict):
            result[provisioning.WORKSPACE_KEY] = dict(workspace)
        return result

    def save(self, expected_revision: int | None = None) -> WorkflowDef:
        root = defs_root()
        root.mkdir(parents=True, exist_ok=True)
        with (root / ".definitions.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                prior = _read(self.name)
                current_revision = prior.version if prior is not None else None
                if expected_revision is not None and current_revision != expected_revision:
                    raise DefinitionRevisionConflict(current_revision)
                document = self.document(prior)
                target = _def_path(self.name)
                target.parent.mkdir(parents=True, exist_ok=True)
                atomic_write(target, json.dumps(document, indent=2, ensure_ascii=False))
                self.snapshot(document)
                return WorkflowDef.from_dict(document)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def snapshot(self, document: dict[str, Any]) -> None:
        try:
            from gideon.automation.workflows import versions

            operations = [
                operation
                for operation in (self.fields.get("_version_ops") or [])
                if isinstance(operation, dict)
            ]
            versions.record_version(
                self.name,
                document,
                source=str(self.fields.get("_version_source") or versions.SOURCE_USER),
                ops=operations,
            )
        except Exception:
            logger.debug(
                "versions: could not record snapshot for %s", self.name, exc_info=True
            )


class NativeWorkflowDefProvider(WorkflowDefProvider):
    @property
    def name(self) -> str:
        return "native"

    @property
    def readonly(self) -> bool:
        return False

    async def list_defs(
        self, *, limit: int = 200, offset: int = 0
    ) -> tuple[list[Any], int]:
        return definition_window(
            definition_names(defs_root(), DEF_FILE), _read, limit, offset
        )

    async def get_def(self, name: str) -> Any | None:
        return _read(name) if valid_name(name) else None

    async def save_def(self, *, expected_revision: int | None = None, **fields: Any) -> Any:
        return await asyncio.to_thread(DefinitionDraft(fields).save, expected_revision)

    async def set_a2a_published(
        self, name: str, published: bool, *, expected_revision: int | None = None
    ) -> Any | None:
        if not valid_name(name):
            return None
        return await asyncio.to_thread(
            self._set_a2a_published_locked, name, published, expected_revision
        )

    @staticmethod
    def _set_a2a_published_locked(
        name: str, published: bool, expected_revision: int | None
    ) -> Any | None:
        root = defs_root()
        root.mkdir(parents=True, exist_ok=True)
        with (root / ".definitions.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                current = _read(name)
                if current is None:
                    return None
                if expected_revision is not None and current.version != expected_revision:
                    raise DefinitionRevisionConflict(current.version)
                path = _def_path(name)
                document = read_definition(path, name, logger)
                if document is None:
                    return None
                metadata = dict(document.get("metadata") or {})
                metadata["a2a_published"] = bool(published)
                document["metadata"] = metadata
                document["version"] = current.version + 1
                document["updated_at"] = _now()
                atomic_write(path, json.dumps(document, indent=2, ensure_ascii=False))
                DefinitionDraft(document).snapshot(document)
                return WorkflowDef.from_dict(document)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    async def delete_def(self, name: str) -> bool:
        if not valid_name(name):
            return False
        return await asyncio.to_thread(self._delete_locked, name)

    @staticmethod
    def _delete_locked(name: str) -> bool:
        root = defs_root()
        root.mkdir(parents=True, exist_ok=True)
        with (root / ".definitions.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                target = _def_path(name)
                if not target.is_file():
                    return False
                target.unlink()
                empty = next(target.parent.iterdir(), None) is None
                if empty:
                    target.parent.rmdir()
                return True
            except OSError:
                logger.warning("could not delete workflow def %s", name, exc_info=True)
                return False
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)


def _read(name: str) -> WorkflowDef | None:
    document = read_definition(_def_path(name), name, logger)
    if document is None:
        return None
    try:
        return WorkflowDef.from_dict(document)
    except (ValueError, TypeError):
        logger.warning("workflow def %s: unusable spec", name)
        return None


class DefinitionRevisionConflict(Exception):
    def __init__(self, current_revision: int | None):
        self.current_revision = current_revision
        super().__init__(f"workflow definition revision changed to {current_revision}")


def _now() -> str:
    import time

    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def register_native_provider() -> None:
    from gideon.automation.workflows.defs import get_provider, register_provider

    if get_provider("native") is None:
        register_provider(NativeWorkflowDefProvider())
