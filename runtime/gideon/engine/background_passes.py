from __future__ import annotations

import asyncio
import time
from typing import Any


class WatchPoll:
    def __init__(self, runtime: Any, *, web: bool, logger: Any) -> None:
        from gideon.automation.triggers.store import TriggerStore
        from gideon.core.config.loader import config_dir

        self.runtime, self.web, self.logger = runtime, web, logger
        if web:
            from gideon.automation.triggers import web_poll as source
        else:
            from gideon.automation.triggers import file_poll as source
        self.source = source
        self.store = TriggerStore(base_dir=config_dir())

    async def cycle(self) -> None:
        from gideon.security.guardrails.incident import incident_active

        if incident_active():
            return
        if self.web:
            payloads, skipped = await asyncio.to_thread(
                self.source.poll_all, self.store, now=time.time()
            )
            for row in skipped:
                self.logger.info(
                    "web_watch %s did not fire: %s", row["trigger_id"], row["reason"]
                )
        else:
            payloads = self.source.poll_all(self.store)
        for payload in payloads:
            await self.runtime._fire_file_trigger(payload)
        if not self.web:
            for scan in (
                self.runtime._scan_scratchpad,
                self.runtime._scan_autonomy_promotions,
            ):
                await asyncio.to_thread(scan)

    async def run(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.source.POLL_INTERVAL_SECS)
                await self.cycle()
            except asyncio.CancelledError:
                raise
            except Exception:
                label = "web_watch" if self.web else "file-watch"
                self.logger.warning(
                    "%s poll loop iteration failed", label, exc_info=True
                )


class AutonomySweep:
    def __init__(self, runtime: Any, *, interval: float, logger: Any) -> None:
        self.runtime, self.interval, self.logger = runtime, interval, logger

    def run(self) -> None:
        now = time.monotonic()
        if now - self.runtime._last_autonomy_scan < self.interval:
            return
        self.runtime._last_autonomy_scan = now
        for operation, failure in (
            (self.promotions, "autonomy promotion scan failed"),
            (self.revocations, "autonomy nodding revocation sweep failed"),
            (self.divergence, "lab_field_divergence sweep failed"),
        ):
            try:
                operation()
            except Exception:
                self.logger.warning(failure, exc_info=True)

    def promotions(self) -> None:
        from gideon.automation.workflows.handlers import promotion_attention_note
        from gideon.security.guardrails.ladder import propose_promotions

        proposed = propose_promotions(note_for=promotion_attention_note)
        if proposed:
            self.logger.info(
                "autonomy: proposed a promotion for %d action type(s): %s",
                len(proposed),
                ", ".join(proposed),
            )

    def revocations(self) -> None:
        from gideon.automation.workflows.handlers import nodding_revocation_cause
        from gideon.security.guardrails.autonomy import (
            registered_action_types,
            rung_state,
        )
        from gideon.security.guardrails.ladder import revoke_granted_scopes

        grants = (rung_state(spec.key) for spec in registered_action_types())
        if not any(state is not None and state.granted_at for state in grants):
            return
        cause = nodding_revocation_cause()
        if not cause:
            return
        revoked = revoke_granted_scopes(
            cause=cause, evidence_id="nodding_gate", source="nodding_loop"
        )
        if revoked:
            self.logger.warning(
                "autonomy: nodding gate revoked %d grant(s): %s",
                len(revoked),
                ", ".join(revoked),
            )

    def divergence(self) -> None:
        from gideon.assurance.evals.field_metrics import sweep_lab_field_divergence

        demoted = sweep_lab_field_divergence()
        if demoted:
            self.logger.warning(
                "autonomy: lab_field_divergence filed demotions for %d subject(s): %s",
                len(demoted),
                ", ".join(demoted),
            )


def scan_scratchpad(runtime: Any, logger: Any) -> None:
    try:
        from gideon.cognition.planning.scratchpad import scan_and_propose

        count = scan_and_propose(runtime.dashboard_state)
        if count:
            logger.info("scratchpad intake raised %d proposal(s)", count)
    except Exception:
        logger.warning("scratchpad intake failed", exc_info=True)


class KnowledgeMaintenance:
    def __init__(self, runtime: Any, logger: Any) -> None:
        self.runtime, self.logger = runtime, logger

    def register(self) -> None:
        try:
            from gideon.cognition.knowledge import maintenance_passes

            registered = maintenance_passes.register_all()
            self.logger.info(
                "graph maintenance passes registered: %s",
                ", ".join(registered) or "none",
            )
        except Exception:
            self.logger.warning(
                "graph maintenance passes not registered", exc_info=True
            )

    def depth(self) -> int:
        state = self.runtime.dashboard_state
        queue = getattr(state, "_knowledge_ingest_queue", None) if state else None
        if queue is None:
            return 0
        try:
            return int(queue.qsize())
        except Exception:
            return 0

    def install_probe(self) -> None:
        try:
            from gideon.cognition.knowledge import maintenance

            maintenance.set_in_flight_probe(self.depth)
        except Exception:
            self.logger.warning(
                "graph-maintenance in-flight probe not installed", exc_info=True
            )
