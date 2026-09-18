"""Tests for the update progress feature."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web

from gideon.interfaces.dashboard.state import ConsoleState
from gideon.operations import self_update as su


def _make_state(monkeypatch, tmp_path) -> ConsoleState:
    """Create a minimal ConsoleState for testing."""
    monkeypatch.setattr(
        "gideon.interfaces.dashboard.state.config_dir", lambda: tmp_path
    )
    return ConsoleState(
        sessions=MagicMock(count=0),
        start_time=0.0,
    )


class TestUpdateProgressState:
    """Tests for ConsoleState update progress tracking."""

    def test_initial_state_is_none(self, monkeypatch, tmp_path) -> None:
        state = _make_state(monkeypatch, tmp_path)
        assert state._update_progress is None

    def test_push_update_progress_sets_state(self, monkeypatch, tmp_path) -> None:
        state = _make_state(monkeypatch, tmp_path)
        state.push_update_progress("pulling", "Pulling latest changes…")
        assert state._update_progress == {
            "step": "pulling",
            "detail": "Pulling latest changes…",
        }

    def test_push_update_progress_updates_step(self, monkeypatch, tmp_path) -> None:
        state = _make_state(monkeypatch, tmp_path)
        state.push_update_progress("pulling", "Pulling…")
        state.push_update_progress("building", "Building…")
        assert state._update_progress == {"step": "building", "detail": "Building…"}

    def test_push_failed_keeps_progress_visible(self, monkeypatch, tmp_path) -> None:
        state = _make_state(monkeypatch, tmp_path)
        state.push_update_progress("pulling", "Pulling…")
        state.push_update_progress("failed", "Something broke")
        assert state._update_progress is not None
        assert state._update_progress["step"] == "failed"

    def test_clear_update_progress(self, monkeypatch, tmp_path) -> None:
        state = _make_state(monkeypatch, tmp_path)
        state.push_update_progress("building", "Building…")
        state.clear_update_progress()
        assert state._update_progress is None

    def test_broadcast_called_on_push(self, monkeypatch, tmp_path) -> None:
        state = _make_state(monkeypatch, tmp_path)
        calls: list[dict] = []
        monkeypatch.setattr(state, "_broadcast", lambda note: calls.append(note))
        state.push_update_progress("installing", "Installing package…")
        assert len(calls) == 1
        assert calls[0]["_type"] == "update_progress"
        assert calls[0]["step"] == "installing"
        assert calls[0]["detail"] == "Installing package…"

    def test_ws_broadcast_format(self, monkeypatch, tmp_path) -> None:
        """Verify the WS message format for update_progress events."""
        state = _make_state(monkeypatch, tmp_path)
        ws_messages: list[str] = []
        mock_ws = MagicMock()
        mock_ws.closed = False

        def fake_send(msg: str) -> None:
            ws_messages.append(msg)

        mock_ws.send_str = fake_send
        state._ws_clients = [mock_ws]

        state.push_update_progress("building", "Rebuilding package…")

        assert len(ws_messages) == 1
        parsed = json.loads(ws_messages[0])
        assert parsed["type"] == "update_progress"
        assert parsed["data"]["step"] == "building"
        assert parsed["data"]["detail"] == "Rebuilding package…"


class TestUpdateEndpoints:
    """Tests for the update HTTP endpoints."""

    @pytest.mark.asyncio
    async def test_simulate_walks_through_steps(self, monkeypatch, tmp_path) -> None:
        """Simulate endpoint broadcasts progress for each step."""
        monkeypatch.setattr(
            "gideon.interfaces.dashboard.state.config_dir", lambda: tmp_path
        )
        monkeypatch.setattr(
            "gideon.interfaces.dashboard.handlers.config_path",
            lambda: tmp_path / "c.json",
        )

        from gideon.interfaces.dashboard.handlers import api_update_simulate

        state = _make_state(monkeypatch, tmp_path)
        steps_seen: list[str] = []
        original_push = state.push_update_progress

        def track_push(step: str, detail: str = "") -> None:
            steps_seen.append(step)
            original_push(step, detail)

        monkeypatch.setattr(state, "push_update_progress", track_push)

        app = web.Application()
        app["state"] = state
        request = MagicMock()
        request.app = app
        request.json = AsyncMock(return_value={"delay": 0.01})

        resp = await api_update_simulate(request)
        data = json.loads(resp.body)
        assert data["status"] == "simulating"

        await asyncio.sleep(0.2)

        assert "pulling" in steps_seen
        assert "installing" in steps_seen
        assert "building" in steps_seen
        assert "restarting" in steps_seen
        assert "done" in steps_seen
        assert "syncing" not in steps_seen

    @pytest.mark.asyncio
    async def test_simulate_fail_at(self, monkeypatch, tmp_path) -> None:
        """Simulate endpoint stops at fail_at step."""
        monkeypatch.setattr(
            "gideon.interfaces.dashboard.state.config_dir", lambda: tmp_path
        )
        monkeypatch.setattr(
            "gideon.interfaces.dashboard.handlers.config_path",
            lambda: tmp_path / "c.json",
        )

        from gideon.interfaces.dashboard.handlers import api_update_simulate

        state = _make_state(monkeypatch, tmp_path)
        steps_seen: list[str] = []
        original_push = state.push_update_progress

        def track_push(step: str, detail: str = "") -> None:
            steps_seen.append(step)
            original_push(step, detail)

        monkeypatch.setattr(state, "push_update_progress", track_push)

        app = web.Application()
        app["state"] = state
        request = MagicMock()
        request.app = app
        request.json = AsyncMock(return_value={"delay": 0.01, "fail_at": "building"})

        await api_update_simulate(request)
        await asyncio.sleep(0.15)

        assert "pulling" in steps_seen
        assert "installing" in steps_seen
        assert "failed" in steps_seen
        assert "building" not in steps_seen
        assert "restarting" not in steps_seen

    @pytest.mark.asyncio
    async def test_simulate_reject(self, monkeypatch, tmp_path) -> None:
        """Simulate endpoint returns 409 when reject=true."""
        monkeypatch.setattr(
            "gideon.interfaces.dashboard.state.config_dir", lambda: tmp_path
        )
        monkeypatch.setattr(
            "gideon.interfaces.dashboard.handlers.config_path",
            lambda: tmp_path / "c.json",
        )

        from gideon.interfaces.dashboard.handlers import api_update_simulate

        state = _make_state(monkeypatch, tmp_path)
        app = web.Application()
        app["state"] = state
        request = MagicMock()
        request.app = app
        request.json = AsyncMock(return_value={"reject": True})

        resp = await api_update_simulate(request)
        assert resp.status == 409
        data = json.loads(resp.body)
        assert "uncommitted" in data["error"]
        assert data["status"] == "paused"

    @pytest.mark.asyncio
    async def test_cancel_clears_progress(self, monkeypatch, tmp_path) -> None:
        """Cancel endpoint clears update progress."""
        monkeypatch.setattr(
            "gideon.interfaces.dashboard.state.config_dir", lambda: tmp_path
        )

        from gideon.interfaces.dashboard.handlers import api_update_cancel

        state = _make_state(monkeypatch, tmp_path)
        state.push_update_progress("building", "Building…")
        assert state._update_progress is not None

        app = web.Application()
        app["state"] = state
        request = MagicMock()
        request.app = app

        resp = await api_update_cancel(request)
        assert resp.status == 200
        assert state._update_progress is None

    @pytest.mark.asyncio
    async def test_restart_probe_reports_active_work(
        self, monkeypatch, tmp_path
    ) -> None:
        """?probe=1 returns the active-work snapshot (running agents + sessions)
        for the confirm gate — WITHOUT restarting."""
        monkeypatch.setattr(
            "gideon.interfaces.dashboard.state.config_dir", lambda: tmp_path
        )
        from gideon.interfaces.dashboard.handlers import api_restart

        state = _make_state(monkeypatch, tmp_path)
        state.subagents = MagicMock()
        state.subagents.all_agents = [
            MagicMock(done=False),
            MagicMock(done=False),
            MagicMock(done=True),
        ]
        state.sessions._sessions = {"a": object(), "b": object()}

        app = web.Application()
        app["state"] = state
        request = MagicMock()
        request.app = app
        request.query = {"probe": "1"}

        resp = await api_restart(request)
        data = json.loads(resp.body)
        assert resp.status == 200
        assert data["running_agents"] == 2
        assert data["sessions"] == 2

    @pytest.mark.asyncio
    async def test_restart_triggers_graceful_reexec(
        self, monkeypatch, tmp_path
    ) -> None:
        """A real POST kicks off the graceful re-exec in the background and returns
        202-style {status: restarting} immediately (never actually exec's in test —
        _graceful_reexec is mocked)."""
        monkeypatch.setattr(
            "gideon.interfaces.dashboard.state.config_dir", lambda: tmp_path
        )
        import gideon.interfaces.dashboard.handlers.updates as upd

        reexec_called: list[bool] = []

        async def fake_reexec(state, *, auth_mode=""):  # type: ignore[no-untyped-def]
            reexec_called.append(True)

        monkeypatch.setattr(upd, "_graceful_reexec", fake_reexec)

        state = _make_state(monkeypatch, tmp_path)
        app = web.Application()
        app["state"] = state
        app["auth_cfg"] = None
        request = MagicMock()
        request.app = app
        request.query = {}

        resp = await upd.api_restart(request)
        data = json.loads(resp.body)
        assert resp.status == 200
        assert data["status"] == "restarting"
        await asyncio.sleep(0.05)
        assert reexec_called == [True]

    @pytest.mark.asyncio
    async def test_update_apply_pauses_on_a_dirty_tree(self, monkeypatch, tmp_path):
        """RUM-75: tracked edits PAUSE the apply — 409, an actionable reason and
        an exposed paused state — before any release lookup or git move."""
        monkeypatch.setattr(
            "gideon.interfaces.dashboard.state.config_dir", lambda: tmp_path
        )
        monkeypatch.setenv("GIDEON_PROJECT_DIR", str(tmp_path))
        (tmp_path / ".git").mkdir(exist_ok=True)

        import gideon.interfaces.dashboard.handlers.updates as upd

        monkeypatch.setattr(upd, "_apply_in_flight", False)
        _catalog()
        calls = _git_script(monkeypatch, tracked=[" M some_file.py"])
        state = _make_state(monkeypatch, tmp_path)
        app = web.Application()
        app["state"] = state
        request = MagicMock()
        request.app = app

        async def _unreachable(*a, **kw):  # pragma: no cover
            raise AssertionError("spawned a process for a paused checkout")

        monkeypatch.setattr("asyncio.create_subprocess_exec", _unreachable)

        resp = await upd.api_update_apply(request)
        data = json.loads(resp.body)
        assert resp.status == 409
        assert data["status"] == "paused"
        assert "commit or stash" in data["error"].lower()
        assert data["paths"] == [" M some_file.py"]
        assert state._update_progress == {"step": "paused", "detail": data["error"]}
        assert [c[0] for c in calls] == ["status"], f"it touched git anyway: {calls}"
        assert upd._apply_in_flight is False


def _catalog(tag: str = "v9.9.9") -> None:
    """Publish a release the resolver can select, through the real cache file."""
    su.write_releases_cache(
        {"releases": [{"tag": tag, "prerelease": False, "name": "", "body": ""}]}
    )


def _git_script(
    monkeypatch,
    *,
    tracked=(),
    head="aaaaaaa",
    target="bbbbbbb",
    ff=True,
    fetch_rc=0,
    merge_rc=0,
):
    """Script self_update's ONE sync git seam and record every invocation."""
    import subprocess as _sp

    calls: list[list[str]] = []

    def _run(args, *, cwd, timeout):
        calls.append(list(args))
        verb = args[0]
        if verb == "fetch":
            return _sp.CompletedProcess(args, fetch_rc, "", "boom" if fetch_rc else "")
        if verb == "rev-parse":
            ref = args[-1]
            sha = head if ref.startswith("HEAD") else target
            return _sp.CompletedProcess(args, 0, sha + "\n", "")
        if verb == "merge-base":
            return _sp.CompletedProcess(args, 0 if ff else 1, "", "")
        if verb == "merge":
            return _sp.CompletedProcess(args, merge_rc, "", "")
        if verb == "status":
            out = "".join(f"{line}\n" for line in tracked)
            return _sp.CompletedProcess(args, 0, out, "")
        return _sp.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(su, "_run_git", _run)
    return calls


