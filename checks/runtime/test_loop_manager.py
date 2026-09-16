"""Unified Loop manager (Slice 2b.ii.b) — the shared lifecycle that arms/pauses/
stops/nudges/reaps every kind, delegating brief+nudge to the kind strategy."""

from __future__ import annotations

import asyncio
import os

import pytest

from gideon.automation.loop import files as loop_files
from gideon.automation.loop import manager, store
from gideon.automation.loop import watchdog as W
from gideon.automation.loop.loop import Loop, LoopStatus


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _tmp_config(monkeypatch, tmp_path):
    monkeypatch.setattr("gideon.automation.loop.files.config_dir", lambda: tmp_path)
    monkeypatch.setattr("gideon.engine.tasks.hierarchy.config_dir", lambda: tmp_path)
    import gideon.engine.tasks.native as nat

    monkeypatch.setattr(nat, "config_dir", lambda: tmp_path, raising=False)
    return tmp_path


class _FakeSession:
    def __init__(self, key):
        self.key = key
        self._trust = False
        self._running = False
        self.acp_provider = ""
        self.acp_provider_agent = ""
        self.reasoning_effort = ""
        self.acp_mode = ""

    @property
    def running(self):
        return self._running


class _FakeState:
    def __init__(self):
        self._sessions = {}

    def get_or_create_session(
        self, *, name, agent, model, workspace_dir, app, project_id=""
    ):
        s = self._sessions.get(name) or _FakeSession(name)
        s.agent = agent
        s.workspace_dir = workspace_dir
        s.app = app
        s.project_id = project_id
        self._sessions[name] = s
        return s

    def push_sessions_update(self):
        pass


class _FakeNudge:
    def __init__(self, lid, session_name):
        self.id, self.session_name, self.active = lid, session_name, True


class _FakeSvc:
    def __init__(self):
        self._loops = {}
        self._n = 0

    async def add(
        self,
        *,
        session_name,
        message,
        idle_secs,
        max_cycles,
        stop_sentinel_path,
        first_idle_secs=0,
    ):
        for lid in [
            lid for lid, lp in self._loops.items() if lp.session_name == session_name
        ]:
            del self._loops[lid]
        self._n += 1
        lp = _FakeNudge(f"N{self._n}", session_name)
        lp.message = message
        self._loops[lp.id] = lp
        return lp

    def get_by_session(self, session_name):
        return next(
            (lp for lp in self._loops.values() if lp.session_name == session_name), None
        )

    def list_all(self):
        return list(self._loops.values())

    async def update(self, loop_id, **kw):
        lp = self._loops.get(loop_id)
        if lp:
            for k, v in kw.items():
                setattr(lp, k, v)

    async def remove(self, loop_id):
        self._loops.pop(loop_id, None)


def _goal(**over):
    base = dict(
        id="",
        name="G",
        kind="goal",
        task="investigate the latency regression",
        kind_config={"goal_type": "open_ended"},
    )
    base.update(over)
    return store.create(Loop(**base))


class TestStopFromAPreLaunchState:
    """`PP-16`: a loop wedged in `intake` or `planning` must have an exit that is not DELETE.

    A dead classifier leaves a loop in `intake`; a dead planner leaves it in `planning`.
    Neither appeared in any `ACTION_SOURCE_STATES` row, so the route answered every
    lifecycle call with a 409 and the only way out was deleting the record. The store
    never forbade the transition — it refuses only moves OUT of a terminal state — so the
    guard was the whole obstacle. These drive the real `manager.stop`, not the guard
    alone: a guard admitting a state is worthless if the teardown it calls raises on a
    loop that never armed a worker.
    """

    @pytest.mark.parametrize("status", [LoopStatus.INTAKE, LoopStatus.PLANNING])
    def test_stop_terminates_a_pre_launch_loop(self, status):
        g = _goal(status=status.value)
        assert store.get(g.id).status == status.value
        state, svc = _FakeState(), _FakeSvc()
        out = _run(manager.stop(state, svc, g.id))
        assert (
            out.status == LoopStatus.STOPPED.value
        ), f"stop from {status.value} did not terminate"
        assert store.get(g.id).status == LoopStatus.STOPPED.value

    @pytest.mark.parametrize("status", [LoopStatus.INTAKE, LoopStatus.PLANNING])
    def test_the_guard_admits_stop_from_it(self, status):
        from gideon.automation.loop.loop import ACTION_SOURCE_STATES

        assert status in ACTION_SOURCE_STATES["stop"], (
            f"{status.value} is not a source state for stop, so loop_routes answers 409 and the "
            "loop stays wedged with DELETE as its only exit"
        )


