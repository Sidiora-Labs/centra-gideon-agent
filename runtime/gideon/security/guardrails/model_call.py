"""``ModelCallGuard`` — the model-call chokepoint adapter (§2.1).

The LLM twin of ``net.fetch``: a :class:`~gideon.integrations.llm.base.ModelProvider`
that wraps the resolved provider for **non-interactive** calls and enforces, per
stream, the cheap-first pipeline this slice owns:

    circuit-breaker check  →  call with hard wall-clock timeout  →  attempt audit

Later stages (secret/PII scan, spend metering, typed-output enforcement, ordered
fallback) compose in front of / behind this same seam in Sessions 2–4.

**Where it wraps (and where it must NOT):** the wrap happens inside the bridge's
single provider-build point (``_resolve_from_config_registry``) gated on the
non-interactive chat-text use case. That gate excludes, by construction, both the
interactive ``NativeAgentRuntime`` (returned before the build point for
``chat``/``code_tools``) and its inner model (resolved with ``chat``/``code_tools``
+ ``_force_model_axis``) — the interactive chat stream a human is watching is
explicitly out of scope for v1.

The guard is a faithful transparent proxy: every ``ModelProvider`` method
delegates to the wrapped provider; only :meth:`stream` / :meth:`complete` (the two
generation paths) are intercepted.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from gideon.integrations.llm.base import (
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    EVENT_THINKING_CHUNK,
    EVENT_TOOL_CALL,
    CancelOutcome,
    LLMEvent,
    ModelProvider,
)
from gideon.security.guardrails.audit import (
    AttemptRecord,
    current_caller,
    now_ms,
    record_attempt,
)
from gideon.security.guardrails.breaker import CircuitBreaker, get_breaker
from gideon.security.guardrails.budgets import (
    Budget,
    BudgetVerdict,
    SpendMeter,
    current_run_budget,
    current_run_key,
    get_meter,
)
from gideon.security.guardrails.failure import (
    BudgetExceededError,
    CircuitOpenError,
    FailureMode,
    FirstTokenTimeout,
    ModelCallTimeout,
    PromptInjectionBlocked,
    SecretLeakBlocked,
)
from gideon.security.guardrails.scan import scan_outbound

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECS = 300.0


def _workflow_stream_observation(action: str, call_id: str, *, event=None, completed=False,
                                 provider="", model="", cost_usd=None,
                                 cost_reported=None) -> None:
    """Report guarded stream activity to a bound workflow step without affecting the call."""
    try:
        from gideon.automation.workflows import step_usage

        if action == "start":
            step_usage.guarded_call_started(call_id, provider, model)
        elif action == "event":
            step_usage.guarded_call_event(
                call_id, event, cost_usd=cost_usd, cost_reported=cost_reported
            )
        else:
            step_usage.guarded_call_ended(call_id, completed=completed)
    except Exception:
        logger.debug("workflow stream observation failed", exc_info=True)


def _new_audit_id() -> str:
    return uuid.uuid4().hex[:16]


def _iso_now() -> str:
    """Wall-clock ISO-UTC stamp for the routing-stats fold's ``updated_at``."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _asdict_row(rec) -> dict:
    """The attempt as the SAME flat dict the JSONL carries, so the live fold and the
    rebuild-from-JSONL path (routing.stats) see byte-identical row shapes."""
    import json as _json

    return _json.loads(rec.to_json_line())


def _joined_content(messages: list[dict]) -> str:
    """The user-authored text of a structured message list, for query classification.

    A message ``content`` is either a plain string or a list of typed blocks
    (``{"type": "text", "text": ...}`` and friends). Join the text of the user turns —
    that's what the classifier's length/signal heuristics key on. Best-effort: an odd
    shape yields "" rather than raising (classification is telemetry, never load-bearing).
    """
    parts: list[str] = []
    try:
        for msg in messages or []:
            if not isinstance(msg, dict) or msg.get("role") not in ("user", None, ""):
                continue
            content = msg.get("content", "")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and isinstance(block.get("text"), str):
                        parts.append(block["text"])
                    elif isinstance(block, str):
                        parts.append(block)
    except Exception:  # noqa: BLE001
        return ""
    return "\n".join(parts)