class TestUpdateApplyPipeline:
    """The public manual-apply pipeline: fetch → `merge --ff-only` onto the
    selected release → pip install -e . → frontend rebuild → graceful re-exec,
    with the in-flight guard."""

    def _make_request(self, state):
        app = web.Application()
        app["state"] = state
        app["auth_cfg"] = None
        request = MagicMock()
        request.app = app
        return request

    @pytest.mark.asyncio
    async def test_full_pipeline_reaches_restart(self, monkeypatch, tmp_path) -> None:
        """All subprocesses succeed → steps pulling/installing/building/
        restarting fire and _graceful_reexec is REACHED (extends coverage
        through the restart step — the old test stopped at early progress,
        masking a dead restart tail)."""
        monkeypatch.setattr(
            "gideon.interfaces.dashboard.state.config_dir", lambda: tmp_path
        )
        monkeypatch.setenv("GIDEON_PROJECT_DIR", str(tmp_path))
        (tmp_path / ".git").mkdir(exist_ok=True)
        import gideon.interfaces.dashboard.handlers.updates as upd

        monkeypatch.setattr(upd, "_apply_in_flight", False)
        _catalog()
        git_calls = _git_script(monkeypatch)

        state = _make_state(monkeypatch, tmp_path)
        steps_seen: list[str] = []
        original_push = state.push_update_progress

        def track_push(step: str, detail: str = "") -> None:
            steps_seen.append(step)
            original_push(step, detail)

        monkeypatch.setattr(state, "push_update_progress", track_push)

        commands: list[tuple] = []

        async def fake_exec(*args, **kwargs):  # type: ignore[no-untyped-def]
            commands.append(args)
            proc = MagicMock()
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"", b""))
            return proc

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

        fe_built: list[str] = []

        async def fake_fe_build(proj, push_progress=None):  # type: ignore[no-untyped-def]
            fe_built.append(proj)

        monkeypatch.setattr(upd, "build_frontend_async", fake_fe_build)

        reexec_calls: list[dict] = []

        async def fake_reexec(state, *, auth_mode=""):  # type: ignore[no-untyped-def]
            reexec_calls.append({"auth_mode": auth_mode})

        monkeypatch.setattr(upd, "_graceful_reexec", fake_reexec)

        resp = await upd.api_update_apply(self._make_request(state))
        assert resp.status == 200
        assert json.loads(resp.body)["status"] == "updating"
        await asyncio.sleep(0.05)

        assert steps_seen == ["pulling", "installing", "building", "restarting"]
        assert "syncing" not in steps_seen
        flat = [str(a) for cmd in commands for a in cmd]
        assert "workspace" not in flat
        assert "make" not in flat
        assert "AIPowerUserCapabilities" not in flat
        assert any("pip" in cmd for cmd in commands)
        assert fe_built == [str(tmp_path)]
        assert len(reexec_calls) == 1
        assert upd._apply_in_flight is False
        assert ["fetch", "--tags", "origin"] in git_calls
        assert ["merge", "--ff-only", "v9.9.9"] in git_calls
        assert not any(c[0] in ("pull", "reset") for c in git_calls)

    @pytest.mark.asyncio
    async def test_pip_failure_stops_before_restart(
        self, monkeypatch, tmp_path
    ) -> None:
        """pip install failure → error step, no frontend build, no re-exec,
        and the in-flight guard is released."""
        monkeypatch.setattr(
            "gideon.interfaces.dashboard.state.config_dir", lambda: tmp_path
        )
        monkeypatch.setenv("GIDEON_PROJECT_DIR", str(tmp_path))
        (tmp_path / ".git").mkdir(exist_ok=True)
        import gideon.interfaces.dashboard.handlers.updates as upd

        monkeypatch.setattr(upd, "_apply_in_flight", False)
        _catalog()
        _git_script(monkeypatch)
        state = _make_state(monkeypatch, tmp_path)

        calls = [0]

        async def fake_exec(*args, **kwargs):  # type: ignore[no-untyped-def]
            calls[0] += 1
            proc = MagicMock()
            if any("pip" in str(a) for a in args):
                proc.communicate = AsyncMock(return_value=(b"", b"resolver exploded"))
                proc.returncode = 1
            else:
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            return proc

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

        fe_build = AsyncMock()
        monkeypatch.setattr(upd, "build_frontend_async", fe_build)
        reexec = AsyncMock()
        monkeypatch.setattr(upd, "_graceful_reexec", reexec)

        resp = await upd.api_update_apply(self._make_request(state))
        assert resp.status == 200
        await asyncio.sleep(0.05)

        assert state._update_progress == {
            "step": "error",
            "detail": "pip install failed",
        }
        fe_build.assert_not_awaited()
        reexec.assert_not_awaited()
        assert upd._apply_in_flight is False

    @pytest.mark.asyncio
    async def test_already_on_the_selected_release_restarts_without_installing(
        self, monkeypatch, tmp_path
    ) -> None:
        """The checkout already sits on the resolved tag ⇒ no move, no install,
        no rebuild — just the restart the user asked for."""
        monkeypatch.setattr(
            "gideon.interfaces.dashboard.state.config_dir", lambda: tmp_path
        )
        monkeypatch.setenv("GIDEON_PROJECT_DIR", str(tmp_path))
        (tmp_path / ".git").mkdir(exist_ok=True)
        import gideon.interfaces.dashboard.handlers.updates as upd

        monkeypatch.setattr(upd, "_apply_in_flight", False)
        _catalog()
        git_calls = _git_script(monkeypatch, head="same", target="same")
        state = _make_state(monkeypatch, tmp_path)
        steps_seen: list[str] = []
        orig = state.push_update_progress
        monkeypatch.setattr(
            state,
            "push_update_progress",
            lambda step, detail="": (steps_seen.append(step), orig(step, detail))[1],
        )
        spawned: list = []

        async def fake_exec(*args, **kwargs):  # type: ignore[no-untyped-def]
            spawned.append(args)
            proc = MagicMock()
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"", b""))
            return proc

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
        monkeypatch.setattr(upd, "_graceful_reexec", AsyncMock())
        monkeypatch.setattr(upd, "build_frontend_async", AsyncMock())

        app = web.Application()
        app["state"] = state
        app["auth_cfg"] = None
        request = MagicMock()
        request.app = app

        resp = await upd.api_update_apply(request)
        assert resp.status == 200
        await asyncio.sleep(0.05)
        assert steps_seen == ["pulling", "restarting"]
        assert "installing" not in steps_seen
        assert not any(c[0] == "merge" for c in git_calls)
        assert spawned == []
        assert upd._apply_in_flight is False

    @pytest.mark.asyncio
    async def test_no_release_for_the_channel_degrades_to_restart(
        self, monkeypatch, tmp_path
    ) -> None:
        """Nothing to move to (no published release) ⇒ restart only, with the
        reason — and it never reaches fetch/install/build, even on a CLEAN tree."""
        monkeypatch.setattr(
            "gideon.interfaces.dashboard.state.config_dir", lambda: tmp_path
        )
        monkeypatch.setenv("GIDEON_PROJECT_DIR", str(tmp_path))
        (tmp_path / ".git").mkdir(exist_ok=True)
        import gideon.interfaces.dashboard.handlers.updates as upd

        monkeypatch.setattr(upd, "_apply_in_flight", False)
        git_calls = _git_script(monkeypatch)
        state = _make_state(monkeypatch, tmp_path)
        steps_seen: list[tuple[str, str]] = []
        orig = state.push_update_progress
        monkeypatch.setattr(
            state,
            "push_update_progress",
            lambda step, detail="": (
                steps_seen.append((step, detail)),
                orig(step, detail),
            )[1],
        )
        spawned: list = []

        async def fake_exec(*args, **kwargs):  # type: ignore[no-untyped-def]
            spawned.append(args)
            proc = MagicMock()
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"", b""))
            return proc

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
        reexec = AsyncMock()
        monkeypatch.setattr(upd, "_graceful_reexec", reexec)
        fe_build = AsyncMock()
        monkeypatch.setattr(upd, "build_frontend_async", fe_build)

        app = web.Application()
        app["state"] = state
        app["auth_cfg"] = None
        request = MagicMock()
        request.app = app

        resp = await upd.api_update_apply(request)
        data = json.loads(resp.body)
        await asyncio.sleep(0.05)
        assert data["status"] == "restarting"
        assert "No published release" in data["detail"]
        assert [s for s, _ in steps_seen] == ["restarting"]
        assert reexec.await_count == 1
        fe_build.assert_not_awaited()
        assert spawned == []
        assert not any(c[0] in ("fetch", "merge") for c in git_calls)
        assert upd._apply_in_flight is False

    @pytest.mark.asyncio
    async def test_a_non_fast_forward_is_refused_instead_of_forced(
        self, monkeypatch, tmp_path
    ) -> None:
        """RUM-59: divergence is an error the operator can read, never a reset."""
        monkeypatch.setattr(
            "gideon.interfaces.dashboard.state.config_dir", lambda: tmp_path
        )
        monkeypatch.setenv("GIDEON_PROJECT_DIR", str(tmp_path))
        (tmp_path / ".git").mkdir(exist_ok=True)
        import gideon.interfaces.dashboard.handlers.updates as upd

        monkeypatch.setattr(upd, "_apply_in_flight", False)
        _catalog()
        git_calls = _git_script(monkeypatch, ff=False)
        state = _make_state(monkeypatch, tmp_path)
        reexec = AsyncMock()
        monkeypatch.setattr(upd, "_graceful_reexec", reexec)
        fe_build = AsyncMock()
        monkeypatch.setattr(upd, "build_frontend_async", fe_build)

        app = web.Application()
        app["state"] = state
        app["auth_cfg"] = None
        request = MagicMock()
        request.app = app

        assert (await upd.api_update_apply(request)).status == 200
        await asyncio.sleep(0.05)
        assert state._update_progress["step"] == "error"
        assert "fast-forward" in state._update_progress["detail"]
        assert not any(c[0] == "merge" for c in git_calls)
        reexec.assert_not_awaited()
        fe_build.assert_not_awaited()
        assert upd._apply_in_flight is False

    @pytest.mark.asyncio
    async def test_concurrent_apply_returns_409(self, monkeypatch, tmp_path) -> None:
        """While one apply is in flight, a second POST /api/update is 409."""
        monkeypatch.setattr(
            "gideon.interfaces.dashboard.state.config_dir", lambda: tmp_path
        )
        monkeypatch.setenv("GIDEON_PROJECT_DIR", str(tmp_path))
        (tmp_path / ".git").mkdir(exist_ok=True)
        import gideon.interfaces.dashboard.handlers.updates as upd

        monkeypatch.setattr(upd, "_apply_in_flight", True)
        state = _make_state(monkeypatch, tmp_path)

        exec_spy = AsyncMock()
        monkeypatch.setattr("asyncio.create_subprocess_exec", exec_spy)

        resp = await upd.api_update_apply(self._make_request(state))
        assert resp.status == 409
        assert "already in progress" in json.loads(resp.body)["error"]
        exec_spy.assert_not_awaited()
        assert upd._apply_in_flight is True


