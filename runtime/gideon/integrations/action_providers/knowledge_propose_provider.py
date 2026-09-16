"""Admit knowledge drafts and collect receipts from the existing review queue."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from gideon.cognition.knowledge import updates
from gideon.integrations.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)

logger = logging.getLogger(__name__)
MAX_DRAFTS_PER_CALL = 10


@dataclass(frozen=True)
class _DraftCandidate:
    source: dict[str, Any]
    title: str
    body: str

    @classmethod
    def read(cls, source: dict[str, Any]) -> _DraftCandidate:
        return cls(
            source,
            str(source.get("title") or source.get("entity") or "").strip(),
            str(source.get("body") or source.get("content") or "").strip(),
        )

    def refusal(self) -> str:
        if not self.title or not self.body:
            return "draft has no title or no body"
        if "sufficient_evidence" in self.source and not _truthy(
            self.source["sufficient_evidence"]
        ):
            return "draft reported insufficient evidence"
        return ""


@dataclass
class _DraftReceipts:
    filed: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)

    def append(self, candidate: _DraftCandidate, result: tuple[str, str, str]) -> None:
        verdict, identifier, reason = result
        row = dict(title=candidate.title, verdict=verdict, id=identifier)
        if reason:
            self.skipped.append({**row, "reason": reason})
        else:
            self.filed.append(row)

    def payload(self, considered: int) -> dict[str, Any]:
        return dict(
            filed=self.filed,
            skipped=self.skipped,
            counts=dict(
                filed=len(self.filed), skipped=len(self.skipped), considered=considered
            ),
            kind=updates.DRAFT_KIND,
            note="Filed as PROPOSALS awaiting review, not written to the knowledge store. A skipped draft is a success: a prior decision already covers it, or it is below the evidence floor.",
        )


@dataclass(frozen=True)
class _DraftSubmission:
    config: dict[str, Any]
    context: ActionContext

    def file(self, drafts: list[dict[str, Any]]) -> dict[str, Any]:
        config = self.config
        shared = dict(
            run_id=str((self.context.payload or {}).get("run_id") or ""),
            source_cadence=str(config.get("source_cadence") or "knowledge-synthesis"),
            provenance=str(config.get("provenance") or "inferred"),
            tags=[str(tag) for tag in config.get("tags") or () if str(tag).strip()],
        )
        evidence = _maybe_json(config.get("evidence"))
        receipt = _DraftReceipts()
        for candidate in map(_DraftCandidate.read, drafts[:MAX_DRAFTS_PER_CALL]):
            reason = candidate.refusal()
            if reason:
                receipt.skipped.append(dict(title=candidate.title, reason=reason))
                continue
            raw = candidate.source
            outcome = updates.queue_draft(
                title=candidate.title,
                body=candidate.body,
                target=str(raw.get("target") or config.get("target") or ""),
                source_excerpt=_excerpt(raw, evidence),
                occurrences=_int(raw.get("mentions", config.get("occurrences")), 0),
                **shared,
            )
            receipt.append(candidate, outcome)
        return receipt.payload(len(drafts))


def _drafts(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    value = cfg.get("drafts")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            logger.debug(
                "knowledge-propose: drafts is a string but not JSON", exc_info=True
            )
            value = None
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return list(filter(lambda entry: isinstance(entry, dict), value))
    return [dict(cfg)] if cfg.get("title") or cfg.get("body") else []


def _maybe_json(raw: Any) -> Any:
    if isinstance(raw, str) and raw.lstrip().startswith(("[", "{")):
        try:
            return json.loads(raw.strip())
        except (TypeError, ValueError):
            pass
    return raw


def _excerpt(draft: dict[str, Any], evidence: Any) -> str:
    own = draft.get("evidence") or draft.get("source_excerpt")
    if own:
        return _flatten(own)
    if isinstance(evidence, dict):
        key = str(draft.get("entity") or draft.get("title") or "")
        return _flatten(evidence[key]) if key in evidence else ""
    return _flatten(evidence) if evidence else ""


def _flatten(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, (tuple, list)):
        return "\n".join(map(str, raw))
    return json.dumps(raw, ensure_ascii=False)


def _truthy(raw: Any) -> bool:
    return (
        raw
        if isinstance(raw, bool)
        else str(raw).strip().lower() in {"true", "1", "yes"}
    )


def _int(raw: Any, fallback: int) -> int:
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return int(raw)
    try:
        return int(str(raw).strip())
    except (ValueError, TypeError):
        return fallback


class KnowledgeProposeActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "knowledge-propose"

    @property
    def display_name(self) -> str:
        return "Propose Knowledge Draft"

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        started = time.monotonic()
        config = action_config or {}
        drafts = _drafts(config)
        if not drafts:
            return ActionResult(
                False,
                error="knowledge-propose has nothing to file — supply `drafts`, or a `title` and `body` for a single draft",
            )
        payload = _DraftSubmission(config, ctx).file(drafts)
        return ActionResult(
            True,
            stdout=json.dumps(payload, ensure_ascii=False),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