class ModelCallGuard(ModelProvider):
    """Wraps ``inner`` with breaker + hard timeout + attempt-level audit."""

    def __init__(
        self,
        inner: ModelProvider,
        *,
        use_case: str,
        provider_name: str,
        model: str,
        timeout_secs: float = _DEFAULT_TIMEOUT_SECS,
        breaker: CircuitBreaker | None = None,
        budget: "Budget | None" = None,
        run_budget: "Budget | None" = None,
        meter: "SpendMeter | None" = None,
        scan_mode: str = "warn",
        routed: bool = False,
        routed_fallback: bool = False,
    ) -> None:
        self._inner = inner
        self._use_case = use_case
        self._provider_name = provider_name
        self._model = model
        self._timeout_secs = max(0.0, float(timeout_secs))
        self._breaker = breaker if breaker is not None else get_breaker(provider_name)
        self._budget = budget if budget is not None else Budget()
        self._run_budget = run_budget if run_budget is not None else Budget()
        self._meter = meter if meter is not None else get_meter()
        self._scan_mode = (
            scan_mode if scan_mode in ("warn", "redact", "block") else "warn"
        )
        self.supports_tools = getattr(inner, "supports_tools", False)
        self._query_class = ""
        self._routed = bool(routed)
        self._routed_fallback = bool(routed_fallback)

    def _stream_deadline(self, now: float, *, startup: bool = False) -> float | None:
        if self._timeout_secs <= 0:
            return None
        startup_secs = (self._inner.first_token_timeout_secs or 0.0) if startup else 0.0
        return now + self._timeout_secs + startup_secs

    def _classify(self, text: str) -> None:
        """Set ``self._query_class`` for the current call from the pure classifier.

        Pure + fail-open: a classification failure must never break a model call, so any
        error leaves the class "" (the audit row simply carries no class). The value is
        stamped onto every attempt this call makes."""
        try:
            from gideon.engine.routing.classifier import classify_query

            self._query_class = classify_query(text, self._use_case)
        except (
            Exception
        ):  # noqa: BLE001 — classification is telemetry, never load-bearing
            self._query_class = ""

    async def stream(self, message: str) -> AsyncIterator[LLMEvent]:
        message = self._prescan(message)
        self._classify(message)
        async for event in self._guarded(
            self._inner.stream(message), strategy="direct"
        ):
            yield event

    async def complete(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        model: str | None = None,
        reasoning_effort: str = "",
    ) -> AsyncIterator[LLMEvent]:
        self._classify(_joined_content(messages))
        inner = self._inner.complete(
            messages, tools=tools, model=model, reasoning_effort=reasoning_effort
        )
        async for event in self._guarded(inner, strategy="direct"):
            yield event

    @property
    def supports_native_commands(self) -> bool:
        """Explicit pass-through, NOT ``__getattr__``. ``ModelProvider`` declares this
        property with a False default, so normal lookup finds the ABC's answer on the
        wrapper and the transparent-fallback hook below never fires — the guard would
        report "no commands" for an agent that has them, and every slash command would
        silently degrade to text (`G4`)."""
        return bool(getattr(self._inner, "supports_native_commands", False))

    async def stream_command(self, command: str) -> AsyncIterator[LLMEvent]:
        command = self._prescan(command)
        self._classify(command)
        async for event in self._guarded(
            self._inner.stream_command(command), strategy="direct"
        ):
            yield event

    def _prescan(self, text: str) -> str:
        """Scan an outbound prompt for secrets/PII and apply the mode ladder.

        Returns the (possibly redacted) text to send. Raises in block mode when there are
        findings — audited and never retried (retrying would let a payload brute-force the
        scan).

        🔴 The failure mode is now CHOSEN, not assumed (S156). Every block recorded
        ``secret_leak``, so ``FailureMode.INJECTION_BLOCKED`` — declared, listed in
        ``NON_RETRYABLE``, and carrying its own retry semantics — could never be recorded by
        anything. §2.2's taxonomy separates the two deliberately: they are both non-retryable
        for *different* reasons, and an operator reading the audit trail cannot tell a
        credential slip from an attack if both say ``secret_leak``."""
        result = scan_outbound(text, mode=self._scan_mode)
        if result.blocked:
            mode = (
                FailureMode.INJECTION_BLOCKED
                if result.injection
                else FailureMode.SECRET_LEAK
            )
            self._audit(_new_audit_id(), 1, mode, 0.0, 0, 0, False, "direct")
            from gideon.security.sel import sel

            try:
                sel().log_api_access(
                    caller=f"model_call:{self._use_case}",
                    operation="guardrails.scan_block",
                    outcome="blocked",
                    source="guardrails",
                    resources=(
                        f"provider={self._provider_name} "
                        f"categories={','.join(result.categories)}"
                        + (
                            f" pattern={result.injection_group}"
                            if result.injection
                            else ""
                        )
                    ),
                )
            except Exception:
                logger.debug("SEL scan-block audit failed", exc_info=True)
            if result.injection:
                raise PromptInjectionBlocked(result.findings, result.injection_group)
            raise SecretLeakBlocked(result.findings)
        return result.text

    async def _guarded(
        self, source: AsyncIterator[LLMEvent], *, strategy: str
    ) -> AsyncIterator[LLMEvent]:
        """Drive ``source`` under the breaker + a cumulative wall-clock deadline,
        recording exactly one attempt row for the whole stream.

        Success is recorded the moment ``EVENT_COMPLETE`` is observed — BEFORE it is
        yielded — because the canonical consumer (``stream_and_collect``) ``break``s
        on ``EVENT_COMPLETE`` rather than draining to ``StopAsyncIteration``: a guard
        that only recorded after loop-exit would then be suspended at the terminal
        ``yield`` forever and never audit. A ``_recorded`` flag makes the outcome
        fire exactly once; a stream that ends via ``StopAsyncIteration`` with no
        COMPLETE event still records once at loop-exit.
        """
        audit_id = _new_audit_id()

        if self._breaker.is_open():
            retry_after = self._breaker.retry_after()
            self._audit(
                audit_id, 1, FailureMode.CIRCUIT_OPEN, 0.0, 0, 0, False, strategy
            )
            await self._aclose(source)
            raise CircuitOpenError(self._provider_name, retry_after)

        if not self._budget.is_unlimited:
            verdict, reason = self._meter.check_day(self._budget)
            if verdict is BudgetVerdict.EXCEEDED:
                self._audit(
                    audit_id, 1, FailureMode.BUDGET_EXCEEDED, 0.0, 0, 0, False, strategy
                )
                await self._aclose(source)
                totals = self._meter.day_totals()
                dim = "tokens" if "token" in reason else "dollars"
                limit = (
                    self._budget.max_tokens
                    if dim == "tokens"
                    else self._budget.max_dollars
                )
                spent = totals.tokens if dim == "tokens" else totals.dollars
                raise BudgetExceededError("day", dim, float(limit), float(spent))

        run_key = current_run_key()
        rb = current_run_budget()
        if rb.is_unlimited:
            rb = self._run_budget
        if run_key and not rb.is_unlimited:
            verdict, reason = self._meter.check_run(run_key, rb)
            if verdict is BudgetVerdict.EXCEEDED:
                self._audit(
                    audit_id, 1, FailureMode.BUDGET_EXCEEDED, 0.0, 0, 0, False, strategy
                )
                await self._aclose(source)
                totals = self._meter.run_totals(run_key)
                dim = "tokens" if "token" in reason else "dollars"
                lim = rb.max_tokens if dim == "tokens" else rb.max_dollars
                spent = totals.tokens if dim == "tokens" else totals.dollars
                raise BudgetExceededError("run", dim, float(lim), float(spent))

        loop = asyncio.get_running_loop()
        startup_timeout = self._inner.first_token_timeout_secs or 0.0
        deadline = self._stream_deadline(loop.time(), startup=True)
        awaiting_first_token = startup_timeout > 0
        started = now_ms()
        tokens_in = tokens_out = 0
        recorded = False
        _workflow_stream_observation(
            "start", audit_id, provider=self._provider_name, model=self._model
        )

        try:
            while True:
                if deadline is not None:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        raise TimeoutError
                    try:
                        event = await asyncio.wait_for(source.__anext__(), remaining)
                    except StopAsyncIteration:
                        break
                else:
                    try:
                        event = await source.__anext__()
                    except StopAsyncIteration:
                        break
                if awaiting_first_token and (
                    event.kind in {EVENT_TEXT_CHUNK, EVENT_THINKING_CHUNK, EVENT_TOOL_CALL, EVENT_COMPLETE}
                    and (event.text or event.kind in {EVENT_TOOL_CALL, EVENT_COMPLETE})
                ):
                    awaiting_first_token = False
                    deadline = self._stream_deadline(loop.time())
                if event.kind == EVENT_COMPLETE and not recorded:
                    tokens_in = int(getattr(event, "input_tokens", 0) or 0)
                    tokens_out = int(getattr(event, "output_tokens", 0) or 0)
                    price = self._estimate_dollars(event, tokens_in, tokens_out)
                    dollars = float(price.cost_usd or 0.0)
                    self._breaker.record_success()
                    self._meter.charge(
                        tokens_in + tokens_out,
                        dollars,
                        run_key=current_run_key() or None,
                    )
                    self._audit(
                        audit_id,
                        1,
                        FailureMode.NONE,
                        now_ms() - started,
                        tokens_in,
                        tokens_out,
                        True,
                        strategy,
                        dollars=dollars,
                        priced=price.priced,
                        price_source=price.source,
                        estimated=price.estimated,
                    )
                    recorded = True
                if event.kind == EVENT_COMPLETE:
                    metadata = (
                        dict(event.tool_meta)
                        if isinstance(getattr(event, "tool_meta", None), dict)
                        else {}
                    )
                    metadata["audit_id"] = audit_id
                    event.tool_meta = metadata
                _workflow_stream_observation(
                    "event",
                    audit_id,
                    event=event,
                    cost_usd=(dollars if event.kind == EVENT_COMPLETE else None),
                    cost_reported=(
                        price.source == "provider_reported"
                        if event.kind == EVENT_COMPLETE
                        else None
                    ),
                )
                yield event
        except TimeoutError:
            self._breaker.record_failure()
            await self._aclose(source)
            if not recorded:
                self._audit(
                    audit_id,
                    1,
                    FailureMode.TIMEOUT,
                    now_ms() - started,
                    0,
                    0,
                    False,
                    strategy,
                )
            raise ModelCallTimeout(
                f"model call for use case {self._use_case!r} (provider "
                f"{self._provider_name!r}) exceeded {self._timeout_secs:.0f}s"
            ) from None
        except (asyncio.CancelledError, GeneratorExit):
            await self._aclose(source)
            raise
        except Exception as error:
            if not recorded:
                self._breaker.record_failure()
                self._audit(
                    audit_id,
                    1,
                    error.mode if isinstance(error, FirstTokenTimeout) else FailureMode.PROVIDER_ERROR,
                    now_ms() - started,
                    tokens_in,
                    tokens_out,
                    False,
                    strategy,
                )
            raise
        finally:
            _workflow_stream_observation("end", audit_id, completed=recorded)

        if not recorded:
            self._breaker.record_success()
            self._audit(
                audit_id,
                1,
                FailureMode.NONE,
                now_ms() - started,
                tokens_in,
                tokens_out,
                True,
                strategy,
            )

    def _estimate_dollars(
        self, event: LLMEvent, tokens_in: int, tokens_out: int
    ):
        """Resolve cost and provenance once for the budget and audit paths."""
        from gideon.engine.routing.rates import resolve_effective_price

        metadata = getattr(event, "tool_meta", None)
        metadata = metadata if isinstance(metadata, dict) else {}
        reported = getattr(event, "cost_usd", None)
        reported_signal = metadata.get("usage_reported")
        provider_reported = (
            bool(reported_signal)
            if reported_signal is not None
            else bool(reported and float(reported) > 0.0)
        )
        return resolve_effective_price(
            self._provider_name,
            self._model,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            cache_read_tokens=int(getattr(event, "cache_read_tokens", 0) or 0),
            cache_creation_tokens=int(
                getattr(event, "cache_creation_tokens", 0) or 0
            ),
            reported_cost_usd=reported,
            provider_reported=provider_reported,
        )

    def _audit(
        self,
        audit_id: str,
        attempt: int,
        mode: FailureMode,
        latency_ms: float,
        tokens_in: int,
        tokens_out: int,
        passed: bool,
        strategy: str,
        *,
        dollars: float = 0.0,
        priced: bool | None = None,
        price_source: str = "",
        estimated: bool = True,
    ) -> None:
        requested_temperature = getattr(
            self._inner, "sampling_temperature", None
        )
        options = getattr(self._inner, "_extra_options", {})
        if isinstance(options, dict):
            requested_temperature = options.get("temperature", requested_temperature)
        unsent_options = getattr(self._inner, "unsent_options", {})
        output_token_limit = getattr(self._inner, "output_token_limit", None)
        extra = {}
        if isinstance(requested_temperature, (int, float)) and not isinstance(
            requested_temperature, bool
        ):
            extra["requested_sampling_temperature"] = float(requested_temperature)
        effective_temperature = getattr(self._inner, "sampling_temperature", None)
        if isinstance(effective_temperature, (int, float)) and not isinstance(
            effective_temperature, bool
        ):
            extra["effective_sampling_temperature"] = float(effective_temperature)
        if isinstance(unsent_options, dict) and unsent_options:
            extra["unsent_options"] = {
                str(key): str(reason) for key, reason in unsent_options.items()
            }
        if isinstance(output_token_limit, int) and not isinstance(
            output_token_limit, bool
        ):
            extra["output_token_limit"] = output_token_limit
        rec = AttemptRecord(
            audit_id=audit_id,
            ts=time.time(),
            use_case=self._use_case,
            provider=self._provider_name,
            model=self._model,
            attempt=attempt,
            failure_mode=mode.value,
            latency_ms=round(latency_ms, 1),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            dollars_est=round(dollars, 6),
            estimated=estimated,
            passed=passed,
            strategy=strategy,
            query_class=self._query_class,
            routed=self._routed,
            routed_fallback=self._routed_fallback,
            caller=current_caller(),
            extra=extra,
        )
        if priced is not None:
            rec.extra.update(priced=priced, price_source=price_source)
        record_attempt(rec)
        try:
            from gideon.core.config.loader import config_dir
            from gideon.engine.routing.stats import record_routing_stats

            record_routing_stats(_asdict_row(rec), home=config_dir(), now=_iso_now())
        except Exception:  # noqa: BLE001 — observability, never load-bearing
            pass

    @staticmethod
    async def _aclose(source: AsyncIterator[LLMEvent]) -> None:
        aclose = getattr(source, "aclose", None)
        if aclose is None:
            return
        try:
            await aclose()
        except Exception:
            logger.debug("guarded source aclose failed", exc_info=True)

    async def start(self) -> None:
        await self._inner.start()

    async def shutdown(self) -> None:
        await self._inner.shutdown()

    async def approve_tool(self, request_id: str | int) -> None:
        await self._inner.approve_tool(request_id)

    async def reject_tool(self, request_id: str | int) -> None:
        await self._inner.reject_tool(request_id)

    def context_usage_pct(self) -> float | None:
        return self._inner.context_usage_pct()

    @property
    def first_token_timeout_secs(self) -> float | None:
        return self._inner.first_token_timeout_secs

    async def served_context_window(self) -> int | None:
        return await self._inner.served_context_window()

    @property
    def session_id(self) -> str:
        return self._inner.session_id

    @property
    def sampling_temperature(self) -> float | None:
        return self._inner.sampling_temperature

    @property
    def unsent_options(self) -> dict[str, str]:
        return self._inner.unsent_options

    @property
    def output_token_limit(self) -> int | None:
        return self._inner.output_token_limit

    async def cleanup_session(self, session_id: str) -> None:
        await self._inner.cleanup_session(session_id)

    async def compact(self, context: str = "") -> None:
        await self._inner.compact(context)

    async def wait_for_compaction(self, timeout: float = 120.0) -> dict:
        return await self._inner.wait_for_compaction(timeout)

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> CancelOutcome:
        return await self._inner.cancel(wait_ack_timeout=wait_ack_timeout)

    def is_alive(self) -> bool:
        return self._inner.is_alive()

    def touch_activity(self) -> None:
        self._inner.touch_activity()

    def set_workspace(self, path: Path) -> None:
        self._inner.set_workspace(path)

    def set_session_key(self, session_key: str, channel_id: str | None = None) -> None:
        self._inner.set_session_key(session_key, channel_id)

    def __getattr__(self, item: str):
        if item == "_inner":
            raise AttributeError(item)
        return getattr(self._inner, item)


