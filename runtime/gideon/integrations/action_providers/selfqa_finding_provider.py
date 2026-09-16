"""Admit one scenario finding and project the shared filing engine's receipt."""

from __future__ import annotations

from typing import Any

from gideon.integrations.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)
from gideon.integrations.action_providers.template import render_template


def _finding_request(config: dict[str, Any], ctx: ActionContext) -> Any:
    from gideon.assurance.selfqa.findings import ScenarioFinding

    identity = {
        key: str(config.get(key) or "").strip() for key in ("sha", "scenario_id")
    }
    title = render_template(str(config.get("title") or ""), ctx).strip()
    if not title or not all(identity.values()):
        raise ValueError(
            "selfqa-file-finding requires 'sha', 'scenario_id' and 'title'"
        )
    steps = config.get("repro_steps")
    return ScenarioFinding(
        **identity,
        title=title,
        scenario_text=render_template(str(config.get("scenario_text") or ""), ctx),
        repro_steps=list(map(str, steps)) if isinstance(steps, list) else [],
        evidence_ref=str(config.get("evidence_ref") or ""),
        fix_branch=str(config.get("fix_branch") or ""),
    )


def _filing_result(finding: Any, receipt: Any) -> ActionResult:
    replay = receipt.already_filed
    message = (
        f"finding {finding.key} was already filed"
        if replay
        else f"filed finding {finding.key}: inbox={receipt.inbox_item_id or '<none>'} task={receipt.task_id}"
    )
    return ActionResult(True, outcome="skip" if replay else "", stdout=message)


class SelfQaFindingActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "selfqa-file-finding"

    @property
    def display_name(self) -> str:
        return "File Self-QA Finding"

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        from gideon.assurance.selfqa.findings import file_finding

        try:
            finding = _finding_request(action_config, ctx)
        except ValueError as exc:
            return ActionResult(False, error=str(exc))
        try:
            receipt = await file_finding(finding)
        except Exception as exc:
            return ActionResult(False, error=f"selfqa-file-finding failed: {exc}")
        return _filing_result(finding, receipt)


def create_provider(
    config: dict[str, Any] | None = None,
) -> SelfQaFindingActionProvider:
    return SelfQaFindingActionProvider()
