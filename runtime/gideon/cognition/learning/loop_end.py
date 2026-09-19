from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from gideon.automation.loop import files as loop_files

logger = logging.getLogger(__name__)
_MAX_TRACE_DRAFTS = 2


@dataclass
class _LoopRunView:
    id: str
    intent: str
    workflow_name: str
    status: str
    project_id: str = ""
    created_at: str = ""
    origin: Any = field(default_factory=lambda: SimpleNamespace(session_key=""))

    @classmethod
    def of(cls, loop: Any) -> "_LoopRunView":
        timestamp = float(getattr(loop, "created_at", 0.0) or 0.0)
        created = (
            datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
            if timestamp
            else ""
        )
        values: dict = {
            "id": str(getattr(loop, "id", "") or ""),
            "intent": str(getattr(loop, "task", "") or "").strip(),
            "workflow_name": f"loop:{getattr(loop, 'kind', '') or 'loop'}",
            "status": str(getattr(loop, "status", "") or ""),
            "project_id": str(getattr(loop, "project_id", "") or ""),
            "created_at": created,
            "origin": SimpleNamespace(
                session_key=str(getattr(loop, "session_key", "") or "")
            ),
        }
        return cls(**values)


class _LoopRunStore:
    def list_runs(
        self, *, workflow_name: str = "", limit: int = 60
    ) -> tuple[list[_LoopRunView], int]:
        from gideon.automation.loop import store as loop_store

        catalog = list(map(_LoopRunView.of, loop_store.list_all()))
        selected = (
            catalog
            if not workflow_name
            else list(filter(lambda view: view.workflow_name == workflow_name, catalog))
        )
        page = selected[: max(1, limit)]
        return page, len(page)


class _LoopLearning:
    def __init__(
        self, view: _LoopRunView, service: Any, journal: Any, store: Any
    ) -> None:
        self.view, self.service = view, service
        self.journal, self.store = journal, store
        self.proposed = 0

    def ingest(self) -> None:
        try:
            loop_files.record_cycle_findings(self.view.id)
        except Exception:
            logger.debug(
                "loop-end: final ingest failed for %s", self.view.id, exc_info=True
            )

    def invert(self) -> None:
        result = self.mining.invert_intent(self.view, journal=self.journal)
        if result.inverted:
            remaining = ", ".join(result.unaddressed[:5]) or "(none)"
            logger.info(
                "loop %s: intent inversion — drift %.2f, unaddressed %s",
                result.run_id,
                result.drift,
                remaining,
            )

    def trace(self) -> None:
        batch, _ = self.mining.positive_path_candidates(
            workflow_name=self.view.workflow_name,
            journal=self.journal,
            store=self.store,
        )
        for candidate in batch[:_MAX_TRACE_DRAFTS]:
            if self.mining.file_positive_trace(
                candidate, session_key=self.view.origin.session_key
            ):
                self.proposed += 1

    def compare(self) -> None:
        from gideon.cognition.learning import run_end
        from gideon.cognition.learning.detectors import Action, similarity_verdict

        self.mining.index_run_spec(self.view, self.service, journal=self.journal)
        matches = self.mining.similar_run_matches(
            self.view, self.service, journal=self.journal
        )
        if matches.blind:
            return
        decision = similarity_verdict(matches=matches.matches, now=time.time())
        if decision.action != Action.AUTO_FILE.value:
            return
        if run_end._file_similarity_draft(self.view, matches, decision):
            self.proposed += 1

    def attempt(self, operation: Any, label: str) -> None:
        try:
            operation()
        except Exception:
            logger.debug("loop-end: %s failed", label, exc_info=True)

    def execute(self) -> dict[str, int]:
        self.ingest()
        try:
            from gideon.cognition.learning import mining
        except Exception:
            logger.debug("loop-end: mining unavailable", exc_info=True)
            return {"mined": 0, "proposed": 0}
        self.mining = mining
        for operation, label in (
            (self.invert, "intent inversion"),
            (self.trace, "positive-path mining"),
        ):
            self.attempt(operation, label)
        if self.service is not None and getattr(self.service, "has_vector", False):
            self.attempt(self.compare, "similarity pass")
        return dict.fromkeys(("mined", "proposed"), self.proposed)


def capture(
    loop: Any, service: Any, *, journal: Any = None, store: Any = None
) -> dict[str, int]:
    from gideon.automation.loop import journal as loop_journal

    journal = journal or loop_journal
    store = store or _LoopRunStore()
    return _LoopLearning(_LoopRunView.of(loop), service, journal, store).execute()
