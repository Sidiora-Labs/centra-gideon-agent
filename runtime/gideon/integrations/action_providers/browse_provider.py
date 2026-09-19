"""Admit browser actions, own their authorization lifetime and publish loop results."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gideon.core.errors import AgentError
from gideon.integrations.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)
from gideon.integrations.action_providers.command_lifecycle import ActionClock
from gideon.integrations.browse.handoff import PARK_LOGIN_REQUIRED
from gideon.integrations.browse.loop import (
    MAX_STEPS_DEFAULT,
    PARK_BUDGET_EXHAUSTED,
    PARK_KILLED,
    PARK_STEP_EXHAUSTED,
    PARK_TAB_CLOSED,
    BrowseLoopResult,
    BrowseStep,
    run_browse_loop,
)

logger = logging.getLogger(__name__)
PROVIDER_NAME = "browse"
OUTCOME_NEEDS_INPUT = "needs_input"
OUTCOME_SKIP = "skip"
USE_CASE = "reasoning"


def _budget_check() -> tuple[str, str]:
    try:
        from gideon.security.guardrails import budgets

        meter = budgets.get_meter()

        def verdicts():
            yield meter.check_day(budgets.budget_from_config())
            key = budgets.current_run_key()
            if key:
                yield meter.check_run(key, budgets.current_run_budget())

        for verdict, detail in verdicts():
            if verdict.value == "exceeded":
                return "exceeded", detail
    except Exception:
        logger.debug("browse: budget verdict unavailable", exc_info=True)
    return "ok", ""


def _kill_check() -> tuple[bool, str]:
    try:
        from gideon.integrations.browse.killswitch import get_kill

        current = get_kill()
        answer = (current.active, current.reason)
    except Exception:
        logger.debug("browse: kill verdict unavailable", exc_info=True)
        answer = (False, "")
    return answer


def _revoke_reason(result: BrowseLoopResult | None) -> str:
    reasons = {PARK_TAB_CLOSED: "tab_closed", PARK_KILLED: "kill_switch"}
    if result is None:
        return "run_ended"
    return reasons.get(getattr(result, "park_reason", ""), "run_complete")


async def _decide(prompt: str) -> str:
    from gideon.integrations.llm_helpers import one_shot_completion

    answer = await one_shot_completion(prompt, use_case=USE_CASE)
    return answer


@dataclass(frozen=True)
class _BrowseRequest:
    goal: str
    url: str
    config: dict[str, Any]

    def step_limit(self) -> int:
        try:
            limit = int(self.config.get("max_steps") or MAX_STEPS_DEFAULT)
        except (TypeError, ValueError):
            limit = MAX_STEPS_DEFAULT
        return max(limit, 1)


@dataclass
class _BrowserOwnership:
    grant: Any = None
    closer: Any = None
    result: BrowseLoopResult | None = None

    async def close(self) -> None:
        close, self.closer = self.closer, None
        try:
            if close is not None:
                try:
                    await close()
                except Exception:
                    logger.debug("browse: session close failed", exc_info=True)
        finally:
            grant, self.grant = self.grant, None
            if grant is not None and grant.granted:
                from gideon.integrations.browse.grant import revoke_grant

                try:
                    revoke_grant(grant, reason=_revoke_reason(self.result))
                except Exception:
                    logger.debug("browse: grant revoke failed", exc_info=True)


class _BrowseExecution:
    def __init__(
        self,
        provider: BrowseActionProvider,
        request: _BrowseRequest,
        ctx: ActionContext,
        clock: ActionClock,
    ):
        self.provider, self.request, self.ctx, self.clock = (
            provider,
            request,
            ctx,
            clock,
        )
        self.owned = _BrowserOwnership()
        self.target = ""
        self.cdp_url = ""
        self.close_check = None
        self.session_before = ""

    def refusal(self, envelope: AgentError, message: str = "") -> ActionResult:
        return self.clock.result(
            False, error=message or envelope.what, agent_error=envelope
        )

    def admit(self) -> ActionResult | None:
        from gideon.integrations.browse import target
        from gideon.integrations.browse.killswitch import browse_killed, get_kill
        from gideon.security.guardrails.incident import incident_active

        try:
            self.target = target.resolve_target(self.request.config)
        except target.UnknownBrowseTarget as error:
            return self.refusal(target.unknown_target_error(error.raw))
        if incident_active():
            return self.refusal(
                AgentError(
                    code="ERR_BROWSE_INCIDENT_ACTIVE",
                    what="browse refused to start because incident mode is active",
                    why="incident mode suspends all unattended work",
                    fix="clear incident mode in Settings → Guardrails, then re-run",
                ),
                "incident mode is active — unattended browsing is suspended",
            )
        if browse_killed():
            current = get_kill()
            return self.refusal(
                AgentError(
                    code="ERR_BROWSE_KILLED",
                    what="browse refused to start because the kill switch is engaged",
                    why=current.reason
                    or "a human stopped unattended browsing from the mirror panel",
                    fix="release the kill switch (the browse mirror's Resume) then re-run",
                ),
                "the browse kill switch is engaged — unattended browsing is stopped",
            )
        if not target.permits_unattended(self.target):
            origin = target.unattended_origin()
            if origin:
                return self.refusal(
                    target.unattended_refusal(self.target, origin=origin)
                )
        self.cdp_url = target.resolve_cdp_url(self.target, self.request.config)
        return None

    async def authorize(self) -> ActionResult | None:
        from gideon.integrations.browse import grant, target

        if self.target != target.TARGET_USER_BROWSER:
            return None
        connection = target.connector_status()
        if not connection.connected:
            problem = target.disconnected_skip(connection)
            return self.clock.result(
                True,
                outcome=OUTCOME_SKIP,
                stdout=json.dumps(
                    dict(skipped=True, target=self.target, reason=connection.reason)
                ),
                stderr=f"{problem.what}. {problem.fix}.",
                agent_error=problem,
            )
        authorization = await grant.request_grant(
            task=self.request.goal,
            scope=grant.scope_for_url(self.request.url),
            gate=grant.grant_gate(),
            bound_device_id=connection.device_id,
            bound_cdp_url=connection.cdp_url,
        )
        self.owned.grant = authorization
        if not authorization.granted:
            return self.refusal(grant.grant_denied_error(authorization))
        self.close_check = grant.make_close_check(authorization)
        return None

    def profile(self) -> ActionResult | None:
        from gideon.integrations.browse import handoff

        state = handoff.session_state(self.request.url)
        self.session_before = state
        must_login = state != handoff.SESSION_FRESH and (
            state != handoff.SESSION_ABSENT
            or handoff.looks_like_login_url(self.request.url)
        )
        if must_login:
            reason = (
                handoff.REASON_SESSION_EXPIRED
                if state == handoff.SESSION_EXPIRED
                else handoff.REASON_NO_SESSION
            )
            return self.provider._login_park(
                self.request.url,
                reason=reason,
                ctx=self.ctx,
                started=self.clock.started,
            )
        handoff.ensure_profile(self.request.url)
        return None

    async def drive(self) -> ActionResult | BrowseLoopResult:
        parked = self.profile()
        if parked is not None:
            return parked
        limit = self.request.step_limit()
        try:
            session, page, self.owned.closer = await self.provider._open(
                self.request.config, self.ctx, cdp_url=self.cdp_url
            )
        except BrowseUnavailable as error:
            return self.provider._error(
                str(error),
                why="browse needs a Chrome DevTools page target to drive",
                fix="set `cdp_url` on the action config to a page target (ws://127.0.0.1:9222/devtools/page/…)",
                started=self.clock.started,
                code="ERR_BROWSE_NO_TARGET",
            )
        except Exception as error:
            return self.provider._error(
                f"the browse session could not be opened: {error}",
                why="connecting to the CDP page target failed",
                fix="check that the browser is running and the `cdp_url` is current",
                started=self.clock.started,
                code="ERR_BROWSE_CONNECT_FAILED",
            )
        self.owned.result = await run_browse_loop(
            goal=self.request.goal,
            start_url=self.request.url,
            session=session,
            page=page,
            decide=_decide,
            max_steps=limit,
            budget_check=_budget_check,
            on_step=self.provider._mirror_sink(self.ctx),
            kill_check=_kill_check,
            close_check=self.close_check,
        )
        return self.owned.result


@dataclass(frozen=True)
class _MirrorRelay:
    run_id: str

    def __call__(self, step: BrowseStep, screenshot: str) -> None:
        from gideon.integrations.browse.mirror import broadcast_browse_step

        frame = dict(
            run_id=self.run_id,
            step_n=step.index,
            url=step.url,
            action=step.action,
            screenshot=screenshot,
            note=step.note,
        )
        broadcast_browse_step(frame)


class BrowseActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return PROVIDER_NAME

    @property
    def display_name(self) -> str:
        return "Browse the web"

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        clock = ActionClock()
        request = _BrowseRequest(
            str(action_config.get("goal") or "").strip(),
            str(action_config.get("start_url") or "").strip(),
            action_config,
        )
        if not request.goal or not request.url:
            return self._error(
                "browse needs both `goal` and `start_url`",
                why="the action config named neither a task nor a page to start from",
                fix='set config to {"goal": "…", "start_url": "https://…"}',
                started=clock.started,
            )
        execution = _BrowseExecution(self, request, ctx, clock)
        try:
            early = execution.admit()
            if early is not None:
                return early
            early = await execution.authorize()
            if early is not None:
                return early
            result = await execution.drive()
        finally:
            await execution.owned.close()
        if isinstance(result, ActionResult):
            return result
        return self._to_result(
            result,
            started=clock.started,
            ctx=ctx,
            start_url=request.url,
            session_before=execution.session_before,
        )

    def _mirror_sink(self, ctx: ActionContext) -> Callable[[BrowseStep, str], None]:
        payload = getattr(ctx, "payload", None) or {}
        return _MirrorRelay(str(payload.get("run_id") or ""))

    async def _open(
        self, action_config: dict[str, Any], ctx: ActionContext, *, cdp_url: str
    ) -> tuple[Any, Any, Any]:
        if not cdp_url:
            raise BrowseUnavailable(
                "no `cdp_url` is configured, so there is no browser to drive"
            )
        from gideon.integrations.browse.cdp import GatedCdpSession
        from gideon.integrations.browse.page import CdpPageDriver
        from gideon.integrations.browse.transport import WebSocketCdpTransport

        transport = await WebSocketCdpTransport.connect(cdp_url)
        try:
            directory = str(action_config.get("screenshot_dir") or "").strip()
            driver = CdpPageDriver(
                transport, screenshot_dir=Path(directory) if directory else None
            )
            session = GatedCdpSession(
                transport,
                caller_identity=f"action:{PROVIDER_NAME}",
                source=str(getattr(ctx, "event", "") or "background"),
            )
        except BaseException:
            try:
                await transport.close()
            except Exception:
                logger.debug("browse: partial session close failed", exc_info=True)
            raise
        return session, driver, transport.close

    def _login_park(
        self, url: str, *, reason: str, ctx: ActionContext, started: float
    ) -> ActionResult:
        from gideon.integrations.browse import handoff

        context = getattr(ctx, "payload", None) or {}
        card = handoff.request_login(
            url,
            reason=reason,
            run_id=str(context.get("run_id") or ""),
            node_id=PROVIDER_NAME,
        )
        if reason == handoff.REASON_SESSION_EXPIRED:
            handoff.mark_expired(url)
            from gideon.integrations.browse.mirror import surface_auth_expired

            surface_auth_expired(url)
        payload = card.to_payload()
        payload.update(
            headful_launch_args=handoff.chrome_launch_args(url, headful=True)
        )
        return ActionClock(started).result(
            True,
            outcome=OUTCOME_NEEDS_INPUT,
            stdout=json.dumps(payload),
            stderr=card.sentence,
        )

    def _to_result(
        self,
        result: BrowseLoopResult,
        *,
        started: float,
        ctx: ActionContext,
        start_url: str = "",
        session_before: str = "",
    ) -> ActionResult:
        from gideon.integrations.browse import handoff

        if result.parked and result.park_reason == PARK_LOGIN_REQUIRED:
            return self._login_park(
                start_url or result.final_url,
                reason=handoff.REASON_CREDENTIAL_FIELD,
                ctx=ctx,
                started=started,
            )
        completed = result.ok and not result.parked
        if completed and start_url and session_before != handoff.SESSION_FRESH:
            try:
                handoff.record_login(start_url)
            except Exception:
                logger.debug("browse: could not record the login", exc_info=True)
        fields: dict = dict(stdout=json.dumps(result.to_payload()))
        if not result.ok:
            detail = result.error or result.park_detail
            fields.update(
                error=detail or "the browse run failed",
                agent_error=AgentError(
                    code="ERR_BROWSE_FAILED",
                    what=f"browse could not pursue {result.goal!r}",
                    why=detail or "the run ended without a result",
                    fix="check the start URL against the BROWSE egress policy, then re-run",
                ),
            )
        elif result.parked:
            fields.update(
                outcome=OUTCOME_NEEDS_INPUT, stderr=self._park_sentence(result)
            )
        return ActionClock(started).result(bool(result.ok), **fields)

    @staticmethod
    def _park_sentence(result: BrowseLoopResult) -> str:
        messages = {
            PARK_STEP_EXHAUSTED: f"Browse stopped after {result.step_count} steps without finishing",
            PARK_BUDGET_EXHAUSTED: "Browse stopped because the model budget is spent",
            PARK_KILLED: "Browse was stopped by the kill switch",
            PARK_TAB_CLOSED: "Browse stopped because you closed the task's browser tab",
            PARK_LOGIN_REQUIRED: "Browse stopped because the site needs you to sign in",
        }
        head = messages.get(
            result.park_reason, f"Browse stopped early ({result.park_reason})"
        )
        kept = (
            f"{len(result.notes)} note(s) kept" if result.notes else "no notes recorded"
        )
        return f"{head}; {kept}. Last page: {result.final_url or 'unknown'}."

    def _error(
        self,
        message: str,
        *,
        why: str,
        fix: str,
        started: float,
        code: str = "ERR_BROWSE_CONFIG",
    ) -> ActionResult:
        envelope = AgentError(code=code, what=message, why=why, fix=fix)
        return ActionClock(started).result(False, error=message, agent_error=envelope)


class BrowseUnavailable(RuntimeError):
    """The action has no configured browser target."""


def create_provider(config: dict[str, Any] | None = None) -> BrowseActionProvider:
    return BrowseActionProvider()