class TestPackageRoot:
    """package_root: git runs at GIDEON_PROJECT_DIR (repo root), but
    pip/frontend need the dir with pyproject.toml — top-level on a standalone
    checkout, nested at <repo>/Gideon in the monorepo layout."""

    def test_standalone_checkout_top_level(self, tmp_path) -> None:
        from gideon.operations.self_update import package_root

        (tmp_path / "pyproject.toml").write_text("[project]\n")
        assert package_root(str(tmp_path)) == str(tmp_path)

    def test_monorepo_nested_package(self, tmp_path) -> None:
        from gideon.operations.self_update import package_root

        nested = tmp_path / "Gideon"
        nested.mkdir()
        (nested / "pyproject.toml").write_text("[project]\n")
        assert package_root(str(tmp_path)) == str(nested)

    def test_no_pyproject_falls_back_to_proj(self, tmp_path) -> None:
        from gideon.operations.self_update import package_root

        assert package_root(str(tmp_path)) == str(tmp_path)


class TestReexecPreservesAuthMode:
    """#46: _graceful_reexec must carry the live auth mode into the child env via
    os.execve, so a Restart never silently flips auth-none → token-required (the
    original launcher's env may not survive the re-exec / reparent to PID 1)."""

    def test_reexec_passes_auth_mode_in_child_env(self, monkeypatch, tmp_path):
        import asyncio

        import gideon.interfaces.dashboard.handlers.updates as U

        state = _make_state(monkeypatch, tmp_path)
        monkeypatch.setattr(
            "gideon.interfaces.dashboard.chat.save_all_sessions_to_history",
            lambda s: None,
        )

        class _Sessions:
            async def close_all(self):
                return None

        monkeypatch.setattr(state, "sessions", _Sessions(), raising=False)

        captured = {}

        def _fake_execve(exe, argv, env):
            captured["env"] = env
            raise SystemExit

        monkeypatch.setattr(U.os, "execve", _fake_execve)
        monkeypatch.setattr(U.os.path, "isfile", lambda p: True)
        monkeypatch.setattr(U.os, "access", lambda p, m: True)

        with pytest.raises(SystemExit):
            asyncio.run(U._graceful_reexec(state, auth_mode="none"))
        assert captured["env"].get("GIDEON_AUTH_MODE") == "none"

    def test_reexec_without_auth_mode_leaves_env_unset(self, monkeypatch, tmp_path):
        import asyncio

        import gideon.interfaces.dashboard.handlers.updates as U

        state = _make_state(monkeypatch, tmp_path)
        monkeypatch.setattr(
            "gideon.interfaces.dashboard.chat.save_all_sessions_to_history",
            lambda s: None,
        )

        class _Sessions:
            async def close_all(self):
                return None

        monkeypatch.setattr(state, "sessions", _Sessions(), raising=False)
        monkeypatch.delenv("GIDEON_AUTH_MODE", raising=False)

        captured = {}

        def _fake_execve(exe, argv, env):
            captured["env"] = env
            raise SystemExit

        monkeypatch.setattr(U.os, "execve", _fake_execve)
        monkeypatch.setattr(U.os.path, "isfile", lambda p: True)
        monkeypatch.setattr(U.os, "access", lambda p, m: True)

        with pytest.raises(SystemExit):
            asyncio.run(U._graceful_reexec(state))
        assert "GIDEON_AUTH_MODE" not in captured["env"]


