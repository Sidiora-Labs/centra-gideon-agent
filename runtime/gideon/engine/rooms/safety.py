"""Member authority, human approval, and isolated spend scopes."""

from __future__ import annotations

import json
import math
import os
from contextlib import contextmanager
from dataclasses import asdict
from uuid import uuid4

from gideon.security.guardrails.budgets import (
    Budget,
    BudgetVerdict,
    get_meter,
    reset_current_run_budget,
    reset_current_run_key,
    set_current_run_budget,
    set_current_run_key,
)
from gideon.security.guardrails.policy import (
    TOOL_CUSTOM,
    TOOL_READ,
    TOOL_READ_WRITE,
    SafetyProfile,
    tool_grant_denial,
)
from gideon.security.guardrails.registries import SCALE_SCAN


class ProfileRefusal(ValueError):
    pass


def narrow_profile(base: SafetyProfile, declaration: dict | None) -> SafetyProfile:
    declaration = {} if declaration is None else declaration
    if not isinstance(declaration, dict):
        raise ProfileRefusal("profile_narrowing must be an object")
    forbidden = {"egress_tier", "denylist_extra", "path_allowlist"} & declaration.keys()
    if forbidden:
        raise ProfileRefusal(
            "unsupported profile axes: " + ", ".join(sorted(forbidden))
        )
    unknown = declaration.keys() - {
        "approval",
        "tool_grants",
        "tool_allowlist",
        "scan_mode",
        "budget",
    }
    if unknown:
        raise ProfileRefusal("unknown profile axes: " + ", ".join(sorted(unknown)))
    if declaration.get("approval", "ask") != "ask":
        raise ProfileRefusal("room approval must remain ask")
    default_tier = TOOL_CUSTOM if base.tool_grants == TOOL_CUSTOM else TOOL_READ
    tier = declaration.get(
        "tool_grants", TOOL_CUSTOM if "tool_allowlist" in declaration else default_tier
    )
    if tier not in (TOOL_READ, TOOL_READ_WRITE, TOOL_CUSTOM):
        raise ProfileRefusal("unknown tool_grants tier")
    allow = declaration.get(
        "tool_allowlist",
        (
            list(base.tool_allowlist)
            if tier == TOOL_CUSTOM and base.tool_grants == TOOL_CUSTOM
            else []
        ),
    )
    if not isinstance(allow, list) or not all(
        isinstance(item, str) and item.strip() for item in allow
    ):
        raise ProfileRefusal("tool_allowlist must contain nonempty strings")
    if tier != TOOL_CUSTOM and allow:
        raise ProfileRefusal("tool_allowlist requires the custom tier")
    if base.tool_grants == TOOL_CUSTOM:
        if tier != TOOL_CUSTOM or not set(allow).issubset(base.tool_allowlist):
            raise ProfileRefusal("tool grants widen the inherited custom scope")
    elif base.tool_grants == TOOL_READ:
        if tier == TOOL_READ_WRITE:
            raise ProfileRefusal("tool grants widen the inherited read-only scope")
        if tier == TOOL_CUSTOM:
            from gideon.automation.workflows.batch_compile import is_write_tool

            if any(
                any(char in name for char in "*?[") or is_write_tool(name)
                for name in allow
            ):
                raise ProfileRefusal("custom tools widen the inherited read-only scope")
    scan = declaration.get("scan_mode", base.scan_mode)
    if scan not in SCALE_SCAN or SCALE_SCAN.index(scan) < SCALE_SCAN.index(
        base.scan_mode
    ):
        raise ProfileRefusal("scan_mode widens the inherited scope")
    limits = declaration.get("budget", {})
    if not isinstance(limits, dict) or limits.keys() - {"max_tokens", "max_dollars"}:
        raise ProfileRefusal("budget accepts max_tokens and max_dollars")
    budget = asdict(base.budget)
    for axis, value in limits.items():
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise ProfileRefusal(f"invalid budget {axis}")
        if axis == "max_tokens" and not isinstance(value, int):
            raise ProfileRefusal("max_tokens must be an integer")
        if budget[axis] > 0 and (value == 0 or value > budget[axis]):
            raise ProfileRefusal(f"budget {axis} widens the inherited scope")
        budget[axis] = value
    if (
        not {"tool_grants", "tool_allowlist"} & declaration.keys()
        and base.tool_grants == TOOL_CUSTOM
    ):
        from gideon.automation.workflows.batch_compile import is_write_tool

        allow = [
            name
            for name in allow
            if not any(char in name for char in "*?[") and not is_write_tool(name)
        ]
    return base.with_overrides(
        approval="ask",
        tool_grants=tier,
        tool_allowlist=tuple(allow),
        scan_mode=scan,
        budget=Budget(**budget),
    )