def _is_local_provider(provider: ModelProvider) -> bool:
    """Use the same resolved endpoint locality as rates and routing."""
    try:
        from gideon.integrations.llm.registry import serving_is_local

        reference = str(getattr(provider, "served_model_ref", "") or "")
        name = reference.partition(":")[0] if reference else ""
        return serving_is_local(name, actual_provider=provider)
    except Exception:
        return False


def wrap_model_call_guard(
    provider: ModelProvider,
    *,
    use_case: str,
    provider_name: str,
    model: str,
    budget: Budget | None = None,
    run_budget: Budget | None = None,
    meter: SpendMeter | None = None,
    scan_mode: str = "warn",
    breaker: CircuitBreaker | None = None,
    timeout_secs: float = _DEFAULT_TIMEOUT_SECS,
    routed: bool = False,
    routed_fallback: bool = False,
) -> ModelProvider:
    """Wrap ``provider`` in a :class:`ModelCallGuard` for a non-interactive call.

    Idempotent: an already-guarded provider is returned unchanged (defends against
    double-wrapping if two resolution layers both reach for the guard). A local
    provider's scan mode is forced to ``warn`` regardless of ``scan_mode``.
    """
    if isinstance(provider, ModelCallGuard):
        return provider
    effective_scan = "warn" if _is_local_provider(provider) else scan_mode
    return ModelCallGuard(
        provider,
        use_case=use_case,
        provider_name=provider_name,
        model=model,
        budget=budget,
        run_budget=run_budget,
        meter=meter,
        scan_mode=effective_scan,
        breaker=breaker,
        timeout_secs=timeout_secs,
        routed=routed,
        routed_fallback=routed_fallback,
    )
