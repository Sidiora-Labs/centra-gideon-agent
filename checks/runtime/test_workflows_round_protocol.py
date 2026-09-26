"""Real git-worktree checks for persistent rounds and change boundaries."""

import asyncio
import subprocess

from gideon.automation.workflows.round_protocol import admit_round, complete_round, read_rounds, release_round


def _git(path, *args):
    subprocess.run(["git", *args], cwd=path, check=True, capture_output=True, text=True)


def test_rounds_resume_and_quarantine_out_of_bound_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "gideon"))
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-q")
    _git(source, "config", "user.email", "round@example.test")
    _git(source, "config", "user.name", "Round")
    (source / "allowed.txt").write_text("initial\n")
    (source / "review.txt").write_text("initial\n")
    (source / "blocked.txt").write_text("initial\n")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "initial")
    worktree = tmp_path / "round"
    _git(source, "worktree", "add", "-qb", "round", str(worktree))
    config = {
        "worktree": str(worktree),
        "branch": "round",
        "roles": [
            {"name": "builder", "allowed_paths": ["allowed.txt"]},
            {"name": "reviewer", "allowed_paths": ["review.txt"]},
        ],
        "max_rounds": 2,
        "verify_command": "test -f allowed.txt",
    }
    assert not admit_round("unreviewed", {key: value for key, value in config.items() if key != "branch"}, {}).allow_next
    assert admit_round("round-run", config, {}).allow_next
    assert admit_round("round-run", config, {}).allow_next
    blocked = admit_round("other-run", config, {})
    assert not blocked.allow_next

    (worktree / "allowed.txt").write_text("changed\n")
    first = complete_round(run_id="round-run", iteration=0, config=config, inputs={}, output="continue")
    assert first.allow_next and not first.handoff["stop"]
    assert first.handoff["changed_paths"] == ["allowed.txt"]
    assert complete_round(run_id="round-run", iteration=0, config=config, inputs={}, output="ignored") == first

    (worktree / "review.txt").write_text("reviewed\n")
    (worktree / "allowed.txt").write_text("unauthorized overwrite\n")
    (worktree / "blocked.txt").write_text("forbidden\n")
    second = complete_round(run_id="round-run", iteration=1, config=config, inputs={}, output="done")
    assert not second.allow_next
    assert second.handoff["quarantined_paths"] == ["allowed.txt", "blocked.txt"]
    assert second.handoff["changed_paths"] == ["allowed.txt", "blocked.txt", "review.txt"]
    assert (worktree / "allowed.txt").read_text() == "changed\n"
    assert (worktree / "blocked.txt").read_text() == "initial\n"
    visible = read_rounds("round-run")
    assert [entry["completed_role"] for entry in visible] == ["builder", "reviewer"]
    assert "path_hashes" not in visible[0]
    assert {entry["path"]: entry["content"] for entry in visible[1]["quarantine_evidence"]} == {
        "allowed.txt": "unauthorized overwrite\n", "blocked.txt": "forbidden\n"}
    release_round("round-run", config, {})
    assert admit_round("other-run", config, {}).allow_next