class TestStartArmsWorker:
    def test_start_writes_brief_arms_session_and_nudge(self):
        g = _goal()
        state, svc = _FakeState(), _FakeSvc()
        out = _run(manager.start(state, svc, g.id))
        assert out.status == "running"
        d = loop_files.safe_loop_dir(g.id)
        assert (d / "brief.md").exists() and "# Goal Loop Brief" in (
            d / "brief.md"
        ).read_text()
        sess = state._sessions[manager.session_key(g.id)]
        assert sess._trust is True and sess.agent == "gideon-loop"
        assert store.get(g.id).session_key == manager.session_key(g.id)
        nl = svc.get_by_session(manager.session_key(g.id))
        assert nl is not None and "findings/cycle_NNN.json" in nl.message

    def test_code_kind_uses_coder_agent(self):
        c = store.create(
            Loop(
                id="",
                name="C",
                kind="code",
                task="add oauth login to the app",
                kind_config={"entry_stage": "design"},
            )
        )
        state, svc = _FakeState(), _FakeSvc()
        _run(manager.start(state, svc, c.id))
        assert state._sessions[manager.session_key(c.id)].agent == "gideon-coder"

    def test_goal_start_does_not_provision_tasks_project(self):
        g = _goal(plan=[{"title": "sub-goal one"}, {"title": "sub-goal two"}])
        state, svc = _FakeState(), _FakeSvc()
        _run(manager.start(state, svc, g.id))
        assert store.get(g.id).tasks_project_id == ""

    def test_code_start_provisions_tasks_project(self):
        c = store.create(
            Loop(
                id="",
                name="C",
                kind="code",
                task="add oauth login to the app",
                plan=[{"stage": "implementation", "title": "Impl"}],
                kind_config={"entry_stage": "implementation"},
            )
        )
        state, svc = _FakeState(), _FakeSvc()
        _run(manager.start(state, svc, c.id))
        assert store.get(c.id).tasks_project_id != ""

    def test_rearm_nudge_message_refreshes_to_current_stage(self):
        c = store.create(
            Loop(
                id="",
                name="C",
                kind="code",
                task="add oauth login here",
                plan=[
                    {"stage": "design", "title": "D", "objective": "design it"},
                    {"stage": "implementation", "title": "I", "objective": "build it"},
                ],
                phase_status={"design": "active"},
            )
        )
        state, svc = _FakeState(), _FakeSvc()
        _run(manager.start(state, svc, c.id))
        nl = svc.get_by_session(manager.session_key(c.id))
        assert "stage 1/2" in nl.message
        store.set_phase_status(c.id, "design", "done")
        store.set_phase_status(c.id, "implementation", "active")
        _run(manager.rearm_nudge_message(svc, c.id))
        assert "stage 2/2" in svc.get_by_session(manager.session_key(c.id)).message

    def test_rearm_nudge_message_noop_without_live_worker(self):
        c = store.create(
            Loop(
                id="",
                name="C",
                kind="code",
                task="add oauth login here",
                kind_config={"entry_stage": "design"},
            )
        )
        _run(manager.rearm_nudge_message(_FakeSvc(), c.id))

    def test_design_start_does_not_provision_empty_project(self):
        d = store.create(
            Loop(
                id="",
                name="D",
                kind="design",
                task="Build a design system for the marketing site",
            )
        )
        state, svc = _FakeState(), _FakeSvc()
        _run(manager.start(state, svc, d.id))
        assert store.get(d.id).tasks_project_id == ""


