"""Native grant enforcement through actual runtime and skill consumers."""

import asyncio
import json
from pathlib import Path

import pytest

from gideon.engine.agents.tool_list import AgentTools, agent_tools, widens
from gideon.engine.agents.skill_list import AgentSkills, hold, let_go, held
from gideon.extensions.skills.loader import ProcedureLibrary


def skill(root, name, body=None):
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: orbital diagnostics {name}\ntriggers: orbital diagnostics\n---\n{body or name + '-private-body'}\n"
    )
    return folder


@pytest.mark.parametrize("raw", ["bash", True, {}, [False, 2], [""]])
def test_bad_tool_configuration_denies_execution(raw):
    grants = AgentTools.of("observer", raw)
    assert not grants.allows("bash")
    assert grants.allows("tool_result_get")


def test_patterns_and_widening():
    grants = AgentTools.of("reader", ["read_*", "read_*"])
    assert grants.patterns == ("read_*",)
    assert grants.allows("read_file")
    assert not grants.allows("bash")
    assert not widens([], ["bash"])
    assert not widens(["read_*"], ["read_file"])
    assert widens(["read_file"], [])
    assert widens(["read_*"], ["bash"])


def test_corrupt_actual_configuration_fails_closed(tmp_path, monkeypatch):
    from gideon.core.config.schema import AppConfig

    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text("{broken")
    grants = agent_tools(None, AppConfig.load())
    assert grants.unreadable
    assert not grants.allows("bash")


def test_narrowed_real_library_read_index_and_resources(tmp_path):
    skill(tmp_path, "allowed")
    skill(tmp_path, "withheld")
    library = ProcedureLibrary(tmp_path, install_builtins=False)
    view = AgentSkills.of("reader", ["allowed"]).library(library)
    assert [row["key"] for row in view.list_skills()] == ["allowed"]
    assert view.load_skill("withheld") is None
    assert view.skill_file("withheld") is None
    assert "allowed-private-body" in view.load_skill("allowed")
    assert len(library.list_skills()) == 2
    tighter = AgentSkills.of("reader", ["withheld"]).library(view)
    assert tighter.list_skills() == []


def test_agent_own_tier_retained_without_other_agents(tmp_path, monkeypatch):
    from gideon.extensions.skills.loader import agent_skills_dir

    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    skill(tmp_path / "skills", "allowed")
    skill(tmp_path / "skills", "withheld")
    skill(agent_skills_dir("reader"), "own")
    skill(agent_skills_dir("other"), "other-private")
    view = AgentSkills.of("reader", ["allowed"]).library(
        ProcedureLibrary(install_builtins=False, agent="reader")
    )
    keys = {row["key"] for row in view.list_skills()}
    assert keys == {"allowed", "own"}
    assert view.skill_file("own") is not None
    assert view.skill_file("other-private") is None


@pytest.mark.asyncio
async def test_context_scopes_are_isolated():
    async def turn(name):
        token = hold(AgentSkills.of(name, [name]))
        try:
            await asyncio.sleep(0)
            assert held().names == (name,)
        finally:
            let_go(token)

    await asyncio.gather(turn("one"), turn("two"))
    assert held() is None


def test_actual_mcp_skill_consumers_withheld(tmp_path, monkeypatch):
    from gideon.integrations.mcp_core import _call_tool_inner

    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    skill(tmp_path / "skills", "allowed")
    skill(tmp_path / "skills", "withheld", "NEVER-DISCLOSE")
    token = hold(AgentSkills.of("reader", ["allowed"]))
    try:
        refused = _call_tool_inner("skill_invoke", {"name": "withheld"})
        assert "skill list" in refused and "NEVER-DISCLOSE" not in refused
        refused = _call_tool_inner(
            "skill_resource", {"skill": "withheld", "path": "secret.txt"}
        )
        assert "skill list" in refused
        found = _call_tool_inner("skill_search", {"query": "orbital diagnostics"})
        assert "allowed" in found and "withheld" not in found
        assert "allowed-private-body" in _call_tool_inner(
            "skill_invoke", {"name": "allowed"}
        )
    finally:
        let_go(token)


def test_actual_prompt_skill_parts_withheld(tmp_path):
    from gideon.cognition.context import PromptAssembler, _Parts

    skill(tmp_path, "allowed")
    skill(tmp_path, "withheld", "NEVER-DISCLOSE")
    library = AgentSkills.of("reader", ["allowed"]).library(
        ProcedureLibrary(tmp_path, install_builtins=False)
    )
    assembler = PromptAssembler(skills=library)
    parts = _Parts()
    assembler._skill_parts(
        parts,
        "orbital diagnostics",
        None,
        False,
        ["allowed", "withheld"],
        [],
        [],
        library,
    )
    assert "allowed-private-body" in parts.text()
    assert "NEVER-DISCLOSE" not in parts.text()


@pytest.mark.asyncio
async def test_actual_native_inventory_and_both_execution_paths(tmp_path, monkeypatch):
    from gideon.integrations.llm.scripted import ScriptedProvider
    from gideon.engine.agents.provider import AgentRuntimeDefinition
    from gideon.engine.agents.native.runtime import NativeAgentRuntime
    from gideon.engine.agents.native.builtin_tools import NativeBuiltinToolProvider
    from gideon.engine.agents.native.tools import InProcessMcpToolProvider

    script = tmp_path / "model.json"
    script.write_text(json.dumps({"version": 1, "turns": [{"text": "ready"}]}))
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIDEON_SCRIPTED_MODEL_SCRIPT", str(script))
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(
            name="reader", tools=["read_file", "skill_invoke"], skills=["allowed"]
        ),
        model_provider=ScriptedProvider(),
        tool_providers=[
            NativeBuiltinToolProvider(
                cwd=tmp_path,
                categories={"filesystem", "shell"},
                provider_name="gideon-filesystem",
            ),
            InProcessMcpToolProvider(),
        ],
        cwd=tmp_path,
    )
    skill(tmp_path / "home" / "skills", "allowed")
    skill(tmp_path / "home" / "skills", "withheld", "NEVER-DISCLOSE")
    await runtime.start()
    try:
        assert "read_file" in runtime._tool_index
        assert "bash" not in runtime._tool_index
        assert {item.name for item in runtime._tool_defs} == {
            "read_file",
            "skill_invoke",
        }
        skill_meta = {}
        refused_skill = await runtime._invoke(
            "skill_invoke", {"name": "withheld"}, meta_sink=skill_meta
        )
        assert "skill list" in refused_skill and "NEVER-DISCLOSE" not in refused_skill
        assert held() is None
        permitted = await runtime._invoke(
            "skill_invoke", {"name": "allowed"}, meta_sink={}
        )
        assert "allowed-private-body" in permitted
        assert held() is None
        for method in ("_invoke", "_guard_and_invoke"):
            meta = {}
            args = {"command": f"touch {tmp_path / 'must-not-exist'}"}
            if method == "_invoke":
                result = await runtime._invoke("bash", args, meta_sink=meta)
            else:
                result = await runtime._guard_and_invoke(None, "bash", args, meta=meta)
            assert "tool list" in result
            assert meta["ok"] is False and meta["refused_by"] == "agent_tools"
        assert not (tmp_path / "must-not-exist").exists()
    finally:
        await runtime.shutdown()
