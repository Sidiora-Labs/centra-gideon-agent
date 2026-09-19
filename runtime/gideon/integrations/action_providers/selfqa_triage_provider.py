"""Classify commit batches, persist their rationale, then bound scenario admission."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from gideon.integrations.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)

logger = logging.getLogger(__name__)


def _configured_cap() -> int:
    try:
        from gideon.core.config.loader import AppConfig

        return int(AppConfig.load().agent.self_qa.max_scenarios_per_fire)
    except Exception:
        return 3


@dataclass(frozen=True)
class _CommitBatch:
    repository: str
    revisions: list[str]

    @classmethod
    def read(cls, config: dict[str, Any]) -> _CommitBatch:
        supplied = config.get("commits")
        if isinstance(supplied, str):
            try:
                parsed = json.loads(supplied)
            except ValueError:
                parsed = [
                    piece.strip() for piece in supplied.split(",") if piece.strip()
                ]
            supplied = parsed.get("commits") if isinstance(parsed, dict) else parsed
        revisions = [value for item in supplied or [] if (value := str(item).strip())]
        return cls(str(config.get("repo") or "").strip(), revisions)

    @property
    def usable(self) -> bool:
        return bool(self.repository and self.revisions)


def _scenario_limit(config: dict[str, Any]) -> int:
    supplied = config.get("max_scenarios")
    explicit = (
        int(supplied)
        if isinstance(supplied, (int, float, str)) and str(supplied).isdigit()
        else 0
    )
    return max(1, explicit or _configured_cap())


@dataclass(frozen=True)
class _TriageBatch:
    verdicts: list[Any]

    def record(self, ctx: ActionContext) -> int:
        from gideon.assurance.selfqa.ledger import record_triage
        from gideon.automation.workflows.journal import Journal

        run = str(ctx.payload.get("run_id") or "")
        path = str(ctx.payload.get("instance_path") or "")
        if not run:
            logger.warning("selfqa-triage: no run_id in payload; verdicts not recorded")
            return 0
        journal = Journal(run_id=run)
        recorded = 0
        for verdict in self.verdicts:
            record_triage(journal, verdict, instance_path=path)
            recorded += 1
        return recorded

    def result(self, limit: int, recorded: int) -> ActionResult:
        all_rows: list[dict[str, Any]] = []
        selected: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for verdict in self.verdicts:
            row = verdict.to_dict()
            all_rows.append(row)
            if verdict.skipped:
                skipped.append(row)
            elif len(selected) < limit:
                selected.append(row)
        payload = dict(
            verdicts=all_rows,
            impactful=selected,
            skipped=skipped,
            has_impactful=bool(selected),
            recorded=recorded,
        )
        return ActionResult(True, stdout=json.dumps(payload))


class SelfQaTriageActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "selfqa-triage"

    @property
    def display_name(self) -> str:
        return "Triage Commits for Self-QA"

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        from gideon.assurance.selfqa.triage import triage_commits

        request = _CommitBatch.read(action_config)
        if not request.usable:
            return ActionResult(
                False, error="selfqa-triage requires 'repo' and a non-empty 'commits'"
            )
        batch = _TriageBatch(triage_commits(request.repository, request.revisions))
        try:
            recorded = batch.record(ctx)
        except Exception as exc:
            return ActionResult(
                False, error=f"selfqa-triage could not record its verdicts: {exc}"
            )
        return batch.result(_scenario_limit(action_config), recorded)


def create_provider(config: dict[str, Any] | None = None) -> SelfQaTriageActionProvider:
    return SelfQaTriageActionProvider()
