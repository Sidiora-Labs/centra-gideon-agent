"""Read-only packaged workflow templates, expanded when their files change."""

from __future__ import annotations

import logging
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

from gideon.automation.workflows.blocks import BlockError, resolve_spec
from gideon.automation.workflows.definition_catalog import (
    definition_names,
    definition_window,
    read_definition,
)
from gideon.automation.workflows.defs import WorkflowDefProvider
from gideon.automation.workflows.macros import MacroError, expand_spec
from gideon.automation.workflows.models import SPEC_SEMVER, WorkflowDef, valid_name

logger = logging.getLogger(__name__)
_BUNDLED_PKG = "gideon.automation.workflows.bundled"
DEF_FILE = "workflow.json"
PROVIDER_NAME = "bundled"


def bundled_root() -> Path:
    return Path(str(resources.files(_BUNDLED_PKG)))


def template_names() -> list[str]:
    return definition_names(bundled_root(), DEF_FILE)


class BundledWorkflowDefProvider(WorkflowDefProvider):
    @property
    def name(self) -> str:
        return PROVIDER_NAME

    @property
    def readonly(self) -> bool:
        return True

    async def list_defs(
        self, *, limit: int = 200, offset: int = 0
    ) -> tuple[list[Any], int]:
        return definition_window(template_names(), read_template, limit, offset)

    async def get_def(self, name: str) -> Any | None:
        return read_template(name) if valid_name(name) else None


@lru_cache(maxsize=64)
def _read_cached(name: str, mtime_ns: int) -> WorkflowDef | None:
    document = read_definition(
        bundled_root() / name / DEF_FILE, name, logger, bundled=True
    )
    if document is None:
        return None
    document.update(source="bundled")
    document.setdefault("spec_semver", SPEC_SEMVER)
    try:
        for transform in (expand_spec, resolve_spec):
            document = transform(document)
    except (MacroError, BlockError) as exc:
        logger.warning("bundled template %s: macro expansion failed — %s", name, exc)
        return None
    try:
        return WorkflowDef.from_dict(document)
    except (ValueError, TypeError) as exc:
        logger.warning("bundled template %s: unusable spec — %s", name, exc)
        return None


def read_template(name: str) -> WorkflowDef | None:
    try:
        stamp = (bundled_root() / name / DEF_FILE).stat().st_mtime_ns
    except OSError:
        return None
    return _read_cached(name, stamp)


def register_bundled_provider() -> None:
    from gideon.automation.workflows.defs import get_provider, register_provider

    if get_provider(PROVIDER_NAME) is None:
        register_provider(BundledWorkflowDefProvider())
