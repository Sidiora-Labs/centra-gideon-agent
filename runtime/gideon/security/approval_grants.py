"""Live approval settings and bounded standing-grant decisions."""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

YOU = "you"
NOBODY = "nobody"
TRUST = "trust"
YOLO = "yolo"
TRUST_READS = "trust_reads"
AGENT_FLOOR = "agent_floor"
PARENT_TRUST = "parent_trust"
APPROVAL_MODE = "approval_mode"
SETTING = "setting"
HOOK_SETTING = "hook_setting"
HOOK_PATTERN = "hook_pattern"
SOURCE = "source"
CLI = "cli"
APP = "app_grant"
REMEMBERED = "remembered"
GATE_POLICY = "gate_policy"
SESSION_POLICY = "session_policy"
AUTO_EXECUTE = "auto_execute"
NO_SURFACE = "no_approval_surface"
ACP_MODE = "acp_mode"
INJECTION = "result_injection"
EVAL_SAFE_TOOLS = "eval_safe_tools"
LEVEL_AUTO = "auto"
LEVEL_HOOK = "hook_based"


@dataclass(frozen=True)
class ToolDecision:
    approved: bool
    outcome: str
    decided_by: str

    def __bool__(self) -> bool:
        return self.approved


def decision_of(answer: object) -> ToolDecision:
    if isinstance(answer, ToolDecision):
        return answer
    approved = bool(answer)
    return ToolDecision(approved, "approved" if approved else "rejected", YOU)


def stands(grant: str, *, caller: str, subject: str = "", level: str = LEVEL_AUTO,
           audit: bool = True) -> bool:
    try:
        from gideon.security.guardrails.ceiling import approval_permits_now

        permitted = approval_permits_now(level)
    except Exception:
        logger.warning("could not read the operator ceiling; refusing %s", grant, exc_info=True)
        permitted = False
    if permitted:
        return True
    if audit:
        try:
            from gideon.security.sel import sel

            sel().log_api_access(
                caller=caller or "approval",
                operation="approval.grant_refused",
                outcome="blocked",
                source="guardrails",
                resources=f"grant={grant},refused_by=governance_ceiling"
                + (f",{subject[:120]}" if subject else ""),
            )
        except Exception:
            logger.warning("approval grant refusal audit failed", exc_info=True)
    return False


def approval_mode_now() -> str:
    try:
        from gideon.core.config import AppConfig

        return str(AppConfig.load().agent.approval_mode or "")
    except Exception:
        logger.warning("could not read approval mode; asking", exc_info=True)
        return ""


def agent_mode_now(agent_name: str = "") -> str:
    try:
        from gideon.core.config import AppConfig

        cfg = AppConfig.load()
        profile = cfg.agents.get(agent_name) if agent_name else None
        return str((getattr(profile, "approval_mode", "") if profile else "") or cfg.agent.approval_mode or "")
    except Exception:
        logger.warning("could not read agent approval mode; asking", exc_info=True)
        return ""


def approval_window_secs(default_secs: float = 7200.0) -> float:
    try:
        from gideon.core.config import AppConfig

        value = getattr(AppConfig.load().agent, "approval_timeout_minutes", None)
        if value is None:
            return float(max(1.0, default_secs))
        minutes = int(value)
    except Exception:
        return float(max(1.0, default_secs))
    return float(max(1, minutes) * 60)


def hooks_now():
    from gideon.core.config import AppConfig
    from gideon.engine.hooks import HooksConfig

    return HooksConfig.from_dict(AppConfig.load().hooks or {})
