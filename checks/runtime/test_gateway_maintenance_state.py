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
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    _, checkout = upstream_pair(tmp_path)
    (checkout / "tracked.txt").write_text("local edits")
    (checkout / "notes.txt").write_text("untracked notes")
    before = git(checkout, "rev-parse", "HEAD")
    monkeypatch.setenv("GIDEON_PROJECT_DIR", str(checkout))
    asyncio.run(coordinator()._auto_apply_update())
    assert git(checkout, "rev-parse", "HEAD") == before
    assert (checkout / "tracked.txt").read_text() == "local edits"
    assert (checkout / "notes.txt").read_text() == "untracked notes"
    assert git(checkout, "rev-parse", "origin/main") != before


def test_clean_source_stage_updates_only_temporary_checkout(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    origin, checkout = upstream_pair(tmp_path)
    (checkout / "notes.txt").write_text("retained")
    update = RuntimeUpdates(
        coordinator(), build_frontend_async, logging.getLogger(__name__)
    )
    assert asyncio.run(update.refresh_source(str(checkout), "main"))
    assert git(checkout, "rev-parse", "HEAD") == git(origin, "rev-parse", "HEAD")
    assert (checkout / "tracked.txt").read_text() == "updated"
    assert (checkout / "notes.txt").read_text() == "retained"
    assert not asyncio.run(update.refresh_source(str(checkout), "main"))


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
