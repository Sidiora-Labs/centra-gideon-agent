"""Pre-send spend admission and once-only accounting for unattended ACP turns."""
from __future__ import annotations

import time
from dataclasses import replace
from gideon.core.turn_streams import closing_stream
from gideon.integrations.llm.events import EVENT_COMPLETE

TURN_ROOM_WAIT_SECS = 1800.0


class AcpTurnMeter:
    _spend_axis = ""

    def set_spend_axis(self, axis: str) -> None:
        from gideon.extensions.providers.provider_bridge import METERED_AXES
        self._spend_axis = axis if axis in METERED_AXES else ""

    @property
    def spend_axis(self) -> str:
        return self._spend_axis

    async def _metered(self, source, *, prompt: str = ""):
        from gideon.security.guardrails.audit import AttemptRecord, current_caller, now_ms, record_attempt
        from gideon.security.guardrails.budgets import budget_from_config, current_run_key, get_meter, run_budget_from_config
        from gideon.security.guardrails.model_call import admit_call, call_cost, _new_audit_id
        from gideon.security.guardrails.failure import BudgetExceededError, FailureMode
        from gideon.security.guardrails.policy import is_unattended_session
        from gideon.engine.routing.rates import EffectiveModelPrice, resolve_effective_price

        key = str(getattr(self, "_session_key", "") or "")
        axis = self.spend_axis or ("background" if getattr(self, "_unattended", False)
            or current_run_key() or (key and is_unattended_session(key)) else "")
        async with closing_stream(source) as events:
            if not axis:
                async for event in events:
                    yield event
                return
            provider = str(self.provider_id)
            model = str(self.agent_model or "")
            # Only the parent-resolved registered serving identity can affect locality/pricing.
            ref = str(getattr(self, "served_model_ref", "") or "")
            if ref and ":" in ref:
                provider, model = ref.split(":", 1)
            ref = f"{provider}:{model}"
            meter = get_meter()
            audit_id = _new_audit_id()
            hold = None
            started = now_ms()
            settled = False
            observed = None

            def record(mode, price, counts, passed=False):
                record_attempt(AttemptRecord(
                    audit_id=audit_id, ts=time.time(), use_case=axis,
                    provider=provider, model=model, attempt=1,
                    failure_mode=mode.value, latency_ms=round(now_ms()-started, 1),
                    tokens_in=counts[0], tokens_out=counts[1],
                    dollars_est=price.cost_usd or 0.0, estimated=price.estimated,
                    passed=passed, caller=current_caller(),
                    extra={"priced":price.priced, "price_source":price.source,
                           "cache_read_tokens":counts[2], "cache_creation_tokens":counts[3]},
                ))

            def price_of(event, *, completed):
                metadata = event.tool_meta if isinstance(event.tool_meta, dict) else {}
                counts = tuple(max(0,int(getattr(event, name, 0) or 0)) for name in
                    ("input_tokens","output_tokens","cache_read_tokens","cache_creation_tokens"))
                reported = bool(metadata.get("usage_reported") or any(counts) or event.cost_usd)
                # A token usage report is not itself a provider-reported dollar bill.
                price = resolve_effective_price(provider, model, input_tokens=counts[0],
                    output_tokens=counts[1], cache_read_tokens=counts[2], cache_creation_tokens=counts[3],
                    reported_cost_usd=event.cost_usd,
                    provider_reported=bool(metadata.get("cost_reported") or event.cost_usd > 0))
                if not reported:
                    if completed and hold is not None:
                        counts=(max(0,hold.tokens-hold.answer_tokens),hold.answer_tokens,0,0)
                        price=replace(price,cost_usd=hold.dollars if price.priced else None,estimated=True)
                    elif call_cost(provider,model,prompt_chars=0).free:
                        price=replace(price,cost_usd=0.0)
                    else:
                        price=EffectiveModelPrice(provider,model,None,False,"unknown",False)
                return counts,price,reported

            def settle(event, *, completed, mode):
                nonlocal settled
                if settled:
                    return None
                counts,price,reported=price_of(event,completed=completed)
                meter.settle(hold,ref=ref,tokens=counts[0]+counts[1],answer_tokens=counts[1],
                    dollars=price.cost_usd or 0.0,priced=price.priced,
                    run_key=current_run_key() or None,usage_reported=True)
                settled=True
                record(mode,price,counts,passed=completed)
                if event.kind == EVENT_COMPLETE:
                    metadata=dict(event.tool_meta) if isinstance(event.tool_meta,dict) else {}
                    metadata.update(audit_id=audit_id,spend_charged=True,priced=price.priced,
                        price_source=price.source,charged_cost_usd=price.cost_usd,
                        price_estimated=price.estimated,usage_status=("measured" if completed else "partial") if reported else "absent")
                    event.tool_meta=metadata
                    event.input_tokens,event.output_tokens,event.cache_read_tokens,event.cache_creation_tokens=counts
                return price

            try:
                hold=await admit_call(meter,call_cost(provider,model,prompt_chars=len(prompt)),
                    budget_from_config(),run_budget_from_config(),wait_secs=TURN_ROOM_WAIT_SECS)
            except BudgetExceededError:
                record(FailureMode.BUDGET_EXCEEDED,
                    EffectiveModelPrice(provider,model,0.0,True,"unsent",False),(0,0,0,0))
                raise
            sent = False
            try:
                sent=True
                async for event in events:
                    if event.input_tokens or event.output_tokens or event.cost_usd or event.tool_meta.get("usage_reported"):
                        observed=event
                    if event.kind==EVENT_COMPLETE:
                        cancelled=event.stop_reason=="cancelled"
                        settle(event,completed=not cancelled,mode=FailureMode.PROVIDER_ERROR if cancelled else FailureMode.NONE)
                    yield event
            finally:
                if sent and not settled:
                    from gideon.integrations.llm.events import AgentEvent
                    settle(observed or AgentEvent(kind=EVENT_COMPLETE),completed=False,mode=FailureMode.PROVIDER_ERROR)
                meter.release(hold)