class TestPauseStopResume:
    def test_pause_then_resume(self):
        g = _goal()
        state, svc = _FakeState(), _FakeSvc()
        _run(manager.start(state, svc, g.id))
        _run(manager.pause(state, svc, g.id))
        assert store.get(g.id).status == "paused"
        _run(manager.start(state, svc, g.id))
        assert store.get(g.id).status == "running"

    def test_resume_rearms_with_current_stage_not_original(self):
        c = store.create(
            Loop(
                id="",
                name="C",
                kind="code",
                task="add oauth login here",
                plan=[
                    {"stage": "design", "title": "D", "objective": "design it"},
                    {"stage": "implementation", "title": "I", "objective": "build it"},
                ],
                phase_status={"design": "active"},
            )
        )
        state, svc = _FakeState(), _FakeSvc()
        _run(manager.start(state, svc, c.id))
        assert "stage 1/2" in svc.get_by_session(manager.session_key(c.id)).message
        _run(manager.pause(state, svc, c.id))
        store.set_phase_status(c.id, "design", "done")
        store.set_phase_status(c.id, "implementation", "active")
        _run(manager.start(state, svc, c.id))
        assert "stage 2/2" in svc.get_by_session(manager.session_key(c.id)).message

    def test_pause_deactivates_main_and_task_workers(self):
        c = store.create(
            Loop(
                id="",
                name="C",
                kind="code",
                task="add oauth login here",
                kind_config={},
            )
        )
        state, svc = _FakeState(), _FakeSvc()
        _run(
            svc.add(
                session_name=manager.session_key(c.id),
                message="",
                idle_secs=1,
                max_cycles=1,
                stop_sentinel_path="",
            )
        )
        _run(
            svc.add(
                session_name=manager.task_session_key(c.id, "t-1"),
                message="",
                idle_secs=1,
                max_cycles=1,
                stop_sentinel_path="",
            )
        )
        _run(manager.pause(state, svc, c.id))
        main = svc.get_by_session(manager.session_key(c.id))
        worker = svc.get_by_session(manager.task_session_key(c.id, "t-1"))
        assert main is not None and main.active is False
        assert worker is not None and worker.active is False

    def test_stop_is_terminal_and_drops_sentinel(self):
        g = _goal()
        state, svc = _FakeState(), _FakeSvc()
        _run(manager.start(state, svc, g.id))
        _run(manager.stop(state, svc, g.id))
        assert store.get(g.id).status == "stopped"
        assert manager.session_key(g.id) not in {
            lp.session_name for lp in svc._loops.values()
        }
        assert loop_files.stop_sentinel_path(g.id).exists()


class TestNudge:
    def test_nudge_writes_guidance_and_appends_history(self):
        g = _goal()
        state, svc = _FakeState(), _FakeSvc()
        _run(manager.start(state, svc, g.id))
        _run(manager.nudge(state, svc, g.id, "focus on the db path"))
        assert loop_files.read_guidance(g.id) == "focus on the db path"
        assert loop_files.get_nudges(g.id)[0]["text"] == "focus on the db path"

    def test_nudge_on_needs_input_resumes(self):
        g = _goal()
        state, svc = _FakeState(), _FakeSvc()
        _run(manager.start(state, svc, g.id))
        store.update_status(g.id, LoopStatus.NEEDS_INPUT)
        loop_files.write_question(g.id, "which db?")
        _run(manager.nudge(state, svc, g.id, "use postgres"))
        assert store.get(g.id).status == "running"
        assert loop_files.pending_question(g.id) is None

    def test_nudge_on_needs_input_brownfield_missing_workspace_stays_paused(self):
        c = store.create(
            Loop(
                id="",
                name="C",
                kind="code",
                task="fix the auth bug in the existing repo",
                kind_config={"project_kind": "brownfield"},
                workspace_dir="/nonexistent/repo/path",
            )
        )
        store.update_status(c.id, LoopStatus.NEEDS_INPUT)
        state, svc = _FakeState(), _FakeSvc()
        _run(manager.nudge(state, svc, c.id, "just an answer, not a re-pick"))
        assert store.get(c.id).status == LoopStatus.NEEDS_INPUT.value
        assert svc.get_by_session(manager.session_key(c.id)) is None
        assert (
            "missing"
            in (loop_files.pending_question(c.id) or {}).get("question", "").lower()
        )


