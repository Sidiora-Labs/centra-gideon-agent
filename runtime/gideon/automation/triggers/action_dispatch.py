"""Trigger action claims, journal settlement and completion ownership."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from gideon.automation.schedule_history import ExecutionJournal
from gideon.automation.triggers.store import TriggerStore


class TriggerDispatcher:
    def __init__(self, trigger_store: Callable[[], TriggerStore], runs_store: Callable[[], ExecutionJournal], logger: logging.Logger):
        self.trigger_store = trigger_store
        self.runs_store = runs_store
        self.logger = logger

    async def _dispatch_store_action(self, trigger: Any, payload: dict[str, Any], *, event: str='manual.run', reentry_note_id: str='', reentry_principal: Any=None, reentry_state: Any=None, admitted_claim: Any=None, accepted_origin: Any=None) -> tuple[bool, str]:
        """Run a store trigger's declared action through the action-provider registry.

        The same path `gateway._fire_file_trigger` uses — a manual Run and an autonomous fire share one
        dispatch so their behaviour cannot drift. Returns `(ran, note)`: whether the action actually
        executed, and a short status string for the run result.

        `event` labels the source to the action provider the way `gateway._fire_store_trigger` does
        (`file.changed`, `trigger.chained`): a manual Run keeps the default `manual.run`, a pull-on-view
        refresh passes `view.rendered`. It is a label only — the dispatch is the ONE store-action path,
        not a per-caller fork.

        🔴 BOTH ACTION SHAPES, because a real store holds both (#395). This read the FLAT
        `workflow["provider"]` only, and every trigger the API/CLI/app-reconciler/digest writes nests
        its action under `workflow["inline"]` — so `provider_name` was None for essentially every stored
        row and the Run button was a silent no-op on all of them. The docstring above claimed this path
        "cannot drift" from the autonomous fire while `gateway._fire_store_trigger` unwrapped `inline`
        and this one did not. `schedule_view._inline_action` and `screen.requested_capabilities` both
        document the same two-shape contract; this now matches the idiom all three use.

        Provider AND config come from the SAME resolved dict. Taking the provider from `inline` and the
        config from the outer dict would run the right action with an empty config — a worse failure
        than the no-op, because it looks like it worked.

        `ran` is returned rather than folded into the note because the caller answers HTTP `ok` with it:
        a run that resolved no provider is not a success, and reporting `ok: true` for it is what let
        this bug hide behind a 200 for a whole release.
        """
        import time
        import uuid
        from gideon.integrations.action_providers import ActionContext, get_action_provider
        from gideon.integrations.action_providers.registry import _ensure_default_providers_registered
        trigger_id = str(getattr(trigger, 'id', '') or '')
        workflow = trigger.workflow or {}
        inline = workflow.get('inline') if isinstance(workflow.get('inline'), dict) else None
        action = inline or workflow
        provider_name = str(action.get('provider') or '')
        if not provider_name:
            return await self._record_manual_refusal(trigger, 'no action provider configured')
        _ensure_default_providers_registered()
        provider = get_action_provider(provider_name)
        if provider is None:
            return await self._record_manual_refusal(trigger, f'unknown action provider {provider_name!r}')
        trigger_id = str(getattr(trigger, 'id', '') or '')
        ctx = ActionContext(event=event, trigger_id=trigger_id, context=trigger_id if provider_name == 'run-workflow' else '', payload=payload, accepted_origin=accepted_origin)
        started = time.time()
        holder = self._manual_owner(trigger_id, active=True, admitted_claim=admitted_claim)
        if not holder:
            return await self._record_manual_refusal(trigger, 'another run owns this trigger, or its process ownership is unknown', outcome='skipped_overlap')
        attempt_id = f'manual-{uuid.uuid4().hex}'
        from contextlib import AbstractContextManager
        reentry_context: AbstractContextManager
        if reentry_note_id:
            from gideon.security.auto_denials import owner_reentry_attempt
            reentry_context = owner_reentry_attempt(reentry_state, reentry_note_id, reentry_principal, origin_kind='trigger', origin_id=trigger_id, attempt_id=attempt_id)
        else:
            from contextlib import nullcontext
            reentry_context = nullcontext(None)
        with reentry_context as reentry:
            if reentry_note_id and reentry is None:
                self._manual_owner(trigger_id, active=False, holder=holder)
                return await self._record_manual_refusal(trigger, 'the unanswered call is no longer open for this trigger')
            try:
                from gideon.automation.triggers.secrets import resolve
                from gideon.integrations.action_providers.command_lifecycle import action_timeout
                config = resolve(action.get('config') or {})
                timeout = action_timeout(config, {'bash': 300}.get(provider_name, 30))
                from gideon.security.guardrails.policy import unattended_dispatch_key
                from gideon.security.net.policy import egress_held_to
                with egress_held_to(unattended_dispatch_key(f'trigger:{trigger_id}')):
                    result = await provider.execute(config, ctx, timeout=timeout)
            except Exception as exc:
                await self._record_manual_run(trigger, started=started, exc=exc, run_id=attempt_id)
                self._manual_owner(trigger_id, active=False, holder=holder)
                return (False, f'failed: {type(exc).__name__}: {exc}')
            recorded = await self._record_manual_run(trigger, started=started, result=result, run_id=attempt_id)
        from gideon.engine.trigger_outcomes import status_for_result
        from gideon.integrations.action_providers.services import get_action_services
        status = status_for_result(result)
        if recorded is None:
            if status not in {'launched', 'queued', 'waiting'}:
                self._manual_owner(trigger_id, active=False, holder=holder)
            return (False, 'the action ran but its completion could not be recorded')
        if status in {'launched', 'queued', 'waiting'} and getattr(result, 'completion', None) is not None:
            services = get_action_services()
            if services is None:
                return (False, 'completion services unavailable')
            pending = self._settle_manual_action(trigger, result.completion, started, payload, holder=holder)
            try:
                import asyncio
                scheduled = services.spawn_background(pending)
                if isinstance(scheduled, asyncio.Future):
                    services.state._background_tasks.add(scheduled)
                    scheduled.add_done_callback(services.state._background_tasks.discard)
            except BaseException:
                pending.close()
                raise
            return (True, status)
        from gideon.automation.triggers import parks
        if status not in {'launched', 'queued', 'waiting'} or parks.parked(result):
            self._manual_owner(trigger_id, active=False, holder=holder)
        if parks.parked(result) and payload.get('review_id'):
            parks.associate_review(trigger, str(payload['review_id']))
        if event == 'manual.answer' and payload.get('review_id'):
            await self._finish_manual_review(trigger, payload, result, recorded)
        if status not in {'launched', 'queued', 'waiting', 'interrupted', 'failure'}:
            from gideon.automation.triggers.routing import routed
            from gideon.automation.triggers.service import retire_after_run
            retire_after_run(routed(self.trigger_store()), trigger, status=status, from_review=bool(payload.get('review_id')), settled_holder=holder)
        if result is not None and (not bool(getattr(result, 'success', True))):
            note = str(getattr(result, 'error', '') or '') or 'the action reported failure'
            return (False, f'failed: {note}')
        return (True, status if status in {'launched', 'queued', 'waiting'} else 'ran')

    async def _record_manual_refusal(self, trigger: Any, reason: str, *, outcome: str='skipped_gate') -> tuple[bool, str]:
        import time
        from gideon.integrations.action_providers.base import ActionResult
        await self._record_manual_run(trigger, started=time.time(), result=ActionResult(False, error=reason, outcome=outcome))
        return (False, f'refused: {reason}')

    def _manual_owner(self, trigger_id: str, *, active: bool, holder: str='', admitted_claim: Any=None) -> str:
        import os
        import time
        from uuid import uuid4
        from gideon.automation.triggers import claims
        from gideon.automation.triggers.routing import routed
        from gideon.automation.triggers.scheduling import Claim
        store = routed(self.trigger_store())
        row = store.get(trigger_id)
        if active:
            if row is None:
                return ''
            if row.trigger.run_owner_pid and (not claims.read_claims(trigger_id, base_dir=store.base_dir)):
                return ''
            if admitted_claim is not None and admitted_claim.trigger_id == trigger_id:
                holder = claims.bind_owner(trigger_id, owner_pid=os.getpid(), base_dir=store.base_dir, expected_holder=admitted_claim.holder)
            else:
                claim = Claim(trigger_id, f'manual:{uuid4().hex}', time.time())
                if not claims.acquire_claim(claim, owner_pid=os.getpid(), overlap=row.trigger.overlap, base_dir=store.base_dir):
                    return ''
                holder = claim.holder
            if not holder:
                return ''
        elif not claims.release_claim(trigger_id, base_dir=store.base_dir, holder=holder, owner_pid=os.getpid()):
            return ''
        if row is None:
            return holder
        if active or row.trigger.run_owner_pid == os.getpid():
            row.trigger.run_owner_pid = os.getpid() if active else next((claim.owner_pid for claim in claims.read_claims(trigger_id, base_dir=store.base_dir)), 0)
            try:
                store.upsert(row.trigger)
            except BaseException:
                if active:
                    claims.release_claim(trigger_id, base_dir=store.base_dir, holder=holder, owner_pid=os.getpid())
                raise
        return holder

    async def _settle_manual_action(self, trigger: Any, completion: Any, started: float, payload: dict[str, Any], *, holder: str) -> None:
        import asyncio
        from gideon.automation.triggers.routing import routed
        from gideon.automation.triggers.service import retire_after_run
        from gideon.engine.trigger_outcomes import status_for_result
        from gideon.integrations.action_providers.base import ActionResult
        try:
            try:
                from gideon.security.guardrails.policy import unattended_dispatch_key
                from gideon.security.net.policy import egress_held_to
                with egress_held_to(unattended_dispatch_key(f'trigger:{trigger.id}')):
                    result = await completion()
            except asyncio.CancelledError:
                result = ActionResult(False, outcome='interrupted', error='completion observer stopped during shutdown')
            except Exception as error:
                result = ActionResult(False, error=f'{type(error).__name__}: {error}')
            record = await self._record_manual_run(trigger, started=started, result=result)
            status = status_for_result(result)
            await self._finish_manual_review(trigger, payload, result, record)
            if record is not None and status in {'success', 'ran_late', 'degraded', 'skipped_noop'}:
                retire_after_run(routed(self.trigger_store()), trigger, status=status, from_review=bool(payload.get('review_id')), settled_holder=holder)
        finally:
            self._manual_owner(trigger.id, active=False, holder=holder)

    async def _finish_manual_review(self, trigger: Any, payload: dict[str, Any], result: Any, record: dict[str, Any] | None) -> None:
        from gideon.automation.triggers import parks
        from gideon.automation.triggers.review import TriggerReviewStore, record_review_outcome
        from gideon.engine.trigger_outcomes import status_for_result
        review_id = str(payload.get('review_id') or '')
        reviews = TriggerReviewStore(self.trigger_store().base_dir)
        card = reviews.get(review_id) if review_id else None
        if card is None:
            return
        status = status_for_result(result)
        if status == 'waiting':
            parks.associate_review(trigger, review_id)
            return
        if record is None:
            reviews.release_run(review_id, error='completion could not be recorded')
        elif status in {'success', 'ran_late', 'degraded', 'skipped_noop'}:
            outcome = 'interrupted_retried' if card.get('reason') == 'interrupted' else 'ran_late'
            if reviews.resolve(review_id, decision='run_now', outcome=outcome, allow_running=True):
                await record_review_outcome(card, outcome, action_record=record, base_dir=reviews.base_dir)
        else:
            reviews.release_run(review_id, error=str(getattr(result, 'error', '') or status))

    async def _record_manual_run(self, trigger: Any, *, started: float, result: Any=None, exc: BaseException | None=None, run_id: str | None=None) -> dict[str, Any] | None:
        """Append a MANUAL run record and advance the trigger's last-run stamp (#308).

        Reuses the SAME ledger the autonomous fire path appends to — `ExecutionJournal`, keyed by the
        trigger id (via this module's `_runs_store()`) — and the SAME
        `last_success_at`/`last_failure_at` stamp `gateway._record_fire_outcome` writes, so a Run button
        and an autonomous tick leave the same evidence that a run happened. This is not a parallel
        recorder: it writes the identical `ExecutionRecord` shape to the identical store, and stamps the
        identical trigger fields. The read surfaces (`/history`, `_last_run_ts`, the completion watcher)
        already work — they were simply reading a store nothing wrote to on this path.

        Tagged `trigger="manual"`, not the autonomous exit type, for two behaviours the run store
        already depends on: `ExecutionJournal.count_since` excludes `manual` rows from the hourly cap (a
        person clicking Run is not the machine running away), and `autopause.consecutive_failures_from`
        treats a `manual` exit as transparent — so testing a broken automation by hand can neither
        autopause it nor reset a real failure streak.

        🔴 `run_count` is deliberately NOT incremented and the autopause engine is deliberately NOT run
        — this records the run HISTORY the manual path was missing, never the fire ALLOWANCE it
        correctly skips. `Trigger.run_count` is the `max_fires` fire-budget meter
        (`service._budget_remaining` reads it, written only at the autonomous fire-GRANT in
        `service.tick`), and `tools.MANUAL_NEVER_BYPASSES` pins `budget` among the gates a manual fire
        never spends — the same reason `_run_event` skips `record_fire` and `count_since` excludes
        manual rows. Spending the budget from a Run button would let a user lock themselves out of their
        own automation by testing it. Likewise a manual run must not drive `state`/`health`/`enabled`: a
        hand-run of a healthy trigger that fails once is not the machine deciding to autopause itself.

        Never raises: a bookkeeping failure must not turn a completed manual run into a crashed request,
        the same contract `_record_fire_outcome` holds. Losing a run record is recoverable; losing the
        response is not.
        """
        try:
            import time
            from datetime import datetime, timezone
            from gideon.automation.schedule_history import ExecutionRecord
            trigger_id = str(getattr(trigger, 'id', '') or '')
            if not trigger_id:
                return None
            finished = time.time()
            from gideon.engine.trigger_outcomes import status_for_result
            status = status_for_result(result, exc)
            if exc is not None:
                error = f'{type(exc).__name__}: {exc}'
            elif status in {'failure', 'interrupted', 'refused', 'skipped_gate', 'skipped_overlap'}:
                error = str(getattr(result, 'error', '') or '') or 'the action reported failure'
            else:
                error = ''
            from gideon.automation.schedule_history import action_summary
            summary = action_summary(status, result, error)
            trace = str(getattr(result, 'stdout', '') or '') if result is not None else ''
            run_id = run_id or f'manual-{int(finished * 1000)}'
            record = ExecutionRecord(run_id=run_id, job_id=trigger_id, trigger='manual', started_at=started, finished_at=finished, duration_ms=int(max(0.0, finished - started) * 1000), status=status, summary=summary, trace=trace, error=error)
            await self.runs_store().append(record)
            store = self.trigger_store()
            row = store.get(trigger_id)
            if row is None:
                return record.to_dict()
            live = row.trigger
            if status not in {'refused', 'skipped_gate', 'skipped_overlap'}:
                live.last_run_id = run_id
            stamp = datetime.now(timezone.utc).isoformat()
            if status == 'failure':
                live.last_failure_at = stamp
                live.last_error_summary = (error or 'manual run failed')[:200]
            elif status in {'success', 'ran_late', 'degraded', 'skipped_noop'}:
                live.last_success_at = stamp
            store.upsert(live)
            from gideon.automation.triggers import parks
            parks.settle(trigger, result)
            return record.to_dict()
        except Exception:
            self.logger.debug('could not record the manual run for %s', trigger, exc_info=True)
            return None
