import asyncio
import logging
import subprocess
import time

from gideon.core.config import AppConfig
from gideon.engine.gateway import RuntimeCoordinator, build_frontend_async
from gideon.engine.gateway_maintenance import (
    DependencyRepair,
    RuntimeUpdates,
    run_command,
)
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.operations.self_update import SourcePlan


def git(root, *arguments):
    return (
        subprocess.check_output(
            ["git", "-C", str(root), *arguments], stderr=subprocess.PIPE
        )
        .decode()
        .strip()
    )


def upstream_pair(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-b", "main")
    git(origin, "config", "user.email", "test@example.invalid")
    git(origin, "config", "user.name", "Gideon test")
    (origin / "tracked.txt").write_text("original")
    git(origin, "add", "tracked.txt")
    git(origin, "commit", "-m", "Initial fixture")
    checkout = tmp_path / "checkout"
    subprocess.run(
        ["git", "clone", str(origin), str(checkout)], check=True, capture_output=True
    )
    (origin / "tracked.txt").write_text("updated")
    git(origin, "add", "tracked.txt")
    git(origin, "commit", "-m", "Fixture update")
    return origin, checkout


def coordinator():
    runtime = RuntimeCoordinator(AppConfig())
    runtime.sessions = ConversationDirectory(runtime.config)
    runtime.dashboard_state = ConsoleState(runtime.sessions, time.time())
    return runtime


def test_dirty_real_repository_refuses_update_and_retains_files(tmp_path, monkeypatch):
    """RUM-75: tracked edits pause the unattended update BEFORE anything is
    fetched or moved, and every local file survives untouched."""
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    _, checkout = upstream_pair(tmp_path)
    (checkout / "tracked.txt").write_text("local edits")
    (checkout / "notes.txt").write_text("untracked notes")
    before = git(checkout, "rev-parse", "HEAD")
    monkeypatch.setenv("GIDEON_PROJECT_DIR", str(checkout))
    runtime = coordinator()
    asyncio.run(runtime._auto_apply_update())
    assert git(checkout, "rev-parse", "HEAD") == before
    assert (checkout / "tracked.txt").read_text() == "local edits"
    assert (checkout / "notes.txt").read_text() == "untracked notes"
    assert not (checkout / ".git" / "FETCH_HEAD").exists()
    progress = runtime.dashboard_state._update_progress
    assert progress["step"] == "paused"
    assert "commit or stash" in progress["detail"].lower()


def test_clean_source_stage_fast_forwards_onto_the_release_tag(tmp_path, monkeypatch):
    """A clean checkout moves to the SELECTED TAG by fast-forward — no reset."""
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    origin, checkout = upstream_pair(tmp_path)
    git(origin, "tag", "v0.2.0")
    (checkout / "notes.txt").write_text("retained")
    update = RuntimeUpdates(
        coordinator(), build_frontend_async, logging.getLogger(__name__)
    )
    plan = SourcePlan("tag", "v0.2.0")
    assert asyncio.run(update.refresh_source(str(checkout), plan))
    assert git(checkout, "rev-parse", "HEAD") == git(origin, "rev-parse", "v0.2.0")
    assert (checkout / "tracked.txt").read_text() == "updated"
    assert (checkout / "notes.txt").read_text() == "retained"
    assert not asyncio.run(update.refresh_source(str(checkout), plan))


def test_a_diverged_checkout_is_refused_not_rewritten(tmp_path, monkeypatch):
    """Fast-forward only: a checkout carrying its own commit keeps it."""
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    origin, checkout = upstream_pair(tmp_path)
    git(origin, "tag", "v0.2.0")
    (checkout / "mine.txt").write_text("local work")
    git(checkout, "add", "mine.txt")
    git(checkout, "config", "user.email", "test@example.invalid")
    git(checkout, "config", "user.name", "Gideon test")
    git(checkout, "commit", "-m", "local work")
    before = git(checkout, "rev-parse", "HEAD")
    runtime = coordinator()
    update = RuntimeUpdates(runtime, build_frontend_async, logging.getLogger(__name__))
    assert not asyncio.run(
        update.refresh_source(str(checkout), SourcePlan("tag", "v0.2.0"))
    )
    assert git(checkout, "rev-parse", "HEAD") == before
    assert (checkout / "mine.txt").read_text() == "local work"


def test_the_plan_reports_a_paused_checkout_and_declines_to_move(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    _, checkout = upstream_pair(tmp_path)
    (checkout / "tracked.txt").write_text("local edits")
    runtime = coordinator()
    update = RuntimeUpdates(runtime, build_frontend_async, logging.getLogger(__name__))
    assert asyncio.run(update.plan(str(checkout), AppConfig().updates)) is None
    assert runtime.dashboard_state._update_progress["step"] == "paused"


def test_a_staged_update_waits_for_active_work_then_proceeds(tmp_path, monkeypatch):
    """RUM-58: staged means the apply holds while a chat or subagent is running."""
    from gideon.interfaces.dashboard.handlers import updates as upd

    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    runtime = coordinator()
    state = runtime.dashboard_state
    update = RuntimeUpdates(runtime, build_frontend_async, logging.getLogger(__name__))

    busy = {"n": 2}
    monkeypatch.setattr(
        upd,
        "_active_work_snapshot",
        lambda _s: {"running_agents": busy["n"], "sessions": 0},
    )
    monkeypatch.setattr(upd, "QUIESCENCE_POLL_S", 0.01)

    async def _drive():
        waiter = asyncio.ensure_future(update.stage())
        await asyncio.sleep(0.05)
        assert not waiter.done(), "the staged apply did not wait for active work"
        busy["n"] = 0
        return await asyncio.wait_for(waiter, timeout=5)

    assert asyncio.run(_drive()) is True
    assert state._update_progress["step"] == "staged"
    assert "waiting for active work" in state._update_progress["detail"]


def test_a_staged_update_that_never_quiesces_defers_instead_of_interrupting(
    tmp_path, monkeypatch
):
    from gideon.interfaces.dashboard.handlers import updates as upd

    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    runtime = coordinator()
    update = RuntimeUpdates(runtime, build_frontend_async, logging.getLogger(__name__))
    monkeypatch.setattr(
        upd, "_active_work_snapshot", lambda _s: {"running_agents": 1, "sessions": 0}
    )
    monkeypatch.setattr(upd, "QUIESCENCE_POLL_S", 0.01)
    monkeypatch.setattr(upd, "QUIESCENCE_TIMEOUT_S", 0.05)
    assert asyncio.run(update.stage()) is False
    assert "deferred" in runtime.dashboard_state._update_progress["detail"]


def test_an_idle_gateway_stages_immediately(tmp_path, monkeypatch):
    """Vacuity guard: with nothing running the staged apply does not wait at all."""
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    runtime = coordinator()
    update = RuntimeUpdates(runtime, build_frontend_async, logging.getLogger(__name__))
    assert asyncio.run(update.stage()) is True


def test_feature_checkout_skips_fetch_without_origin(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    checkout = tmp_path / "feature"
    checkout.mkdir()
    git(checkout, "init", "-b", "feature/work")
    git(checkout, "config", "user.name", "Gideon test")
    git(checkout, "config", "user.email", "test@example.invalid")
    git(checkout, "commit", "--allow-empty", "-m", "Initial fixture")
    monkeypatch.setenv("GIDEON_PROJECT_DIR", str(checkout))
    before = git(checkout, "rev-parse", "HEAD")
    asyncio.run(coordinator()._auto_apply_update())
    assert git(checkout, "rev-parse", "HEAD") == before
    assert not (checkout / ".git" / "FETCH_HEAD").exists()


def test_command_transport_collects_real_stdout_and_exit_code(tmp_path):
    result = asyncio.run(run_command(("git", "--version"), str(tmp_path), 10))
    assert result.code == 0 and result.out.startswith(b"git version")
    assert not result.err


def test_dependency_repair_skips_absent_project_and_present_module(monkeypatch):
    monkeypatch.delenv("GIDEON_PROJECT_DIR", raising=False)
    DependencyRepair(
        [("gideon_missing_test_dependency", "never-installed")],
        logging.getLogger(__name__),
    ).run()
    DependencyRepair([("json", "json")], logging.getLogger(__name__)).run()
