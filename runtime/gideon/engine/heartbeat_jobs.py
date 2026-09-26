from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from gideon.core.config import AppConfig
from gideon.engine.heartbeat import is_keep_response, strip_keep_sentinel
from gideon.engine.session import BACKGROUND_KEY
from gideon.security.security import redact_credentials, redact_exfiltration_urls


def _safe_text(text: str) -> str:
    return redact_credentials(redact_exfiltration_urls(text)[0])[0]


class UsageWitness:
    def __init__(self, client: Any) -> None:
        model = getattr(getattr(client, "client", None), "_model", "") or ""
        self.model = model if isinstance(model, str) and model != "auto" else ""

    def __call__(self, event: Any) -> None:
        from gideon.operations.usage_ledger import record_from_event

        record_from_event(
            event,
            source="background",
            session_key=BACKGROUND_KEY,
            provider="acp",
            model=self.model,
        )


class HeartbeatJobs:
    def __init__(self, runtime: Any, *, collect: Any, audit: Any, logger: Any) -> None:
        self.runtime = runtime
        self.collect = collect
        self.audit = audit
        self.logger = logger

    @asynccontextmanager
    async def background_client(self):
        directory = self.runtime.sessions
        assert directory is not None
        client, is_new, _ = await directory.get_or_create(BACKGROUND_KEY)
        try:
            yield client, is_new
        finally:
            directory.release(BACKGROUND_KEY)
            await directory.recycle_background()

    async def task(self, text: str, target: str) -> str:
        from gideon.security.guardrails.policy import approval_policy_for_session

        runtime = self.runtime
        assert runtime.sessions is not None
        assert runtime.ctx_builder is not None
        try:
            async with self.background_client() as (client, is_new):
                message, _ = runtime.ctx_builder.build_message(text, is_new)
                reply = await self.collect(
                    client,
                    message,
                    approval_policy=approval_policy_for_session(BACKGROUND_KEY),
                    hooks=runtime.ctx_builder.hooks,
                    on_tool_approval=None,
                    on_complete=UsageWitness(client),
                )
                reply = reply or "_No response._"
        except Exception:
            self.logger.exception("Heartbeat task failed: %s", text[:80])
            raise
        safe = _safe_text(reply)
        if is_keep_response(safe):
            self.logger.info(
                "Heartbeat task incomplete, suppressing delivery: %s", text[:80]
            )
        else:
            await runtime._deliver_result(
                "Heartbeat", _safe_text(text[:100]), strip_keep_sentinel(safe), target
            )
        return safe

    async def commitments(self) -> None:
        if not AppConfig.load().memory.proactive_commitments:
            return
        consolidator = self.runtime.consolidator
        if consolidator is None:
            return
        service = consolidator._svc
        if not service.has_vector:
            return
        try:
            pending = service.due_commitments_all(
                now_iso=datetime.now(timezone.utc).isoformat()
            )
        except Exception:
            self.logger.debug("due-commitment scan failed", exc_info=True)
            return
        for item in pending:
            from gideon.engine.proactive_plan import plan_commitment
            from gideon.engine import proactive_decisions
            from gideon.extensions.providers.entity_routes import notification_posture

            topic = str(item.get("key") or "")
            previous = proactive_decisions.get(topic)
            if proactive_decisions.suppressed(previous) or (previous and previous.get("delivered_at")):
                service.dismiss_commitment(topic)
                continue
            try:
                recent = service.episodic_list(limit=5)
                routines = service.procedural_priors(limit=3)
            except Exception:
                self.logger.debug("proactive context read failed", exc_info=True)
                recent, routines = [], []
            context = {
                "recent_sessions": [{"id": row.get("id"), "text": row.get("text"),
                                     "created_at": row.get("created_at")} for row in recent],
                "learned_routines": [{"key": row.get("key"), "text": row.get("text")}
                                     for row in routines],
            }
            posture = notification_posture("info")
            plan = plan_commitment(
                item,
                posture=posture,
                dashboard_available=self.runtime.dashboard_state is not None,
                context=context,
            )
            text = _safe_text(item.get("text", ""))
            proactive_decisions.record(
                topic, agent=str(item.get("agent") or ""), text=text,
                action=plan.action, reason=plan.reason, policy=posture,
                destination=plan.channel, context=context,
            )
            self.audit().log_api_access(
                caller="heartbeat",
                operation="commitment_plan",
                outcome=plan.action,
                source="gateway",
                resources=f"agent={item.get('agent', '')},reason={plan.reason}",
            )
            if plan.action == "silence":
                service.dismiss_commitment(item["key"])
                continue
            if plan.action == "defer":
                continue
            try:
                await self.runtime._deliver_result(
                    "Proactive check-in", "", text, plan.channel
                )
                proactive_decisions.mark_delivery(topic, destination=plan.channel)
                self.audit().log_api_access(
                    caller="heartbeat",
                    operation="commitment_deliver",
                    outcome="approved",
                    source="gateway",
                    resources=f"agent={item.get('agent', '')},channel={plan.channel}",
                )
                service.dismiss_commitment(item["key"])
            except Exception:
                proactive_decisions.mark_delivery(topic, destination=plan.channel, error="delivery failed")
                self.logger.warning(
                    "Commitment delivery failed for %s", item["key"], exc_info=True
                )

    async def archive(self) -> None:
        state = getattr(self.runtime, "dashboard_state", None)
        if state is None:
            return
        days = int(AppConfig.load().session.auto_archive_days)
        if days <= 0:
            return
        from gideon.interfaces.dashboard.chat_persistence import save_session_to_history
        from gideon.interfaces.dashboard.session_lifecycle import run_auto_archive

        changed = run_auto_archive(state, days=days)
        for key in changed:
            session = state._sessions.get(key)
            if session is not None:
                save_session_to_history(state, session, force=True)
        if changed:
            state.push_sessions_update()
