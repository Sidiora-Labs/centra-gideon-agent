"""Tool grants held by one native agent."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable

logger = logging.getLogger(__name__)
ALWAYS_KEPT = frozenset({"tool_result_get"})
REFUSED_BY = "agent_tools"
_NAMED = 20
NO_CONFIG = "the configuration file could not be read"


@dataclass(frozen=True)
class AgentTools:
    agent: str = ""
    patterns: tuple[str, ...] = ()
    listed: bool = False
    unreadable: str = ""
    fixed: bool = False

    @classmethod
    def of(cls, agent: str, value: Any, *, fixed: bool = False) -> "AgentTools":
        if value is None or value == [] or value == ():
            return cls(agent=agent, fixed=fixed)
        if not isinstance(value, (list, tuple)):
            return cls.cannot_read(
                agent,
                "it is not a list of tool names",
                type(value).__name__,
                fixed=fixed,
            )
        names = _names(value)
        if not names:
            return cls.cannot_read(
                agent, "none of its entries is a tool name", fixed=fixed
            )
        return cls(agent=agent, patterns=names, listed=True, fixed=fixed)

    @classmethod
    def cannot_read(
        cls, agent: str, why: str, detail: str = "", *, fixed: bool = False
    ) -> "AgentTools":
        held = cls(agent=agent, listed=True, unreadable=why, fixed=fixed)
        logger.warning(
            "the tool list of the agent %s could not be read (%s%s): it may use no tool until %s",
            agent,
            why,
            f": {detail}" if detail else "",
            held._until,
        )
        return held

    @property
    def _until(self) -> str:
        if self.unreadable == NO_CONFIG:
            return "the configuration file is repaired"
        if self.fixed:
            return "its entry in the configuration file is fixed"
        return "the list is fixed on the Agents page"

    def allows(self, tool_name: str) -> bool:
        if not self.listed or tool_name in ALWAYS_KEPT:
            return True
        from gideon.security.guardrails.registries import name_glob

        return any((name_glob(tool_name, pattern) for pattern in self.patterns))

    def refusal(self, tool_name: str) -> str:
        if self.unreadable:
            return f"the tool list of the agent {self.agent} could not be read ({self.unreadable}), so it may use no tool until {self._until}, {tool_name} included"
        if self.fixed:
            return f"the built-in agent {self.agent} may use only the tools Gideon gives it, and {tool_name} is not one of them"
        return f"the agent {self.agent} may use only the tools on its tool list, set on the Agents page, and {tool_name} is not one of them"

    def refuse(self, tool_name: str, meta: dict[str, Any]) -> str:
        from gideon.security import security

        _, observation = security.classify_denial(
            security.DENY_KIND_POLICY, self.refusal(tool_name), tool_name
        )
        meta["ok"] = False
        meta["refused_by"] = REFUSED_BY
        logger.warning(
            "native: refused %s, which the agent %s may not use",
            tool_name[:120],
            self.agent,
        )
        return observation

    def say_narrowed(self, *, kept: list[str], withheld: list[str]) -> None:
        shown = ", ".join(kept[:_NAMED]) or "none"
        if len(kept) > _NAMED:
            shown += f" and {len(kept) - _NAMED} more"
        unmatched = self.unmatched([*kept, *withheld])
        total = len(kept) + len(withheld)
        logger.log(
            logging.WARNING if unmatched else logging.INFO,
            "native: the agent %s may use %d of the %d %s on offer, as its tool list says: %s%s",
            self.agent,
            len(kept),
            total,
            "tool" if total == 1 else "tools",
            shown,
            (
                f"; its entries {', '.join(unmatched)} match no tool on offer"
                if unmatched
                else ""
            ),
        )

    def unmatched(self, names: Iterable[str]) -> list[str]:
        from gideon.security.guardrails.registries import name_glob

        offered = list(names)
        return [p for p in self.patterns if not any((name_glob(n, p) for n in offered))]


def _names(value: list | tuple) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            (str(v).strip() for v in value if isinstance(v, str) and str(v).strip())
        )
    )


def _allowed(value: Any) -> tuple[str, ...] | None:
    if value is None or value == [] or value == ():
        return None
    if not isinstance(value, (list, tuple)):
        return ()
    return _names(value)


def _covers(patterns: tuple[str, ...], entry: str) -> bool:
    if entry in ALWAYS_KEPT or entry in patterns:
        return True
    if any((ch in entry for ch in "*?[")):
        return False
    from gideon.security.guardrails.registries import name_glob

    return any((name_glob(entry, pattern) for pattern in patterns))


def widens(current: Any, new: Any) -> bool:
    before, after = (_allowed(current), _allowed(new))
    if before is None:
        return False
    if after is None:
        return True
    return any((not _covers(before, entry) for entry in after))


def agent_tools(agent: str | None, cfg: Any) -> AgentTools:
    from gideon.core.config.loader import resolve_config_dir
    from gideon.engine.agents.defaults import (
        default_agent_name,
        is_reserved_agent,
        normalize_agent_name,
    )

    wanted = (agent or "").strip()
    key = normalize_agent_name(wanted) if wanted else default_agent_name(cfg)
    unreadable = cfg is None
    if cfg is not None and getattr(cfg, "_loaded_missing", False):
        try:
            unreadable = (resolve_config_dir() / "config.json").exists()
        except OSError:
            unreadable = True
    if unreadable:
        return AgentTools.cannot_read(key, NO_CONFIG, fixed=is_reserved_agent(key))
    profile = (getattr(cfg, "agents", None) or {}).get(key)
    value = getattr(profile, "tools", None) if profile is not None else None
    raw_profiles = (getattr(cfg, "_loaded_values", {}) or {}).get("agents", {})
    raw_profile = raw_profiles.get(key) if isinstance(raw_profiles, dict) else None
    if isinstance(raw_profile, dict) and "tools" in raw_profile:
        value = raw_profile["tools"]
    return AgentTools.of(key, value, fixed=is_reserved_agent(key))
