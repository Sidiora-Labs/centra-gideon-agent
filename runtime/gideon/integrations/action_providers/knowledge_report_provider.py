"""Resolve scheduled report evidence and publish one finding under its run lease."""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Sequence

from gideon.integrations.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)
from gideon.integrations.action_providers.knowledge_persist_provider import _open_store

logger = logging.getLogger(__name__)
MAX_SCOPE_ITEMS = 40
CONTINUE_TOKEN = "CONTINUE"


@dataclass(frozen=True)
class _ReportLease:
    report_id: str

    def acquire(self) -> bool:
        from gideon.automation.triggers import claims
        from gideon.automation.triggers.scheduling import Claim
        from gideon.cognition.knowledge.research_reports import report_claim_id

        identifier = report_claim_id(self.report_id)
        available = not claims.is_running(identifier)
        if available:
            claims.write_claim(
                Claim(
                    trigger_id=identifier,
                    holder="knowledge-report",
                    claimed_at=time.time(),
                )
            )
        return available

    def release(self) -> None:
        from gideon.automation.triggers import claims
        from gideon.cognition.knowledge.research_reports import report_claim_id

        try:
            claims.release_claim(report_claim_id(self.report_id))
        except Exception:
            logger.debug("knowledge-report: claim release failed", exc_info=True)


def _hold_claim(report_id: str) -> bool:
    return _ReportLease(report_id).acquire()


def _release_claim(report_id: str) -> None:
    _ReportLease(report_id).release()


def _report_id(config: dict[str, Any]) -> str:
    return str((config or {}).get("report_id", "") or "").strip()


def _missing_report() -> ActionResult:
    return ActionResult(
        success=False,
        error="knowledge-report is missing 'report_id' — name the report to run",
    )


class KnowledgeReportActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "knowledge-report"

    @property
    def display_name(self) -> str:
        return "Run Research Report"

    @property
    def supports_dry_run(self) -> bool:
        return True

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        identifier = _report_id(action_config)
        if not identifier:
            return _missing_report()
        acquired = _hold_claim(identifier)
        if acquired:
            try:
                return await self._execute_locked(action_config, ctx, timeout)
            finally:
                _release_claim(identifier)
        return ActionResult(
            success=True,
            stdout=json.dumps({"report_id": identifier, "skipped": "already_running"}),
        )

    async def _execute_locked(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        return await _ReportRun(action_config or {}, ctx, timeout).execute()


@dataclass(frozen=True)
class _ResolvedEvidence:
    timestamp: float
    source: list[dict[str, Any]]
    context: list[dict[str, Any]]

    @classmethod
    def read(cls, definition: Any, finding_kind: str) -> _ResolvedEvidence:
        stamp = time.time()
        store = _open_store()
        try:
            source = _resolve_scope(
                store,
                getattr(definition, "source", None),
                cutoff_ts=_source_cutoff(definition, now=stamp),
                exclude_kind=finding_kind,
            )
            context = _resolve_scope(
                store,
                getattr(definition, "context", None),
                cutoff_ts=_context_cutoff(definition, now=stamp),
                exclude_kind=finding_kind,
            )
            return cls(stamp, source, context)
        finally:
            close = getattr(store, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    logger.debug("report scope connection close failed", exc_info=True)


@dataclass
class _ReportRun:
    config: dict[str, Any]
    context: ActionContext
    timeout: int
    started: float = field(default_factory=time.monotonic)

    def result(
        self, payload: dict[str, Any] | None = None, *, error: str = ""
    ) -> ActionResult:
        return ActionResult(
            success=not bool(error),
            error=error,
            stdout=json.dumps(payload) if payload is not None else "",
            duration_ms=int((time.monotonic() - self.started) * 1000),
        )

    async def execute(self) -> ActionResult:
        identifier = _report_id(self.config)
        if not identifier:
            return _missing_report()
        rr = _reports_module()
        definition = rr.get_report(identifier)
        if definition is None:
            return ActionResult(
                success=False,
                error=f"knowledge-report: no report definition {identifier!r}",
            )
        if not getattr(definition, "enabled", True):
            return self.result({"report_id": identifier, "skipped": "disabled"})
        if not bool(self.config.get("manual")):
            due, reason = rr.is_due(definition, now=time.time())
            if not due:
                return self.result(
                    {"report_id": identifier, "skipped": "not_due", "reason": reason}
                )
        try:
            evidence = _ResolvedEvidence.read(definition, str(rr.FINDING_KIND))
        except Exception as exc:
            logger.debug(
                "knowledge-report %s: scope resolution failed",
                identifier,
                exc_info=True,
            )
            rr.record_run(identifier, ok=False, error=f"scope resolution failed: {exc}")
            return self.result(
                error=f"knowledge-report: could not resolve the scope for {identifier}: {exc}"
            )
        if self.config.get("dry_run"):
            return self.result(
                {
                    "report_id": identifier,
                    "dry_run": True,
                    "source_items": [
                        str(row.get("id") or "") for row in evidence.source
                    ],
                    "context_items": [
                        str(row.get("id") or "") for row in evidence.context
                    ],
                    "resolution_ts": evidence.timestamp,
                }
            )
        if evidence.source:
            return await self.publish(rr, definition, evidence)
        rr.record_run(identifier, ok=True, watermark_ts=evidence.timestamp)
        return self.result(
            {
                "report_id": identifier,
                "source_items": 0,
                "note": "nothing new arrived in this report's source scope — no finding "
                "written, watermark advanced",
                "watermark_ts": evidence.timestamp,
                "model_calls": 0,
            }
        )

    async def publish(
        self, rr: Any, definition: Any, evidence: _ResolvedEvidence
    ) -> ActionResult:
        identifier = _report_id(self.config)
        try:
            refs = _numbered_refs(rr, definition, evidence.source, evidence.context)
            text, calls = await _write_finding(definition, refs)
            if not text.strip():
                rr.record_run(
                    identifier, ok=False, error="the model returned an empty finding"
                )
                return self.result(
                    error=f"knowledge-report: {identifier} produced an empty finding"
                )
            write = _persist_config(rr, definition, text=text, refs=refs)
            receipt = await _persist(write, self.context, timeout=self.timeout)
        except Exception as exc:
            logger.debug("knowledge-report %s: run failed", identifier, exc_info=True)
            rr.record_run(identifier, ok=False, error=str(exc)[:200])
            return self.result(error=f"knowledge-report: {identifier} failed: {exc}")
        if receipt.success:
            _emit_persisted_finding(definition, identifier, text, receipt)
            rr.record_run(identifier, ok=True, watermark_ts=evidence.timestamp)
            return self.result(
                {
                    "report_id": identifier,
                    "source_items": len(evidence.source),
                    "context_items": len(evidence.context),
                    "registered_sources": len(refs),
                    "citation_policy": str(getattr(definition, "citation_policy", "")),
                    "model_calls": calls,
                    "watermark_ts": evidence.timestamp,
                    "persist": _persist_body(receipt),
                }
            )
        rr.record_run(identifier, ok=False, error=receipt.error[:200])
        return self.result(
            error=f"knowledge-report: {identifier} could not persist its finding: {receipt.error}"
        )


def _reports_module() -> Any:
    import gideon.cognition.knowledge.research_reports as rr

    return rr


def _window_start(scope: Any, now: float, fallback: float) -> float:
    seconds = int(getattr(scope, "window_secs", 0) or 0)
    return now - seconds if seconds > 0 else fallback


def _source_cutoff(defn: Any, *, now: float) -> float:
    scope = getattr(defn, "source", None)
    if int(getattr(scope, "window_secs", 0) or 0) > 0:
        return _window_start(scope, now, 0.0)
    return float(getattr(defn, "watermark_ts", 0.0) or 0.0)


def _context_cutoff(defn: Any, *, now: float) -> float:
    return _window_start(getattr(defn, "context", None), now, 0.0)


@dataclass(frozen=True)
class _ScopeSelection:
    store: Any
    cutoff: float
    excluded_kind: str

    def read(self, scope: Any) -> list[dict[str, Any]]:
        roots = (
            [str(tag) for tag in (getattr(scope, "tags", ()) or ())]
            if scope is not None
            else []
        )
        if not roots:
            return []
        identifiers: set = set()
        for tag_id in _tag_closure(self.store, roots):
            identifiers.update(
                str(identifier) for identifier in self.store._items_with_tag(tag_id)
            )
        selected = []
        for identifier in identifiers:
            row = self.store.get_item(identifier)
            if not row or row.get("is_archived"):
                continue
            if str(row.get("kind") or "") == self.excluded_kind:
                continue
            if str(row.get("status") or "active") != "active":
                continue
            changed = _epoch(row)
            if changed > self.cutoff:
                selected.append((changed, str(row.get("id") or ""), row))
        selected.sort(key=lambda entry: entry[:2])
        return [entry[2] for entry in selected[-MAX_SCOPE_ITEMS:]]


def _resolve_scope(
    store: Any, scope: Any, *, cutoff_ts: float, exclude_kind: str
) -> list[dict[str, Any]]:
    return _ScopeSelection(store, cutoff_ts, exclude_kind).read(scope)


def _tag_closure(store: Any, roots: Sequence[str]) -> set[int]:
    try:
        rows = list(store.list_tags())
    except Exception:
        logger.debug("knowledge-report: tag taxonomy unreadable", exc_info=True)
        return set()
    names = {root.strip().lower() for root in roots if root and root.strip()}
    descendants: dict[int, list[int]] = defaultdict(list)
    pending: deque[int] = deque()
    for row in rows:
        try:
            identifier = int(row["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if str(row.get("name") or "").strip().lower() in names:
            pending.append(identifier)
        if row.get("parent_id") is not None:
            try:
                descendants[int(row["parent_id"])].append(identifier)
            except (TypeError, ValueError):
                pass
    visited: set[int] = set()
    while pending:
        identifier = pending.popleft()
        if identifier not in visited:
            visited.add(identifier)
            pending.extend(descendants.get(identifier, ()))
    return visited


def _epoch(item: dict[str, Any]) -> float:
    for raw in (item.get("updated_at"), item.get("created_at")):
        if isinstance(raw, str) and raw.strip():
            try:
                return datetime.fromisoformat(raw.strip()).timestamp()
            except ValueError:
                pass
    return 0.0


def _numbered_refs(
    rr: Any,
    defn: Any,
    source_items: Sequence[dict[str, Any]],
    context_items: Sequence[dict[str, Any]],
) -> tuple[Any, ...]:
    from gideon.cognition.knowledge import citations

    policy = str(getattr(defn, "citation_policy", ""))
    evidence = (
        (*source_items, *context_items)
        if policy == str(rr.ALLOW_CITING_CONTEXT)
        else tuple(source_items)
    )
    return citations.register_sources(evidence)


@dataclass
class _FindingDraft:
    definition: Any
    references: Sequence[Any]
    limit: int
    calls: int = 0
    text: str = ""
    notes: list[str] = field(default_factory=list)

    def accept(self, reply: Any) -> bool:
        self.calls += 1
        raw = str(reply or "")
        self.text = _strip_continue(raw)
        continuing = _wants_another_pass(raw)
        if continuing:
            self.notes.append(self.text)
        return continuing and self.calls < self.limit

    async def run(self) -> tuple[str, int]:
        while self.calls < self.limit:
            prompt = _build_prompt(
                self.definition,
                self.references,
                turn=self.calls + 1,
                cap=self.limit,
                notes=self.notes,
            )
            if not self.accept(await _one_shot(prompt)):
                break
        return self.text, self.calls


async def _write_finding(defn: Any, refs: Sequence[Any]) -> tuple[str, int]:
    limit = max(1, int(getattr(defn, "iteration_cap", 1) or 1))
    return await _FindingDraft(defn, refs, limit).run()


def _wants_another_pass(text: str) -> bool:
    return (
        next(
            (
                line.strip()
                for line in reversed(str(text or "").splitlines())
                if line.strip()
            ),
            "",
        )
        == CONTINUE_TOKEN
    )


def _strip_continue(text: str) -> str:
    lines = str(text or "").rstrip().splitlines()
    stop = len(lines)
    while stop and lines[stop - 1].strip() in ("", CONTINUE_TOKEN):
        stop -= 1
    return "\n".join(lines[:stop]).strip()


def _build_prompt(
    defn: Any, refs: Sequence[Any], *, turn: int, cap: int, notes: Sequence[str]
) -> str:
    numbers = [f"[{int(getattr(ref, 'marker', 0))}]" for ref in refs]
    evidence = [
        f"{number} {getattr(ref, 'excerpt', '')}" for number, ref in zip(numbers, refs)
    ]
    sections = [
        "You are writing ONE research finding for a scheduled report.",
        f"Report: {getattr(defn, 'name', '') or getattr(defn, 'id', '')}",
        "",
        str(getattr(defn, "prompt", "") or ""),
        "",
        "SOURCES — cite a claim by appending the source's bracketed number:",
        *evidence,
        "",
        f"The only valid citation markers are {', '.join(numbers) or '(none)'}. Do NOT invent a citation marker, and "
        "do not cite a number that is not listed above: an invented marker is dropped when the "
        "finding is stored, so the sentence it was supposed to support silently loses its provenance.",
        f"This is pass {turn} of at most {cap}.",
        "Reply with the finished finding. Only if you genuinely need another pass, end your "
        f"reply with {CONTINUE_TOKEN} alone on the last line.",
    ]
    if notes:
        sections.extend(("", "Your notes from earlier passes:", *notes))
    return "\n".join(sections)


async def _one_shot(prompt: str) -> str:
    from gideon.integrations.llm_helpers import one_shot_completion

    return await one_shot_completion(prompt, use_case="reasoning")


def _persist_config(
    rr: Any, defn: Any, *, text: str, refs: Sequence[Any]
) -> dict[str, Any]:
    sources = []
    for ref in refs:
        sources.append(
            dict(
                marker=int(getattr(ref, "marker", 0)),
                item_id=str(getattr(ref, "item_id", "") or ""),
                chunk_index=int(getattr(ref, "chunk_index", -1)),
                excerpt=str(getattr(ref, "excerpt", "") or ""),
            )
        )
    headline = next((line.strip() for line in text.splitlines() if line.strip()), "")
    title = getattr(defn, "name", "") or getattr(defn, "id", "") or "Research finding"
    return dict(
        title=str(title),
        content=text,
        kind=str(rr.FINDING_KIND),
        summary=headline[:280],
        tags=[
            str(tag)
            for tag in (getattr(getattr(defn, "source", None), "tags", ()) or ())
        ],
        citation_sources=sources,
        mode="upsert",
        source_ref=f"research-report:{getattr(defn, 'id', '')}",
    )


async def _persist(
    action_config: dict[str, Any], ctx: ActionContext, *, timeout: int = 30
) -> ActionResult:
    from gideon.integrations.action_providers.knowledge_persist_provider import (
        KnowledgePersistActionProvider,
    )

    writer = KnowledgePersistActionProvider()
    return await writer.execute(action_config, ctx, timeout=timeout)


def _persist_body(result: ActionResult) -> dict[str, Any]:
    try:
        decoded = json.loads(result.stdout or "{}")
    except (json.JSONDecodeError, ValueError):
        decoded = None
    return decoded if isinstance(decoded, dict) else {}


def _emit_persisted_finding(
    definition: Any, report_id: str, text: str, receipt: ActionResult
) -> None:
    """Surface a finding only after the knowledge write has succeeded."""
    try:
        from gideon.integrations.action_providers.services import get_action_services
        from gideon.workspace import notification_kinds

        services = get_action_services()
        state = getattr(services, "state", None)
        if state is None:
            return
        persisted = _persist_body(receipt)
        title = str(
            getattr(definition, "name", "")
            or getattr(definition, "id", "")
            or "Research finding"
        )
        body = next((line.strip() for line in text.splitlines() if line.strip()), "")
        state.notify(
            notification_kinds.RESEARCH_FINDING,
            title,
            body[:280],
            meta={
                "report_id": report_id,
                "knowledge_item": str(persisted.get("item_id") or ""),
            },
        )
    except Exception:
        logger.debug("knowledge-report: finding notification failed", exc_info=True)
