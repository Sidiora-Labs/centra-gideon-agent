"""Skill grants held by one native turn and its tool calls."""

from __future__ import annotations

import contextvars
import logging
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:
    from gideon.extensions.skills.loader import ProcedureLibrary

logger = logging.getLogger(__name__)
_HELD: contextvars.ContextVar[AgentSkills | None] = contextvars.ContextVar(
    "gideon_agent_skills", default=None
)


@dataclass(frozen=True)
class AgentSkills:
    agent: str = ""
    names: tuple[str, ...] = ()
    listed: bool = False
    fixed: bool = False

    @classmethod
    def of(cls, agent: str, value: Any, *, fixed: bool = False) -> "AgentSkills":
        if value is None or value == [] or value == ():
            return cls(agent=agent, fixed=fixed)
        if isinstance(value, (list, tuple)):
            names = tuple(
                dict.fromkeys(
                    (
                        str(v).strip()
                        for v in value
                        if isinstance(v, str) and str(v).strip()
                    )
                )
            )
            if names:
                return cls(agent=agent, names=names, listed=True, fixed=fixed)
        logger.warning(
            "the skill list of the agent %s could not be read (%s): its turns are offered skills as an agent with no list is",
            agent,
            type(value).__name__,
        )
        return cls(agent=agent, fixed=fixed)

    def allows(self, name: str) -> bool:
        return not self.listed or name in self.names

    def refusal(self, name: str) -> str:
        if self.fixed:
            return f"the built-in agent {self.agent} may use only the skills Gideon gives it, and {name} is not one of them"
        return f"the agent {self.agent} may use only the skills on its skill list, set on the Agents page, and {name} is not one of them"

    def beside(self, names: Iterable[str]) -> "AgentSkills":
        extra = tuple(
            dict.fromkeys(
                (
                    n.strip()
                    for n in names
                    if isinstance(n, str)
                    and n.strip()
                    and (n.strip() not in self.names)
                )
            )
        )
        if not self.listed or not extra:
            return self
        return replace(self, names=self.names + extra)

    def library(self, loader: "ProcedureLibrary") -> "ProcedureLibrary":
        if not self.listed:
            return loader
        from gideon.extensions.skills.loader import narrowed

        return narrowed(loader, self.allows)


def hold(skills: AgentSkills) -> contextvars.Token:
    return _HELD.set(skills)


def let_go(token: contextvars.Token) -> None:
    try:
        _HELD.reset(token)
    except (ValueError, LookupError):
        pass


def held() -> AgentSkills | None:
    return _HELD.get()


def agent_skills(agent: str | None, cfg: Any) -> AgentSkills:
    from gideon.engine.agents.defaults import (
        default_agent_name,
        is_reserved_agent,
        normalize_agent_name,
    )

    wanted = (agent or "").strip()
    key = normalize_agent_name(wanted) if wanted else default_agent_name(cfg)
    if cfg is None:
        return AgentSkills(agent=key)
    profile = (getattr(cfg, "agents", None) or {}).get(key)
    if profile is None:
        return AgentSkills(agent=key)
    runtime = str(
        getattr(profile, "provider", "")
        or getattr(getattr(cfg, "agent", None), "provider", "")
    )
    if runtime.startswith("acp"):
        return AgentSkills(agent=key)
    raw_profiles = (getattr(cfg, "_loaded_values", {}) or {}).get("agents", {})
    raw_profile = raw_profiles.get(key) if isinstance(raw_profiles, dict) else None
    value = (
        raw_profile.get("skills")
        if isinstance(raw_profile, dict) and "skills" in raw_profile
        else getattr(profile, "skills", None)
    )
    return AgentSkills.of(key, value, fixed=is_reserved_agent(key))