class TestGitCheckReadsRemoteVersion:
    """PUBL-8: the git-kind check must read the remote version through REAL git.

    These drive ``_do_update_check`` against genuine repositories (no mocked
    subprocesses) because the two defects the PUBL-8 drive found were both in the
    git plumbing itself: the blob path did not exist in the published layout, and
    the version literal it scraped had moved out of ``__init__.py``. A mocked
    ``git show`` cannot see either.
    """

    @staticmethod
    def _git(cwd, *args) -> None:
        import subprocess

        subprocess.run(
            ["git", "-c", "user.email=t@example.com", "-c", "user.name=T", *args],
            cwd=str(cwd),
            check=True,
            capture_output=True,
        )

    def _behind_clone(self, tmp_path, prefix: str):
        """An upstream whose tip bumps the version, plus a clone one commit behind.

        ``prefix`` selects the layout: ``""`` is the published standalone checkout
        (repo root IS the package root), ``"Gideon"`` the nested monorepo.
        Returns the clone's PACKAGE root — what GIDEON_PROJECT_DIR holds.
        """
        origin = tmp_path / "origin"
        origin.mkdir()
        self._git(origin, "init", "-q", "-b", "main")
        pkg = origin / prefix if prefix else origin
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "pyproject.toml").write_text(
            '[project]\nname = "gideon"\nversion = "0.1.3"\n'
        )
        self._git(origin, "add", "-A")
        self._git(origin, "commit", "-qm", "v0.1.3")
        (pkg / "pyproject.toml").write_text(
            '[project]\nname = "gideon"\nversion = "0.1.4"\n'
        )
        self._git(origin, "add", "-A")
        self._git(origin, "commit", "-qm", "v0.1.4")

        work = tmp_path / "work"
        self._git(tmp_path, "clone", "-q", str(origin), str(work))
        self._git(work, "reset", "--hard", "-q", "HEAD~1")
        return work / prefix if prefix else work

    @pytest.mark.parametrize("prefix", ["", "Gideon"])
    def test_check_detects_remote_version_in_both_layouts(
        self, monkeypatch, tmp_path, prefix
    ):
        """One commit behind a version-bumping tip ⇒ latest/available reflect it."""
        from gideon.interfaces.dashboard.handlers import updates as U

        proj = self._behind_clone(tmp_path, prefix)
        monkeypatch.setenv("GIDEON_PROJECT_DIR", str(proj))
        monkeypatch.setattr(U, "_local_version", "0.1.3")
        saved = dict(U._update_info)
        try:
            asyncio.run(U._do_update_check(force=True))
            assert U._update_info["latest"] == "0.1.4", U._update_info
            assert U._update_info["available"] is True
            assert U._update_info["checked"] is True
        finally:
            U._update_info.clear()
            U._update_info.update(saved)

    def test_check_reports_no_update_when_versions_match(self, monkeypatch, tmp_path):
        """Vacuity guard: the same probe must NOT claim an update at parity."""
        from gideon.interfaces.dashboard.handlers import updates as U

        proj = self._behind_clone(tmp_path, "")
        monkeypatch.setenv("GIDEON_PROJECT_DIR", str(proj))
        monkeypatch.setattr(U, "_local_version", "0.1.4")
        saved = dict(U._update_info)
        try:
            asyncio.run(U._do_update_check(force=True))
            assert U._update_info["available"] is False
        finally:
            U._update_info.clear()
            U._update_info.update(saved)