def test_declared_round_loop_stops_and_projects_visible_handoffs(tmp_path, monkeypatch):
    from gideon.automation.workflows import service, store
    from gideon.automation.workflows.controller import EngineServices, RunController
    from gideon.automation.workflows.models import RunStatus, WorkflowRun
    from gideon.integrations.action_providers.registry import _ensure_default_providers_registered

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(store, "config_dir", lambda: home)
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-q")
    _git(source, "config", "user.email", "round@example.test")
    _git(source, "config", "user.name", "Round")
    (source / "proof.txt").write_text("ready\n")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "initial")
    worktree = tmp_path / "round"
    _git(source, "worktree", "add", "-qb", "round", str(worktree))
    spec = {
        "name": "round-journey",
        "root": {
            "kind": "loop", "id": "rounds",
            "config": {
                "mode": "counted", "n": 5, "session": "fresh",
                "round_protocol": {
                    "worktree": str(worktree),
                    "branch": "round",
                    "roles": [
                        {"name": "planner", "allowed_paths": ["created.txt"]},
                        {"name": "builder", "allowed_paths": ["created.txt"]},
                        {"name": "verifier", "allowed_paths": ["created.txt"]},
                    ],
                    "max_rounds": 3,
                    "verify_command": "test -f created.txt",
                },
            },
            "body": {"kind": "action", "id": "create", "config": {
                "provider": "bash", "with": {"command": "pwd; touch created.txt"}}},
        },
    }
    run = store.create(WorkflowRun(id="", workflow_name="round-journey"))
    store.write_spec(run.id, spec)
    _ensure_default_providers_registered()
    controller = RunController(run, spec, services=EngineServices(cwd=str(source)))
    assert asyncio.run(controller.run_to_completion(timeout=25)) == RunStatus.COMPLETE, service.status(run.id)
    assert controller.services.cwd == str(worktree)
    assert (worktree / "created.txt").is_file()
    assert not (source / "created.txt").exists()
    status = service.status(run.id)
    assert status["ok"]
    assert str(worktree) in next(iter(status["round_handoff"].values()))["output"]
    assert [row["completed_role"] for row in status["rounds"]] == ["planner", "builder", "verifier"]
    assert all(row["verification"]["exit_code"] == 0 for row in status["rounds"])
    assert any(handoff["stop"] is True for handoff in status["round_handoff"].values())


def test_orphaned_round_waits_for_explicit_resume(tmp_path, monkeypatch):
    from gideon.automation.workflows import service, store
    from gideon.automation.workflows.controller import EngineServices, RunController
    from gideon.automation.workflows.models import RunStatus, WorkflowRun
    from gideon.automation.workflows.watchdog import WorkflowWatchdog

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(store, "config_dir", lambda: home)
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-q")
    _git(source, "config", "user.email", "round@example.test")
    _git(source, "config", "user.name", "Round")
    (source / "proof.txt").write_text("ready\n")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "initial")
    worktree = tmp_path / "round"
    _git(source, "worktree", "add", "-qb", "round", str(worktree))
    spec = {"name": "recovery", "root": {"kind": "loop", "id": "rounds", "config": {
        "mode": "counted", "n": 3, "round_protocol": {
            "worktree": str(worktree), "branch": "round", "max_rounds": 2,
            "roles": [{"name": "planner", "allowed_paths": ["proof.txt"]},
                      {"name": "builder", "allowed_paths": ["proof.txt"]}],
            "verify_command": "test -f proof.txt",
        }}, "body": {"kind": "transform", "id": "proof", "config": {"expr": "ready"}}}}
    run = store.create(WorkflowRun(id="", workflow_name="recovery"))
    store.write_spec(run.id, spec)

    async def journey():
        first = RunController(run, spec, services=EngineServices(cwd=str(worktree)))
        await first.start()
        for _ in range(1000):
            if len(read_rounds(run.id)) == 1:
                break
            await asyncio.sleep(0.001)
        assert [row["completed_role"] for row in read_rounds(run.id)] == ["planner"], service.status(run.id)
        await first.stop()
        assert store.get(run.id).status == RunStatus.RUNNING
        watchdog = WorkflowWatchdog(services=EngineServices(cwd=str(worktree)))
        await watchdog._poll_once()
        state = service.status(run.id)
        assert state["round_interrupted"] is True
        assert watchdog.controller(run.id) is None
        result = await service.resume_interrupted_round(run.id, supervisor=watchdog)
        assert result["ok"] and result["resumed"]
        assert not (await service.resume_interrupted_round(run.id, supervisor=watchdog))["ok"]
        controller = watchdog.controller(run.id)
        assert controller is not None
        assert await controller.wait_for_terminal(timeout=25) == RunStatus.COMPLETE
        assert [row["completed_role"] for row in service.status(run.id)["rounds"]] == ["planner", "builder"]
        await watchdog.stop()

    asyncio.run(journey())


