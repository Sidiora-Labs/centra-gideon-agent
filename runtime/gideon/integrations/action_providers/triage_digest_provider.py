"""Bind the triage pipeline to a digest window, live inbox and per-run policy."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from gideon.integrations.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)

logger = logging.getLogger(__name__)
TRIAGE_WORKFLOW = "morning-triage"
DEFAULT_WINDOW_HOURS = 24
_NODE_ID = "triage"


def _proactive_config() -> Any:
    from gideon.core.config.loader import AppConfig

    return AppConfig.load().proactive


@dataclass(frozen=True)
class _DigestWindow:
    timestamp: float
    label: str

    @classmethod
    def from_history(cls, config: dict[str, Any]) -> _DigestWindow:
        try:
            hours = float(config.get("window_hours") or DEFAULT_WINDOW_HOURS)
        except (ValueError, TypeError):
            hours = DEFAULT_WINDOW_HOURS
        timestamp = time.time() - 3600 * max(0.0, hours)
        fallback = cls(timestamp, datetime.fromtimestamp(timestamp, UTC).isoformat())
        try:
            from gideon.automation.workflows.store import list_runs

            records, _ = list_runs(
                workflow_name=TRIAGE_WORKFLOW, status="completed", limit=1
            )
        except Exception:
            return fallback
        if not records:
            return fallback
        latest = records[0]
        label = str(
            getattr(latest, "completed_at", "") or getattr(latest, "created_at", "")
        )
        if not label:
            return fallback
        try:
            parsed = datetime.fromisoformat(label.replace("Z", "+00:00"))
        except ValueError:
            logger.debug("triage: unparseable last-digest stamp %r", label)
            return fallback
        return cls(
            (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).timestamp(), label
        )


def _window(config: dict[str, Any]) -> tuple[float, str]:
    window = _DigestWindow.from_history(config)
    return window.timestamp, window.label


def _list_setting(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except ValueError:
        return None


def _rules(config: dict[str, Any]) -> list[Any]:
    from gideon.cognition.proactive.gate import GateRule

    rules = []
    for entry in _list_setting(config.get("filter_rules")) or ():
        if isinstance(entry, str):
            source, text = "*", entry.strip()
        elif isinstance(entry, dict):
            source = str(entry.get("source") or "*")
            text = str(entry.get("rule") or "").strip()
        else:
            continue
        if text:
            rules.append(GateRule(source=source, rule=text))
    return rules


@dataclass(frozen=True)
class _TriageJournal:
    journal: Any
    instance_path: str

    @classmethod
    def bind(cls, ctx: ActionContext) -> _TriageJournal | None:
        run_id = str(ctx.payload.get("run_id") or "")
        instance_path = str(ctx.payload.get("instance_path") or "")
        if not run_id or not instance_path:
            return None
        from gideon.automation.workflows.journal import Journal

        return cls(Journal(run_id=run_id), instance_path)

    def write(self, kind: str, fields: dict[str, Any]) -> None:
        self.journal.write(
            kind,
            node_id=_NODE_ID,
            instance_path=self.instance_path,
            epoch=0,
            actor="triage",
            **fields,
        )

    def record(self, result: Any) -> int:
        from gideon.assurance.ledger.kinds import PROPOSAL_REFUSED, SKIPPED_TRIAGE

        count = 0
        for item in result.gate.dropped:
            decision = result.gate.outcomes.get(item.ordinal)
            self.write(
                SKIPPED_TRIAGE,
                dict(
                    item_ordinal=item.ordinal,
                    item_source=item.source,
                    item_source_id=item.source_id,
                    rationale=(decision.rationale if decision else "")
                    or "dropped by the classifier gate",
                    rule=decision.rule if decision else "",
                ),
            )
            count += 1
        for refusal in result.refused:
            self.write(
                PROPOSAL_REFUSED,
                dict(
                    reason=refusal.reason,
                    item_ordinal=refusal.item_id,
                    action_type=refusal.action_type,
                    detail=refusal.detail,
                ),
            )
            count += 1
        return count


def _record(result: Any, ctx: ActionContext) -> int:
    journal = _TriageJournal.bind(ctx)
    return journal.record(result) if journal else 0


def _approval_rules(memory: Any = None) -> list[Any]:
    from gideon.cognition.proactive.approval import APPROVAL_KEY_PREFIX, rules_from_rows

    owned = None
    try:
        if memory is None:
            from gideon.cognition.memory_service import MemoryService
            from gideon.cognition.vector_memory import SemanticArchive
            from gideon.integrations.embedding_providers.registry import (
                get_active_embedding_dim,
            )

            owned = SemanticArchive(embedding_dim=get_active_embedding_dim() or 384)
            owned.init()
            memory = MemoryService.over_vector_store(owned)
        rows = []
        for row in memory.get_all_semantic():
            key = str(row.get("key") or "")
            if key.startswith(APPROVAL_KEY_PREFIX):
                rows.append((key, row.get("value_json")))
    except Exception:
        logger.warning("triage: approval rules unreadable", exc_info=True)
        return []
    finally:
        if owned is not None:
            try:
                owned.close()
            except Exception:
                logger.debug("triage: approval store close failed", exc_info=True)
    return rules_from_rows(rows)


def _ledger_writer(ctx: ActionContext) -> Any:
    journal = _TriageJournal.bind(ctx)
    return journal.write if journal else None


def _capabilities(action_config: dict[str, Any]) -> frozenset[str]:
    from gideon.cognition.proactive.autoexec import AUTO_CAPABLE_PROVIDERS

    value = _list_setting(action_config.get("capabilities"))
    if isinstance(value, (list, tuple)):
        names = frozenset(str(name).strip() for name in value if str(name).strip())
        if names:
            return names
    return AUTO_CAPABLE_PROVIDERS


@dataclass(frozen=True)
class _AutoExecution:
    run_id: str
    session_key: str
    rules: list[Any]
    ledger: Any
    capabilities: frozenset[str]
    enabled: bool
    limit: int

    async def __call__(self, proposals: Any, manifest: Any) -> Any:
        from gideon.cognition.proactive.autoexec import (
            auto_execute,
            default_budget_check,
        )

        return await auto_execute(
            proposals,
            manifest=manifest,
            rules=self.rules,
            now=datetime.now(UTC),
            enabled=self.enabled,
            cap=self.limit,
            capabilities=self.capabilities,
            session_key=self.session_key,
            budget_check=default_budget_check(self.run_id),
            ledger=self.ledger,
        )


def _auto_stage(action_config: dict[str, Any], ctx: ActionContext, cfg: Any) -> Any:
    from gideon.security.guardrails.policy import unattended_dispatch_key

    run_id = str(ctx.payload.get("run_id") or "")
    trigger_id = str(ctx.payload.get("trigger_id") or "")
    return _AutoExecution(
        run_id=run_id,
        session_key=unattended_dispatch_key(
            f"trigger:{trigger_id or run_id or 'triage-digest'}"
        ),
        rules=_approval_rules(),
        ledger=_ledger_writer(ctx),
        capabilities=_capabilities(action_config),
        enabled=bool(getattr(cfg, "auto_execute_enabled", False)),
        limit=int(getattr(cfg, "max_auto_actions_per_run", 0) or 0),
    )


@dataclass(frozen=True)
class _TriageRun:
    config: dict[str, Any]
    context: ActionContext
    settings: Any

    def collect(self, timestamp: float, label: str) -> list[Any]:
        from gideon.cognition.proactive.collect import collect_all
        from gideon.integrations.action_providers.services import get_action_services

        services = get_action_services()
        state = services.state if services is not None else None
        inbox = getattr(state, "_inbox_store", None)
        if inbox is None:
            inbox = getattr(getattr(state, "_inbox_svc", None), "inbox", None)
        return collect_all(
            inbox_store=inbox, state=state, since_ts=timestamp, since_iso=label
        )

    async def execute(self) -> ActionResult:
        from gideon.cognition.proactive.pipeline import run_triage
        from gideon.cognition.proactive.proposals import MAX_PROPOSALS

        timestamp, label = _window(self.config)
        items = self.collect(timestamp, label)
        try:
            limit = int(self.config.get("max_proposals") or MAX_PROPOSALS)
        except (ValueError, TypeError):
            limit = MAX_PROPOSALS
        result = await run_triage(
            items,
            rules=_rules(self.config),
            gate_enabled=bool(getattr(self.settings, "classifier_gate_enabled", True)),
            max_proposals=limit,
            window_start=label,
            run_id=str(self.context.payload.get("run_id") or ""),
            trigger_id=str(self.context.payload.get("trigger_id") or ""),
            auto_execute=_auto_stage(self.config, self.context, self.settings),
        )
        summary = result.summary()
        summary.update(
            window_start=label,
            ledger_rows=_record(result, self.context),
            notes=list(result.notes),
        )
        return ActionResult(True, exit_code=0, stdout=json.dumps(summary))


class TriageDigestActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "triage-digest"

    @property
    def display_name(self) -> str:
        return "Run the Triage Digest"

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        settings = _proactive_config()
        if not getattr(settings, "triage_enabled", False):
            return ActionResult(
                False,
                error="triage-digest refused: proactive.triage_enabled is off — nothing is collected and no model is called until you turn it on",
            )
        return await _TriageRun(action_config, ctx, settings).execute()


def create_provider(config: dict[str, Any] | None = None) -> TriageDigestActionProvider:
    return TriageDigestActionProvider()
