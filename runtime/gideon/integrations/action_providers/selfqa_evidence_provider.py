"""Seal the run's measured evidence, retain partial proof, and report completion."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gideon.integrations.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)

logger = logging.getLogger(__name__)


def _as_bool(value: Any) -> bool:
    return (
        value
        if isinstance(value, bool)
        else str(value).strip().lower() in ("true", "1", "yes")
    )


@dataclass(frozen=True)
class _EvidenceRequest:
    directory: Path
    scenario: str
    revision: str
    passed: bool
    repository: str
    allow_branch: bool
    required: tuple[str, ...]
    project: str

    @classmethod
    def read(cls, config: dict[str, Any], ctx: ActionContext) -> _EvidenceRequest:
        from gideon.assurance.selfqa.evidence import DEFAULT_REQUIRED_KINDS

        workspace = str(ctx.payload.get("workspace") or "").strip()
        if not workspace:
            raise ValueError(
                "selfqa-evidence has no run workspace to seal — the engine did not thread a workspace into the action payload"
            )
        directory = Path(workspace)
        subdirectory = str(config.get("bundle_subdir") or "").strip()
        if subdirectory:
            directory /= subdirectory
        if not directory.is_dir():
            raise ValueError(f"selfqa-evidence bundle dir does not exist: {directory}")
        kinds = config.get("required_kinds")
        required = (
            tuple(value for item in kinds if (value := str(item).strip()))
            if isinstance(kinds, list) and kinds
            else DEFAULT_REQUIRED_KINDS
        )
        return cls(
            directory,
            str(config.get("scenario_id") or "").strip(),
            str(config.get("sha") or "").strip(),
            _as_bool(config.get("passed", False)),
            str(config.get("repo") or "").strip(),
            _as_bool(config.get("fix_branch_enabled", False)),
            required,
            str(ctx.payload.get("project_id") or ""),
        )

    def seal(self) -> _EvidenceReceipt:
        from gideon.assurance.selfqa import evidence

        derivations = tuple(
            derive(self.directory)
            for derive in (evidence.derive_contact_sheet, evidence.derive_gif)
        )
        metadata = dict(
            scenario_id=self.scenario, sha=self.revision, passed=self.passed
        )
        manifest = evidence.build_manifest(
            self.directory, **metadata, degradations=derivations
        )
        evidence.write_manifest(self.directory, manifest)
        registered = evidence.register_bundle(
            self.directory, manifest=manifest, project_id=self.project, **metadata
        )
        gate = evidence.check_required_kinds(manifest, self.required)
        return _EvidenceReceipt(manifest, registered, gate)

    def fix_branch(self) -> str:
        from gideon.assurance.selfqa.fix_branch import create_fix_branch

        if self.passed or not self.allow_branch or not self.repository:
            return ""
        branch = create_fix_branch(self.repository, self.revision, enabled=True)
        if branch.created or branch.already_existed:
            return branch.branch
        logger.info("selfqa-evidence: no fix branch (%s)", branch.reason)
        return ""


@dataclass(frozen=True)
class _EvidenceReceipt:
    manifest: Any
    registered: Any
    gate: Any

    def result(self, branch: str) -> ActionResult:
        payload = dict(
            evidence_ref=self.registered.ref,
            complete=self.gate.complete,
            present=sorted(self.gate.present),
            missing=list(self.gate.missing),
            degraded=list(self.manifest.degraded),
            fix_branch=branch,
            file_count=self.registered.file_count,
        )
        return ActionResult(
            self.gate.complete,
            stdout=json.dumps(payload),
            error=(
                ""
                if self.gate.complete
                else "required artifacts missing: " + ", ".join(self.gate.missing)
            ),
        )


class SelfQaEvidenceActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "selfqa-evidence"

    @property
    def display_name(self) -> str:
        return "Seal Self-QA Evidence Bundle"

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        try:
            request = _EvidenceRequest.read(action_config, ctx)
        except ValueError as exc:
            return ActionResult(False, error=str(exc))
        try:
            receipt = request.seal()
        except Exception as exc:
            return ActionResult(False, error=f"selfqa-evidence failed: {exc}")
        return receipt.result(request.fix_branch())


def create_provider(
    config: dict[str, Any] | None = None,
) -> SelfQaEvidenceActionProvider:
    return SelfQaEvidenceActionProvider()