def test_paused_round_budget_extension_is_persisted_and_monotone(tmp_path, monkeypatch):
    from gideon.automation.workflows import service, store
    from gideon.automation.workflows.models import RunBudget, RunStatus, WorkflowRun

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(store, "config_dir", lambda: home)
    run = store.create(WorkflowRun(id="", workflow_name="budget-round", status=RunStatus.PAUSED,
                                   budget=RunBudget(max_tokens=100, max_cost=1.0)))
    store.write_spec(run.id, {"root": {"kind": "loop", "id": "rounds", "config": {
        "round_protocol": {"max_rounds": 2}}}})
    refused = service.extend_round_budget(run.id, {"max_tokens": 50, "max_cost": 2.0})
    assert not refused["ok"]
    assert store.get(run.id).budget.max_tokens == 100
    raised = service.extend_round_budget(run.id, {"max_tokens": 200, "max_cost": 2.0})
    assert raised["ok"] and raised["resumed"]
    assert service.status(run.id)["budget"]["max_tokens"] == 200
    assert store.get(run.id).budget.max_cost == 2.0


def test_verifier_check_handback_is_bounded_and_returns_to_named_builder(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "gideon"))
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-q")
    _git(source, "config", "user.email", "round@example.test")
    _git(source, "config", "user.name", "Round")
    (source / "proof.txt").write_text("ready\n")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "initial")
    worktree = tmp_path / "round"
    _git(source, "worktree", "add", "-qb", "round", str(worktree))
    config = {
        "worktree": str(worktree), "branch": "round", "max_rounds": 5,
        "max_handbacks": 1, "handback_role": "builder",
        "roles": [
            {"name": "planner", "allowed_paths": ["proof.txt"], "verify_command": "test -f proof.txt"},
            {"name": "builder", "allowed_paths": ["approved.txt"], "verify_command": "test -f proof.txt"},
            {"name": "verifier", "allowed_paths": ["checks/**"], "verify_command": "test -f approved.txt"},
        ],
    }
    assert admit_round("handback", config, {}).allow_next
    assert complete_round(run_id="handback", iteration=0, config=config, inputs={}, output="plan").allow_next
    assert complete_round(run_id="handback", iteration=1, config=config, inputs={}, output="build").allow_next
    failed = complete_round(run_id="handback", iteration=2, config=config, inputs={}, output="verify")
    assert failed.allow_next and failed.handoff["handback"]
    assert failed.handoff["next_role"] == "builder"
    assert failed.handoff["verification"]["exit_code"] != 0
    assert read_rounds("handback")[-1]["handback"] is True
    assert read_rounds("handback")[-1]["verification"]["exit_code"] != 0
    (worktree / "approved.txt").write_text("fixed\n")
    repaired = complete_round(run_id="handback", iteration=3, config=config, inputs={}, output="fix")
    assert repaired.allow_next and repaired.handoff["completed_role"] == "builder"
    verified = complete_round(run_id="handback", iteration=4, config=config, inputs={}, output="verified")
    assert verified.allow_next and verified.handoff["completed_role"] == "verifier"
    assert verified.handoff["verification"]["exit_code"] == 0
    release_round("handback", config, {})
    (worktree / "approved.txt").unlink()
    assert admit_round("handback-limit", config, {}).allow_next
    for iteration in (0, 1, 2, 3):
        assert complete_round(run_id="handback-limit", iteration=iteration, config=config, inputs={}, output="unchanged").allow_next
    exhausted = complete_round(run_id="handback-limit", iteration=4, config=config, inputs={}, output="still failing")
    assert not exhausted.allow_next
    assert exhausted.handoff["completed_role"] == "verifier"
    assert not exhausted.handoff["handback"]
