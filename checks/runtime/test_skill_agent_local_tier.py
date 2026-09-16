"""skill-agent-local-tier: an agent's own skills override global/bundled for it.

Precedence: bundled ⊂ user-global ⊂ agent-local (higher wins), scoped to the one
agent. The agent-local dir is ~/.gideon/agents/<slug>/skills/.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gideon.extensions.skills import loader as loader_mod
from gideon.extensions.skills.loader import (
    ProcedureLibrary,
    _agent_slug,
    agent_skills_dir,
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Point config_dir() (and thus every skills tier) at a temp home."""
    monkeypatch.setattr(loader_mod, "config_dir", lambda: tmp_path)
    import gideon.extensions.skills.marketplace as mp

    monkeypatch.setattr(mp, "SKILL_DISCOVERY_PATHS", [])
    return tmp_path


def _write_skill(base: Path, name: str, body: str, *, always: bool = False):
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    fm = (
        f"---\nname: {name}\ndescription: {name} desc\n"
        + ("always: true\n" if always else "")
        + "---\n"
    )
    (d / "SKILL.md").write_text(fm + body, encoding="utf-8")


def test_agent_slug_canonicalizes_and_sanitizes():
    assert _agent_slug(None) == "gideon"
    assert _agent_slug("Gideon") == "gideon"
    assert _agent_slug("My Agent/../x") == "my-agent-x"


def test_agent_local_dir_path(home):
    p = agent_skills_dir("researcher")
    assert p == home / "agents" / "researcher" / "skills"


def test_global_only_when_no_agent(home):
    _write_skill(home / "skills", "greet", "GLOBAL greet")
    loader = ProcedureLibrary(install_builtins=False)
    assert "GLOBAL greet" in (loader.load_skill("greet") or "")


def test_agent_local_overrides_global(home):
    _write_skill(home / "skills", "greet", "GLOBAL greet")
    _write_skill(agent_skills_dir("researcher"), "greet", "AGENT greet")
    base = ProcedureLibrary(install_builtins=False)
    assert "GLOBAL greet" in (base.load_skill("greet") or "")
    scoped = ProcedureLibrary(install_builtins=False, agent="researcher")
    assert "AGENT greet" in (scoped.load_skill("greet") or "")


def test_agent_local_adds_private_skill(home):
    _write_skill(agent_skills_dir("researcher"), "secret-tool", "private steps")
    base = ProcedureLibrary(install_builtins=False)
    assert base.load_skill("secret-tool") is None
    scoped = ProcedureLibrary(install_builtins=False, agent="researcher")
    assert "private steps" in (scoped.load_skill("secret-tool") or "")


def test_list_skills_tags_agent_local(home):
    _write_skill(home / "skills", "greet", "GLOBAL greet")
    _write_skill(agent_skills_dir("researcher"), "solo", "solo steps")
    scoped = ProcedureLibrary(install_builtins=False, agent="researcher")
    rows = {r["key"]: r for r in scoped.list_skills()}
    assert rows["solo"]["agent_local"] is True
    assert rows["greet"]["agent_local"] is False


def test_other_agents_do_not_see_it(home):
    _write_skill(agent_skills_dir("researcher"), "solo", "solo steps")
    other = ProcedureLibrary(install_builtins=False, agent="writer")
    assert other.load_skill("solo") is None


def test_get_context_uses_agent_tier(home):
    _write_skill(home / "skills", "brief", "GLOBAL brief body", always=True)
    _write_skill(
        agent_skills_dir("researcher"), "brief", "AGENT brief body", always=True
    )
    base = ProcedureLibrary(install_builtins=False)
    ctx_global = base.get_context()
    assert "GLOBAL brief body" in ctx_global
    ctx_agent = base.get_context(agent="researcher")
    assert "AGENT brief body" in ctx_agent
    assert "GLOBAL brief body" not in ctx_agent