class TestCheckAgreesWithApplyOnNightly:
    """PUBL-8: on the branch-tracking channel, "behind" IS an available update.

    The drive measured the disagreement: commit tracking on, ``commits_behind``
    1, and the check still reported ``available: false`` while POST /api/update
    happily moved. The commit-tracking mode is now the ``nightly`` channel —
    the retired ``dashboard.update_dev_mode`` bool said the same thing with a
    second setting.
    """

    @staticmethod
    def _run(monkeypatch, *, dev_mode: bool, behind, kind: str = "git") -> dict:
        import types

        from gideon.core.config.loader import UpdatesConfig
        from gideon.interfaces.dashboard.handlers import updates as U

        cfg = types.SimpleNamespace(
            updates=UpdatesConfig(channel="nightly" if dev_mode else "stable")
        )
        monkeypatch.setattr(U.AppConfig, "load", staticmethod(lambda: cfg))
        monkeypatch.setattr(U, "_do_update_check", AsyncMock())
        monkeypatch.setattr(
            U.self_update,
            "build_update_status",
            AsyncMock(
                return_value={
                    "kind": kind,
                    "current": "0.1.3",
                    "latest": "0.1.3",
                    "update_available": False,
                    "commits_behind": behind,
                    "apply_method": "pipeline",
                    "instructions": [],
                }
            ),
        )
        resp = asyncio.run(U.api_update_check(MagicMock()))
        return json.loads(resp.body)

    def test_nightly_behind_is_available(self, monkeypatch) -> None:
        assert self._run(monkeypatch, dev_mode=True, behind=1)["available"] is True

    def test_nightly_up_to_date_is_not_available(self, monkeypatch) -> None:
        """Vacuity guard — the clause must not turn every nightly check green."""
        assert self._run(monkeypatch, dev_mode=True, behind=0)["available"] is False

    def test_a_tag_channel_behind_is_not_available(self, monkeypatch) -> None:
        """Stable rides release TAGS: commits behind is deliberately not news."""
        assert self._run(monkeypatch, dev_mode=False, behind=1)["available"] is False

    def test_non_git_kind_ignores_commits_behind(self, monkeypatch) -> None:
        assert (
            self._run(monkeypatch, dev_mode=True, behind=1, kind="pip")["available"]
            is False
        )
