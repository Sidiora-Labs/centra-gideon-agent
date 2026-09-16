"""SDLC tools exercised against actual persisted loops and nudge registrations."""

import pytest

from gideon.automation.loop import files, manager, store
from gideon.automation.loop.loop import LoopStatus
from gideon.engine.agents.native import sdlc_tools


@pytest.fixture
def runtime_state(tmp_path, monkeypatch):
    from gideon.automation.triggers import nudge
    from gideon.core.config.loader import AppConfig
    from gideon.engine.session import ConversationDirectory
    from gideon.integrations.inbox_providers import native_source
    from gideon.interfaces.dashboard.state import ConsoleState

    home = tmp_path / "home"
    monkeypatch.setenv("GIDEON_HOME", str(home))
    state = ConsoleState(ConversationDirectory(AppConfig()), start_time=0)
    service = nudge.AutoNudgeService(base_dir=home)
    monkeypatch.setattr(nudge, "_INSTANCE", service)
    previous = native_source.get_dashboard_state()
    native_source.set_dashboard_state(state)
    try:
        yield state, service
    finally:
        native_source.set_dashboard_state(previous)
        service.stop()


@pytest.mark.asyncio
async def test_start_and_resume_register_real_worker_state(runtime_state):
    state, service = runtime_state
    draft = await sdlc_tools.project_create(
        {
            "kind": "goal",
            "task": "Document the local service interfaces",
            "max_cycles": 80,
            "sub_goals": ["inventory", "document"],
        }
    )
    assert draft.success, draft.error
    loop = next(item for item in store.list_all() if item.kind == "goal")
    started = await sdlc_tools.project_start({"project_id": loop.id})
    assert started.success, started.error
    assert "⚠" in started.output
    assert store.get(loop.id).status == "running"
    session = state.get_session(manager.session_key(loop.id))
    assert session is not None and session._trust and session._unattended
    assert str(files.loop_dir(loop.id)) in session._extra_tool_roots
    assert (files.loop_dir(loop.id) / "brief.md").is_file()
    worker = service.get_by_session(session.key)
    assert worker is not None and worker.active and worker.max_cycles == 80

    await manager.pause(state, service, loop.id)
    assert not service.get_by_session(session.key).active
    assert store.update_spec(loop.id, {"max_cycles": 99999}) is None
    resumed = await sdlc_tools.goal_loop_start({"loop_id": loop.id})
    assert resumed.success, resumed.error
    assert "⚠" not in resumed.output
    assert service.get_by_session(session.key).max_cycles == 80
    assert len(service.list_all()) == 1
    await manager.stop(state, service, loop.id)
    assert not (await sdlc_tools.project_start({"project_id": loop.id})).success


@pytest.mark.asyncio
async def test_brownfield_launch_blocker_runs_before_worker_registration(runtime_state):
    _, service = runtime_state
    draft = await sdlc_tools.code_project_create(
        {
            "task": "Refactor the service authentication module",
            "project_kind": "brownfield",
            "stage_plan": [
                {
                    "stage": "implementation",
                    "title": "Refactor",
                    "objective": "Simplify authentication",
                }
            ],
        }
    )
    assert draft.success, draft.error
    identifier = next(item.id for item in store.list_all() if item.kind == "code")
    result = await sdlc_tools.code_project_start({"project_id": identifier})
    assert not result.success and "workspace" in result.error.lower()
    assert not service.list_all()
    assert store.get(identifier).status == "ready"


@pytest.mark.asyncio
async def test_unified_create_list_and_status_keep_kind_and_goal_override(
    runtime_state,
):
    empty = await sdlc_tools.project_list({})
    assert empty.success and "no projects yet" in empty.output
    for kind in ("goal", "research", "general"):
        result = await sdlc_tools.project_create(
            {
                "kind": kind,
                "task": "Ignored task because goal is explicit",
                "goal": f"Produce a detailed {kind} report",
            }
        )
        assert result.success, result.error
    loops = store.list_all()
    assert all(loop.task.startswith("Produce a detailed") for loop in loops)
    filtered = await sdlc_tools.project_list({"kind": "research", "limit": 1})
    assert filtered.success and "1 project(s)" in filtered.output
    assert "[research]" in filtered.output and "[goal]" not in filtered.output
    fallback = await sdlc_tools.project_list({"kind": "unknown", "limit": "invalid"})
    assert "3 project(s)" in fallback.output
    for loop in loops:
        status = await sdlc_tools.project_status({"project_id": loop.id})
        assert status.success and f"/#/loops/{loop.id}" in status.output


def test_agent_admission_retains_acp_and_default_agent_rules(runtime_state):
    assert sdlc_tools._agent_exists({})
    assert sdlc_tools._agent_exists(
        {"provider": "acp:external", "agent": "not-installed-here"}
    )
    assert not sdlc_tools._agent_exists({"agent": "not-installed-here"})


@pytest.mark.asyncio
async def test_status_reports_stagnation_reason_from_persisted_state(runtime_state):
    created = await sdlc_tools.goal_loop_create(
        {"goal": "Review the performance measurements"}
    )
    assert created.success, created.error
    identifier = store.list_all()[0].id
    store.update_status(identifier, LoopStatus.RUNNING)
    store.update_status(
        identifier,
        LoopStatus.STAGNANT,
        error_message="Repeated the same finding without new evidence",
    )
    result = await sdlc_tools.sdlc_status({"id": identifier})
    assert result.success and "Stagnant: Repeated the same finding" in result.output
    assert "No findings yet." in result.output