def profile_for_room_key(key: str, base: SafetyProfile) -> SafetyProfile:
    from gideon.engine.rooms.store import RoomStore

    parts = key.split(":")
    if len(parts) != 3:
        raise ProfileRefusal("invalid room session identity")
    try:
        room = RoomStore().get(parts[1])
        member = next(member for member in room.members if member.id == parts[2])
    except (KeyError, StopIteration) as exc:
        raise ProfileRefusal("room member no longer exists") from exc
    return narrow_profile(base, member.profile_narrowing)


def resolved_posture(key: str) -> dict:
    from gideon.security.guardrails.policy import profile_for_session

    try:
        return {"allowed": True, **asdict(profile_for_session(key))}
    except ProfileRefusal as exc:
        return {"allowed": False, "approval": "ask", "refusal": str(exc)}


class RoomApprover:
    def __init__(self, state, *, identity: str):
        from gideon.integrations.mcp_core import (
            _CURRENT_AGENT_ID,
            get_current_session_key,
        )

        if (
            identity != "human"
            or _CURRENT_AGENT_ID.get()
            or os.environ.get("GIDEON_AGENT_ID")
            or os.environ.get("GIDEON_SESSION_KEY", "")
            .removeprefix("dashboard_")
            .startswith(("room:", "subagent:"))
            or get_current_session_key()
            .removeprefix("dashboard_")
            .startswith(("room:", "subagent:"))
        ):
            raise PermissionError("only a human identity may construct a RoomApprover")
        self.state = state

    async def approve(self, key: str, event, profile: SafetyProfile) -> bool:
        if tool_grant_denial(event.title, profile.tool_grants, profile.tool_allowlist):
            return False
        return await self.state.request_approval(
            f"room-{uuid4().hex}",
            "room",
            event.title,
            tool_input=json.dumps(event.tool_input),
            tool_purpose=event.tool_purpose,
            session=key,
        )


class MemberSpend:
    def __init__(self, key: str):
        self.key = key
        self.meter = get_meter()
        self.before = self.meter.run_totals(key)

    def record(self, event) -> None:
        total = self.meter.run_totals(self.key)
        tokens = max(0, event.input_tokens + event.output_tokens)
        dollars = max(0.0, event.cost_usd)
        self.meter.charge(
            max(0, tokens - (total.tokens - self.before.tokens)),
            max(0.0, dollars - (total.dollars - self.before.dollars)),
            run_key=self.key,
        )


def check_member_budget(key: str, profile: SafetyProfile) -> None:
    verdict, reason = get_meter().check_run(key, profile.budget)
    if verdict == BudgetVerdict.EXCEEDED:
        raise ProfileRefusal(reason)


@contextmanager
def member_spend_scope(key: str, profile: SafetyProfile):
    check_member_budget(key, profile)
    scope = set_current_run_key(key)
    ceiling = set_current_run_budget(profile.budget)
    try:
        yield MemberSpend(key)
    finally:
        reset_current_run_budget(ceiling)
        reset_current_run_key(scope)