class TestTaskWorker:
    """Parallel task-worker session lifecycle (code/design): spawn arms a per-task
    worker keyed loop-<id>-<task_id>; teardown removes it + clears its guidance.
    (Tasks storage is isolated by the autouse _tmp_config fixture.)"""

    def test_spawn_and_teardown_task_worker(self):
        from types import SimpleNamespace

        from gideon.automation.loop import tasks_link

        c = store.create(
            Loop(
                id="",
                name="C",
                kind="code",
                task="add oauth login",
                plan=[{"stage": "implementation", "title": "I"}],
                kind_config={"entry_stage": "design"},
            )
        )
        tasks_link.provision(c.id)
        ids = _run(
            tasks_link.decompose_phase(c.id, "implementation", [{"title": "Build it"}])
        )
        tid = ids[0]
        task = SimpleNamespace(
            id=tid, title="Build it", description="", action_plan=[], exit_criteria=[]
        )
        state, svc = _FakeState(), _FakeSvc()
        skey = _run(
            manager.spawn_task_worker(state, svc, store.get(c.id), task, "/ws/.wt/t")
        )
        assert skey == manager.task_session_key(c.id, tid) == f"loop-{c.id}-{tid}"
        assert svc.get_by_session(skey) is not None
        assert state._sessions[skey]._trust is True
        loop_files.write_task_guidance(c.id, tid, "prefer pure fns")
        _run(manager.teardown_task_worker(svc, c.id, tid))
        assert svc.get_by_session(skey) is None
        assert loop_files.read_task_guidance(c.id, tid) == ""

    def test_teardown_reaps_task_workers_with_main(self):
        c = store.create(
            Loop(id="", name="C", kind="code", task="t" * 12, kind_config={})
        )
        svc = _FakeSvc()
        _run(
            svc.add(
                session_name=manager.session_key(c.id),
                message="",
                idle_secs=1,
                max_cycles=1,
                stop_sentinel_path="",
            )
        )
        _run(
            svc.add(
                session_name=manager.task_session_key(c.id, "t-1"),
                message="",
                idle_secs=1,
                max_cycles=1,
                stop_sentinel_path="",
            )
        )
        _run(manager.teardown_worker(svc, c.id))
        assert svc.get_by_session(manager.session_key(c.id)) is None
        assert svc.get_by_session(manager.task_session_key(c.id, "t-1")) is None

    def test_teardown_for_delete_cleans_up_worktrees(self, tmp_path):
        import subprocess

        from gideon.automation.loop import worktree

        ws = tmp_path / "repo"
        ws.mkdir()
        for args in (
            ["init", "-q"],
            ["config", "user.email", "t@t"],
            ["config", "user.name", "t"],
        ):
            subprocess.run(["git", *args], cwd=ws, check=True)
        (ws / "f.txt").write_text("x")
        subprocess.run(["git", "add", "-A"], cwd=ws, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=ws, check=True)
        c = store.create(
            Loop(
                id="",
                name="C",
                kind="code",
                task="add oauth login here",
                workspace_dir=str(ws),
                kind_config={},
            )
        )
        assert worktree.ensure_base_commit(str(ws))
        wt = worktree.add_worktree(str(ws), "t-1", c.tasks_project_id)
        assert wt and os.path.isdir(wt)
        _run(manager.teardown_for_delete(_FakeSvc(), c.id))
        assert not os.path.isdir(wt)


