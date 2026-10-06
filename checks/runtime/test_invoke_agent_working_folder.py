import asyncio
import json
from pathlib import Path

import pytest

from gideon.cognition.context import PromptAssembler
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.engine.subagent import DelegationSupervisor
from gideon.extensions.apps.app_config import read_config, write_config
from gideon.integrations.action_providers import services
from gideon.integrations.action_providers.base import ActionContext
from gideon.integrations.action_providers.invoke_agent_provider import (
    InvokeAgentActionProvider,
)
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.security.sel import sel


@pytest.mark.asyncio
async def test_allowed_folder_reaches_spawn_and_foreign_home_is_refused(
    tmp_path, monkeypatch
):
    home = tmp_path / "gideon"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    allowed = tmp_path / "allowed"
    project = allowed / "project"
    project.mkdir(parents=True)
    foreign = tmp_path / "other-user"
    foreign.mkdir()
    (allowed / "escape").symlink_to(foreign, target_is_directory=True)
    (allowed / "project-link").symlink_to(project, target_is_directory=True)
    file_path = allowed / "file.txt"
    file_path.write_text("x")
    configuration = {
        "providers": [],
        "agent": {
            "spawn_min_memory_gb": 0,
            "subagent_cwd_allowed_roots": [str(allowed)],
        },
    }
    (home / "config.json").write_text(json.dumps(configuration))
    sessions = ConversationDirectory(AppConfig.load())
    supervisor = DelegationSupervisor(sessions, PromptAssembler(), max_concurrent=0)
    live = services.ActionServices(
        state=ConsoleState(sessions=sessions, start_time=0),
        spawn_background=asyncio.create_task,
        subagents=supervisor,
    )
    monkeypatch.setattr(services, "_services", live)
    action = InvokeAgentActionProvider()
    context = ActionContext("Stop", payload={"session_key": "parent"})
    manifest = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "runtime/gideon/extensions/apps/native/invoke-agent-action/app.json"
        ).read_text()
    )
    schema = manifest["provider"]["settingsSchema"]
    assert schema["properties"]["cwd"]["type"] == "string"
    assert "cwd" not in schema["required"]
    saved = write_config(
        manifest["name"],
        {"task_template": "allowed project", "cwd": str(project)},
        schema,
    )
    assert read_config(manifest["name"]) == saved

    async def invoke(config):
        previous = set(asyncio.all_tasks())
        result = await action.execute(config, context)
        assert result.success and result.outcome == "launched"
        await asyncio.gather(*(set(asyncio.all_tasks()) - previous))

    await invoke(read_config(manifest["name"]))
    child = next(iter(supervisor._agents.values()))
    assert child.queued and not child.done and not child.error
    assert child.cwd == str(project.resolve())
    assert supervisor._runner_arguments(child, "")["cwd"] == str(project.resolve())

    for folder, expected in (
        (str(foreign), "not under any allowed root"),
        ("relative/path", "absolute"),
        (str(allowed / "does-not-exist"), "does not exist"),
        (str(file_path), "directory"),
        (str(allowed / "escape"), "not under any allowed root"),
    ):
        admitted = set(supervisor._agents)
        await invoke({"task_template": "refused folder", "cwd": folder})
        assert set(supervisor._agents) == admitted
        rejection = next(
            event
            for event in sel().recent()
            if event["outcome"] == "rejected_invalid_cwd"
        )
        assert rejection["metadata"]["cwd"] == folder
        assert expected in rejection["metadata"]["reason"]
        assert supervisor._running_count == 0

    await invoke(
        {"task_template": "resolved link", "cwd": str(allowed / "project-link")}
    )
    linked = list(supervisor._agents.values())[-1]
    assert linked.cwd == str(project.resolve())

    configuration["agent"]["subagent_cwd_allowed_roots"] = []
    (home / "config.json").write_text(json.dumps(configuration))
    admitted = set(supervisor._agents)
    await invoke({"task_template": "disabled override", "cwd": str(project)})
    assert set(supervisor._agents) == admitted
    assert "disabled" in sel().recent()[0]["metadata"]["reason"]

    for config in (
        {"task_template": "default directory"},
        {"task_template": "empty directory", "cwd": ""},
    ):
        await invoke(config)
        default = list(supervisor._agents.values())[-1]
        assert default.cwd == "" and not default.error
        assert "cwd" not in supervisor._runner_arguments(default, "")
    assert len(supervisor._queue) == 4
    assert supervisor._tasks == {}
