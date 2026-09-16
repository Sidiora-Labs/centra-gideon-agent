"""Convert action claims into filesystem evidence through the shared verifier."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from gideon.integrations.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)


@dataclass(frozen=True)
class _VerificationRequest:
    text: str
    root: str
    limit: int

    @classmethod
    def read(cls, config: dict[str, Any], ctx: ActionContext) -> _VerificationRequest:
        from gideon.assurance.check_work import MAX_CHECKS

        text = str(config.get("text") or "")
        if not text.strip():
            raise ValueError("check-work requires 'text' — the claims to verify")
        explicit = str(config.get("root") or "").strip()
        root = explicit or str(ctx.payload.get("workspace") or "")
        if not root:
            raise ValueError(
                "check-work has nothing to check against: no 'root' was given and this run has no workspace"
            )
        try:
            limit = int(config.get("max_checks") or MAX_CHECKS)
        except (TypeError, ValueError):
            limit = MAX_CHECKS
        return cls(text, root, limit)


def _verification_result(report: Any) -> ActionResult:
    from gideon.assurance.check_work import render_report

    checks = []
    for observed in report.results:
        row = {
            name: getattr(observed.check, name)
            for name in ("kind", "target", "needle", "claim", "label", "how")
        }
        row.update(status=observed.status, evidence=observed.evidence)
        checks.append(row)
    envelope = dict(
        verdict=report.verdict,
        note=report.note,
        checks=checks,
        report=render_report(report),
    )
    contradicted = report.verdict == "fail"
    reason = ""
    if contradicted:
        reason = "check-work: a derived check failed — " + "; ".join(
            item.check.label for item in report.failed
        )
    return ActionResult(
        not contradicted, stdout=json.dumps(envelope, ensure_ascii=False), error=reason
    )


class CheckWorkActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "check-work"

    @property
    def display_name(self) -> str:
        return "Check Work"

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        from gideon.assurance.check_work import derive_and_run

        try:
            request = _VerificationRequest.read(action_config, ctx)
        except ValueError as exc:
            return ActionResult(False, error=str(exc))
        try:
            report = derive_and_run(
                request.text, root=request.root, max_checks=request.limit
            )
        except Exception as exc:
            return ActionResult(
                False, error=f"check-work failed: {type(exc).__name__}: {exc}"
            )
        return _verification_result(report)


def create_provider(config: dict[str, Any] | None = None) -> CheckWorkActionProvider:
    return CheckWorkActionProvider()