class TestBootSweep:
    """The loop half of `PP-16`'s ONE boot-adoption path.

    These live in the *manager* test file although the entry point is now
    ``LoopWatchdog._boot_sweep``: what they exercise is the re-arm itself
    (``manager.start`` provisioning a real session + nudge, ``plan_walkthrough.advance_plan``
    re-kicking a stranded planner), and this file's ``_FakeState`` is the one that implements
    ``get_or_create_session`` and its autouse fixture is the one that isolates the Tasks
    hierarchy ``manager.start`` writes into. Only the caller moved — from
    ``manager.reap_orphaned_loops``, awaited from a gateway startup hook, to the first poll of
    the watchdog that owns the noun.
    """

    def test_reaps_running_orphan_with_no_live_session(self):
        g = _goal()
        store.update_status(g.id, LoopStatus.RUNNING)
        state, svc = _FakeState(), _FakeSvc()
        decided = _run(W.LoopWatchdog(state, svc)._boot_sweep())
        assert decided == {g.id}
        assert svc.get_by_session(manager.session_key(g.id)) is not None

    def test_skips_paused_and_live(self):
        paused = _goal()
        store.update_status(paused.id, LoopStatus.RUNNING)
        store.update_status(paused.id, LoopStatus.PAUSED)
        state, svc = _FakeState(), _FakeSvc()
        assert _run(W.LoopWatchdog(state, svc)._boot_sweep()) == set()

    def test_a_running_loop_with_an_idle_session_is_live_not_a_survivor(self):
        """The liveness predicate is session ABSENCE, not ``sess.running``. Between cycles a
        live loop's session exists with ``running`` False (autonudge fires a turn every
        ``idle_secs``), and re-arming that is a silent restart of healthy work — plus
        ``manager.start`` re-stamps the RUNNING row, which resets the trust window."""
        g = _goal()
        store.update_status(g.id, LoopStatus.RUNNING)
        state, svc = _FakeState(), _FakeSvc()
        state._sessions[manager.session_key(g.id)] = _FakeSession(
            manager.session_key(g.id)
        )
        assert state._sessions[manager.session_key(g.id)].running is False
        assert _run(W.LoopWatchdog(state, svc)._boot_sweep()) == set()
        assert svc.get_by_session(manager.session_key(g.id)) is None

    def test_rekicks_planning_orphan(self, monkeypatch):
        g = _goal()
        store.update_status(g.id, LoopStatus.PLANNING)
        kicked = []

        async def _fake_advance(state, svc, lid):
            kicked.append(lid)
            return "gated"

        monkeypatch.setattr(
            "gideon.automation.loop.plan_walkthrough.advance_plan", _fake_advance
        )
        decided = _run(W.LoopWatchdog(_FakeState(), _FakeSvc())._boot_sweep())
        assert decided == {g.id} and kicked == [g.id]

    def test_brownfield_orphan_with_missing_workspace_pauses_not_rearms(self):
        c = store.create(
            Loop(
                id="",
                name="C",
                kind="code",
                task="fix the auth bug in the existing repo",
                kind_config={"project_kind": "brownfield"},
                workspace_dir="/nonexistent/repo/path",
            )
        )
        store.update_status(c.id, LoopStatus.RUNNING)
        state, svc = _FakeState(), _FakeSvc()
        _run(W.LoopWatchdog(state, svc)._boot_sweep())
        assert store.get(c.id).status == LoopStatus.NEEDS_INPUT.value
        assert svc.get_by_session(manager.session_key(c.id)) is None
